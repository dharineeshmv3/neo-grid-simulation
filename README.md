# GridShield: Neighbourhood-Scale 100 kVA Transformer Flexibility Platform
**Schneider Electric Hackathon — Challenge 03: Active Grid Flexibility & Renewable Intermittency**

---

## 1. Executive Summary

**GridShield** is a complete, production-grade active grid-flexibility simulation and interactive dashboard designed for Indian 11 kV / 415 V distribution transformers (DTs). It resolves the two most severe operational challenges facing modern distribution utilities:
1. **Midday Overvoltage & Solar Inverter Tripping:** Reverse power flows from rooftop solar drive feeder voltages past statutory limits (253.0 V / 1.10 pu), causing massive renewable curtailment and inverter lockouts.
2. **Evening Peak Transformer Overload:** Coincident residential cooling and cooking loads surge past the transformer continuous rating (85 kW / 100 kVA @ 0.85 PF) for hours, drastically degrading transformer life.

GridShield coordinates four synchronized layers:
- **Smart Inverters (IS 18968:2025):** Autonomous Volt-Watt and Volt-VAr regulation with dynamic export capping.
- **CommVault BESS (100 kWh / 50 kW):** Shared community energy storage with 50 prosumer slice leases (2 kWh each) and true chemical energy accounting ($\eta_c = \eta_d = 0.93$).
- **VoltSense-DSM (PAS 1879):** Automated demand response pool (110 enrolled households) with comfort debt rotational fairness ($CV < 0.15$) and mid-event user override handling.
- **SolarCast & Beckn / UEI Layer:** Cloud-cover forecasting for morning SoC pre-positioning, offline resilience buffering, and decentralized flexibility transactions.

---

## 2. Quick Start & Setup

### Prerequisites
- Python 3.8+ or Python 3.11+
- Virtual environment (recommended)

### Installation
```bash
# 1. Clone or extract the repository
cd "hackthon electric"

# 2. Install dependencies
pip install -r requirements.txt
```

### Running the Test Suite (21 Acceptance Tests)
Every statutory threshold, state machine condition, and mathematical formula is covered by automated unit tests:
```bash
pytest -v
```
*Status: 21 passed in ~17.5 seconds.*

### Launching the Interactive Streamlit Dashboard
```bash
streamlit run app.py
```
*Access the dashboard at `http://localhost:8501`.*

---

## 3. 3-Minute Live Demo Script for Judges

Follow this exact sequence to demonstrate all core capabilities in 3 minutes:

### Step 1: Break the Baseline (0:00 – 0:45)
1. Open the dashboard at `http://localhost:8501`.
2. On the sidebar, select **Scenario Preset: "Stress day"** and **PV Penetration: 100%**.
3. Navigate to **Page 1: Overview (S0 vs S3)**.
4. **Observe S0 (Legacy Baseline):**
   - Max Feeder Voltage surges to **271.8 V** (far beyond the 253.0 V statutory limit).
   - Inverters trip **148 times** across the feeder, losing 42.6 kWh of clean solar.
   - Evening load surges to **133.08 kW**, cooking the transformer with **217 minutes of continuous thermal overload (>85 kW)**.

### Step 2: Activate GridShield Full Flexibility (0:45 – 1:30)
1. Examine the **S3: GridShield Full Flexibility** column on Page 1.
2. **Observe S3 Holding the Grid:**
   - Inverter trips drop to **0**. Overvoltage violations drop to **0**.
   - Max feeder voltage is held safely below **243.8 V** via autonomous Volt-Watt and dynamic export capping.
   - Peak evening transformer load is shaved from **133.1 kW down to 74.8 kW**, eliminating 100% of transformer overload minutes.
3. Switch to **Page 2: Feeder Voltage & Power Quality** to see per-node voltage trajectories staying inside the IS 18968 operating band (202.4 V to 253.0 V) with phase unbalance (PVUR) strictly $< 2\%$.

### Step 3: Trigger the 1-Second Cloud Drop Handoff (1:30 – 2:00)
1. Navigate to **Page 4: Cloud Event Zoom (1s)**.
2. **Show the Multi-Stage Control Sequence:**
   - At $t = 60\text{ s}$, solar drops by 80% over 5 seconds.
   - **Layer 0 Droop & Layer 1 BESS Fast Discharge:** BESS instantly catches the 40 kW surge in milliseconds, preventing a voltage collapse.
   - **VoltSense-DSM Handoff:** After 10 seconds of sustained deficit, VoltSense DR ramps up over 20 seconds, shedding enrolled residential ACs.
   - **BESS Taper:** Once DR is active, the BESS smoothly tapers down at 2 kW/s.
   - **Measured Result:** **96.07% of battery energy is conserved** (0.11 kWh drained vs 2.80 kWh without DR), preserving battery capacity for the evening peak.

### Step 4: Inject Total Communications Loss (2:00 – 2:30)
1. In the sidebar, set **Communication Resilience Mode: "Mode 3: Total Comms Outage"** and set **Fault Hour: 12.0**.
2. Navigate to **Page 7: Resilience & Outage Modes**.
3. **Observe Fail-Safe Behavior:**
   - At 12:00, central SCADA and AMI communications are severed.
   - **Safe DR Release:** All smart plugs revert to consumer control immediately (0 kW shed), respecting life-safety and comfort.
   - **Autonomous Layer 0 Droop:** The BESS inverter falls back to local physical PT/CT sensors, absorbing power autonomously to prevent midday solar overvoltage.
   - Zero crashes, zero unhandled exceptions, and **zero transformer blackout**.

### Step 5: Protocols & Financial Return (2:30 – 3:00)
1. Navigate to **Page 6: Protocols & MQTT Telemetry**:
   - Inspect the generated **PAS 1879 `DSR_Curtailment_Event`** and **`CEM_Override_Response`** JSON payloads (both verified with **Valid ✓** schema checks and matching `event_id`s).
   - Review the **Beckn / UEI v1.1.0** decentralized flexibility order flow (`search` $\to$ `select` $\to$ `init` $\to$ `confirm`).
2. Navigate to **Page 8: Financial & Business Case**:
   - Total CapEx: ₹19.70 Lakh; RDSS 60% grant + ₹2.0 Lakh VGF $\to$ Net DISCOM equity = **₹5.88 Lakh**.
   - Net annual cash flow: **₹3.18 Lakh/year**, achieving a simple payback of **1.85 years** and a 10-year cumulative return of **₹25.92 Lakh**.

---

## 4. Provenance & Classification Legend

Judges can verify the integrity of all numbers using the three standardized labels:

| Label | Meaning | Where Used |
|---|---|---|
| <span style="background-color:#d1e7dd;color:#0f5132;padding:2px 6px;border-radius:4px;font-weight:bold;">SIMULATED</span> | Calculated live from physics equations, $Z_{\text{bus}}$ power flow, finite state machines, or optimization models. | Voltages, transformer loading, BESS SoC, DR shed, DLMP stack, financial payback. |
| <span style="background-color:#fff3cd;color:#664d03;padding:2px 6px;border-radius:4px;font-weight:bold;">MOCKED</span> | Standardized in-process representation of hardware, smart plugs, or network protocol brokers. | MQTT broker, PAS 1879 Interface A payloads, Beckn/UEI transaction schemas. |
| <span style="background-color:#cfe2ff;color:#084298;padding:2px 6px;border-radius:4px;font-weight:bold;">CITED</span> | Quoted verbatim from statutory standards (IS 18968, PAS 1879, CERC, RDSS) or verified utility pilots. | Inverter voltage setpoints, conductor impedance, utility tariffs, FMEA RPN scores. |

---

## 5. Architectural Directory Layout

```text
├── app.py                      # Master 9-Page Streamlit interactive dashboard
├── config.py -> src/config.py  # Authoritative parameter dictionary with source citations
├── requirements.txt            # Pinned dependency definitions
├── MASTER_PROMPT.md            # Hackathon project blueprint & acceptance criteria
├── data/
│   └── weather_cache_chennai.json  # Offline fallback solar forecast cache
├── docs/
│   ├── ASSUMPTIONS.md          # Parameter calibration log & ambiguity resolutions
│   ├── TRACEABILITY.md         # Full requirement -> module -> test -> UI mapping
│   └── VALIDATION.md           # Numerical test results & Backend A vs B validation
├── specs/
│   ├── ALGORITHM_AND_DEPLOYMENT.md # Edge controller & state machine specifications
│   ├── PROJECT_REPORT.md           # Business case, pilot data, and system overview
│   └── TECHNICAL_SPECIFICATIONS.md # Technical standards, DLMP math, and protocol schemas
├── src/
│   ├── bess.py                 # CommVault 100 kWh / 50 kW BESS physical model
│   ├── config.py               # Central configuration with zero magic numbers
│   ├── controller.py           # Layer 1 master dispatch state machine (5 states)
│   ├── dlmp.py                 # 3-part Distribution Locational Marginal Pricing
│   ├── dr.py                   # VoltSense-DSM comfort debt rotational fairness manager
│   ├── export_cap.py           # Prosumer dynamic export capping formulation
│   ├── feeder.py               # 3-phase 4-wire radial network (Zbus + pandapower)
│   ├── finance.py              # 10-year project cash flow & payback model
│   ├── inverter.py             # IS 18968:2025 Volt-Watt/Volt-VAr & legacy inverter
│   ├── messages.py             # PAS 1879 & Beckn JSON schema validators & builders
│   ├── mqtt_sim.py             # Mock MQTT broker with offline queueing & flush
│   ├── p2p.py                  # 15-minute time-coincident credit attribution
│   ├── profiles.py             # 150-household load & PV profile generators
│   ├── scenarios.py            # S0-S3 scenario runner & 1-second cloud zoom
│   └── solarcast.py            # Open-Meteo forecast client with offline cache
└── tests/
    ├── test_controller.py      # T08, T09, T10, T11, T12, T16, T18
    ├── test_export_cap.py      # T04
    ├── test_feeder_grid.py     # T14, T19, T20
    ├── test_inverter.py        # T01, T02, T03
    ├── test_market.py          # T05, T06, T07
    └── test_resilience.py      # T13, T15, T17, Mode 2
```

---

## 6. Standards Compliance

- **IS 18968:2025:** Grid Interconnection of Distributed Energy Resources — Volt-Watt ($243.8\text{ V} \to 253.0\text{ V}$) & Volt-VAr ($\pm 44\%$ reactive power).
- **PAS 1879:** Energy Smart Appliances — Interface A DSR Curtailment & CEM Override protocols.
- **Beckn / Unified Energy Interface (UEI) v1.1.0:** Open protocol for decentralized energy flexibility.
- **RDSS & CERC:** Revamped Distribution Sector Scheme 60% CAPEX grant & Time-of-Day tariff regulations.
