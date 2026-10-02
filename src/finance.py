"""
src/finance.py - Financial Model & Sensitivity Analysis Engine
SIMULATED, CITED: Exact financial formulas from PROJECT_REPORT.md §9.
Reproduces:
- CapEx ₹19.70 L, Grants ₹13.82 L (60% RDSS + ₹2L VGF), Net Equity ₹5.88 L
- 3 Revenue streams (Lease ₹1.50 L, DISCOM avoided ₹1.80 L, ToD Arbitrage ₹0.84 L)
- OpEx ₹0.96 L, Annual Net ₹3.18 L, Payback 1.85 years, 10-year Net Profit ₹25.92 L
Supports simulation-measured inputs side-by-side with report values.
"""

from dataclasses import dataclass
from typing import Dict, Any, Tuple
from src.config import GridShieldConfig, CFG


@dataclass
class FinancialSummary:
    capex_total_inr: float
    grants_total_inr: float
    net_equity_inr: float
    rev_lease_annual_inr: float
    rev_discom_avoided_annual_inr: float
    rev_arbitrage_annual_inr: float
    total_revenue_annual_inr: float
    opex_annual_inr: float
    net_income_annual_inr: float
    payback_years: float
    ten_year_net_profit_inr: float
    capex_per_household_inr: float
    is_simulated_override: bool = False

    @property
    def capex_lakh_inr(self) -> float:
        return self.capex_total_inr / 100000.0

    @property
    def equity_lakh_inr(self) -> float:
        return self.net_equity_inr / 100000.0

    @property
    def annual_net_lakh_inr(self) -> float:
        return self.net_income_annual_inr / 100000.0

    @property
    def cumulative_cash_flow_lakh(self) -> list:
        flows = [-self.equity_lakh_inr]
        for y in range(1, 11):
            flows.append(round(flows[-1] + self.annual_net_lakh_inr, 2))
        return flows


def calculate_project_financials(
    arbitrage_kwh_yr: float = None,
    peak_shaving_kwh_yr: float = None,
    grant_fraction: float = None,
    vgf_lakh_inr: float = None,
    monthly_lease_fee_inr: float = None,
    avoided_dt_failure_inr: float = None,
    cfg: GridShieldConfig = CFG
) -> FinancialSummary:
    """
    Convenience wrapper matching dashboard interface.
    """
    daily_arbitrage = (arbitrage_kwh_yr / cfg.ANNUAL_OPERATING_DAYS) if arbitrage_kwh_yr else None
    daily_peak_shaved = (peak_shaving_kwh_yr / cfg.ANNUAL_OPERATING_DAYS) if peak_shaving_kwh_yr else None
    vgf_inr = (vgf_lakh_inr * 1e5) if vgf_lakh_inr is not None else None

    return compute_financials(
        cfg=cfg,
        rdss_grant_pct=grant_fraction,
        vgf_grant_inr=vgf_inr,
        prosumer_monthly_lease_inr=monthly_lease_fee_inr,
        avoided_dt_cost_annual_inr=avoided_dt_failure_inr,
        simulated_daily_arbitrage_kwh=daily_arbitrage,
        simulated_peak_shaved_kwh_day=daily_peak_shaved
    )


def compute_financials(
    cfg: GridShieldConfig = CFG,
    rdss_grant_pct: float = None,
    vgf_grant_inr: float = None,
    prosumer_monthly_lease_inr: float = None,
    prosumer_count: int = None,
    avoided_dt_cost_annual_inr: float = None,
    simulated_daily_arbitrage_kwh: float = None,
    simulated_peak_shaved_kwh_day: float = None
) -> FinancialSummary:
    """
    Computes financial metrics with optional custom or simulation overrides.
    """
    capex = cfg.CAPEX_TOTAL_INR  # ₹19,70,000

    # 1. Grants & Equity
    rdss_pct = rdss_grant_pct if rdss_grant_pct is not None else cfg.GRANT_RDSS_GBS_PCT  # 0.60
    vgf = vgf_grant_inr if vgf_grant_inr is not None else cfg.GRANT_VGF_INR              # ₹2,00,000
    grants_total = (capex * rdss_pct) + vgf                                              # ₹13,82,000
    net_equity = max(0.0, capex - grants_total)                                          # ₹5,88,000

    # 2. Revenue Stream 1: Prosumer Lease Fees
    lease_fee = prosumer_monthly_lease_inr if prosumer_monthly_lease_inr is not None else cfg.PROSUMER_MONTHLY_LEASE_INR
    pros_count = prosumer_count if prosumer_count is not None else cfg.PROSUMER_LEASE_COUNT
    rev_lease = pros_count * lease_fee * 12.0  # 50 * 250 * 12 = ₹1,50,000

    # 3. Revenue Stream 2: DISCOM Avoided-Cost / DT Deferral
    rev_discom = avoided_dt_cost_annual_inr if avoided_dt_cost_annual_inr is not None else cfg.DT_FAILURE_AVOIDED_ANNUAL_INR  # ₹1,80,000

    # 4. Revenue Stream 3: ToD Energy Arbitrage
    # Charge off-peak (₹3.50/kWh), discharge peak (₹7.00/kWh) -> ₹3.50 spread
    arbitrage_spread = cfg.TARIFF_RETAIL_INR - cfg.TARIFF_OFFPEAK_CHARGE_INR  # ₹3.50/kWh
    operating_days = cfg.ANNUAL_OPERATING_DAYS                                # 300 days

    if simulated_daily_arbitrage_kwh is not None:
        daily_kwh = simulated_daily_arbitrage_kwh
        is_sim = True
    else:
        daily_kwh = 80.0  # CITED report baseline (80 kWh/day)
        is_sim = False

    rev_arbitrage = daily_kwh * arbitrage_spread * operating_days  # 80 * 3.50 * 300 = ₹84,000

    # 5. Net Income & Payback
    total_rev = rev_lease + rev_discom + rev_arbitrage             # ₹4,14,000
    opex = cfg.OPEX_ANNUAL_INR                                     # ₹96,000
    net_annual = total_rev - opex                                  # ₹3,18,000

    payback = (net_equity / net_annual) if net_annual > 0 else float("inf")  # 1.849 ≈ 1.85 years
    ten_yr_profit = (net_annual * 10.0) - net_equity                         # ₹25,92,000
    capex_per_home = capex / cfg.TOTAL_HOMES                                 # ₹19,70,000 / 150 = ₹13,133

    return FinancialSummary(
        capex_total_inr=capex,
        grants_total_inr=grants_total,
        net_equity_inr=net_equity,
        rev_lease_annual_inr=rev_lease,
        rev_discom_avoided_annual_inr=rev_discom,
        rev_arbitrage_annual_inr=rev_arbitrage,
        total_revenue_annual_inr=total_rev,
        opex_annual_inr=opex,
        net_income_annual_inr=net_annual,
        payback_years=round(payback, 2),
        ten_year_net_profit_inr=ten_yr_profit,
        capex_per_household_inr=round(capex_per_home, 2),
        is_simulated_override=is_sim
    )
