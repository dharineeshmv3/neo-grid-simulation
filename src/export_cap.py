"""
src/export_cap.py - Dynamic Export Limit Controller
SIMULATED: Implements exact 3-constraint min() formula from R38 / IS 18968:2025.
P_cap,i(t) = min( P_PV,rated,i , (V_max - V_base,i) / (dV_i/dP_i) , (S_DT,max - P_load,DT) / N_active )
"""

from dataclasses import dataclass
from typing import List, Tuple
import numpy as np
from src.config import GridShieldConfig, CFG


@dataclass
class ExportCapResult:
    caps_kw: np.ndarray             # Shape (N,) dynamic export limit per prosumer in kW
    binding_constraint: List[str]   # Constraint label for each prosumer: 'PANEL_RATING', 'VOLTAGE_HEADROOM', 'DT_HEADROOM'


def calculate_dynamic_export_caps(
    v_measured_nodes: np.ndarray,      # Measured node voltages (V LN)
    p_load_dt_kw: float,               # Real-time aggregate load on the distribution transformer (kW)
    p_pv_rated_kw: np.ndarray,         # Rated capacity of prosumers (kW)
    active_prosumer_indices: np.ndarray,# Indices of homes with solar PV
    dv_dp_sensitivities: np.ndarray,   # dV_i / dP_i sensitivity vector (V/kW)
    cfg: GridShieldConfig = CFG,
    v_max_override: float = None
) -> ExportCapResult:
    """
    Computes per-prosumer active power export limit enforcing voltage & DT headroom.
    Recalculated every 5 seconds on the Edge Gateway.
    """
    v_max = v_max_override if v_max_override is not None else cfg.V_VOLT_WATT_ZERO_VOLTS
    n_active = len(active_prosumer_indices)

    if n_active == 0:
        return ExportCapResult(caps_kw=np.array([]), binding_constraint=[])

    # 1. DT capacity headroom allocation
    dt_headroom_kw = max(0.0, cfg.DT_RATED_KVA - p_load_dt_kw)
    dt_share_per_prosumer = dt_headroom_kw / float(n_active)

    caps = np.zeros(n_active, dtype=np.float64)
    binding = []

    for k, idx in enumerate(active_prosumer_indices):
        c1_panel = p_pv_rated_kw[k]

        # 2. Voltage headroom constraint
        v_node = v_measured_nodes[idx]
        v_margin = max(0.0, v_max - v_node)
        sensitivity = dv_dp_sensitivities[idx]
        c2_voltage = v_margin / sensitivity if sensitivity > 1e-4 else c1_panel

        # 3. DT headroom constraint
        c3_dt = dt_share_per_prosumer

        # Evaluate minimum of the three constraints
        min_val = min(c1_panel, c2_voltage, c3_dt)
        min_val = max(0.0, min_val)  # Cap must never be negative
        caps[k] = min_val

        if min_val == c1_panel:
            binding.append("PANEL_RATING")
        elif min_val == c2_voltage:
            binding.append("VOLTAGE_HEADROOM")
        else:
            binding.append("DT_HEADROOM")

    return ExportCapResult(caps_kw=caps, binding_constraint=binding)


def evaluate_export_cap_worked_example(
    p_rated_kw: float = 3.0,
    v_headroom_volts: float = 5.0,
    sensitivity_v_per_kw: float = 2.5,
    dt_capacity_kva: float = 100.0,
    p_load_dt_kw: float = 60.0,
    n_active_prosumers: int = 20
) -> float:
    """
    Direct verification fixture for T04 from TECHNICAL_SPECIFICATIONS.md §3:
    c1 = 3.0 kW
    c2 = 5.0 V / 2.5 V/kW = 2.0 kW
    c3 = (100 - 60) / 20 = 2.0 kW
    min(3.0, 2.0, 2.0) = 2.0 kW exactly.
    """
    c1 = p_rated_kw
    c2 = v_headroom_volts / sensitivity_v_per_kw
    c3 = (dt_capacity_kva - p_load_dt_kw) / float(n_active_prosumers)
    return float(max(0.0, min(c1, c2, c3)))
