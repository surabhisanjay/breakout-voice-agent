from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


class TranscriptLogger:
    def __init__(self, log_dir: str | Path):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.session_id = timestamp
        self.path = self.log_dir / f"session_{timestamp}.json"
        self.records: list[dict[str, Any]] = []
        self._write()

    def log_turn(self, customer_utterance: str, result: dict, memory: dict) -> None:
        record = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "customer_utterance": customer_utterance,
            "agent_response": result.get("response", ""),
            "intent": result.get("intent", ""),
            "collected_context": {
                key: value
                for key, value in memory.items()
                if key != "conversation"
            },
            "route": result.get("route"),
            "handoff_summary": result.get("handoff_summary"),
        }
        self.records.append(record)
        self._write()

    def _write(self) -> None:
        payload = {
            "session_id": self.session_id,
            "started_at": self.session_id,
            "turns": self.records,
        }
        self.path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
