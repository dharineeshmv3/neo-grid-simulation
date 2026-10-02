"""
src/config.py - GridShield Configuration & Authoritative Parameters
All constants derived directly from specs with citations.
Non-negotiable Rule 1: No magic numbers outside this file.
"""

from dataclasses import dataclass, field
from typing import Dict


@dataclass(frozen=True)
class GridShieldConfig:
    # ── Reproducibility (Master Prompt §1 Rule 7) ──────────────────────────
    RANDOM_SEED: int = 42

    # ── Transformer & Grid Parameters (PROJECT_REPORT.md §4, ALGORITHM_AND_DEPLOYMENT.md Part A)
    # 100 kVA Distribution Transformer, 11 kV to 415 V 3-phase 4-wire LT bus
    DT_RATED_KVA: float = 100.0                       # CITED: specs/PROJECT_REPORT.md §4
    DT_CONTINUOUS_LIMIT_KW: float = 85.0              # CITED: specs/PROJECT_REPORT.md §4
    DT_OVERLOAD_ALLOWABLE_KW: float = 102.0           # CITED: 120% of 85 kW, specs/ALGORITHM_AND_DEPLOYMENT.md
    DT_OVERLOAD_MAX_MINUTES: float = 30.0             # CITED: specs/ALGORITHM_AND_DEPLOYMENT.md Part B
    NOMINAL_VOLTAGE_LN: float = 230.0                 # CITED: specs/TECHNICAL_SPECIFICATIONS.md §1
    NOMINAL_VOLTAGE_LL: float = 400.0                 # CITED: specs/TECHNICAL_SPECIFICATIONS.md §1
    GRID_FREQUENCY_HZ: float = 50.0                   # CITED: Indian grid standard

    # ── Voltage Limits per IS 18968:2025 (TECHNICAL_SPECIFICATIONS.md §1) ──
    V_MIN_TRIP_PU: float = 0.50                       # CITED: <115 V LV trip (Clause 5.3)
    V_MANDATORY_RIDE_MIN_PU: float = 0.70             # CITED: 161 V ride-through floor
    V_CONTINUOUS_MIN_PU: float = 0.88                 # CITED: 202.4 V continuous lower limit
    V_VOLT_WATT_START_PU: float = 1.06                # CITED: 243.8 V Volt-Watt start
    V_VOLT_WATT_ZERO_PU: float = 1.10                 # CITED: 253.0 V Volt-Watt cutoff (1.10 pu)
    V_MAX_TRIP_PU: float = 1.20                       # CITED: 276.0 V HV trip (2 s clearing)

    # Absolute Voltage Thresholds (Volts RMS LN)
    V_MIN_TRIP_VOLTS: float = 115.0                   # 0.50 * 230 V
    V_MANDATORY_RIDE_MIN_VOLTS: float = 161.0         # 0.70 * 230 V
    V_CONTINUOUS_MIN_VOLTS: float = 202.4             # 0.88 * 230 V
    V_VOLT_WATT_START_VOLTS: float = 243.8            # 1.06 * 230 V
    V_VOLT_WATT_ZERO_VOLTS: float = 253.0             # 1.10 * 230 V
    V_MAX_TRIP_VOLTS: float = 276.0                   # 1.20 * 230 V

    # Smart Inverter Time Constants (IS 18968:2025 Category B)
    INVERTER_OPEN_LOOP_TIME_SEC: float = 5.0          # CITED: Category B default 5 s
    INVERTER_CATEGORY_A_TIME_SEC: float = 10.0        # CITED: Category A default 10 s
    INVERTER_LEGACY_TRIP_DELAY_SEC: float = 5.0       # ASSUMPTION: 5 s trip delay for legacy
    INVERTER_LEGACY_RECONNECT_SEC: float = 300.0      # ASSUMPTION: 5 min reconnect timer

    # Smart Inverter Volt-VAr limits (IS 18968 Annex J-8)
    VOLT_VAR_MAX_INJECT_KVAR: float = 2.8             # CITED: +2,800 VAR at <=202.4 V
    VOLT_VAR_MAX_ABSORB_KVAR: float = -2.6            # CITED: -2,600 VAR at >=253.0 V

    # Power Quality Standard Thresholds
    PVUR_MAX_LIMIT_PCT: float = 2.0                   # CITED: Phase Voltage Unbalance Rate <= 2%
    PVUR_MAX_PERCENT: float = 2.0                     # Alias for PVUR_MAX_LIMIT_PCT (used in dashboard)
    NEUTRAL_CURRENT_MAX_LIMIT_RATIO: float = 0.50     # CITED: Neutral <= 50% of phase rating

    # ── CommVault BESS Specifications (PROJECT_REPORT.md §4, TECHNICAL_SPECIFICATIONS.md §5)
    BESS_CAPACITY_KWH: float = 100.0                  # CITED: 100 kWh LFP / Na-Ion pack
    BESS_MAX_POWER_KW: float = 50.0                   # CITED: 50 kW bidirectional inverter
    BESS_MIN_SOC_PCT: float = 15.0                    # CITED: 15% emergency reserve
    BESS_MAX_SOC_PCT: float = 95.0                    # CITED: 95% longevity ceiling
    BESS_CHARGE_EFF: float = 0.93                     # CITED: 0.93 * 0.93 = 86.49% round-trip >= 85%
    BESS_DISCHARGE_EFF: float = 0.93                  # CITED: specs/PROJECT_REPORT.md §4
    BESS_RAMP_KW_PER_SEC: float = 10.0                # CITED: 10 kW/s general ramp rate
    BESS_TAPER_KW_PER_SEC: float = 2.0                # CITED: 2 kW/s smooth taper ramp rate
    BESS_AUX_LOAD_KW: float = 1.0                     # ASSUMPTION: 1 kW constant auxiliary HVAC
    BESS_INITIAL_SOC_DEFAULT: float = 60.0            # Default morning SoC %

    # ── VoltSense-DSM Parameters (ALGORITHM_AND_DEPLOYMENT.md Part C) ──────
    TOTAL_HOMES: int = 150                            # CITED: 150 homes per transformer
    HOMES_PER_PHASE: int = 50                         # CITED: 50 homes on A, B, C
    PROSUMER_HOMES: int = 50                          # CITED: 50 prosumers holding 2 kWh slice
    PROSUMER_PV_RATING_KWP: float = 3.0               # CITED: 3 kWp single-phase per prosumer
    DR_ENROLLED_HOMES: int = 110                      # CITED: Tier 1 (30) + Tier 2 (80)
    DR_SHED_PER_HOME_KW: float = 0.6                  # CITED: 0.6 kW average AC shed
    DR_MAX_SHED_KW: float = 20.0                      # CITED: Max 20 kW pool capacity
    DR_RAMP_TIME_SEC: float = 20.0                    # CITED: 20 s appliance ramp-up
    DR_START_DELAY_SEC: float = 10.0                  # CITED: 10 s wait before calling DR
    DR_MAX_DURATION_MIN: float = 45.0                 # CITED: Max 45 min per event
    DR_COOLDOWN_MIN: float = 60.0                     # CITED: Minimum 60 min between events
    DR_MAX_EVENTS_PER_DAY: int = 3                    # CITED: Max 3 events per day
    DR_OVERRIDE_PROBABILITY: float = 0.05             # ASSUMPTION: 5% random consumer override
    DR_COMFORT_DECAY_RATE: float = 0.90               # CITED: 10% daily debt decay (0.90 factor)
    DR_INCENTIVE_INR: float = 5.0                     # CITED: ₹5 per completed DR event

    # ── State Machine Hysteresis Thresholds (ALGORITHM_AND_DEPLOYMENT.md Part B)
    ENTER_DISCHARGE_KW: float = 88.0                  # CITED: 3 kW above 85 kW limit
    EXIT_DISCHARGE_KW: float = 78.0                   # CITED: 7 kW below 85 kW limit
    ENTER_CHARGE_KW: float = 20.0                     # CITED: Surplus threshold
    EXIT_CHARGE_KW: float = 30.0                      # CITED: Stop charging threshold

    # ── Hourly Time-Based SoC Floors (ALGORITHM_AND_DEPLOYMENT.md Part B) ───
    SOC_FLOORS_BY_HOUR: Dict[int, float] = field(default_factory=lambda: {
        0: 15.0, 1: 15.0, 2: 15.0, 3: 15.0, 4: 15.0, 5: 15.0,    # Night
        6: 20.0, 7: 20.0, 8: 20.0, 9: 20.0, 10: 20.0, 11: 20.0,  # Morning
        12: 30.0, 13: 30.0, 14: 30.0,                             # Midday cloud risk
        15: 40.0, 16: 40.0,                                       # Pre-evening
        17: 50.0,                                                 # Peak preparation
        18: 15.0, 19: 15.0, 20: 15.0, 21: 15.0,                   # Evening peak discharge
        22: 15.0, 23: 15.0                                        # Night recharge
    })

    # ── Tariffs & Market Settlements (TECHNICAL_SPECIFICATIONS.md §5, PROJECT_REPORT.md §9)
    TARIFF_FIT_INR: float = 2.00                      # CITED: Lucknow pilot baseline FiT
    TARIFF_RETAIL_INR: float = 7.00                   # CITED: Delhi / UP average retail
    TARIFF_P2P_CLEARING_INR: float = 5.20             # CITED: Midpoint clearing price
    TARIFF_OFFPEAK_CHARGE_INR: float = 3.50           # CITED: Night grid charge (23:00-05:00)
    DISCOM_WHEELING_CHARGE_INR: float = 0.50          # CITED: Network access charge per kWh
    TRAS_UP_INCENTIVE_RATIO: float = 1.10             # CITED: CERC AS 2022 (110% of energy charge)
    TRAS_DOWN_PAYBACK_RATIO: float = 0.90             # CITED: CERC AS 2022 (90% payback)

    # ── SolarCast Parameters (PROJECT_REPORT.md §6) ────────────────────────
    SOLARCAST_LAT: float = 13.08                      # CITED: Chennai coordinates
    SOLARCAST_LON: float = 80.27
    SOLARCAST_CLEAR_THRESHOLD_PCT: float = 20.0       # < 20% cloud -> 30% target SoC
    SOLARCAST_HEDGE_THRESHOLD_PCT: float = 60.0       # 20-60% cloud -> 60% target SoC
    SOLARCAST_PRECHARGE_SOC_PCT: float = 90.0         # > 60% cloud -> 90% target SoC
    SOLARCAST_API_FAIL_SOC_PCT: float = 60.0          # Fallback default SoC %

    # ── Feeder Line Parameters (Calibrated for T14) ────────────────────────
    FEEDER_LENGTH_METERS: float = 400.0               # ASSUMPTION: 400 m radial feeder
    CABLE_R_PER_KM: float = 2.35                      # CALIBRATED for T14: ACSR Weasel conductor (30 mm2)
    R_FEEDER_PHASE_OHMS_PER_KM: float = 2.35          # Alias for CABLE_R_PER_KM (used in DLMP calculation)
    CABLE_X_PER_KM: float = 0.08                      # ASSUMPTION: 0.08 Ohm/km reactance
    NEUTRAL_R_PER_KM: float = 2.90                    # ASSUMPTION: 1.25x phase R
    TRANSFORMER_R_PU: float = 0.02                    # 100 kVA transformer internal resistance
    TRANSFORMER_X_PU: float = 0.04                    # 100 kVA transformer leakage reactance
    NO_LOAD_SUBSTATION_VOLTAGE_LN: float = 237.0      # CALIBRATED for T14: tap = 1.03 pu (standard DT setting)

    # ── Financial Parameters (PROJECT_REPORT.md §9) ────────────────────────
    CAPEX_TOTAL_INR: float = 1970000.0                # CITED: ₹19.70 Lakhs
    GRANT_RDSS_GBS_PCT: float = 0.60                  # CITED: 60% GBS for normal states
    GRANT_VGF_INR: float = 200000.0                   # CITED: ₹2.00 Lakhs VGF
    OPEX_ANNUAL_INR: float = 96000.0                  # CITED: ₹0.96 Lakhs annual O&M
    PROSUMER_MONTHLY_LEASE_INR: float = 250.0         # CITED: ₹250/month per prosumer
    PROSUMER_LEASE_COUNT: int = 50                    # CITED: 50 prosumers
    DT_FAILURE_AVOIDED_ANNUAL_INR: float = 180000.0   # CITED: ₹1.80 Lakhs avoided failure/peaker
    ANNUAL_OPERATING_DAYS: int = 300                  # CITED: 300 active operational days


# Default immutable config instance
CFG = GridShieldConfig()
