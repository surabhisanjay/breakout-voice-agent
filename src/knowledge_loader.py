from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class KnowledgeBase:
    faq: str
    games: str
    events: str
    policies: str

    def combined(self) -> str:
        return "\n\n".join(
            [
                "FAQ\n" + self.faq,
                "GAMES\n" + self.games,
                "EVENTS\n" + self.events,
                "POLICIES\n" + self.policies,
            ]
        )


class KnowledgeLoader:
    REQUIRED_FILES = {
        "faq": "faq.txt",
        "games": "games.txt",
        "events": "events.txt",
        "policies": "policies.txt",
    }

    def __init__(self, knowledge_dir: str | Path):
        self.knowledge_dir = Path(knowledge_dir)

    def load(self) -> KnowledgeBase:
        if not self.knowledge_dir.exists():
            raise FileNotFoundError(f"Knowledge directory not found: {self.knowledge_dir}")

        loaded: dict[str, str] = {}
        for key, filename in self.REQUIRED_FILES.items():
            path = self.knowledge_dir / filename
            if not path.exists():
                raise FileNotFoundError(f"Required knowledge file missing: {path}")
            loaded[key] = path.read_text(encoding="utf-8").strip()

        return KnowledgeBase(**loaded)
