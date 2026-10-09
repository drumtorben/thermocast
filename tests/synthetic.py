"""Synthetic house for testing: air + hidden screed state, sun on surfaces with delays."""
from __future__ import annotations

import math

import numpy as np

LAT = math.radians(53.0)  # northern Germany


def sun_position(day: int, hour: float) -> tuple[float, float]:
    """Very rough solar elevation/azimuth (rad), azimuth compass (0=N)."""
    decl = math.radians(23.44) * math.sin(2 * math.pi * (284 + day) / 365)
    ha = math.radians(15 * (hour - 12))
    sin_el = math.sin(LAT) * math.sin(decl) + math.cos(LAT) * math.cos(decl) * math.cos(ha)
    el = math.asin(max(-1.0, min(1.0, sin_el)))
    cos_az = (math.sin(decl) - math.sin(el) * math.sin(LAT)) / (math.cos(el) * math.cos(LAT) + 1e-9)
    az = math.acos(max(-1.0, min(1.0, cos_az)))
    if ha > 0:
        az = 2 * math.pi - az
    return el, az


def surface_irradiance(el: float, az: float, tilt_deg: float, az_deg: float, cloud: float) -> float:
    if el <= 0:
        return 0.0
    dni = 850 * (1 - cloud) * math.sin(el) ** 0.3
    dhi = 100 * (0.3 + cloud) * math.sin(el)
    tilt, saz = math.radians(tilt_deg), math.radians(az_deg)
    cos_inc = math.sin(el) * math.cos(tilt) + math.cos(el) * math.sin(tilt) * math.cos(az - saz)
    return max(0.0, dni * cos_inc) + dhi * (1 + math.cos(tilt)) / 2


def simulate(
    days: int = 40, seed: int = 0, heat_schedule=None, roof_delay_h: float = 2.0, gain_w=None, gain_coef: float = 3e-4
):
    """Return hourly arrays for a bedroom-like zone with an east window and south roof.

    True dynamics (unknown to the learner): air node + screed node. ``gain_w`` (hour -> W, e.g. a dehumidifier)
    adds an internal gain of ``gain_coef`` K/h per W to the air.
    """
    rng = np.random.default_rng(seed)
    n = days * 24
    t_out = np.empty(n)
    cloud = np.clip(0.5 + 0.35 * np.sin(np.arange(n) / 37.0) + rng.normal(0, 0.15, n), 0, 1)
    irr_e = np.empty(n)
    irr_s_roof = np.empty(n)
    for i in range(n):
        day, hour = 280 + i // 24, i % 24 + 0.5
        t_out[i] = 7 + 5 * math.sin(2 * math.pi * (hour - 9) / 24) + 3 * math.sin(i / 90.0) + rng.normal(0, 0.3)
        el, az = sun_position(day, hour)
        irr_e[i] = surface_irradiance(el, az, 90, 90, cloud[i])
        irr_s_roof[i] = surface_irradiance(el, az, 40, 180, cloud[i])

    air = np.empty(n + 1)
    screed = np.empty(n + 1)
    air[0], screed[0] = 20.5, 22.0
    q = np.zeros(n)
    gain = np.array([float(gain_w(i)) for i in range(n)]) if gain_w is not None else np.zeros(n)
    roof_heat = 0.0
    for i in range(n):
        if heat_schedule is not None:
            heating = heat_schedule(i, air[i])
        else:
            heating = air[i] < 20.0 or (q[i - 1] > 0 and air[i] < 21.0 if i else False)
        q[i] = (35.0 - air[i]) if heating else 0.0
        # roof: first-order delay (default ~2 h) of south roof irradiance
        roof_heat += (irr_s_roof[i] - roof_heat) / roof_delay_h
        d_screed = 0.06 * q[i] - 0.08 * (screed[i] - air[i])
        d_air = (
            0.015 * (t_out[i] - air[i])
            + 0.10 * (screed[i] - air[i])
            + 4e-4 * irr_e[i]
            + 3e-4 * roof_heat
            + gain_coef * gain[i]
            + rng.normal(0, 0.02)
        )
        screed[i + 1] = screed[i] + d_screed
        air[i + 1] = air[i] + d_air
    return {
        "t_out": t_out,
        "air": air,
        "q": q,
        "gain": gain,
        "irr": {"90_90": irr_e, "40_180": irr_s_roof},
    }
