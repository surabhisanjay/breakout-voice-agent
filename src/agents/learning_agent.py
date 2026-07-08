from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any


@dataclass
class LearningReport:
    conversations_analyzed: int
    overall_score: int
    agent_scorecards: dict[str, dict[str, Any]] = field(default_factory=dict)
    recurring_issues: list[dict[str, Any]] = field(default_factory=list)
    underperforming_agents: list[dict[str, Any]] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "conversations_analyzed": self.conversations_analyzed,
            "overall_score": self.overall_score,
            "agent_scorecards": self.agent_scorecards,
            "recurring_issues": self.recurring_issues,
            "underperforming_agents": self.underperforming_agents,
            "recommendations": self.recommendations,
        }


class LearningAgent:
    """Post-call quality monitor that learns from repeated deterministic failures.

    It observes completed transcripts only. It never changes prompts, routing, or
    live customer responses automatically; its output is an auditable report for
    engineers and operators.
    """

    ISSUE_PENALTIES = {
        "permission_loop": 14,
        "fake_loading_state": 28,
        "repeated_question": 16,
        "repeated_response": 18,
        "multiple_questions": 7,
        "non_answer": 12,
        "overlong_voice_turn": 6,
        "backend_error": 20,
        "unresolved_frustration": 18,
    }
    RECOMMENDATIONS = {
        "permission_loop": "Make accepted offers actionable: provide options immediately instead of asking permission again.",
        "fake_loading_state": "Remove invented loading/retry language; answer static questions synchronously from grounded data.",
        "repeated_question": "Read conversation memory before asking and suppress questions already answered or just asked.",
        "repeated_response": "Detect when the previous answer did not resolve the turn; repair or advance instead of replaying the same response.",
        "multiple_questions": "Keep voice turns to one next question so callers know what to answer.",
        "non_answer": "Answer the caller's request first, then ask the single missing qualification detail.",
        "overlong_voice_turn": "Shorten spoken responses to the essential answer and one next step.",
        "backend_error": "Add tool failure telemetry and a deterministic recovery path for the affected agent.",
        "unresolved_frustration": "Trigger repair or human escalation when frustration persists after an agent response.",
    }

    def analyze(self, conversations: list[dict[str, Any]]) -> LearningReport:
        issue_counts: Counter[str] = Counter()
        agent_issues: dict[str, Counter[str]] = defaultdict(Counter)
        agent_turns: Counter[str] = Counter()

        for conversation in conversations:
            turns = conversation.get("turns") or conversation.get("transcript") or []
            previous_question = ""
            previous_response = ""
            previous_customer = ""
            for turn in turns:
                if not isinstance(turn, dict):
                    continue
                role = str(turn.get("role") or turn.get("speaker") or turn.get("speaker_type") or "").lower()
                text = str(turn.get("response") or turn.get("text") or turn.get("content") or "").strip()
                if role in {"customer", "user"}:
                    previous_customer = text.lower()
                    continue
                if role not in {"agent", "assistant"} or not text:
                    continue
                agent = str(turn.get("agent") or turn.get("agent_name") or "inbound_agent")
                agent_turns[agent] += 1
                issues = self._turn_issues(text, previous_customer, previous_question)
                if previous_response and self._normalize(text) == self._normalize(previous_response):
                    issues.add("repeated_response")
                for issue in issues:
                    issue_counts[issue] += 1
                    agent_issues[agent][issue] += 1
                questions = self._questions(text)
                if questions:
                    previous_question = questions[-1]
                previous_response = text

            for _error in conversation.get("backend_errors") or []:
                agent = str(conversation.get("error_agent") or "booking_agent")
                issue_counts["backend_error"] += 1
                agent_issues[agent]["backend_error"] += 1
            sentiment = conversation.get("sentiment") or conversation.get("memory", {}).get("sentiment_analysis") or {}
            if str(sentiment.get("current_sentiment") or sentiment.get("sentiment") or "").lower() in {"frustrated", "angry"}:
                if not (conversation.get("escalation") or {}).get("escalate"):
                    issue_counts["unresolved_frustration"] += 1
                    agent_issues["inbound_agent"]["unresolved_frustration"] += 1

        scorecards: dict[str, dict[str, Any]] = {}
        for agent in sorted(set(agent_turns) | set(agent_issues)):
            turns = agent_turns[agent]
            penalty = sum(self.ISSUE_PENALTIES[name] * count for name, count in agent_issues[agent].items())
            # Express issue density per ten spoken turns. A failure repeated
            # three times in a short call must not disappear inside a large
            # aggregate turn count.
            normalized_penalty = round((penalty * 10) / max(1, turns))
            score = max(0, 100 - normalized_penalty)
            scorecards[agent] = {
                "score": score,
                "status": "underperforming" if score < 80 else "watch" if score < 90 else "healthy",
                "agent_turns": turns,
                "issues": dict(agent_issues[agent].most_common()),
            }

        underperforming = [
            {"agent": agent, **card}
            for agent, card in scorecards.items()
            if card["score"] < 80
        ]
        underperforming.sort(key=lambda item: item["score"])
        recurring = [
            {"issue": issue, "count": count, "severity": self.ISSUE_PENALTIES[issue]}
            for issue, count in issue_counts.most_common()
        ]
        recommendations = [self.RECOMMENDATIONS[item["issue"]] for item in recurring]
        overall = round(sum(card["score"] for card in scorecards.values()) / len(scorecards)) if scorecards else 100
        return LearningReport(len(conversations), overall, scorecards, recurring, underperforming, recommendations)

    def _turn_issues(self, response: str, customer: str, previous_question: str) -> set[str]:
        lowered = response.lower()
        issues: set[str] = set()
        if re.search(r"would you like (?:me to|to hear|to see)|brief overview|full list", lowered):
            if re.search(r"\b(?:yes|yeah|okay|ok|sure|tell me|go ahead|all rooms)\b", customer):
                issues.add("permission_loop")
        if re.search(r"\b(?:loading|please hold|hold on|try again|still fetching)\b", lowered):
            issues.add("fake_loading_state")
        questions = self._questions(response)
        if len(questions) > 1:
            issues.add("multiple_questions")
        if previous_question and any(self._normalize(question) == self._normalize(previous_question) for question in questions):
            issues.add("repeated_question")
        if len(response.split()) > 65:
            issues.add("overlong_voice_turn")
        if re.search(r"\b(?:tell me|all rooms|what rooms|what options)\b", customer) and re.search(
            r"\b(?:just let me know|something specific|help you with something else)\b", lowered
        ):
            issues.add("non_answer")
        if re.search(r"\b(?:want to book|make a booking|want book|need to book)\b", customer) and re.search(
            r"i don't have that detail to hand|someone can call you back", lowered
        ):
            issues.add("non_answer")
        return issues

    @staticmethod
    def _questions(text: str) -> list[str]:
        return [part.strip() + "?" for part in text.split("?")[:-1] if part.strip()]

    @staticmethod
    def _normalize(text: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()
