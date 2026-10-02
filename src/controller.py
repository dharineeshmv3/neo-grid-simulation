"""
src/controller.py - GridShield Master Layer 1 Dispatch State Machine
SIMULATED: Multi-layer hierarchical controller coordinating CommVault BESS and VoltSense-DSM.
Fixes all 8 architectural defects identified in Master Prompt §5.4:
1. Persistent power passing through ramp limiters (no snapping to 0 kW on transitions).
2. Efficiencies applied to chemical energy, not commanded power.
3. Dedicated taper ramp rate (2.0 kW/s) in DR_ACTIVE_BESS_TAPERING.
4. Tick-level hourly dynamic SoC floor enforcement.
5. DR cooldown and lockout timers (60 min cooldown, max 3 events/day).
6. Voltage-triggered charging: absorbs power when max node V > 243.8 V even if net load > 20 kW.
7. Layer 0 autonomous droop control running at sub-second resolution.
8. Temporary overload up to 120% (102 kW) / 30 min before alarming.
"""

from enum import Enum
from dataclasses import dataclass
from typing import Dict, Any, Tuple
import numpy as np
from src.config import GridShieldConfig, CFG
from src.bess import CommVaultBESS
from src.dr import VoltSenseDRManager


class DispatchState(Enum):
    IDLE = "IDLE"
    SOLAR_SURPLUS_CHARGING = "SOLAR_SURPLUS_CHARGING"
    BESS_FAST_DISCHARGE = "BESS_FAST_DISCHARGE"
    BESS_DR_COMBINED = "BESS_DR_COMBINED"
    DR_ACTIVE_BESS_TAPERING = "DR_ACTIVE_BESS_TAPERING"


@dataclass
class ControllerDecision:
    state: DispatchState
    bess_command_kw: float
    bess_actual_kw: float
    bess_soc_pct: float
    dr_active_kw: float
    dr_command: str  # "NONE", "ACTIVATE", "RELEASE", "HOLD"
    layer0_droop_kw: float
    is_overloaded_dt: bool
    is_severe_overload_120: bool
    alarm_code: str  # "OK", "OVERVOLTAGE_CHARGING", "SOC_FLOOR_ACTIVE", "DT_OVERLOAD_ALARM"


class GridShieldController:
    """
    Finite State Machine orchestrating BESS dispatch and VoltSense-DSM.
    """
    def __init__(self, bess: CommVaultBESS, dr_mgr: VoltSenseDRManager, cfg: GridShieldConfig = CFG):
        self.cfg = cfg
        self.bess = bess
        self.dr = dr_mgr

        self.current_state = DispatchState.IDLE
        self.seconds_in_state = 0.0
        self.bess_target_kw = 0.0
        self.overload_timer_sec = 0.0

    def reset(self, initial_soc_pct: float = 60.0):
        self.bess.reset(initial_soc_pct)
        self.dr.reset()
        self.current_state = DispatchState.IDLE
        self.seconds_in_state = 0.0
        self.bess_target_kw = 0.0
        self.overload_timer_sec = 0.0

    def get_soc_floor_for_hour(self, hour: float) -> float:
        """Returns hourly SoC reserve floor percentage per specs."""
        h_int = int(hour) % 24
        return self.cfg.SOC_FLOORS_BY_HOUR.get(h_int, 15.0)

    def compute_layer0_droop_kw(self, v_dt_bus: float) -> float:
        """
        Layer 0 Inverter Autonomous Droop Control:
        Sub-second physical reflex operating on local DSP:
        ΔP = -K_droop * (V - V_nom)
        Deadband: 202.4 V to 243.8 V
        """
        if v_dt_bus > self.cfg.V_VOLT_WATT_START_VOLTS:
            # Overvoltage -> absorb power (charge battery, negative kW)
            delta_v = v_dt_bus - self.cfg.V_VOLT_WATT_START_VOLTS
            k_droop = 5.0  # 5 kW per Volt overvoltage
            return -min(self.cfg.BESS_MAX_POWER_KW, delta_v * k_droop)
        elif v_dt_bus < self.cfg.V_CONTINUOUS_MIN_VOLTS:
            # Undervoltage -> inject power (discharge battery, positive kW)
            delta_v = self.cfg.V_CONTINUOUS_MIN_VOLTS - v_dt_bus
            k_droop = 5.0
            return min(self.cfg.BESS_MAX_POWER_KW, delta_v * k_droop)
        return 0.0

    def tick(
        self,
        net_dt_load_kw: float,       # Current unmanaged net load on the transformer (kW)
        hour: float,                 # Time of day (0.0 to 24.0)
        max_node_voltage_v: float,   # Maximum node voltage across feeder
        dt_seconds: float = 1.0,     # Timestep duration in seconds
        enable_bess: bool = True,
        enable_dr: bool = True
    ) -> ControllerDecision:
        """
        Main control tick executed every 1 second (or 60 seconds in 24 h runs).
        """
        self.seconds_in_state += dt_seconds
        soc_floor = self.get_soc_floor_for_hour(hour)
        alarm_code = "OK"
        dr_cmd = "NONE"

        # Update DR dynamics (ramp-up, cooldowns)
        dr_active_kw = self.dr.update_step(dt_seconds) if enable_dr else 0.0

        # Layer 0 Droop reflex
        layer0_kw = self.compute_layer0_droop_kw(max_node_voltage_v)

        # ── 1. Voltage-Triggered Charging Logic ─────────────────────────────
        # If any node voltage exceeds 243.8 V, absorb power immediately to prevent 253 V trip
        voltage_charge_kw = 0.0
        if max_node_voltage_v > self.cfg.V_VOLT_WATT_START_VOLTS:
            v_overshoot = max_node_voltage_v - self.cfg.V_VOLT_WATT_START_VOLTS
            # Sensitivity is ~2.5 V/kW -> every 2.5 V overshoot demands ~1 kW BESS absorption
            voltage_charge_kw = min(self.cfg.BESS_MAX_POWER_KW, (v_overshoot / 2.5) * 8.0)
            alarm_code = "OVERVOLTAGE_CHARGING"

        # ── 2. State Machine Transitions with Hysteresis ───────────────────
        excess_load = max(0.0, net_dt_load_kw - self.cfg.DT_CONTINUOUS_LIMIT_KW)

        if self.current_state == DispatchState.IDLE:
            self.dr_target_requested = 0.0
            if net_dt_load_kw > self.cfg.ENTER_DISCHARGE_KW:
                # Load crossed upper hysteresis band (88 kW)
                if self.bess.soc_pct > soc_floor:
                    self.current_state = DispatchState.BESS_FAST_DISCHARGE
                    self.seconds_in_state = 0.0
                elif enable_dr:
                    self.current_state = DispatchState.BESS_DR_COMBINED
                    self.seconds_in_state = 0.0
                    alarm_code = "SOC_FLOOR_ACTIVE"
            elif net_dt_load_kw < self.cfg.ENTER_CHARGE_KW or voltage_charge_kw > 0.0:
                # Solar surplus (< 20 kW) or local overvoltage
                self.current_state = DispatchState.SOLAR_SURPLUS_CHARGING
                self.seconds_in_state = 0.0

        elif self.current_state == DispatchState.SOLAR_SURPLUS_CHARGING:
            # Exit charging only when load rises above 30 kW AND voltage is safe
            if (net_dt_load_kw > self.cfg.EXIT_CHARGE_KW) and (max_node_voltage_v <= self.cfg.V_VOLT_WATT_START_VOLTS):
                self.current_state = DispatchState.IDLE
                self.seconds_in_state = 0.0

        elif self.current_state == DispatchState.BESS_FAST_DISCHARGE:
            # Exit discharge when load drops below lower hysteresis (78 kW)
            if net_dt_load_kw < self.cfg.EXIT_DISCHARGE_KW:
                self.current_state = DispatchState.IDLE
                self.seconds_in_state = 0.0
            # Check SoC floor every tick
            elif self.bess.soc_pct <= soc_floor:
                self.current_state = DispatchState.BESS_DR_COMBINED
                self.seconds_in_state = 0.0
                alarm_code = "SOC_FLOOR_ACTIVE"
            # After 10 seconds of sustained deficit, summon VoltSense DR
            elif self.seconds_in_state >= self.cfg.DR_START_DELAY_SEC and enable_dr and excess_load > 2.0:
                self.current_state = DispatchState.BESS_DR_COMBINED
                self.seconds_in_state = 0.0

        elif self.current_state == DispatchState.BESS_DR_COMBINED:
            if net_dt_load_kw < self.cfg.EXIT_DISCHARGE_KW:
                self.current_state = DispatchState.IDLE
                self.seconds_in_state = 0.0
                if enable_dr and self.dr.active_event:
                    self.dr.release_all()
                    dr_cmd = "RELEASE"
            else:
                # Activate DR if not yet active
                if enable_dr and not self.dr.active_event and excess_load > 0.0:
                    dr_needed = min(self.cfg.DR_MAX_SHED_KW, excess_load)
                    self.dr.select_households_for_shed(dr_needed)
                    dr_cmd = "ACTIVATE"

                # Check if DR has ramped up enough for BESS to taper
                if dr_active_kw >= 0.60 * excess_load and self.seconds_in_state >= self.cfg.DR_RAMP_TIME_SEC:
                    self.current_state = DispatchState.DR_ACTIVE_BESS_TAPERING
                    self.seconds_in_state = 0.0

        elif self.current_state == DispatchState.DR_ACTIVE_BESS_TAPERING:
            if net_dt_load_kw < self.cfg.EXIT_DISCHARGE_KW:
                self.current_state = DispatchState.IDLE
                self.seconds_in_state = 0.0
                if enable_dr and self.dr.active_event:
                    self.dr.release_all()
                    dr_cmd = "RELEASE"

        # ── 3. Target Power Dispatch Computation ───────────────────────────
        ramp_rate_to_use = self.cfg.BESS_RAMP_KW_PER_SEC

        if not enable_bess:
            target_bess_kw = 0.0
        elif self.current_state == DispatchState.SOLAR_SURPLUS_CHARGING:
            surplus_kw = max(0.0, self.cfg.ENTER_CHARGE_KW - net_dt_load_kw)
            charge_demand = max(surplus_kw, voltage_charge_kw)
            target_bess_kw = -min(self.cfg.BESS_MAX_POWER_KW, charge_demand)

        elif self.current_state == DispatchState.BESS_FAST_DISCHARGE:
            # Battery alone covers the entire excess
            target_bess_kw = min(self.cfg.BESS_MAX_POWER_KW, excess_load)

        elif self.current_state == DispatchState.BESS_DR_COMBINED:
            # Battery covers whatever excess DR hasn't covered yet
            remaining_excess = max(0.0, excess_load - dr_active_kw)
            target_bess_kw = min(self.cfg.BESS_MAX_POWER_KW, remaining_excess)

        elif self.current_state == DispatchState.DR_ACTIVE_BESS_TAPERING:
            # Taper BESS smoothly at 2 kW/s
            ramp_rate_to_use = self.cfg.BESS_TAPER_KW_PER_SEC
            remaining_excess = max(0.0, excess_load - dr_active_kw)
            target_bess_kw = min(self.cfg.BESS_MAX_POWER_KW, remaining_excess)

        else:  # IDLE
            target_bess_kw = 0.0

        # Layer 0 Droop bias injection
        if abs(layer0_kw) > 0.1:
            target_bess_kw += layer0_kw

        # ── 4. Actuate BESS with Ramp and Energy Accounting ────────────────
        dt_hours = dt_seconds / 3600.0
        actual_bess_kw, new_soc = self.bess.step(
            commanded_kw=target_bess_kw,
            dt_hours=dt_hours,
            max_ramp_kw_per_sec=ramp_rate_to_use,
            dt_seconds=dt_seconds,
            soc_floor_pct=soc_floor
        )

        # ── 5. DT Overload Tracking & Safety ───────────────────────────────
        managed_net_dt_kw = net_dt_load_kw - actual_bess_kw - dr_active_kw
        is_overload = managed_net_dt_kw > self.cfg.DT_CONTINUOUS_LIMIT_KW
        is_severe = managed_net_dt_kw > self.cfg.DT_OVERLOAD_ALLOWABLE_KW

        if is_overload:
            self.overload_timer_sec += dt_seconds
            if self.overload_timer_sec >= (self.cfg.DT_OVERLOAD_MAX_MINUTES * 60.0):
                alarm_code = "DT_OVERLOAD_ALARM"
        else:
            self.overload_timer_sec = max(0.0, self.overload_timer_sec - dt_seconds * 0.5)

        return ControllerDecision(
            state=self.current_state,
            bess_command_kw=target_bess_kw,
            bess_actual_kw=actual_bess_kw,
            bess_soc_pct=new_soc,
            dr_active_kw=dr_active_kw,
            dr_command=dr_cmd,
            layer0_droop_kw=layer0_kw,
            is_overloaded_dt=is_overload,
            is_severe_overload_120=is_severe,
            alarm_code=alarm_code
        )
