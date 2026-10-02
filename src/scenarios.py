"""
src/scenarios.py - Scenario Simulation Runner (S0, S1, S2, S3 & Cloud Zoom)
SIMULATED: 24-hour comparative evaluations and 1-second cloud-drop zoom.
- S0: Legacy baseline (legacy inverters, no BESS, no DR)
- S1: IS 18968 smart inverters only (Volt-Watt + Volt-VAr, no BESS, no DR)
- S2: + CommVault BESS (S1 + BESS state machine, no DR, no export cap)
- S3: Full GridShield (S2 + dynamic export cap + VoltSense DR + SolarCast)
"""

from dataclasses import dataclass, field
from typing import Dict, Any, List, Tuple, Optional
import numpy as np
from src.config import GridShieldConfig, CFG
from src.feeder import LVFeederModel, FeederState
from src.profiles import generate_feeder_profiles
from src.inverter import LegacyInverter, SmartInverterIS18968
from src.bess import CommVaultBESS
from src.dr import VoltSenseDRManager
from src.controller import GridShieldController, DispatchState
from src.export_cap import calculate_dynamic_export_caps
from src.solarcast import get_solar_forecast
from src.messages import (
    build_dsr_curtailment_event,
    build_cem_override_response,
    build_beckn_flexibility_flow,
    SCHEMA_PAS1879_CURTAILMENT,
    SCHEMA_PAS1879_OVERRIDE,
    SCHEMA_BECKN_FLEXIBILITY
)
from src.mqtt_sim import MockMQTTBroker


@dataclass
class ScenarioResults:
    scenario_id: str
    times_hours: np.ndarray
    dt_load_kw: np.ndarray
    node_voltages: np.ndarray
    max_voltages: np.ndarray
    min_voltages: np.ndarray
    pvur_pct: np.ndarray
    neutral_current_a: np.ndarray
    bess_power_kw: np.ndarray
    bess_soc_pct: np.ndarray
    dr_active_kw: np.ndarray
    curtailed_energy_kwh: float
    overload_minutes: int
    overload_120_minutes: int
    overvoltage_node_minutes: int
    inverter_trips: int
    peak_dt_load_kw: float
    min_node_voltage_v: float
    max_node_voltage_v: float
    controller_states: List[str]
    broker: Optional[MockMQTTBroker] = None
    generated_messages: List[Dict[str, Any]] = field(default_factory=list)
    comms_mode: int = 1
    fault_hour: float = 12.0
    retained_functionality_pct: float = 100.0


def run_scenario(
    scenario_id: str = "S3",
    cfg: GridShieldConfig = CFG,
    scenario_preset: str = "Stress day",
    load_scale: float = 1.0,
    pv_placement: str = "balanced",
    pv_penetration_pct: float = 100.0,
    cloud_event_active: bool = True,
    seed: int = 42,
    comms_mode: int = 1,
    fault_hour: float = 12.0
) -> ScenarioResults:
    """
    Executes a 24-hour, 1-minute resolution (1440 steps) simulation for S0, S1, S2, or S3.
    Includes resilience modes:
    - comms_mode 1: Full connectivity
    - comms_mode 2: Internet down, local network up (cached forecast, local DR/smart plugs, billing queued)
    - comms_mode 3: Total comms failure (DR pauses safely, BESS falls back to Layer 0 droop, no blackout, zero exceptions)
    """
    feeder = LVFeederModel(cfg)
    p_load_kw, q_load_kvar, p_pv_avail_kw, meta = generate_feeder_profiles(
        cfg=cfg,
        scenario_preset=scenario_preset,
        load_scale=load_scale,
        pv_placement=pv_placement,
        pv_penetration_pct=pv_penetration_pct,
        cloud_event_active=cloud_event_active,
        seed=seed
    )

    timesteps = 1440
    times_hours = np.arange(timesteps) / 60.0
    prosumer_indices = meta["prosumer_indices"]
    sensitivities = feeder.get_voltage_sensitivity_vector()

    # Pre-positioning via SolarCast (S3 only)
    if scenario_id == "S3":
        force_fc_failure = (comms_mode == 2 and fault_hour <= 0.0)
        forecast = get_solar_forecast(manual_preset=scenario_preset, force_failure=force_fc_failure, cfg=cfg)
        initial_soc = forecast["target_soc_pct"]
    else:
        initial_soc = cfg.BESS_INITIAL_SOC_DEFAULT

    bess = CommVaultBESS(cfg, initial_soc_pct=initial_soc)
    dr_mgr = VoltSenseDRManager(cfg, seed=seed)
    controller = GridShieldController(bess, dr_mgr, cfg)

    # Inverters
    if scenario_id == "S0":
        inverters = [
            LegacyInverter(
                p_rated_kw=cfg.PROSUMER_PV_RATING_KWP,
                trip_delay_sec=cfg.INVERTER_LEGACY_TRIP_DELAY_SEC,
                reconnect_sec=cfg.INVERTER_LEGACY_RECONNECT_SEC,
                cfg=cfg
            )
            for _ in range(cfg.TOTAL_HOMES)
        ]
    else:
        inverters = [
            SmartInverterIS18968(p_rated_kw=cfg.PROSUMER_PV_RATING_KWP, category="B", cfg=cfg)
            for _ in range(cfg.TOTAL_HOMES)
        ]

    # Protocol & Message Layer (MOCKED)
    broker = MockMQTTBroker()
    generated_messages: List[Dict[str, Any]] = []
    active_dr_events: Dict[int, str] = {}
    override_simulated = False

    # In S3, generate Beckn/UEI flexibility procurement transaction
    if scenario_id == "S3":
        txn_id = f"txn_flex_{seed}"
        for act in ["search", "select", "init", "confirm"]:
            beckn_msg = build_beckn_flexibility_flow(act, transaction_id=txn_id)
            generated_messages.append(beckn_msg)
            broker.publish("gridshield/uei/flexibility", beckn_msg)

    # Arrays
    dt_load_kw = np.zeros(timesteps, dtype=np.float64)
    node_voltages = np.zeros((timesteps, cfg.TOTAL_HOMES), dtype=np.float64)
    max_voltages = np.zeros(timesteps, dtype=np.float64)
    min_voltages = np.zeros(timesteps, dtype=np.float64)
    pvur_pct = np.zeros(timesteps, dtype=np.float64)
    neutral_current_a = np.zeros(timesteps, dtype=np.float64)
    bess_power_kw = np.zeros(timesteps, dtype=np.float64)
    bess_soc_pct = np.zeros(timesteps, dtype=np.float64)
    dr_active_kw = np.zeros(timesteps, dtype=np.float64)
    controller_states = []

    total_curtailed_kwh = 0.0
    trip_events = 0
    was_tripped_prev = [False] * cfg.TOTAL_HOMES
    v_measured_prev = np.full(cfg.TOTAL_HOMES, cfg.NOMINAL_VOLTAGE_LN)

    enable_bess_base = scenario_id in ("S2", "S3")
    enable_dr_base = (scenario_id == "S3")
    enable_export_cap_base = (scenario_id == "S3")

    for t in range(timesteps):
        hour = times_hours[t]
        p_raw_load = np.sum(p_load_kw[t])

        # Resilience Fault Injection Check
        is_faulted = (comms_mode > 1) and (hour >= fault_hour)
        if is_faulted:
            if comms_mode == 2:
                # Mode 2: WAN internet down, local network up
                broker.set_comms_state(False)
                step_enable_bess = enable_bess_base
                step_enable_dr = enable_dr_base
                step_enable_export_cap = enable_export_cap_base
            elif comms_mode == 3:
                # Mode 3: Total comms failure
                broker.set_comms_state(False)
                step_enable_bess = False  # Central Layer 1 down, runs Layer 0 droop
                step_enable_dr = False     # DR pauses safely
                step_enable_export_cap = False  # Aggregator dynamic cap unavailable
                if dr_mgr.active_event:
                    dr_mgr.release_all()
        else:
            broker.set_comms_state(True)
            step_enable_bess = enable_bess_base
            step_enable_dr = enable_dr_base
            step_enable_export_cap = enable_export_cap_base

        # 1. Dynamic Export Cap (S3)
        if step_enable_export_cap and len(prosumer_indices) > 0:
            rated_vector = np.full(len(prosumer_indices), cfg.PROSUMER_PV_RATING_KWP)
            export_caps = calculate_dynamic_export_caps(
                v_measured_nodes=v_measured_prev,
                p_load_dt_kw=p_raw_load,
                p_pv_rated_kw=rated_vector,
                active_prosumer_indices=prosumer_indices,
                dv_dp_sensitivities=sensitivities,
                cfg=cfg
            ).caps_kw
        else:
            export_caps = None

        # 2. Inverter Actuation
        p_pv_actual = np.zeros(cfg.TOTAL_HOMES, dtype=np.float64)
        q_pv_actual = np.zeros(cfg.TOTAL_HOMES, dtype=np.float64)

        for k, h in enumerate(range(cfg.TOTAL_HOMES)):
            p_avail = p_pv_avail_kw[t, h]
            if p_avail > 0.0:
                if export_caps is not None and h in prosumer_indices:
                    idx_pros = int(np.where(prosumer_indices == h)[0][0])
                    p_capped = min(p_avail, export_caps[idx_pros])
                else:
                    p_capped = p_avail

                inv_out = inverters[h].step(v_measured=v_measured_prev[h], p_available_kw=p_capped, dt_sec=60.0)
                p_pv_actual[h] = inv_out.p_actual_kw
                q_pv_actual[h] = inv_out.q_actual_kvar
                curtailed = p_avail - inv_out.p_actual_kw
                total_curtailed_kwh += max(0.0, curtailed / 60.0)

                if inv_out.is_tripped and not was_tripped_prev[h]:
                    trip_events += 1
                was_tripped_prev[h] = inv_out.is_tripped

        # 3. Unmanaged Net Load
        unmanaged_dt_net_kw = np.sum(p_load_kw[t]) - np.sum(p_pv_actual)
        max_v_prev = float(np.max(v_measured_prev))

        # 4. Controller Tick / Mode 3 Fallback
        if is_faulted and comms_mode == 3:
            # Mode 3 Autonomous Layer 0 Droop & Local Reflex
            layer0_kw = controller.compute_layer0_droop_kw(max_v_prev)
            voltage_charge_kw = 0.0
            if max_v_prev > cfg.V_VOLT_WATT_START_VOLTS:
                v_overshoot = max_v_prev - cfg.V_VOLT_WATT_START_VOLTS
                voltage_charge_kw = min(cfg.BESS_MAX_POWER_KW, (v_overshoot / 2.5) * 8.0)

            # High voltage -> charge; low voltage / local DT overload -> discharge
            target_bess_kw = layer0_kw - (voltage_charge_kw if layer0_kw == 0.0 else 0.0)
            if target_bess_kw == 0.0 and unmanaged_dt_net_kw > cfg.DT_CONTINUOUS_LIMIT_KW:
                target_bess_kw = min(cfg.BESS_MAX_POWER_KW, unmanaged_dt_net_kw - cfg.DT_CONTINUOUS_LIMIT_KW)

            # Firmware SoC limits (15% to 90%)
            if target_bess_kw < 0.0 and bess.soc_pct >= cfg.BESS_MAX_SOC_PCT:
                target_bess_kw = 0.0
            elif target_bess_kw > 0.0 and bess.soc_pct <= cfg.BESS_MIN_SOC_PCT:
                target_bess_kw = 0.0

            actual_bess_kw, new_soc = bess.step(
                commanded_kw=target_bess_kw,
                dt_hours=60.0 / 3600.0,
                max_ramp_kw_per_sec=cfg.BESS_RAMP_KW_PER_SEC,
                dt_seconds=60.0,
                soc_floor_pct=cfg.BESS_MIN_SOC_PCT
            )
            bess_power_kw[t] = actual_bess_kw
            bess_soc_pct[t] = new_soc
            dr_active_kw[t] = 0.0
            controller_states.append("MODE3_AUTONOMOUS_DROOP")
            cur_bess_actual = actual_bess_kw
            cur_dr_active = 0.0
        else:
            dec = controller.tick(
                net_dt_load_kw=unmanaged_dt_net_kw,
                hour=hour,
                max_node_voltage_v=max_v_prev,
                dt_seconds=60.0,
                enable_bess=step_enable_bess,
                enable_dr=step_enable_dr
            )
            bess_power_kw[t] = dec.bess_actual_kw
            bess_soc_pct[t] = dec.bess_soc_pct
            dr_active_kw[t] = dec.dr_active_kw
            controller_states.append(dec.state.value)
            cur_bess_actual = dec.bess_actual_kw
            cur_dr_active = dec.dr_active_kw

            # S3 PAS 1879 Message Generation
            if scenario_id == "S3":
                if dec.dr_command == "ACTIVATE":
                    for h in dr_mgr.households.values():
                        if h.is_shed and h.house_id not in active_dr_events:
                            evt_msg = build_dsr_curtailment_event(
                                house_id=h.house_id,
                                power_limit_watts=1500.0,
                                duration_seconds=2700,
                                incentive_inr=5.0
                            )
                            active_dr_events[h.house_id] = evt_msg["event_id"]
                            generated_messages.append(evt_msg)
                            broker.publish(f"gridshield/house/{h.house_id}/dr_command", evt_msg)
                elif dec.dr_command == "RELEASE":
                    active_dr_events.clear()

                # Simulate a comfort override on 1 shed household mid-event to validate PAS 1879 Interface A
                if len(active_dr_events) > 0 and not override_simulated and (t % 3 == 0):
                    target_h = next(iter(active_dr_events.keys()))
                    matched_id = active_dr_events[target_h]
                    ovr_msg = build_cem_override_response(
                        event_id=matched_id,
                        house_id=target_h,
                        current_power_draw_watts=2200.0,
                        penalty_forfeit_inr=5.0,
                        reason="USER_COMFORT_SETPOINT_TRIGGERED"
                    )
                    generated_messages.append(ovr_msg)
                    broker.publish(f"gridshield/house/{target_h}/dr_override", ovr_msg)
                    dr_mgr.process_user_override(target_h)
                    del active_dr_events[target_h]
                    override_simulated = True

        # 5. Feeder Solution
        p_load_net = p_load_kw[t].copy()
        if cur_dr_active > 0.0:
            shed_homes = [h for h in dr_mgr.households.values() if h.is_shed]
            if len(shed_homes) > 0:
                shed_per_home = cur_dr_active / len(shed_homes)
                for h in shed_homes:
                    p_load_net[h.house_id] = max(0.05, p_load_net[h.house_id] - shed_per_home)

        p_net = p_pv_actual - p_load_net
        q_net = q_pv_actual - q_load_kvar[t]

        state = feeder.solve_sensitivity_backend_a(
            p_net_kw=p_net,
            q_net_kvar=q_net,
            p_bess_kw=cur_bess_actual,
            q_bess_kvar=0.0
        )

        v_measured_prev = state.node_voltages
        node_voltages[t] = state.node_voltages
        max_voltages[t] = state.max_voltage
        min_voltages[t] = state.min_voltage
        pvur_pct[t] = state.pvur_pct
        neutral_current_a[t] = state.neutral_current_a

        # Net load on DT
        dt_load_kw[t] = np.sum(p_load_net) - np.sum(p_pv_actual) - cur_bess_actual + state.line_losses_kw

        # Periodic SCADA / Telemetry Publish (every 15 min)
        if scenario_id == "S3" and (t % 15 == 0):
            telem_payload = {
                "time_hour": float(hour),
                "dt_load_kw": float(dt_load_kw[t]),
                "max_node_voltage_v": float(max_voltages[t]),
                "bess_soc_pct": float(bess_soc_pct[t]),
                "bess_power_kw": float(cur_bess_actual),
                "dr_active_kw": float(cur_dr_active),
                "comms_mode": comms_mode
            }
            broker.publish("dt_07/telemetry", telem_payload)

    overload_minutes = int(np.sum(dt_load_kw > cfg.DT_CONTINUOUS_LIMIT_KW))
    overload_120_minutes = int(np.sum(dt_load_kw > cfg.DT_OVERLOAD_ALLOWABLE_KW))
    overvoltage_node_minutes = int(np.sum(node_voltages > cfg.V_VOLT_WATT_ZERO_VOLTS))

    # Retained Functionality Calculation (measured, not claimed)
    if comms_mode == 1:
        retained_functionality_pct = 100.0
    elif comms_mode == 2:
        retained_functionality_pct = float(max(0.0, min(100.0, 100.0 * (1.0 - (overload_minutes / 217.0)))))
    else:  # Mode 3
        retained_functionality_pct = float(max(0.0, min(100.0, 100.0 * (1.0 - (overload_minutes / 217.0)))))

    return ScenarioResults(
        scenario_id=scenario_id,
        times_hours=times_hours,
        dt_load_kw=dt_load_kw,
        node_voltages=node_voltages,
        max_voltages=max_voltages,
        min_voltages=min_voltages,
        pvur_pct=pvur_pct,
        neutral_current_a=neutral_current_a,
        bess_power_kw=bess_power_kw,
        bess_soc_pct=bess_soc_pct,
        dr_active_kw=dr_active_kw,
        curtailed_energy_kwh=float(total_curtailed_kwh),
        overload_minutes=overload_minutes,
        overload_120_minutes=overload_120_minutes,
        overvoltage_node_minutes=overvoltage_node_minutes,
        inverter_trips=trip_events,
        peak_dt_load_kw=float(np.max(dt_load_kw)),
        min_node_voltage_v=float(np.min(node_voltages)),
        max_node_voltage_v=float(np.max(node_voltages)),
        controller_states=controller_states,
        broker=broker,
        generated_messages=generated_messages,
        comms_mode=comms_mode,
        fault_hour=fault_hour,
        retained_functionality_pct=retained_functionality_pct
    )


def run_s0_legacy_baseline(cfg: GridShieldConfig = CFG, **kwargs) -> ScenarioResults:
    return run_scenario(scenario_id="S0", cfg=cfg, **kwargs)


def run_all_scenarios(
    cfg: GridShieldConfig = CFG,
    scenario_preset: str = "Stress day",
    load_scale: float = 1.0,
    pv_placement: str = "balanced",
    pv_penetration_pct: float = 100.0,
    cloud_event_active: bool = True,
    seed: int = 42,
    comms_mode: int = 1,
    fault_hour: float = 12.0
) -> Dict[str, ScenarioResults]:
    """
    Executes all four scenarios (S0, S1, S2, S3) on identical feeder profiles.
    """
    results = {}
    for sc_id in ["S0", "S1", "S2", "S3"]:
        results[sc_id] = run_scenario(
            scenario_id=sc_id,
            cfg=cfg,
            scenario_preset=scenario_preset,
            load_scale=load_scale,
            pv_placement=pv_placement,
            pv_penetration_pct=pv_penetration_pct,
            cloud_event_active=cloud_event_active,
            seed=seed,
            comms_mode=comms_mode if sc_id == "S3" else 1,
            fault_hour=fault_hour
        )
    return results


def run_cloud_event_zoom(
    cfg: GridShieldConfig = CFG,
    duration_seconds: int = 600,  # 10 minutes around 13:30 (13:28 to 13:38)
    seed: int = 42
) -> Dict[str, Any]:
    """
    Executes a high-resolution 1-second simulation zoom comparing:
    - BESS Only (no DR handoff)
    - Full GridShield (BESS with Layer 0 + Layer 1 + DR Handoff)
    Demonstrates Layer 0 -> Layer 1 -> DR handoff and computes exact battery energy saved.
    """
    t_steps = duration_seconds
    time_sec = np.arange(t_steps)

    # Simulate cloud drop: solar ramps down by 80% at t=60s over 5s
    solar_base_kw = 45.0  # 45 kW solar on feeder
    load_base_kw = 98.0   # 98 kW load -> net load surges to 98 - 9 = 89 kW (> 88 kW threshold)
    unmanaged_net = np.zeros(t_steps)

    for s in range(t_steps):
        if s < 60:
            solar = solar_base_kw
        elif s < 65:
            # 5-second fast solar drop ramp
            alpha = (s - 60) / 5.0
            solar = solar_base_kw * (1.0 - 0.80 * alpha)
        else:
            solar = solar_base_kw * 0.20  # -80% drop

        unmanaged_net[s] = load_base_kw - solar

    # 1. Run BESS Only (no DR)
    bess_only = CommVaultBESS(cfg, initial_soc_pct=60.0)
    dr_dummy = VoltSenseDRManager(cfg, seed=seed)
    ctrl_bess_only = GridShieldController(bess_only, dr_dummy, cfg)

    bess_only_p = np.zeros(t_steps)
    bess_only_soc = np.zeros(t_steps)

    for s in range(t_steps):
        dec = ctrl_bess_only.tick(
            net_dt_load_kw=unmanaged_net[s],
            hour=13.5,
            max_node_voltage_v=230.0,
            dt_seconds=1.0,
            enable_bess=True,
            enable_dr=False
        )
        bess_only_p[s] = dec.bess_actual_kw
        bess_only_soc[s] = dec.bess_soc_pct

    # 2. Run Full GridShield (BESS + DR Handoff)
    bess_full = CommVaultBESS(cfg, initial_soc_pct=60.0)
    dr_full = VoltSenseDRManager(cfg, seed=seed)
    ctrl_full = GridShieldController(bess_full, dr_full, cfg)

    full_bess_p = np.zeros(t_steps)
    full_dr_p = np.zeros(t_steps)
    full_soc = np.zeros(t_steps)
    states = []

    for s in range(t_steps):
        dec = ctrl_full.tick(
            net_dt_load_kw=unmanaged_net[s],
            hour=13.5,
            max_node_voltage_v=230.0,
            dt_seconds=1.0,
            enable_bess=True,
            enable_dr=True
        )
        full_bess_p[s] = dec.bess_actual_kw
        full_dr_p[s] = dec.dr_active_kw
        full_soc[s] = dec.bess_soc_pct
        states.append(dec.state.value)

    energy_bess_only_kwh = float(bess_only.total_discharged_kwh)
    energy_full_bess_kwh = float(bess_full.total_discharged_kwh)
    energy_saved_kwh = max(0.0, energy_bess_only_kwh - energy_full_bess_kwh)
    saved_pct = (energy_saved_kwh / energy_bess_only_kwh * 100.0) if energy_bess_only_kwh > 0 else 0.0

    return {
        "time_sec": time_sec,
        "unmanaged_net_kw": unmanaged_net,
        "bess_only_p_kw": bess_only_p,
        "bess_only_soc_pct": bess_only_soc,
        "full_bess_p_kw": full_bess_p,
        "full_dr_p_kw": full_dr_p,
        "full_soc_pct": full_soc,
        "controller_states": states,
        "energy_bess_only_kwh": energy_bess_only_kwh,
        "energy_full_bess_kwh": energy_full_bess_kwh,
        "energy_saved_kwh": energy_saved_kwh,
        "saved_pct": saved_pct
    }
