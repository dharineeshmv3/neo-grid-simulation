"""
src/inverter.py - Smart Inverter (IS 18968:2025) and Legacy Inverter Models
SIMULATED, CITED: Exact Volt-Watt, Volt-VAr, and Ride-Through Classifiers per IS 18968:2025.
"""

from dataclasses import dataclass
from typing import Tuple
import numpy as np
from src.config import GridShieldConfig, CFG


@dataclass
class InverterOutput:
    p_actual_kw: float
    q_actual_kvar: float
    curtailed_kw: float
    is_tripped: bool
    status_label: str  # "CONTINUOUS", "RIDE_THROUGH", "VOLT_WATT", "VOLT_VAR", "TRIP_LV", "TRIP_HV", "TRIP_OVERVOLTAGE"


def classify_voltage_ride_through(v_ln_volts: float, cfg: GridShieldConfig = CFG) -> str:
    """
    Classifies voltage per IS 18968 Annex J-10 / Table 14:
    - 202.4 V to 253.0 V (0.88 - 1.10 pu): Continuous Operation
    - 161.0 V to 202.4 V (0.70 - 0.88 pu): Mandatory Ride-Through
    - < 115.0 V (< 0.50 pu): LV Trip (Cease to energize)
    - > 276.0 V (> 1.20 pu): HV Overvoltage Trip (2 s clearing)
    """
    if v_ln_volts < cfg.V_MIN_TRIP_VOLTS:
        return "TRIP_LV"
    elif v_ln_volts < cfg.V_CONTINUOUS_MIN_VOLTS:
        return "RIDE_THROUGH"
    elif v_ln_volts <= cfg.V_VOLT_WATT_ZERO_VOLTS:
        return "CONTINUOUS"
    elif v_ln_volts > cfg.V_MAX_TRIP_VOLTS:
        return "TRIP_HV"
    else:
        return "OVERVOLTAGE_BAND"


def calculate_is18968_volt_watt_ratio(v_ln_volts: float, cfg: GridShieldConfig = CFG) -> float:
    """
    Calculates steady-state active power output fraction per IS 18968 Clause 5.3.4 / Table 10:
    - 100% at V <= 243.8 V (1.06 pu)
    - Linearly derated from 100% down to 0% at V = 253.0 V (1.10 pu)
    - 0% at V >= 253.0 V
    """
    if v_ln_volts <= cfg.V_VOLT_WATT_START_VOLTS:
        return 1.0
    elif v_ln_volts >= cfg.V_VOLT_WATT_ZERO_VOLTS:
        return 0.0
    else:
        # Linear derating
        v_span = cfg.V_VOLT_WATT_ZERO_VOLTS - cfg.V_VOLT_WATT_START_VOLTS
        v_offset = v_ln_volts - cfg.V_VOLT_WATT_START_VOLTS
        return float(np.clip(1.0 - (v_offset / v_span), 0.0, 1.0))


def calculate_is18968_volt_var_kvar(v_ln_volts: float, cfg: GridShieldConfig = CFG) -> float:
    """
    Calculates dynamic reactive power injection/absorption per IS 18968 Clause 5.3.3 / Table 8:
    - Injects +2.8 kVAR at V <= 202.4 V (0.88 pu)
    - Absorbs -2.6 kVAR at V >= 253.0 V (1.10 pu)
    - Linear interpolation between (zero-crossing near 228.8 V)
    Sign convention: Positive = Inductive VAR injected (boosts voltage), Negative = Capacitive VAR absorbed (lowers voltage)
    """
    if v_ln_volts <= cfg.V_CONTINUOUS_MIN_VOLTS:
        return cfg.VOLT_VAR_MAX_INJECT_KVAR
    elif v_ln_volts >= cfg.V_VOLT_WATT_ZERO_VOLTS:
        return cfg.VOLT_VAR_MAX_ABSORB_KVAR
    else:
        # Linear interpolation between (202.4 V, +2.8) and (253.0 V, -2.6)
        v_min = cfg.V_CONTINUOUS_MIN_VOLTS
        v_max = cfg.V_VOLT_WATT_ZERO_VOLTS
        frac = (v_ln_volts - v_min) / (v_max - v_min)
        q_val = cfg.VOLT_VAR_MAX_INJECT_KVAR - frac * (cfg.VOLT_VAR_MAX_INJECT_KVAR - cfg.VOLT_VAR_MAX_ABSORB_KVAR)
        return float(q_val)


class SmartInverterIS18968:
    """
    Grid-interactive inverter conforming strictly to BIS IS 18968:2025.
    Features:
    - Volt-Watt active power derating with first-order low-pass lag (5 s open-loop response for Cat B).
    - Volt-VAr reactive power dynamic compensation.
    - Full voltage ride-through envelope.
    """
    def __init__(self, p_rated_kw: float = 3.0, category: str = "B", cfg: GridShieldConfig = CFG):
        self.p_rated_kw = p_rated_kw
        self.category = category
        self.cfg = cfg
        self.time_constant_sec = cfg.INVERTER_OPEN_LOOP_TIME_SEC if category == "B" else cfg.INVERTER_CATEGORY_A_TIME_SEC
        self.filtered_power_ratio = 1.0  # Internal state of first-order filter

    def reset(self):
        self.filtered_power_ratio = 1.0

    def step(self, v_measured: float, p_available_kw: float, dt_sec: float = 1.0) -> InverterOutput:
        # 1. Voltage Ride-Through check
        vt_status = classify_voltage_ride_through(v_measured, self.cfg)
        if vt_status in ("TRIP_LV", "TRIP_HV"):
            self.filtered_power_ratio = 0.0
            return InverterOutput(
                p_actual_kw=0.0,
                q_actual_kvar=0.0,
                curtailed_kw=p_available_kw,
                is_tripped=True,
                status_label=vt_status
            )

        # 2. Target Volt-Watt ratio
        target_ratio = calculate_is18968_volt_watt_ratio(v_measured, self.cfg)

        # 3. First-order open-loop response time filter
        # dy/dt = (target - y) / tau  =>  y(t+dt) = y(t) + (dt / tau) * (target - y(t))
        alpha = dt_sec / (self.time_constant_sec + dt_sec)
        self.filtered_power_ratio = self.filtered_power_ratio + alpha * (target_ratio - self.filtered_power_ratio)

        # Output active power
        p_actual = min(p_available_kw, self.p_rated_kw) * self.filtered_power_ratio
        curtailed = max(0.0, p_available_kw - p_actual)

        # 4. Target Volt-VAr output
        q_actual = calculate_is18968_volt_var_kvar(v_measured, self.cfg)

        status_label = "VOLT_WATT" if self.filtered_power_ratio < 0.999 else "CONTINUOUS"

        return InverterOutput(
            p_actual_kw=p_actual,
            q_actual_kvar=q_actual,
            curtailed_kw=curtailed,
            is_tripped=False,
            status_label=status_label
        )


class LegacyInverter:
    """
    Baseline non-smart residential inverter (pre-IS 18968).
    - No Volt-Watt power curtailment.
    - No Volt-VAr reactive power control (operates at unity power factor, Q = 0).
    - Trips OFF when V > 253.0 V (1.10 pu) sustained for > trip_delay_sec (default 5 s).
    - Reconnects only after reconnect_timer_sec (default 300 s / 5 minutes) of healthy voltage.
    Produces the generation loss, voltage instability, and tripping seen in real Indian feeders.
    """
    def __init__(
        self,
        p_rated_kw: float = 3.0,
        trip_delay_sec: float = 5.0,
        reconnect_sec: float = 300.0,
        cfg: GridShieldConfig = CFG
    ):
        self.p_rated_kw = p_rated_kw
        self.trip_delay_sec = trip_delay_sec
        self.reconnect_sec = reconnect_sec
        self.cfg = cfg

        self.is_tripped = False
        self.seconds_in_overvoltage = 0.0
        self.seconds_since_trip = 0.0

    def reset(self):
        self.is_tripped = False
        self.seconds_in_overvoltage = 0.0
        self.seconds_since_trip = 0.0

    def step(self, v_measured: float, p_available_kw: float, dt_sec: float = 1.0) -> InverterOutput:
        # Check overvoltage threshold (253.0 V)
        if v_measured > self.cfg.V_VOLT_WATT_ZERO_VOLTS:
            self.seconds_in_overvoltage += dt_sec
            if self.seconds_in_overvoltage >= self.trip_delay_sec:
                self.is_tripped = True
        else:
            self.seconds_in_overvoltage = 0.0

        if self.is_tripped:
            self.seconds_since_trip += dt_sec
            # Can only reconnect if voltage is back in continuous band for reconnect_sec
            if (v_measured <= self.cfg.V_VOLT_WATT_ZERO_VOLTS) and (self.seconds_since_trip >= self.reconnect_sec):
                self.is_tripped = False
                self.seconds_since_trip = 0.0
                self.seconds_in_overvoltage = 0.0
            else:
                return InverterOutput(
                    p_actual_kw=0.0,
                    q_actual_kvar=0.0,
                    curtailed_kw=p_available_kw,
                    is_tripped=True,
                    status_label="TRIP_OVERVOLTAGE"
                )

        p_actual = min(p_available_kw, self.p_rated_kw)
        return InverterOutput(
            p_actual_kw=p_actual,
            q_actual_kvar=0.0,
            curtailed_kw=0.0,
            is_tripped=False,
            status_label="CONTINUOUS"
        )
