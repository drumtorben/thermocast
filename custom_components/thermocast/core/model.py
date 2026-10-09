"""Online-learning thermal zone model (no Home Assistant imports).

Discrete hourly ARX-style grey-box model per zone:

    T[t+1] - T[t] = b0
                  + a   * (T_out[t] - T[t])                 heat loss
                  + sum b_s,l * I_s[t-l]                     solar per surface & lag
                  + sum c_k   * Q[t-k]                       heating proxy with lags (screed!)
                  + sum d_n   * (T_n[t] - T[t])              neighbour coupling
                  + sum e_g   * G_g[t]                       internal gains

Parameters are learned online with recursive least squares (RLS) and a
forgetting factor, projected to physically sensible signs.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

SURFACE_LAGS: dict[str, tuple[int, ...]] = {
    # glazing acts immediately, opaque roof/wall with delay through insulation
    "window": (0, 1),
    "roof": (1, 2, 3, 4, 6),  # insulated roofs: heat arrives 1–6 h later (attic peak 3–7 h after the sun)
    "wall": (2, 4, 6),
}

HEAT_LAGS: dict[str, tuple[int, ...]] = {
    # underfloor heating: screed delays the effect by hours
    "fbh": (0, 1, 2, 3, 4, 6),
    "radiator": (0, 1),
}


@dataclass(frozen=True)
class SurfaceSpec:
    """A sun-exposed surface of a zone."""

    kind: str  # window | roof | wall
    azimuth: float  # compass degrees, 0 = N, 90 = E, 180 = S
    tilt: float  # degrees, 90 = vertical
    name: str = ""

    @property
    def lags(self) -> tuple[int, ...]:
        return SURFACE_LAGS[self.kind]

    @property
    def key(self) -> str:
        """Key of the irradiance series (same orientation -> same series)."""
        return f"{round(self.tilt)}_{round(self.azimuth) % 360}"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SurfaceSpec:
        kind = str(data.get("kind", "window")).lower()
        if kind not in SURFACE_LAGS:
            raise ValueError(f"unknown surface kind: {kind}")
        azimuth = float(data["azimuth"])
        tilt = float(data.get("tilt", 90 if kind != "roof" else 40))
        if not 0 <= tilt <= 90:
            raise ValueError("tilt must be within 0..90")
        return cls(kind=kind, azimuth=azimuth % 360, tilt=tilt, name=str(data.get("name", "")))


@dataclass(frozen=True)
class ZoneSpec:
    heat_type: str = "fbh"  # fbh | radiator
    surfaces: tuple[SurfaceSpec, ...] = ()
    n_neighbors: int = 0
    n_gains: int = 0

    @property
    def heat_lags(self) -> tuple[int, ...]:
        return HEAT_LAGS[self.heat_type]

    @property
    def max_lag(self) -> int:
        lags = list(self.heat_lags)
        for s in self.surfaces:
            lags.extend(s.lags)
        return max(lags) if lags else 0


@dataclass
class HourRecord:
    """Inputs of one hour. ``temp`` is the zone temperature at the start of the hour."""

    temp: float
    t_out: float
    irr: dict[str, float] = field(default_factory=dict)  # W/m² per surface key
    q: float = 0.0  # heating proxy (K, e.g. flow - room while heating)
    neighbors: tuple[float, ...] = ()
    gains: tuple[float, ...] = ()
    valid: bool = True  # False e.g. while a window is open
    # weather uncertainty of a forecast hour (σ of t_out in K, of irr in W/m²) – not stored, measured hours have none
    t_out_sd: float = 0.0
    irr_sd: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "temp": self.temp,
            "t_out": self.t_out,
            "irr": self.irr,
            "q": self.q,
            "neighbors": list(self.neighbors),
            "gains": list(self.gains),
            "valid": self.valid,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> HourRecord:
        # JSON stores (HA's orjson) write NaN as null
        return cls(
            temp=d["temp"] if d["temp"] is not None else math.nan,
            t_out=d["t_out"] if d["t_out"] is not None else math.nan,
            irr=dict(d.get("irr", {})),
            q=d.get("q", 0.0),
            neighbors=tuple(d.get("neighbors", ())),
            gains=tuple(d.get("gains", ())),
            valid=d.get("valid", True),
        )


BLOCK_PUMP_SHARE = 0.75  # an hour is a block hour when the heating pump ran at least this share of it
Q_ON_ALPHA = 0.2  # q_on follows the last ~5 block hours (a changed heating curve shows after one or two blocks)


def block_q(q: float, pump_share: float | None) -> float | None:
    """Heating proxy of a full block hour from an hour's mean ``q``, None if the hour was no block hour.

    Only hours in which the pump ran (almost) the whole hour count, normalised to the pump time: short pump
    runs (overrun, a block starting mid-hour) would make the planner underestimate a block several times
    over. Without a pump signal (``None``) hours with q > 1 count, as before.
    """
    if pump_share is None:
        return q if q > 1.0 else None
    if pump_share < BLOCK_PUMP_SHARE or q <= 0.0:
        return None
    return q / pump_share


def update_q_on(q_on: float | None, q: float, pump_share: float | None) -> float | None:
    """``q_on`` (typical heating proxy while a block runs, what the planner assumes) after one more hour:
    follows the recent block hours, so the old always-on low-flow operation fades after a block or two."""
    b = block_q(q, pump_share) if math.isfinite(q) else None
    if b is None:
        return q_on
    return b if q_on is None else (1.0 - Q_ON_ALPHA) * q_on + Q_ON_ALPHA * b


@dataclass
class Prediction:
    mean: list[float]
    std: list[float]  # model and weather uncertainty: sqrt(model variance + weather²)
    contrib: list[dict[str, float]] = field(default_factory=list)  # per hour: change by cause (K/h)
    weather: list[float] = field(default_factory=list)  # per hour: σ share of the weather forecast (K)

    def lower(self, z: float = 1.0) -> list[float]:
        return [m - z * s for m, s in zip(self.mean, self.std)]


def _group_of(name: str) -> str:
    """Parameter name -> display group (all lags of one surface/heating share a group)."""
    if name == "bias":
        return "base"
    if name.startswith("solar:"):
        _, key, kind, _lag = name.split(":")
        return f"sun:{key}:{kind}"
    if name.startswith("heat:"):
        return "heat"
    return name  # loss, neighbor:<i>, gain:<i>


class OnlineZoneModel:
    """RLS-learned thermal model of one zone."""

    def __init__(
        self,
        spec: ZoneSpec,
        forgetting: float = 0.996,  # per hour -> memory ~ 1/(1-λ) = 250 h ≈ 10 days
        p0: float = 1.0,
        max_trace: float = 50.0,
    ) -> None:
        self.spec = spec
        self.forgetting = forgetting
        self.max_trace = max_trace
        self._build_layout()
        self.theta = self._prior()
        self.P = np.diag(self._prior_scale()) * p0
        self.history: list[HourRecord] = []
        self.n_updates = 0
        self.resid_var = 0.05**2  # (K/h)²
        self.mae = 0.0

    # ------------------------------------------------------------------ layout
    def _build_layout(self) -> None:
        self._surface_lags = [(s.key, s.lags) for s in self.spec.surfaces]  # features() runs per predicted hour
        names: list[str] = ["bias", "loss"]
        nonneg: list[bool] = [False, True]
        for s in self.spec.surfaces:
            for lag in s.lags:
                names.append(f"solar:{s.key}:{s.kind}:{lag}")
                nonneg.append(True)
        for lag in self.spec.heat_lags:
            names.append(f"heat:{lag}")
            nonneg.append(True)
        for i in range(self.spec.n_neighbors):
            names.append(f"neighbor:{i}")
            nonneg.append(True)
        for i in range(self.spec.n_gains):
            names.append(f"gain:{i}")
            nonneg.append(True)
        self.names = names
        self._solar_cols = [  # (surface key, lag, column) – same order as the features
            (key, lag, 2 + i) for i, (key, lag) in enumerate((s.key, lag) for s in self.spec.surfaces for lag in s.lags)
        ]
        self._neighbor_cols = [i for i, n in enumerate(names) if n.startswith("neighbor:")]
        self.nonneg = np.array(nonneg)
        self.dim = len(names)
        self.groups = [_group_of(n) for n in names]

    def _prior(self) -> np.ndarray:
        theta = np.zeros(self.dim)
        n_heat = len(self.spec.heat_lags)
        for i, name in enumerate(self.names):
            if name == "loss":
                theta[i] = 0.02  # time constant ~ 50 h
            elif name.startswith("solar"):
                theta[i] = 1e-4  # 500 W/m² -> 0.05 K/h per lag
            elif name.startswith("heat"):
                theta[i] = 0.03 / n_heat  # 10 K proxy -> 0.3 K/h total
            elif name.startswith("neighbor"):
                theta[i] = 0.01
        return theta

    def _prior_scale(self) -> np.ndarray:
        """Initial covariance per parameter, scaled to the feature magnitude."""
        scale = np.ones(self.dim)
        for i, name in enumerate(self.names):
            if name == "bias":
                scale[i] = 0.01
            elif name == "loss":
                scale[i] = 1e-3
            elif name.startswith("solar"):
                scale[i] = 1e-7
            elif name.startswith(("heat", "neighbor", "gain")):
                scale[i] = 1e-3
        return scale

    # ---------------------------------------------------------------- features
    def features(self, rec: HourRecord, history: list[HourRecord]) -> np.ndarray:
        """Feature vector for ``rec`` given previous records (oldest first)."""

        def past(lag: int) -> HourRecord:
            if lag == 0:
                return rec
            if len(history) >= lag:
                return history[-lag]
            return history[0] if history else rec

        phi = [1.0, rec.t_out - rec.temp]
        for key, lags in self._surface_lags:
            for lag in lags:
                phi.append(past(lag).irr.get(key, 0.0))
        for lag in self.spec.heat_lags:
            phi.append(past(lag).q)
        for i in range(self.spec.n_neighbors):
            n = rec.neighbors[i] if i < len(rec.neighbors) else rec.temp
            phi.append(n - rec.temp)
        for i in range(self.spec.n_gains):
            phi.append(rec.gains[i] if i < len(rec.gains) else 0.0)
        return np.asarray(phi, dtype=float)

    # ------------------------------------------------------------------ update
    def update(self, rec: HourRecord, temp_next: float) -> float | None:
        """One RLS step. Returns the a-priori error (K/h) or None if skipped."""
        if not rec.valid or not (math.isfinite(rec.temp) and math.isfinite(temp_next)):
            self._push(rec)
            return None

        phi = self.features(rec, self.history)
        y = temp_next - rec.temp
        err = y - float(phi @ self.theta)

        # robust: Huber-style clipping of the innovation
        sigma = math.sqrt(self.resid_var)
        clip = 3.0 * sigma
        err_used = max(-clip, min(clip, err))

        lam = self.forgetting
        Pphi = self.P @ phi
        denom = lam + float(phi @ Pphi)
        k = Pphi / denom
        self.theta = self.theta + k * err_used
        self.P = (self.P - np.outer(k, Pphi)) / lam
        self.P = 0.5 * (self.P + self.P.T)

        # keep physically sensible signs
        self.theta[self.nonneg] = np.maximum(self.theta[self.nonneg], 0.0)
        # anti wind-up: bounded uncertainty when inputs are not excited (e.g. summer)
        tr = float(np.sum(np.diag(self.P) / self._prior_scale()))
        if tr > self.max_trace:
            self.P *= self.max_trace / tr

        self.resid_var = 0.98 * self.resid_var + 0.02 * min(err * err, 1.0)
        self.mae = abs(err) if self.n_updates == 0 else 0.97 * self.mae + 0.03 * abs(err)
        self.n_updates += 1
        self._push(rec)
        return err

    def _push(self, rec: HourRecord) -> None:
        self.history.append(rec)
        keep = max(self.spec.max_lag, 1)
        if len(self.history) > keep:
            self.history = self.history[-keep:]

    # ----------------------------------------------------------------- predict
    def weather_std(
        self, future: list[HourRecord], history: list[HourRecord] | None = None, w0: float = 0.0
    ) -> list[float]:
        """σ share of the weather forecast per predicted hour (K).

        An error e in the outdoor temperature shifts the next hour by a·e, an irradiance error by Σ b·e (lagged
        like the sun). Weather errors last for hours (a whole model run is too warm or too cloudy), so they add up
        linearly instead of as independent noise; the room forgets an old error at the rate of its losses.
        ``w0`` carries the share across the rollout's re-plans.
        """
        hist = list(self.history if history is None else history)
        seq = hist + list(future)
        n_h = len(hist)
        th = self.theta
        decay = max(0.0, 1.0 - float(th[1]) - sum(float(th[c]) for c in self._neighbor_cols))
        w, out = float(w0), []
        for t, rec in enumerate(future):
            step = float(th[1]) * rec.t_out_sd
            for key, lag, col in self._solar_cols:
                if lag == 0:
                    past = rec
                elif n_h + t >= lag:
                    past = seq[n_h + t - lag]
                else:  # fewer records than the lag: the oldest one (as in ``features``)
                    past = seq[0] if n_h + t > 0 else rec
                if past.irr_sd:
                    step += float(th[col]) * past.irr_sd.get(key, 0.0)
            w = decay * w + step
            out.append(w)
        return out

    def predict(
        self,
        temp_now: float,
        future: list[HourRecord],
        var0: float = 0.0,
        history: list[HourRecord] | None = None,
        with_contrib: bool = True,
        w0: float = 0.0,
    ) -> Prediction:
        """Roll the model forward. ``future[i].temp`` is ignored (simulated).

        ``var0`` is the model variance of ``temp_now`` and ``w0`` its weather σ share (rollouts carry uncertainty
        across re-plans); ``history`` replaces the model's own lag history without modifying it.
        """
        weather = self.weather_std(future, history, w0)
        hist = list(self.history if history is None else history)
        keep = max(self.spec.max_lag, 1)
        temp = temp_now
        means: list[float] = []
        stds: list[float] = []
        contribs: list[dict[str, float]] = []
        var = var0
        sigma2 = self.resid_var
        for rec, w in zip(future, weather):
            r = HourRecord(
                temp=temp,
                t_out=rec.t_out,
                irr=rec.irr,
                q=rec.q,
                neighbors=rec.neighbors,
                gains=rec.gains,
            )
            phi = self.features(r, hist)
            terms = phi * self.theta
            if with_contrib:
                contrib: dict[str, float] = {}
                for group, value in zip(self.groups, terms):
                    contrib[group] = contrib.get(group, 0.0) + float(value)
                contribs.append(contrib)
            temp = temp + float(terms.sum())
            var += sigma2 + float(phi @ self.P @ phi) * sigma2
            means.append(temp)
            stds.append(math.sqrt(var + w * w))
            hist.append(r)
            if len(hist) > keep:
                hist = hist[-keep:]
        return Prediction(mean=means, std=stds, contrib=contribs, weather=weather)

    def predict_batch(
        self,
        temp_now: float,
        future: list[HourRecord],
        q: np.ndarray,
        var0: float = 0.0,
        history: list[HourRecord] | None = None,
        w0: float = 0.0,
    ) -> tuple[np.ndarray, np.ndarray]:
        """``predict`` for many heating schedules at once (the planner's candidates).

        ``q`` (schedules × hours) replaces ``future[i].q``. Returns (mean, std), each schedules × hours.
        Only the temperature-dependent columns (loss, neighbours) and the heating lags differ between
        schedules; everything else is built once per hour (also the weather σ share).
        """
        weather = np.asarray(self.weather_std(future, history, w0), dtype=float)
        hist = list(self.history if history is None else history)
        n_s, horizon = q.shape
        n_h = len(hist)

        def source(t: int, lag: int) -> tuple[bool, int]:
            """Record that ``past(lag)`` of ``predict`` reads at step ``t``: (from history?, index)."""
            if lag == 0:
                return False, t
            if n_h + t >= lag:
                i = n_h + t - lag
                return (True, i) if i < n_h else (False, i - n_h)
            if n_h + t > 0:  # fewer records than the lag: the oldest one
                return (True, 0) if n_h else (False, 0)
            return False, t

        # constant part per hour (the temperature-dependent columns are completed in the loop)
        const = np.zeros((horizon, self.dim))
        heat_from_q: list[list[tuple[int, int]]] = [[] for _ in range(horizon)]  # (column, q index) per hour
        temp_cols: list[list[int]] = [[] for _ in range(horizon)]  # columns holding x - T
        for t, rec in enumerate(future):
            col = 0
            const[t, col] = 1.0
            const[t, col + 1] = rec.t_out
            temp_cols[t].append(col + 1)
            col += 2
            for key, lags in self._surface_lags:
                for lag in lags:
                    from_hist, i = source(t, lag)
                    const[t, col] = (hist[i] if from_hist else future[i]).irr.get(key, 0.0)
                    col += 1
            for lag in self.spec.heat_lags:
                from_hist, i = source(t, lag)
                if from_hist:
                    const[t, col] = hist[i].q
                else:
                    heat_from_q[t].append((col, i))
                col += 1
            for i in range(self.spec.n_neighbors):
                if i < len(rec.neighbors):  # a missing neighbour reads the zone itself: n - T = 0
                    const[t, col] = rec.neighbors[i]
                    temp_cols[t].append(col)
                col += 1
            for i in range(self.spec.n_gains):
                const[t, col] = rec.gains[i] if i < len(rec.gains) else 0.0
                col += 1

        means = np.empty((n_s, horizon))
        var = np.empty((n_s, horizon))
        temp = np.full(n_s, float(temp_now))
        v = np.full(n_s, float(var0))
        sigma2 = self.resid_var
        phi = np.empty((n_s, self.dim))
        for t in range(horizon):
            phi[:] = const[t]
            for c in temp_cols[t]:
                phi[:, c] -= temp
            for c, i in heat_from_q[t]:
                phi[:, c] = q[:, i]
            temp = temp + (phi * self.theta).sum(axis=1)
            v = v + sigma2 + ((phi @ self.P) * phi).sum(axis=1) * sigma2
            means[:, t] = temp
            var[:, t] = v
        return means, np.sqrt(var + weather**2)

    # -------------------------------------------------------------- inspection
    def params(self) -> dict[str, float]:
        return {n: float(v) for n, v in zip(self.names, self.theta)}

    def solar_response(self) -> dict[str, float]:
        """Summed solar coefficient per surface (K/h per kW/m²) – for diagnostics."""
        out: dict[str, float] = {}
        for n, v in zip(self.names, self.theta):
            if n.startswith("solar:"):
                _, key, kind, _lag = n.split(":")
                label = f"{kind}:{key}"
                out[label] = out.get(label, 0.0) + float(v) * 1000.0
        return out

    def group_labels(self) -> dict[str, str]:
        """Display names of the surface groups (name from the config, else kind + orientation)."""
        labels: dict[str, str] = {}
        for s in self.spec.surfaces:
            labels.setdefault(f"sun:{s.key}:{s.kind}", s.name or f"{s.kind} {round(s.azimuth)}°/{round(s.tilt)}°")
        return labels

    # ------------------------------------------------------------- persistence
    def to_dict(self) -> dict[str, Any]:
        return {
            "names": self.names,
            "theta": self.theta.tolist(),
            "P": self.P.tolist(),
            "history": [h.to_dict() for h in self.history],
            "n_updates": self.n_updates,
            "resid_var": self.resid_var,
            "mae": self.mae,
        }

    def load_dict(self, data: dict[str, Any]) -> bool:
        """Restore state. Returns False if the layout changed (config edited)."""
        if data.get("names") != self.names:
            return False
        self.theta = np.asarray(data["theta"], dtype=float)
        self.P = np.asarray(data["P"], dtype=float)
        self.history = [HourRecord.from_dict(h) for h in data.get("history", [])]
        self.n_updates = int(data.get("n_updates", 0))
        self.resid_var = float(data.get("resid_var", self.resid_var))
        self.mae = float(data.get("mae", 0.0))
        return True
