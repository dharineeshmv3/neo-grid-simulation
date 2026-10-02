"""
src/messages.py - PAS 1879 Interface A & Beckn / UEI Protocol Builders and Validators
MOCKED, CITED: Exact JSON Schemas extracted from TECHNICAL_SPECIFICATIONS.md §9.
Validates:
- PAS 1879: DSR_Curtailment_Event (DSRSP -> CEM)
- PAS 1879: CEM_Override_Response (CEM -> DSRSP)
- Beckn/UEI v1.1.0: search -> select -> init -> confirm for uei:energy:flexibility
"""

import json
import uuid
from datetime import datetime, timezone
from typing import Dict, Any, Tuple
import jsonschema


# ── 1. JSON Schemas (per TECHNICAL_SPECIFICATIONS.md §9) ───────────────────

SCHEMA_PAS1879_CURTAILMENT = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "DSR_Curtailment_Event",
    "type": "object",
    "required": ["message_type", "event_id", "timestamp", "dsrsp_id", "cem_id", "payload"],
    "properties": {
        "message_type": {"type": "string", "enum": ["DSR_Curtailment_Event"]},
        "event_id": {"type": "string"},
        "timestamp": {"type": "string"},
        "dsrsp_id": {"type": "string"},
        "cem_id": {"type": "string"},
        "payload": {
            "type": "object",
            "required": ["start_time", "duration_seconds", "power_limit_watts", "target_appliance_category", "incentive_credit_inr"],
            "properties": {
                "start_time": {"type": "string"},
                "duration_seconds": {"type": "integer", "minimum": 1},
                "power_limit_watts": {"type": "number", "minimum": 0.0},
                "target_appliance_category": {"type": "string"},
                "incentive_credit_inr": {"type": "number", "minimum": 0.0}
            }
        }
    }
}

SCHEMA_PAS1879_OVERRIDE = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "CEM_Override_Response",
    "type": "object",
    "required": ["message_type", "event_id", "timestamp", "cem_id", "override_status"],
    "properties": {
        "message_type": {"type": "string", "enum": ["CEM_Override_Response"]},
        "event_id": {"type": "string"},
        "timestamp": {"type": "string"},
        "cem_id": {"type": "string"},
        "override_status": {
            "type": "object",
            "required": ["user_override_active", "override_reason", "current_power_draw_watts", "penalty_forfeit_inr"],
            "properties": {
                "user_override_active": {"type": "boolean"},
                "override_reason": {"type": "string"},
                "current_power_draw_watts": {"type": "number"},
                "penalty_forfeit_inr": {"type": "number"}
            }
        }
    }
}

SCHEMA_BECKN_FLEXIBILITY = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "Beckn_UEI_Flexibility_Message",
    "type": "object",
    "required": ["context", "message"],
    "properties": {
        "context": {
            "type": "object",
            "required": ["domain", "action", "bap_id", "transaction_id", "timestamp"],
            "properties": {
                "domain": {"type": "string", "enum": ["uei:energy:flexibility"]},
                "action": {"type": "string", "enum": ["search", "select", "init", "confirm"]},
                "bap_id": {"type": "string"},
                "bpp_id": {"type": "string"},
                "transaction_id": {"type": "string"},
                "timestamp": {"type": "string"}
            }
        },
        "message": {"type": "object"}
    }
}


# ── 2. Message Builders & Validators ───────────────────────────────────────

def build_dsr_curtailment_event(
    house_id: int,
    power_limit_watts: float = 1500.0,
    duration_seconds: int = 2700,
    incentive_inr: float = 5.0,
    event_id: str = None
) -> Dict[str, Any]:
    """
    Constructs and validates a PAS 1879 Interface A load curtailment command.
    """
    evt_id = event_id if event_id is not None else f"evt_{uuid.uuid4().hex[:8]}"
    now_str = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    msg = {
        "message_type": "DSR_Curtailment_Event",
        "event_id": evt_id,
        "timestamp": now_str,
        "dsrsp_id": "dsrsp_gridshield_01",
        "cem_id": f"cem_home_{house_id:04d}",
        "payload": {
            "start_time": now_str,
            "duration_seconds": int(duration_seconds),
            "power_limit_watts": float(power_limit_watts),
            "target_appliance_category": "HVAC_INVERTER_AC",
            "incentive_credit_inr": float(incentive_inr)
        }
    }

    # Validate against schema
    jsonschema.validate(instance=msg, schema=SCHEMA_PAS1879_CURTAILMENT)
    return msg


def build_cem_override_response(
    event_id: str,
    house_id: int,
    current_power_draw_watts: float = 2200.0,
    penalty_forfeit_inr: float = 5.0,
    reason: str = "USER_COMFORT_SETPOINT_TRIGGERED"
) -> Dict[str, Any]:
    """
    Constructs and validates a PAS 1879 Interface A comfort override notification.
    """
    now_str = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    msg = {
        "message_type": "CEM_Override_Response",
        "event_id": event_id,
        "timestamp": now_str,
        "cem_id": f"cem_home_{house_id:04d}",
        "override_status": {
            "user_override_active": True,
            "override_reason": reason,
            "current_power_draw_watts": float(current_power_draw_watts),
            "penalty_forfeit_inr": float(penalty_forfeit_inr)
        }
    }

    jsonschema.validate(instance=msg, schema=SCHEMA_PAS1879_OVERRIDE)
    return msg


def build_beckn_flexibility_flow(
    action: str = "confirm",
    transaction_id: str = None,
    rate_kw: float = 20.0,
    duration_minutes: int = 120,
    rate_per_kwh: float = 5.20
) -> Dict[str, Any]:
    """
    Constructs and validates Beckn/UEI transaction payloads: search, select, init, confirm.
    """
    txn_id = transaction_id if transaction_id is not None else f"txn_{uuid.uuid4().hex[:8]}"
    now_str = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    if action == "search":
        msg_body = {
            "intent": {
                "item": {"descriptor": {"name": "BESS_PEAK_DISCHARGE"}},
                "fulfillment": {
                    "stops": [{
                        "location": {"gps": "13.0827,80.2707"},
                        "time": {"range": {
                            "start": now_str,
                            "end": now_str
                        }}
                    }]
                }
            }
        }
    elif action == "confirm":
        msg_body = {
            "order": {
                "status": "DISPATCH_CONFIRMED",
                "items": [{
                    "id": "bess_slice_100kwh",
                    "dispatch_schedule": {
                        "rate_kw": float(rate_kw),
                        "duration_minutes": int(duration_minutes),
                        "target_voltage_pu": 1.00
                    }
                }],
                "fulfillment": {
                    "tracking": True,
                    "telemetry_stream": "mqtts://mock.gridshield.local:8883/dt_07/telemetry"
                },
                "payment": {
                    "type": "POST_FULFILLMENT_WALLET_CREDIT",
                    "settlement_mechanism": "RDSS_PREPAID_SMART_METER",
                    "currency": "INR",
                    "rate_per_kwh": float(rate_per_kwh)
                }
            }
        }
    else:  # select, init
        msg_body = {
            "order": {
                "items": [{"id": "bess_slice_100kwh", "quantity": {"count": 1}}],
                "quote": {"price": {"currency": "INR", "value": str(rate_per_kwh)}}
            }
        }

    full_payload = {
        "context": {
            "domain": "uei:energy:flexibility",
            "action": action,
            "bap_id": "discom.delhi.grid",
            "bpp_id": "commvault.gridshield.aggregator",
            "transaction_id": txn_id,
            "timestamp": now_str
        },
        "message": msg_body
    }

    jsonschema.validate(instance=full_payload, schema=SCHEMA_BECKN_FLEXIBILITY)
    return full_payload
