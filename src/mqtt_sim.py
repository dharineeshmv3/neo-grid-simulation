"""
src/mqtt_sim.py - In-Process Mock MQTT Broker & Offline Message Queue
MOCKED: Simulates local and wide-area MQTT message telemetry and offline buffering.
Topics:
- dt_07/telemetry
- gridshield/house/{id}/dr_command
- gridshield/bess/dispatch
- gridshield/p2p/settlement
Never calls external network hosts.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional
import json


@dataclass
class MQTTMessage:
    topic: str
    payload: Dict[str, Any]
    timestamp: str
    qos: int = 1
    retained: bool = False
    delivered: bool = True


class MockMQTTBroker:
    """
    In-memory Mock MQTT broker supporting local network disconnects and offline queuing.
    """
    def __init__(self):
        self.comms_online: bool = True
        self.published_history: List[MQTTMessage] = []
        self.offline_queue: List[MQTTMessage] = []
        self.subscriptions: Dict[str, List[Any]] = {}

    def set_comms_state(self, online: bool):
        """Toggles connectivity. If coming back online, flushes offline queue."""
        was_offline = not self.comms_online
        self.comms_online = online
        if online and was_offline:
            self.flush_offline_queue()

    def publish(
        self,
        topic: str,
        payload: Dict[str, Any],
        qos: int = 1,
        retained: bool = False
    ) -> MQTTMessage:
        """
        Publishes a message to a topic. Buffers to offline queue if comms are down.
        """
        now_str = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        delivered = self.comms_online

        msg = MQTTMessage(
            topic=topic,
            payload=payload,
            timestamp=now_str,
            qos=qos,
            retained=retained,
            delivered=delivered
        )

        if delivered:
            self.published_history.append(msg)
        else:
            self.offline_queue.append(msg)

        return msg

    def flush_offline_queue(self) -> List[MQTTMessage]:
        """Flushes all queued messages upon comms reconnection."""
        flushed = []
        while self.offline_queue:
            msg = self.offline_queue.pop(0)
            msg.delivered = True
            self.published_history.append(msg)
            flushed.append(msg)
        return flushed

    def get_messages_for_topic(self, topic_prefix: str) -> List[MQTTMessage]:
        """Returns delivered messages matching topic prefix."""
        return [m for m in self.published_history if m.topic.startswith(topic_prefix)]

    def get_recent_messages(self, limit: int = 20) -> List[MQTTMessage]:
        """Returns the most recent messages."""
        return self.published_history[-limit:]
