"""Panel view: the JSON contract (version 1) built from pure inputs – no HA imports."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from typing import Any

from .explain import explain, robustness
from .planner import ZonePlanInput, plan
from .rollout import DEFAULT_BLOCK_LENGTHS, ActuatorState, RolloutResult, rollout
from .rules import Rules

VIEW_VERSION = 1
HOUR = timedelta(hours=1)


@dataclass(frozen=True)
class Window:
    hours: list[datetime]  # UTC hour starts: local yesterday 00:00 … local tomorrow 23:00
    now_index: int
    tz: str

    @property
    def end(self) -> datetime:
        return self.hours[-1] + HOUR


def make_window(now: datetime, tz: tzinfo, tz_name: str) -> Window:
    local = now.astimezone(tz)
    start = datetime.combine(local.date() - timedelta(days=1), time(0), tzinfo=tz).astimezone(UTC)
    end = datetime.combine(local.date() + timedelta(days=2), time(0), tzinfo=tz).astimezone(UTC)
    hours: list[datetime] = []
    t = start
    while t < end:
        hours.append(t)
        t += HOUR
    now_hour = now.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    return Window(hours=hours, now_index=hours.index(now_hour), tz=tz_name)


def align(times: list[datetime], values: list, hours: list[datetime]) -> list:
    idx = {t: i for i, t in enumerate(times)}
    return [values[idx[h]] if h in idx else None for h in hours]


@dataclass
class ZoneViewInput:
    id: str
    name: str
    heat_type: str
    leads: bool
    measured: list[float | None]  # aligned to window hours
    comfort_low: list[float | None]  # aligned to window hours
    group_labels: dict[str, str]  # group key -> display name (surfaces, neighbours, gains)
    contrib_past: list[dict[str, float] | None] = field(default_factory=list)  # hindcast causes, aligned
    forecast6: list[float | None] = field(default_factory=list)  # prediction made 6 h earlier, aligned


@dataclass
class Outlook:
    rollout: RolloutResult
    first_inputs: list[ZonePlanInput]
    explanation: dict
    robustness: dict


def compute_outlook(
    zones: list[ZonePlanInput],
    steps: int,
    rules: Rules,
    state: ActuatorState,
    day_index: list[int],
    hour0: datetime,
    z: float = 1.0,
    lookahead: int = 24,
    block_lengths: tuple[int, ...] = DEFAULT_BLOCK_LENGTHS,
) -> Outlook:
    """CPU part (run in an executor): rollout, z=0 comparison plan, explanation."""
    ro = rollout(zones, steps, rules, state, day_index, lookahead=lookahead, block_lengths=block_lengths, z=z)
    first_inputs = [replace(zi, future=zi.future[:lookahead], comfort_low=zi.comfort_low[:lookahead]) for zi in zones]
    res_z0 = plan(first_inputs, block_lengths=block_lengths, z=0.0)
    return Outlook(ro, first_inputs, explain(ro.first, first_inputs, hour0, z), robustness(ro.first, res_z0))


def _r(x: float | None, nd: int = 2) -> float | None:
    return None if x is None else round(float(x), nd)


def _runs(flags: list[bool]) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    start: int | None = None
    for i, f in enumerate([*flags, False]):
        if f and start is None:
            start = i
        elif not f and start is not None:
            out.append((start, i))
            start = None
    return out


def _group_kind(key: str) -> str:
    return key.split(":", 1)[0]


def _zone_view(zi: ZoneViewInput, outlook: Outlook | None, n: int, now: int) -> dict[str, Any]:
    plan_mean: list[float | None] = [None] * n
    plan_std: list[float | None] = [None] * n
    free_mean: list[float | None] = [None] * n
    free_std: list[float | None] = [None] * n
    contrib: list[dict[str, float] | None] = [None] * n
    tr = outlook.rollout.zones.get(zi.id) if outlook else None
    # the rollout keeps the raw model σ (it feeds the forecast log); show the calibrated one
    scale = next((x.std_scale for x in outlook.first_inputs if x.name == zi.id), ()) if outlook else ()
    if tr is not None:
        plan_mean[now] = free_mean[now] = _r(tr.temp0)
        plan_std[now] = free_std[now] = 0.0
        for h in range(len(tr.mean)):
            if now + h + 1 < n:
                factor = scale[min(h, len(scale) - 1)] if scale else 1.0
                plan_mean[now + h + 1] = _r(tr.mean[h])
                plan_std[now + h + 1] = _r(tr.std[h] * factor, 3)
                free_mean[now + h + 1] = _r(tr.free_mean[h])
                free_std[now + h + 1] = _r(tr.free_std[h], 3)
            if now + h < n:
                contrib[now + h] = {k: round(v, 4) for k, v in tr.contrib[h].items()}
    for i, c in enumerate(zi.contrib_past[:now]):  # the past: hindcast with measured inputs
        if c is not None and contrib[i] is None:
            contrib[i] = {k: round(v, 4) for k, v in c.items()}
    keys = sorted({k for c in contrib if c for k in c})
    return {
        "id": zi.id,
        "name": zi.name,
        "heat_type": zi.heat_type,
        "leads": zi.leads,
        "measured": [_r(v) for v in zi.measured],
        "comfort_low": zi.comfort_low,
        "plan": {"mean": plan_mean, "std": plan_std},
        "free": {"mean": free_mean, "std": free_std},
        "forecast6": [_r(v) for v in zi.forecast6] if zi.forecast6 else [None] * n,
        "contrib": contrib,
        "groups": [{"key": k, "label": zi.group_labels.get(k, k), "kind": _group_kind(k)} for k in keys],
    }


def _candidates(outlook: Outlook, n: int, now: int, iso) -> list[dict[str, Any]]:
    first = outlook.rollout.first
    none = next((s for s in first.ranked if s.candidate.start is None), None)
    top = [s for s in first.ranked if s.candidate.start is not None][:5]
    out: list[dict[str, Any]] = []
    for sc in sorted(([none] if none else []) + top, key=lambda s: s.cost):
        c = sc.candidate
        traj: dict[str, list[float | None]] = {}
        for zid, pred in sc.preds.items():
            arr: list[float | None] = [None] * n
            tr = outlook.rollout.zones.get(zid)
            arr[now] = _r(tr.temp0) if tr else None
            for h, m in enumerate(pred.mean):
                if now + h + 1 < n:
                    arr[now + h + 1] = _r(m)
            traj[zid] = arr
        out.append(
            {
                "start": iso(now + c.start) if c.start is not None else None,
                "end": iso(now + c.start + c.length) if c.start is not None else None,
                "cost": round(sc.cost, 3),
                "violation_kh": round(sc.violation, 3),
                "parts": {k: round(v, 3) for k, v in sc.parts.items()},
                "chosen": c == first.best,
                "trajectories": traj,
            }
        )
    return out


def _next_block_from_rollout(explanation: dict[str, Any], outlook: Outlook, now: int, iso) -> dict[str, Any]:
    """The story shows the block the controller will actually run (rollout), not the planner's
    single-block candidate – the rollout re-plans hourly and may stop earlier or split the block."""
    blocks = outlook.rollout.blocks
    out = dict(explanation)
    out["planner_block"] = explanation.get("next_block")
    if not blocks:
        out["next_block"] = None
        return out
    start, end = blocks[0]
    out["next_block"] = {"start": iso(now + start), "end": iso(now + end)}
    if out.get("code") in ("heating_now", "block_planned"):
        out["code"] = "heating_now" if start == 0 else "block_planned"
        if out.get("first_violation"):
            violation = datetime.fromisoformat(out["first_violation"])
            out["lead_h"] = round((violation - datetime.fromisoformat(iso(now + start))) / HOUR)
    return out


def build_view(
    *,
    window: Window,
    tz: tzinfo,
    generated_at: datetime,
    t_out_measured: list[float | None],
    t_out_forecast: list[float | None],
    irr: list[dict[str, Any]],
    heating_actual: list[float | None],
    release: list[bool | None],
    planner: list[bool | None],
    zones: list[ZoneViewInput],
    outlook: Outlook | None,
    z: float,
    decision: dict[str, Any],
    plan_change: dict | None,
    events: list[dict[str, Any]],
    errors: list[str],
    dhw: list[float | None] | None = None,
) -> dict[str, Any]:
    n = len(window.hours)
    now = window.now_index

    def iso(i: int) -> str:
        return (window.hours[0] + i * HOUR).isoformat()

    on_plan: list[bool | None] = [None] * n
    if outlook:
        for h, on in enumerate(outlook.rollout.on):
            if now + h < n:
                on_plan[now + h] = on
    past_flags = [i < now and a is not None and a >= 0.5 for i, a in enumerate(heating_actual)]
    zviews = [_zone_view(zi, outlook, n, now) for zi in zones]

    # --- days
    dates: list[date] = [h.astimezone(tz).date() for h in window.hours]
    today = dates[now]
    leading = [zv for zv in zviews if zv["leads"]]

    def planned_start(i: int) -> bool:
        if not on_plan[i]:
            return False
        if i - 1 >= now:
            return not on_plan[i - 1]
        return not (i >= 1 and past_flags[i - 1])

    days: list[dict[str, Any]] = []
    for d in sorted(set(dates)):
        idx = [i for i in range(n) if dates[i] == d]
        past = [i for i in idx if i < now]
        fut = [i for i in idx if i >= now]
        kind = "past" if d < today else "today" if d == today else "future"
        heat_vals = [heating_actual[i] for i in past if heating_actual[i] is not None]
        meas = [
            zv["measured"][i] for zv in leading for i in past
            if zv["comfort_low"][i] is not None and zv["measured"][i] is not None
        ]
        low = [
            zv["plan"]["mean"][i] - z * zv["plan"]["std"][i] for zv in leading for i in fut
            if zv["comfort_low"][i] is not None and zv["plan"]["mean"][i] is not None
        ]
        pairs = [(release[i], planner[i]) for i in past if release[i] is not None and planner[i] is not None]
        has_plan = outlook is not None and kind != "past"
        last_std = [zv["plan"]["std"][idx[-1]] for zv in zviews if zv["plan"]["std"][idx[-1]] is not None]
        days.append(
            {
                "date": d.isoformat(),
                "kind": kind,
                "heat_hours": round(sum(heat_vals), 1) if kind != "future" and heat_vals else None,
                "blocks": sum(1 for i in past if past_flags[i] and (i == 0 or not past_flags[i - 1]))
                if kind != "future" else None,
                "heat_hours_planned": float(sum(1 for i in fut if on_plan[i])) if has_plan else None,
                "blocks_planned": sum(1 for i in fut if planned_start(i)) if has_plan else None,
                "min_leading": _r(min(meas)) if meas else None,
                "min_leading_planned": _r(min(low)) if low else None,
                "release_followed": round(sum(a == b for a, b in pairs) / len(pairs), 3) if pairs else None,
                "std_end": _r(max(last_std)) if last_std and kind == "future" else None,
            }
        )

    explanation: dict[str, Any] = dict(outlook.explanation) if outlook else {"code": "no_forecast"}
    if outlook:
        explanation = _next_block_from_rollout(explanation, outlook, now, iso)
    if decision.get("override") == "failsafe":
        explanation = {**explanation, "code": "failsafe", "reason": decision.get("failsafe_reason")}

    start, end = window.hours[0], window.end
    return {
        "version": VIEW_VERSION,
        "generated_at": generated_at.isoformat(),
        "window": {"start": start.isoformat(), "end": end.isoformat(), "now_index": now, "tz": window.tz},
        "hours": [h.isoformat() for h in window.hours],
        "weather": {
            "t_out": [_r(m if m is not None else f, 1) for m, f in zip(t_out_measured, t_out_forecast)],
            "t_out_measured": [m is not None for m in t_out_measured],
            "irr": irr,
        },
        "heating": {
            "actual": heating_actual,
            "release": release,
            "planner": planner,
            "dhw": dhw if dhw is not None else [None] * n,
            "past_blocks": [{"start": iso(s), "end": iso(e)} for s, e in _runs(past_flags)],
            "planned_blocks": [{"start": iso(s), "end": iso(e)} for s, e in _runs([bool(o) for o in on_plan])],
        },
        "zones": zviews,
        "decision": decision,
        "explanation": explanation,
        "robustness": outlook.robustness if outlook else None,
        "plan_change": plan_change,
        "candidates": _candidates(outlook, n, now, iso) if outlook else [],
        "days": days,
        "events": [e for e in events if start <= datetime.fromisoformat(e["time"]) < end],
        "errors": errors,
    }
