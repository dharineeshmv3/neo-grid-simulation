"""
src/dlmp.py - Distribution Locational Marginal Pricing (DLMP) Engine
SIMULATED, CITED: Implements 3-part marginal pricing per TECHNICAL_SPECIFICATIONS.md §4.
DLMP_i(t) = lambda_energy(t) + mu_loss,i(t) + gamma_congestion,i(t)
- Energy: wholesale base tariff (lambda)
- Loss: marginal losses sensitivity (mu_loss = lambda * LF_i)
- Congestion: shadow price when transformer load > 85 kW limit
Reproduces exact spec unit-test examples (₹3.80, ₹8.50, ₹3.10).
"""

from dataclasses import dataclass
from typing import Dict, Any, Tuple
import numpy as np
from src.config import GridShieldConfig, CFG


@dataclass
class DLMPResult:
    dlmp_per_node: np.ndarray       # Shape (150,) in ₹/kWh
    energy_component: float         # λ_energy in ₹/kWh
    loss_components: np.ndarray     # μ_loss in ₹/kWh
    congestion_component: float     # γ_congestion in ₹/kWh
    average_dlmp: float
    max_dlmp: float
    min_dlmp: float


@dataclass
class SingleDLMPResult:
    energy_component_inr: float
    loss_component_inr: float
    congestion_component_inr: float
    dlmp_inr_per_kwh: float


def compute_feeder_loss_factor(dt_kw: float, r_feeder_ohms: float = 0.94, v_nom: float = 230.0) -> float:
    """Computes marginal loss factor dP_loss/dP."""
    i_load = (dt_kw * 1000.0) / (3.0 * v_nom)
    loss_factor = (2.0 * i_load * r_feeder_ohms) / v_nom
    return float(np.clip(loss_factor, -0.15, 0.25))


def compute_congestion_shadow_price(dt_kw: float, dt_limit_kw: float = 85.0) -> float:
    """Computes congestion shadow price if DT limit is exceeded."""
    if dt_kw > dt_limit_kw:
        overshoot = dt_kw - dt_limit_kw
        return float(min(3.0, overshoot * 0.10))
    return 0.0


def calculate_dlmp(
    hour: float,
    loss_factor: float,
    shadow_price_congestion: float,
    cfg: GridShieldConfig = CFG
) -> SingleDLMPResult:
    """Computes single-bus 3-part DLMP stack for the dashboard."""
    if (hour >= 18.0) and (hour <= 22.0):
        energy = cfg.TARIFF_RETAIL_INR
    elif (hour >= 23.0) or (hour <= 5.0):
        energy = cfg.TARIFF_OFFPEAK_CHARGE_INR
    elif (hour >= 11.0) and (hour <= 15.0):
        energy = 3.50
    else:
        energy = 5.00

    loss_comp = energy * loss_factor
    congestion_comp = shadow_price_congestion
    total_dlmp = max(0.50, energy + loss_comp + congestion_comp)

    return SingleDLMPResult(
        energy_component_inr=float(energy),
        loss_component_inr=float(loss_comp),
        congestion_component_inr=float(congestion_comp),
        dlmp_inr_per_kwh=float(total_dlmp)
    )


def compute_dlmp_direct(
    lambda_energy_inr: float,
    loss_factor: float,
    shadow_price_inr: float = 0.0,
    ptdf: float = 1.0
) -> float:
    """
    Direct formula for a single bus or test case:
    DLMP = lambda + (lambda * LF) + (pi * PTDF)
    """
    mu_loss = lambda_energy_inr * loss_factor
    gamma_congestion = shadow_price_inr * ptdf
    return float(lambda_energy_inr + mu_loss + gamma_congestion)


def compute_dlmp_feeder(
    p_dt_net_kw: float,
    line_loss_kw: float,
    hour: float,
    cfg: GridShieldConfig = CFG,
    dv_dp_sensitivities: np.ndarray = None
) -> DLMPResult:
    """
    Computes DLMP dynamically across all 150 nodes from simulated operating conditions.
    """
    # 1. Base Energy Price (Time of Day wholesale rate at 11 kV bus)
    if (hour >= 18.0) and (hour <= 22.0):
        # Evening peak
        lambda_energy = cfg.TARIFF_RETAIL_INR  # ₹7.00/kWh peaker cost
    elif (hour >= 23.0) or (hour <= 5.0):
        # Night off-peak
        lambda_energy = cfg.TARIFF_OFFPEAK_CHARGE_INR  # ₹3.50/kWh off-peak
    else:
        # Normal daytime
        lambda_energy = 4.00  # ₹4.00/kWh daytime wholesale

    # 2. Congestion Shadow Price (Non-zero only when DT exceeds 85 kW continuous limit)
    excess_dt_kw = max(0.0, p_dt_net_kw - cfg.DT_CONTINUOUS_LIMIT_KW)
    if excess_dt_kw > 0.0:
        # Shadow price increases with thermal stress up to ₹4.00/kWh at severe overload
        gamma_congestion = min(4.00, (excess_dt_kw / (cfg.DT_OVERLOAD_ALLOWABLE_KW - cfg.DT_CONTINUOUS_LIMIT_KW)) * 4.00)
    else:
        gamma_congestion = 0.0

    # 3. Marginal Loss Factor (LF_i) per node
    # Based on distance from transformer and net power flow direction
    # Prosumer solar export reduces line losses (LF < 0); heavy load increases line losses (LF > 0)
    num_nodes = cfg.TOTAL_HOMES
    if dv_dp_sensitivities is not None:
        norm_sens = dv_dp_sensitivities / np.max(dv_dp_sensitivities)
    else:
        norm_sens = np.linspace(0.1, 1.0, num_nodes)

    if p_dt_net_kw < 0.0:
        # Feeder is exporting (surplus solar): local generation reduces upstream losses
        loss_factors = -0.05 * norm_sens  # -0.01 to -0.05
    else:
        # Feeder is consuming: losses increase towards tail end
        loss_factors = 0.12 * norm_sens   # +0.01 to +0.12

    mu_losses = lambda_energy * loss_factors
    dlmp_nodes = lambda_energy + mu_losses + gamma_congestion

    return DLMPResult(
        dlmp_per_node=dlmp_nodes,
        energy_component=lambda_energy,
        loss_components=mu_losses,
        congestion_component=gamma_congestion,
        average_dlmp=float(np.mean(dlmp_nodes)),
        max_dlmp=float(np.max(dlmp_nodes)),
        min_dlmp=float(np.min(dlmp_nodes))
    )
