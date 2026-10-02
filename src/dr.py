"""
src/dr.py - VoltSense-DSM Demand Response Pool & Comfort Debt Manager
SIMULATED: PAS 1879-compliant automated residential load modulation.
Features:
- Rotating participation fairness using comfort debt tracking.
- Midnight 10% debt decay (0.90 factor).
- Strict comfort constraints: max 45 min/event, max 3 events/day, 60 min cooldown.
- Mid-event user override simulation (default 5% dropout).
"""

from dataclasses import dataclass
from typing import List, Dict, Tuple
import numpy as np
from src.config import GridShieldConfig, CFG


@dataclass
class HouseholdDRState:
    house_id: int
    tier: int                         # Tier 1 (Smart AC), Tier 2 (Smart Plug), Tier 3 (Passive)
    is_enrolled: bool
    controllable_kw: float
    comfort_debt: float = 0.0         # Accumulated burden (kW * minutes)
    is_shed: bool = False
    override_active: bool = False
    events_today: int = 0
    current_event_minutes: float = 0.0
    cooldown_remaining_min: float = 0.0


class VoltSenseDRManager:
    """
    Manages 110 enrolled DR households (30 Tier 1 + 80 Tier 2) out of 150 total.
    """
    def __init__(self, cfg: GridShieldConfig = CFG, seed: int = 42):
        self.cfg = cfg
        self.rng = np.random.RandomState(seed)
        self.households: Dict[int, HouseholdDRState] = {}

        # 30 Tier 1 homes (indices 0..29), 80 Tier 2 homes (indices 30..109), 40 Tier 3 passive (110..149)
        for h in range(cfg.TOTAL_HOMES):
            if h < 30:
                tier = 1
                enrolled = True
                kw = cfg.DR_SHED_PER_HOME_KW
            elif h < 110:
                tier = 2
                enrolled = True
                kw = cfg.DR_SHED_PER_HOME_KW
            else:
                tier = 3
                enrolled = False
                kw = 0.0

            self.households[h] = HouseholdDRState(
                house_id=h,
                tier=tier,
                is_enrolled=enrolled,
                controllable_kw=kw
            )

        self.active_event = False
        self.event_elapsed_seconds = 0.0
        self.requested_target_kw = 0.0
        self.current_shed_kw = 0.0
        self.total_dr_events_completed = 0
        self.total_dr_energy_shed_kwh = 0.0

    def reset(self):
        for h in self.households.values():
            h.comfort_debt = 0.0
            h.is_shed = False
            h.override_active = False
            h.events_today = 0
            h.current_event_minutes = 0.0
            h.cooldown_remaining_min = 0.0
        self.active_event = False
        self.event_elapsed_seconds = 0.0
        self.requested_target_kw = 0.0
        self.current_shed_kw = 0.0

    def get_eligible_households(self) -> List[HouseholdDRState]:
        """Returns enrolled homes that are not in cooldown, not at daily limit, and not overridden."""
        return [
            h for h in self.households.values()
            if h.is_enrolled
            and not h.is_shed
            and not h.override_active
            and h.cooldown_remaining_min <= 0.0
            and h.events_today < self.cfg.DR_MAX_EVENTS_PER_DAY
        ]

    def select_households_for_shed(self, target_kw: float) -> Tuple[List[int], float]:
        """
        Fair selection algorithm based on Comfort Debt (lowest debt chosen first).
        """
        eligible = self.get_eligible_households()
        # Sort ascending by comfort debt
        eligible.sort(key=lambda x: x.comfort_debt)

        selected_ids = []
        accumulated_kw = 0.0
        target = min(target_kw, self.cfg.DR_MAX_SHED_KW)

        for h in eligible:
            if accumulated_kw >= target:
                break
            h.is_shed = True
            h.events_today += 1
            h.current_event_minutes = 0.0
            selected_ids.append(h.house_id)
            accumulated_kw += h.controllable_kw

        self.requested_target_kw = accumulated_kw
        self.active_event = len(selected_ids) > 0
        self.event_elapsed_seconds = 0.0
        return selected_ids, accumulated_kw

    def update_step(self, dt_seconds: float = 1.0) -> float:
        """
        Updates DR ramp-up, event duration, and user overrides.
        Returns the instantaneous actual shed in kW.
        """
        dt_min = dt_seconds / 60.0

        # Update cooldown for inactive homes
        for h in self.households.values():
            if not h.is_shed and h.cooldown_remaining_min > 0.0:
                h.cooldown_remaining_min = max(0.0, h.cooldown_remaining_min - dt_min)

        if not self.active_event:
            self.current_shed_kw = 0.0
            return 0.0

        self.event_elapsed_seconds += dt_seconds
        active_homes = [h for h in self.households.values() if h.is_shed]

        # Check maximum duration constraint (45 minutes)
        for h in active_homes:
            h.current_event_minutes += dt_min
            if h.current_event_minutes >= self.cfg.DR_MAX_DURATION_MIN:
                # Event expired for this home
                self.release_home(h.house_id)

        # Re-fetch active homes
        active_homes = [h for h in self.households.values() if h.is_shed]
        if not active_homes:
            self.active_event = False
            self.current_shed_kw = 0.0
            return 0.0

        # Model 20-second appliance ramp time
        ramp_fraction = min(1.0, self.event_elapsed_seconds / self.cfg.DR_RAMP_TIME_SEC)
        target_sum_kw = sum(h.controllable_kw for h in active_homes)
        self.current_shed_kw = target_sum_kw * ramp_fraction

        self.total_dr_energy_shed_kwh += (self.current_shed_kw * dt_seconds / 3600.0)
        return self.current_shed_kw

    def trigger_mid_event_overrides(self, override_fraction: float = None) -> List[int]:
        """Simulates PAS 1879 user overrides (default 5% drop out mid-event)."""
        prob = override_fraction if override_fraction is not None else self.cfg.DR_OVERRIDE_PROBABILITY
        overridden = []
        active_homes = [h for h in self.households.values() if h.is_shed]

        for h in active_homes:
            if self.rng.rand() < prob:
                h.override_active = True
                self.release_home(h.house_id)
                overridden.append(h.house_id)

        return overridden

    def release_home(self, house_id: int):
        """Releases an individual home, commits comfort debt, and starts cooldown."""
        h = self.households.get(house_id)
        if h and h.is_shed:
            # Debt increment: kW * duration in minutes
            h.comfort_debt += (h.controllable_kw * h.current_event_minutes)
            h.is_shed = False
            h.cooldown_remaining_min = self.cfg.DR_COOLDOWN_MIN

    def process_user_override(self, house_id: int):
        """Processes manual user comfort override per PAS 1879."""
        h = self.households.get(house_id)
        if h:
            h.override_active = True
            if h.is_shed:
                self.release_home(house_id)

    def release_all(self):
        """Terminates active DR event across all households."""
        for h in self.households.values():
            if h.is_shed:
                self.release_home(h.house_id)
        self.active_event = False
        self.event_elapsed_seconds = 0.0
        self.requested_target_kw = 0.0
        self.current_shed_kw = 0.0
        self.total_dr_events_completed += 1

    def midnight_decay(self):
        """Executes midnight 10% debt decay (CITED: 0.90 factor)."""
        for h in self.households.values():
            h.comfort_debt *= self.cfg.DR_COMFORT_DECAY_RATE
            h.events_today = 0
            h.override_active = False

    def get_comfort_debt_cv(self) -> float:
        """Returns coefficient of variation (sigma / mean) of comfort debt across enrolled homes."""
        enrolled_debts = [h.comfort_debt for h in self.households.values() if h.is_enrolled]
        mean_d = float(np.mean(enrolled_debts))
        if mean_d < 1e-4:
            return 0.0
        std_d = float(np.std(enrolled_debts))
        return float(std_d / mean_d)
