from __future__ import annotations

import re
from dataclasses import dataclass

from .knowledge_loader import KnowledgeBase


@dataclass(frozen=True)
class RetrievedSection:
    source: str
    text: str
    score: int


class KnowledgeRetriever:
    def __init__(self, knowledge_base: KnowledgeBase):
        self.sections = self._build_sections(knowledge_base)

    def search(self, query: str, limit: int = 6) -> str:
        terms = self._tokenize(query)
        if not terms:
            return ""

        scored: list[RetrievedSection] = []
        for source, text in self.sections:
            lowered = text.lower()
            score = sum(lowered.count(term) for term in terms)
            if score:
                scored.append(RetrievedSection(source=source, text=text, score=score))

        scored.sort(key=lambda item: item.score, reverse=True)
        return "\n\n".join(
            f"SOURCE: {section.source}\n{section.text}" for section in scored[:limit]
        )

    @staticmethod
    def _build_sections(knowledge_base: KnowledgeBase) -> list[tuple[str, str]]:
        raw_sources = {
            "faq.txt": knowledge_base.faq,
            "games.txt": knowledge_base.games,
            "events.txt": knowledge_base.events,
            "policies.txt": knowledge_base.policies,
        }
        sections: list[tuple[str, str]] = []
        for source, text in raw_sources.items():
            chunks = re.split(r"\n(?=(?:Q\d+\.|Sheet:|Page \d+|[A-Z][A-Za-z ]+\s\|))", text)
            for chunk in chunks:
                cleaned = chunk.strip()
                if cleaned:
                    sections.append((source, cleaned[:1600]))
        return sections

    @staticmethod
    def _tokenize(query: str) -> list[str]:
        stopwords = {
            "the",
            "and",
            "for",
            "with",
            "this",
            "that",
            "have",
            "what",
            "where",
            "which",
            "can",
            "you",
            "your",
            "are",
            "is",
            "do",
            "does",
        }
        return [
            token
            for token in re.findall(r"[a-z0-9]+", query.lower())
            if len(token) > 2 and token not in stopwords
        ]
