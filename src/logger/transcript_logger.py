"""
TranscriptLogger — structured per-turn JSON session log.

Each session produces one file under logs/conversations/.
Records include timing, routing, and booking data for observability.

Log format (session_YYYYMMDD_HHMMSS.json):
{
  "session_id": "20260618_143022",
  "started_at": "2026-06-18T14:30:22",
  "turns": [
    {
      "turn": 1,
      "timestamp": "2026-06-18T14:30:25",
      "latency_ms": 120,
      "active_agent": "inbound_agent",
      "customer_utterance": "I'd like a corporate event",
      "agent_response": "Absolutely. Corporate events can include...",
      "intent": "corporate_event",
      "next_agent": "qualification_agent",
      "should_handoff": false,
      "missing_fields": ["participants", "location", ...],
      "booking_result": null,
      "collected_context": { ... memory snapshot without conversation ... }
    }
  ]
}
"""
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any


class TranscriptLogger:
    def __init__(self, log_dir: str | Path):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        started = datetime.now()
        self.session_id = started.strftime("%Y%m%d_%H%M%S")
        self.started_at = started.isoformat(timespec="seconds")
        self.path = self.log_dir / f"session_{self.session_id}.json"
        self.records: list[dict[str, Any]] = []
        self._turn_counter = 0
        self._turn_start: float = time.monotonic()
        self._write()

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def start_turn(self) -> None:
        """Call just before processing a customer message to track latency."""
        self._turn_start = time.monotonic()

    def log_turn(
        self,
        customer_utterance: str,
        result: dict,
        memory: dict,
        active_agent: str = "inbound_agent",
    ) -> None:
        """
        Record one completed turn.

        Parameters
        ----------
        customer_utterance : raw transcript or typed message
        result             : AgentResponse.__dict__ or legacy dict
        memory             : ConversationMemory.data snapshot
        active_agent       : which agent handled this turn
        """
        self._turn_counter += 1
        latency_ms = round((time.monotonic() - self._turn_start) * 1000)

        record: dict[str, Any] = {
            "turn": self._turn_counter,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "latency_ms": latency_ms,
            "active_agent": active_agent,
            "customer_utterance": customer_utterance,
            "agent_response": result.get("response", ""),
            "intent": result.get("intent", ""),
            "next_agent": result.get("next_agent", result.get("route", {}).get("next_agent", "")),
            "should_handoff": result.get("should_handoff", result.get("route", {}).get("should_handoff", False)),
            "missing_fields": result.get("missing_fields", []),
            "booking_result": result.get("booking_result"),
            "handoff_summary": result.get("handoff_summary"),
            "sentiment_analysis": result.get("sentiment_analysis", {}),
            "escalation": result.get("escalation", {}),
            "scoring": result.get("scoring", {}),
            "learning_metrics": result.get("learning_metrics", {}),
            "metrics_report": result.get("metrics_report", {}),
            "collected_context": {
                key: value
                for key, value in memory.items()
                if key != "conversation"
            },
        }
        self.records.append(record)
        self._write()

    # ------------------------------------------------------------------ #
    # Internal                                                             #
    # ------------------------------------------------------------------ #

    def _write(self) -> None:
        payload = {
            "session_id": self.session_id,
            "started_at": self.started_at,
            "turns": self.records,
        }
        self.path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
