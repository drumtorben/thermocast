"""Model quality: hourly logs, hindcast, error metrics, parameter interpretation (no HA imports).

Two different questions, kept apart on purpose:
* *Hindcast* – how well does the current model simulate the past when fed the **measured** inputs?
  (pure model quality, restart at every local midnight)
* *Forecast* – how good was the operational prediction (forecast weather + planned blocks)
  1/3/6/12/24 h ahead? (what the planner actually relied on)
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, tzinfo
from statistics import fmean
from typing import Any

from .model import HourRecord, OnlineZoneModel, Prediction

HOUR = timedelta(hours=1)
HORIZONS: tuple[int, ...] = (1, 3, 6, 12, 24)
LOG_HOURS = 14 * 24


def _finite(x: Any) -> float | None:
    """A stored number, or None for missing/NaN (HA's JSON store writes NaN as null)."""
    return float(x) if isinstance(x, int | float) and math.isfinite(x) else None


class RingLog:
    """Bounded list of JSON-serialisable entries (oldest dropped first)."""

    def __init__(self, maxlen: int = LOG_HOURS) -> None:
        self._items: deque[dict[str, Any]] = deque(maxlen=maxlen)

    def append(self, item: dict[str, Any]) -> None:
        self._items.append(item)

    def to_list(self) -> list[dict[str, Any]]:
        return list(self._items)

    def load(self, items: list[dict[str, Any]]) -> None:
        self._items.clear()
        self._items.extend(items)

    def __len__(self) -> int:
        return len(self._items)


class ForecastLog:
    """Operational predictions per issue hour: horizon k -> (mean, std) for the start of hour issue + k."""

    def __init__(self, max_issues: int = LOG_HOURS) -> None:
        self._max = max_issues
        self._d: dict[str, dict[str, list[float]]] = {}

    def record(self, issue: datetime, pred: Prediction, horizons: tuple[int, ...] = HORIZONS) -> None:
        entry = {
            str(k): [round(pred.mean[k - 1], 3), round(pred.std[k - 1], 3)] for k in horizons if len(pred.mean) >= k
        }
        key = issue.isoformat()
        self._d.pop(key, None)
        self._d[key] = entry
        while len(self._d) > self._max:
            self._d.pop(next(iter(self._d)))

    def predicted(self, target: datetime, k: int) -> tuple[float, float] | None:
        entry = self._d.get((target - k * HOUR).isoformat())
        if not entry or str(k) not in entry:
            return None
        mean, std = (_finite(x) for x in entry[str(k)])
        return None if mean is None or std is None else (mean, std)

    def to_dict(self) -> dict[str, dict[str, list[float]]]:
        return dict(self._d)

    def load(self, data: dict[str, dict[str, list[float]]]) -> None:
        self._d = dict(list(data.items())[-self._max :])


# ------------------------------------------------------------------ hindcast
def _usable(rec: HourRecord) -> bool:
    return math.isfinite(rec.t_out) and math.isfinite(rec.temp)


def hindcast(model: OnlineZoneModel, entries: list[dict[str, Any]], tz: tzinfo) -> list[dict[str, Any] | None]:
    """Simulate every logged hour with the current model and the logged (measured) inputs.

    Restarts from the measured temperature at local midnight, after gaps and after unusable hours.
    Returns per entry ``{"start", "mean", "contrib"}`` (temperature at start/end of the hour) or None.
    """
    keep = max(model.spec.max_lag, 1)
    out: list[dict[str, Any] | None] = []
    hist: list[HourRecord] = []
    temp: float | None = None
    prev_t: datetime | None = None
    for e in entries:
        t = datetime.fromisoformat(e["t"])
        rec = HourRecord.from_dict(e["rec"])
        gap = prev_t is None or t - prev_t != HOUR
        if gap:
            hist = []
        if gap or temp is None or t.astimezone(tz).hour == 0:
            temp = rec.temp if math.isfinite(rec.temp) else None
        prev_t = t
        if temp is None or not _usable(rec):
            out.append(None)
            temp = None  # restart with the next measurement
            hist = [*hist, rec][-keep:]
            continue
        p = model.predict(temp, [rec], history=hist)
        out.append({"start": temp, "mean": p.mean[0], "contrib": p.contrib[0]})
        temp = p.mean[0]
        hist = [*hist, rec][-keep:]
    return out


# ------------------------------------------------------------------- metrics
def _since(entries: list[dict[str, Any]], since: datetime | None) -> list[int]:
    return [i for i, e in enumerate(entries) if since is None or datetime.fromisoformat(e["t"]) >= since]


def one_step_metrics(entries: list[dict[str, Any]], since: datetime | None) -> dict[str, Any]:
    """A-priori RLS errors (K/h) of the learned hours."""
    errs = [entries[i]["err"] for i in _since(entries, since) if entries[i].get("err") is not None]
    if not errs:
        return {"n": 0, "mae": None, "bias": None}
    return {"n": len(errs), "mae": fmean(abs(x) for x in errs), "bias": fmean(errs)}


def hindcast_metrics(
    entries: list[dict[str, Any]], hc: list[dict[str, Any] | None], since: datetime | None
) -> dict[str, Any]:
    diffs = [
        hc[i]["mean"] - entries[i]["temp_next"]
        for i in _since(entries, since)
        if hc[i] is not None and entries[i]["rec"].get("valid", True)
        and _finite(entries[i].get("temp_next")) is not None
    ]
    if not diffs:
        return {"n": 0, "mae": None, "bias": None}
    return {"n": len(diffs), "mae": fmean(abs(d) for d in diffs), "bias": fmean(diffs)}


def measured_by_hour(entries: list[dict[str, Any]]) -> dict[datetime, float]:
    """Measured temperature at hour starts (from the log: start and end of each logged hour)."""
    out: dict[datetime, float] = {}
    for e in entries:
        t = datetime.fromisoformat(e["t"])
        if (temp := _finite(e["rec"].get("temp"))) is not None:
            out[t] = temp
        if (temp := _finite(e.get("temp_next"))) is not None:
            out[t + HOUR] = temp
    return out


def forecast_metrics(
    flog: ForecastLog,
    measured: dict[datetime, float],
    since: datetime | None,
    horizons: tuple[int, ...] = HORIZONS,
) -> dict[str, dict[str, Any]]:
    """MAE, bias (prediction − measurement) and ±1σ coverage per horizon."""
    out: dict[str, dict[str, Any]] = {}
    for k in horizons:
        diffs: list[float] = []
        hits = 0
        for target, value in measured.items():
            if since is not None and target < since:
                continue
            pred = flog.predicted(target, k)
            if pred is None:
                continue
            d = pred[0] - value
            diffs.append(d)
            hits += abs(d) <= pred[1]
        out[str(k)] = {
            "n": len(diffs),
            "mae": fmean(abs(d) for d in diffs) if diffs else None,
            "bias": fmean(diffs) if diffs else None,
            "coverage": hits / len(diffs) if diffs else None,
        }
    return out


# --------------------------------------------------------------- calibration
COVERAGE = 0.6827  # share of a normal distribution within ±1σ


def calibration(
    flog: ForecastLog,
    measured: dict[datetime, float],
    since: datetime | None,
    horizons: tuple[int, ...] = HORIZONS,
    min_n: int = 48,
    max_factor: float = 4.0,
) -> dict[int, float]:
    """σ inflation per horizon so that ±σ covers ~68 % of the observed errors.

    Only inflates (≥ 1 – never less cautious than the model itself) and needs ``min_n`` checks per
    horizon, otherwise 1.0. The log must hold the *raw* model σ, or the calibration feeds on itself.
    """
    out: dict[int, float] = {}
    for k in horizons:
        ratios: list[float] = []
        for target, value in measured.items():
            if since is not None and target < since:
                continue
            pred = flog.predicted(target, k)
            if pred is None or pred[1] <= 0:
                continue
            ratios.append(abs(pred[0] - value) / pred[1])
        if len(ratios) < min_n:
            out[k] = 1.0
            continue
        ratios.sort()
        q = ratios[min(len(ratios) - 1, int(COVERAGE * len(ratios)))]
        out[k] = round(min(max(q, 1.0), max_factor), 3)
    return out


def std_scale_profile(factors: dict[int, float], length: int = 48) -> tuple[float, ...]:
    """Per hour ahead (index 0 = 1 h): linear interpolation between the calibrated horizons."""
    if not factors or all(f == 1.0 for f in factors.values()):
        return ()
    ks = sorted(factors)
    out: list[float] = []
    for i in range(length):
        h = i + 1
        if h <= ks[0]:
            out.append(factors[ks[0]])
        elif h >= ks[-1]:
            out.append(factors[ks[-1]])
        else:
            lo = max(k for k in ks if k <= h)
            hi = min(k for k in ks if k >= h)
            w = 0.0 if hi == lo else (h - lo) / (hi - lo)
            out.append(round(factors[lo] + w * (factors[hi] - factors[lo]), 4))
    return tuple(out)


# ------------------------------------------------------------- interpretation
def _param_std(model: OnlineZoneModel) -> dict[str, float]:
    sigma = math.sqrt(model.resid_var)
    return {n: math.sqrt(max(float(model.P[i, i]), 0.0)) * sigma for i, n in enumerate(model.names)}


def param_snapshot(model: OnlineZoneModel) -> dict[str, dict[str, float]]:
    return {
        "theta": {n: round(v, 8) for n, v in model.params().items()},
        "std": {n: round(v, 8) for n, v in _param_std(model).items()},
    }


def interpret(model: OnlineZoneModel, q_on: float) -> list[dict[str, Any]]:
    """Physical reading of the learned parameters (value ± std, unit). Labels for neighbours and
    gains are filled in by the caller (entity names)."""
    theta, std = model.params(), _param_std(model)
    labels = model.group_labels()

    def group(prefix: str) -> tuple[float, float]:
        keys = [n for n in model.names if n.startswith(prefix)]
        return sum(theta[k] for k in keys), math.sqrt(sum(std[k] ** 2 for k in keys))

    out: list[dict[str, Any]] = []
    loss, loss_std = theta["loss"], std["loss"]
    out.append(
        {
            "key": "tau", "kind": "loss", "label": None, "unit": "h",
            "value": 1.0 / loss if loss > 1e-6 else None,
            "std": loss_std / loss**2 if loss > 1e-6 else None,
            "raw": "loss",
        }
    )
    for key, label in labels.items():
        _, skey, kind = key.split(":")
        v, s = group(f"solar:{skey}:{kind}:")
        out.append({"key": key, "kind": "sun", "label": label, "unit": "K/h per kW/m²",
                    "value": v * 1000.0, "std": s * 1000.0, "raw": key})
    v, s = group("heat:")
    out.append({"key": "heat", "kind": "heat", "label": None, "unit": "K/h", "value": v * q_on, "std": s * q_on,
                "raw": "heat"})
    for i in range(model.spec.n_neighbors):
        n = f"neighbor:{i}"
        out.append({"key": n, "kind": "neighbor", "label": None, "unit": "1/h", "value": theta[n], "std": std[n],
                    "raw": n})
    for i in range(model.spec.n_gains):
        n = f"gain:{i}"
        out.append({"key": n, "kind": "gain", "label": None, "unit": "K/h per unit", "value": theta[n],
                    "std": std[n], "raw": n})
    out.append({"key": "base", "kind": "base", "label": None, "unit": "K/h", "value": theta["bias"],
                "std": std["bias"], "raw": "bias"})
    return out


def param_value(theta: dict[str, float], key: str, q_on: float) -> float | None:
    """Value of an interpreted parameter (see ``interpret``) from a stored theta snapshot."""
    if key == "tau":
        loss = theta.get("loss", 0.0)
        return 1.0 / loss if loss > 1e-6 else None
    if key.startswith("sun:"):
        _, skey, kind = key.split(":")
        return 1000.0 * sum(v for n, v in theta.items() if n.startswith(f"solar:{skey}:{kind}:"))
    if key == "heat":
        return q_on * sum(v for n, v in theta.items() if n.startswith("heat:"))
    if key == "base":
        return theta.get("bias")
    return theta.get(key)


def heat_lag_profile(model: OnlineZoneModel) -> list[dict[str, float]]:
    """Heating coefficient per lag (screed delay profile)."""
    theta = model.params()
    return [{"lag": lag, "value": theta[f"heat:{lag}"]} for lag in model.spec.heat_lags]


def solar_lag_profiles(model: OnlineZoneModel) -> list[dict[str, Any]]:
    """Per sun-exposed surface: effect per hour of delay (K/h per kW/m²) – when the sun arrives in the room."""
    theta = model.params()
    labels = model.group_labels()
    out = []
    for s in model.spec.surfaces:
        key = f"sun:{s.key}:{s.kind}"
        out.append(
            {
                "key": key,
                "label": labels.get(key, key),
                "lags": [{"lag": lag, "value": theta[f"solar:{s.key}:{s.kind}:{lag}"] * 1000.0} for lag in s.lags],
            }
        )
    return out


# ------------------------------------------------------------- zone report
@dataclass
class QualityInput:
    """Everything the model tab needs for one zone (copies – safe to process in an executor)."""

    id: str
    name: str
    leads: bool
    heat_type: str
    model: OnlineZoneModel
    q_on: float
    entries: list[dict[str, Any]]
    flog: ForecastLog
    params: list[dict[str, Any]] = field(default_factory=list)
    labels: dict[str, str] = field(default_factory=dict)  # neighbour/gain display names


def _r(x: float | None, nd: int = 3) -> float | None:
    return None if x is None else round(x, nd)


def zone_quality(z: QualityInput, tz: tzinfo, now: datetime, days: int = 7) -> dict[str, Any]:
    """Model-tab payload for one zone: 7-day series, metrics, parameters (contract in the B/C spec)."""
    since = now - timedelta(days=days)
    entries = z.entries
    hc = hindcast(z.model, entries, tz)
    hours, measured, hind, fc6, error = [], [], [], [], []
    for i, e in enumerate(entries):
        if datetime.fromisoformat(e["t"]) < since:
            continue
        end = datetime.fromisoformat(e["t"]) + HOUR
        meas = _finite(e.get("temp_next"))
        sim = hc[i]["mean"] if hc[i] else None
        pred = z.flog.predicted(end, 6)
        hours.append(end.isoformat())
        measured.append(_r(meas, 2))
        hind.append(_r(sim, 2))
        fc6.append(_r(pred[0], 2) if pred else None)
        error.append(_r(sim - meas, 3) if sim is not None and meas is not None else None)

    params = []
    for p in interpret(z.model, z.q_on):
        history = [[s["date"], _r(param_value(s["theta"], p["key"], z.q_on), 4)] for s in z.params]
        params.append(
            {
                "key": p["key"], "kind": p["kind"], "label": p["label"] or z.labels.get(p["key"]),
                "unit": p["unit"], "value": _r(p["value"], 4), "std": _r(p["std"], 4), "history": history,
            }
        )
    one = one_step_metrics(entries, since)
    hcm = hindcast_metrics(entries, hc, since)
    measured_all = measured_by_hour(entries)
    horizons = forecast_metrics(z.flog, measured_all, since)
    factors = calibration(z.flog, measured_all, now - timedelta(days=14))  # same window as the coordinator
    for k, h in horizons.items():
        h["factor"] = factors.get(int(k), 1.0)
    return {
        "id": z.id,
        "name": z.name,
        "leads": z.leads,
        "heat_type": z.heat_type,
        "hours": hours,
        "measured": measured,
        "hindcast": hind,
        "forecast6": fc6,
        "error": error,
        "metrics": {
            "n_learned": z.model.n_updates,
            "n_logged": len(entries),
            "running_mae": _r(z.model.mae),
            "one_step_mae": _r(one["mae"]),
            "one_step_bias": _r(one["bias"]),
            "hindcast_mae": _r(hcm["mae"]),
            "hindcast_bias": _r(hcm["bias"]),
            "horizons": {k: {m: (_r(v) if isinstance(v, float) else v) for m, v in h.items()} for k, h in horizons.items()},
        },
        "params": params,
        "heat_lags": [{"lag": lg["lag"], "value": _r(lg["value"], 6)} for lg in heat_lag_profile(z.model)],
        "sun_lags": [
            {**p, "lags": [{"lag": lg["lag"], "value": _r(lg["value"], 4)} for lg in p["lags"]]}
            for p in solar_lag_profiles(z.model)
        ],
        "q_on": _r(z.q_on, 2),
    }
