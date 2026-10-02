"""
src/bess.py - CommVault Community Battery Energy Storage System (BESS)
SIMULATED: 100 kWh / 50 kW physical battery pack with true energy accounting.
Sign convention (Rule 5.3):
    POSITIVE (+) = DISCHARGING (injecting real power into 415 V bus)
    NEGATIVE (-) = CHARGING (absorbing real power from 415 V bus)
Efficiencies applied strictly to stored energy, not commanded power.
"""

from typing import Tuple
import numpy as np
from src.config import GridShieldConfig, CFG


class CommVaultBESS:
    """
    Physical BESS simulation:
    - 100 kWh nominal capacity
    - 50 kW bidirectional inverter
    - 15% min emergency reserve, 95% max longevity ceiling
    - eta_charge = eta_discharge = 0.93 (86.49% round-trip)
    - Enforces 10 kW/s ramp rate and auxiliary HVAC load (1.0 kW)
    """
    def __init__(self, cfg: GridShieldConfig = CFG, initial_soc_pct: float = None):
        self.cfg = cfg
        self.capacity_kwh = cfg.BESS_CAPACITY_KWH
        self.max_power_kw = cfg.BESS_MAX_POWER_KW
        self.min_soc_pct = cfg.BESS_MIN_SOC_PCT
        self.max_soc_pct = cfg.BESS_MAX_SOC_PCT
        self.eta_c = cfg.BESS_CHARGE_EFF
        self.eta_d = cfg.BESS_DISCHARGE_EFF

        self.soc_pct = initial_soc_pct if initial_soc_pct is not None else cfg.BESS_INITIAL_SOC_DEFAULT
        self.current_power_kw = 0.0
        self.total_discharged_kwh = 0.0
        self.total_charged_kwh = 0.0

    def reset(self, initial_soc_pct: float = 60.0):
        self.soc_pct = initial_soc_pct
        self.current_power_kw = 0.0
        self.total_discharged_kwh = 0.0
        self.total_charged_kwh = 0.0

    def get_available_discharge_energy_kwh(self, soc_floor_pct: float = None) -> float:
        """Energy in kWh that can be extracted before hitting the specified SoC floor."""
        floor = soc_floor_pct if soc_floor_pct is not None else self.min_soc_pct
        effective_floor = max(self.min_soc_pct, floor)
        usable_soc_delta = max(0.0, self.soc_pct - effective_floor)
        # Usable chemical energy * discharge efficiency
        return (usable_soc_delta / 100.0) * self.capacity_kwh * self.eta_d

    def get_available_charge_capacity_kwh(self) -> float:
        """Energy in kWh that can be absorbed before hitting the 95% SoC ceiling."""
        usable_soc_delta = max(0.0, self.max_soc_pct - self.soc_pct)
        return (usable_soc_delta / 100.0) * self.capacity_kwh / self.eta_c

    def step(
        self,
        commanded_kw: float,
        dt_hours: float,
        max_ramp_kw_per_sec: float = None,
        dt_seconds: float = None,
        soc_floor_pct: float = None
    ) -> Tuple[float, float]:
        """
        Executes one timestep of BESS operation.

        Args:
            commanded_kw: Target dispatch power (pos = discharge, neg = charge)
            dt_hours: Time duration in hours (e.g. 1/60 for 1-minute step)
            max_ramp_kw_per_sec: Ramp rate limit (default 10 kW/s)
            dt_seconds: Time duration in seconds (computed from dt_hours if None)
            soc_floor_pct: Hourly dynamic SoC floor (default 15%)

        Returns:
            (actual_dispatched_kw, new_soc_pct)
        """
        dt_sec = dt_seconds if dt_seconds is not None else (dt_hours * 3600.0)
        ramp_limit = max_ramp_kw_per_sec if max_ramp_kw_per_sec is not None else self.cfg.BESS_RAMP_KW_PER_SEC
        active_soc_floor = max(self.min_soc_pct, soc_floor_pct if soc_floor_pct is not None else self.min_soc_pct)

        # 1. Ramp Rate Limiting
        max_delta = ramp_limit * dt_sec
        power_delta = commanded_kw - self.current_power_kw
        if abs(power_delta) > max_delta:
            power_delta = max_delta if power_delta > 0 else -max_delta
        ramped_target = self.current_power_kw + power_delta

        # 2. Hardware Inverter Power Limits [-50 kW, +50 kW]
        clamped_target = float(np.clip(ramped_target, -self.max_power_kw, self.max_power_kw))

        # 3. Energy Constraints & SoC Integration
        actual_power = 0.0

        if clamped_target > 0.0:
            # Discharging: check energy available above active SoC floor
            max_discharge_kwh = self.get_available_discharge_energy_kwh(active_soc_floor)
            max_discharge_kw = max_discharge_kwh / dt_hours if dt_hours > 0 else 0.0
            actual_power = min(clamped_target, max_discharge_kw)

            # Energy drained from chemical storage = (P_out / eta_d) * dt
            energy_drained_kwh = (actual_power / self.eta_d) * dt_hours
            soc_delta_pct = (energy_drained_kwh / self.capacity_kwh) * 100.0
            self.soc_pct = max(active_soc_floor, self.soc_pct - soc_delta_pct)
            self.total_discharged_kwh += actual_power * dt_hours

        elif clamped_target < 0.0:
            # Charging: check room available below max SoC (95%)
            charge_demand_kw = abs(clamped_target)
            max_charge_kwh = self.get_available_charge_capacity_kwh()
            max_charge_kw = max_charge_kwh / dt_hours if dt_hours > 0 else 0.0
            actual_charge_kw = min(charge_demand_kw, max_charge_kw)
            actual_power = -actual_charge_kw

            # Energy stored into chemical storage = (P_in * eta_c) * dt
            energy_stored_kwh = (actual_charge_kw * self.eta_c) * dt_hours
            soc_delta_pct = (energy_stored_kwh / self.capacity_kwh) * 100.0
            self.soc_pct = min(self.max_soc_pct, self.soc_pct + soc_delta_pct)
            self.total_charged_kwh += actual_charge_kw * dt_hours

        else:
            actual_power = 0.0

        # Auxiliary HVAC load accounts for small continuous battery parasitic drain
        aux_energy_kwh = (self.cfg.BESS_AUX_LOAD_KW * dt_hours) / self.eta_d
        aux_soc_delta = (aux_energy_kwh / self.capacity_kwh) * 100.0
        self.soc_pct = max(self.min_soc_pct, self.soc_pct - aux_soc_delta)

        self.current_power_kw = actual_power
        return actual_power, self.soc_pct
