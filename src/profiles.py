"""
src/profiles.py - Calibrated Feeder Load, Solar PV & Cloud Profiles
SIMULATED: 150-home diversified residential demand & 50 rooftop prosumer solar curves.
Resolves Known Problem #1: Evening peak genuinely exceeds 85 kW limit on Stress day (~104 kW at 19:30).
"""

from typing import Tuple, Dict
import numpy as np
from src.config import GridShieldConfig, CFG


def generate_feeder_profiles(
    cfg: GridShieldConfig = CFG,
    scenario_preset: str = "Stress day",
    load_scale: float = 1.0,
    pv_placement: str = "balanced",  # "balanced" or "concentrated_phase_a"
    pv_penetration_pct: float = 100.0, # % of DT capacity (100 kVA): 10% = 10 kWp, 100% = 150 kWp (all 50 @ 3 kWp)
    cloud_event_active: bool = True,
    cloud_start_hour: float = 13.5,
    cloud_duration_hours: float = 0.75,
    seed: int = 42
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, Dict[str, np.ndarray]]:
    """
    Generates 24-hour, 1-minute resolution (1440 steps) profiles for 150 households.

    Returns:
        p_load_kw: shape (1440, 150) - Per-home active power consumption (kW)
        q_load_kvar: shape (1440, 150) - Per-home reactive power consumption (kVAR)
        p_pv_available_kw: shape (1440, 150) - Per-home available unconstrained solar PV (kW)
        metadata: dict with summary time-series (aggregate_load_kw, aggregate_pv_kw, net_unmanaged_kw)
    """
    rng = np.random.RandomState(seed)
    timesteps = 1440
    time_minutes = np.arange(timesteps)
    hours = time_minutes / 60.0

    # ── 1. Base Diurnal Shape (kW per home average) ────────────────────────
    # Calibrated to Indian residential profiles:
    # Midday off-peak baseline: ~0.15 - 0.20 kW per home (fans, fridge, standby)
    # Morning shoulder: ~0.35 kW
    # Evening super-peak (18:30 - 21:00): ~0.85 - 1.05 kW with heavy AC usage
    # 150 homes * 0.90 kW average peak gives ~100-110 kW peak on Stress Day (exceeds 85 kW limit)
    base_shape = (
        0.16
        + 0.18 * np.exp(-0.5 * ((hours - 7.5) / 1.2) ** 2)    # Morning peak (~0.34 kW)
        + 0.08 * np.exp(-0.5 * ((hours - 13.5) / 2.0) ** 2)   # Midday off-peak (~0.24 kW)
        + 0.62 * np.exp(-0.5 * ((hours - 19.5) / 1.8) ** 2)   # Evening peak (~0.94 kW)
    )

    preset_multipliers = {
        "Clear": 0.85,
        "Partly cloudy": 0.88,
        "Overcast": 0.75,
        "Stress day": 1.05  # Hot summer stress day: evening peak hits ~104 kW
    }
    scenario_scale = preset_multipliers.get(scenario_preset, 1.0) * load_scale

    p_load_kw = np.zeros((timesteps, cfg.TOTAL_HOMES), dtype=np.float64)
    q_load_kvar = np.zeros((timesteps, cfg.TOTAL_HOMES), dtype=np.float64)

    for h in range(cfg.TOTAL_HOMES):
        home_factor = rng.lognormal(mean=0.0, sigma=0.22)
        noise = rng.normal(loc=0.0, scale=0.03, size=timesteps)
        home_curve = (base_shape * scenario_scale * home_factor) + noise
        home_curve = np.clip(home_curve, 0.06, 3.8)
        p_load_kw[:, h] = home_curve

        pf = rng.uniform(0.90, 0.95)
        tan_phi = np.tan(np.arccos(pf))
        q_load_kvar[:, h] = home_curve * tan_phi

    # ── 2. Solar PV Generation Model ───────────────────────────────────────
    p_pv_available_kw = np.zeros((timesteps, cfg.TOTAL_HOMES), dtype=np.float64)

    # Clear-sky bell curve (sunrise ~06:00, sunset ~18:30)
    daylight_mask = (hours >= 6.0) & (hours <= 18.5)
    solar_base = np.zeros(timesteps, dtype=np.float64)
    solar_base[daylight_mask] = np.sin(np.pi * (hours[daylight_mask] - 6.0) / 12.5) ** 1.8

    temp_derating = 1.0 - 0.10 * np.exp(-0.5 * ((hours - 14.5) / 2.5) ** 2)
    solar_curve = solar_base * temp_derating

    weather_factors = {
        "Clear": 1.00,
        "Partly cloudy": 0.65,
        "Overcast": 0.25,
        "Stress day": 1.00
    }
    weather_factor = weather_factors.get(scenario_preset, 1.0)

    # Cloud Event Injection (default 13:30 to 14:15, -80% drop)
    cloud_attenuation = np.ones(timesteps, dtype=np.float64)
    if cloud_event_active:
        cloud_end_hour = cloud_start_hour + cloud_duration_hours
        cloud_mask = (hours >= cloud_start_hour) & (hours <= cloud_end_hour)
        t_ramp = 0.05
        for t_idx in np.where(cloud_mask)[0]:
            h_now = hours[t_idx]
            if h_now < cloud_start_hour + t_ramp:
                alpha = (h_now - cloud_start_hour) / t_ramp
                cloud_attenuation[t_idx] = 1.0 - 0.80 * alpha
            elif h_now > cloud_end_hour - t_ramp:
                alpha = (cloud_end_hour - h_now) / t_ramp
                cloud_attenuation[t_idx] = 1.0 - 0.80 * alpha
            else:
                cloud_attenuation[t_idx] = 0.20

    # Determine prosumer indices uniformly along the feeder branches
    # Phase A: 50 homes (indices 0..49)
    # Phase B: 50 homes (indices 50..99)
    # Phase C: 50 homes (indices 100..149)
    if pv_placement == "concentrated_phase_a":
        # All 50 prosumers on Phase A
        prosumer_indices = np.arange(0, cfg.PROSUMER_HOMES)
    else:
        # Uniformly distributed along the length of each phase branch
        pros_a = np.linspace(0, 49, 17, dtype=int)
        pros_b = 50 + np.linspace(0, 49, 17, dtype=int)
        pros_c = 100 + np.linspace(0, 49, 16, dtype=int)
        prosumer_indices = np.concatenate([pros_a, pros_b, pros_c])

    # Penetration scaling:
    # 100% penetration = 150 kWp (all 50 prosumers at 3 kWp)
    # At pv_penetration_pct = 35%, each active prosumer generates proportional power
    penetration_scale = pv_penetration_pct / 100.0

    for idx in prosumer_indices:
        roof_factor = rng.uniform(0.94, 1.06)
        p_pv_available_kw[:, idx] = (
            cfg.PROSUMER_PV_RATING_KWP
            * solar_curve
            * weather_factor
            * cloud_attenuation
            * roof_factor
            * penetration_scale
        )

    aggregate_load_kw = np.sum(p_load_kw, axis=1)
    aggregate_pv_kw = np.sum(p_pv_available_kw, axis=1)
    net_unmanaged_kw = aggregate_load_kw - aggregate_pv_kw

    metadata = {
        "aggregate_load_kw": aggregate_load_kw,
        "aggregate_pv_kw": aggregate_pv_kw,
        "net_unmanaged_kw": net_unmanaged_kw,
        "prosumer_indices": prosumer_indices,
        "cloud_attenuation": cloud_attenuation
    }

    return p_load_kw, q_load_kvar, p_pv_available_kw, metadata
