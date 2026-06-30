"""
AgentResponse — standardised return type for all Breakout agents.

Every agent's `handle_message()` returns an `AgentResponse`.
This design is LangGraph-ready: each field maps directly to a LangGraph
node output key, so migrating to a graph-based orchestrator requires
no interface changes — only wiring.

LangGraph mapping
-----------------
    AgentResponse.state      →  graph State dict (passed between nodes)
    AgentResponse.next_agent →  graph conditional edge target
    AgentResponse.response   →  spoken output (voice layer)
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class AgentResponse:
    # ------------------------------------------------------------------ #
    # Customer-facing output                                               #
    # ------------------------------------------------------------------ #
    response: str
    """The spoken/text response delivered to the customer."""

    # ------------------------------------------------------------------ #
    # Routing                                                              #
    # ------------------------------------------------------------------ #
    intent: str
    """Detected intent for this turn, e.g. 'corporate_event'."""

    next_agent: str
    """
    Agent that should handle the next turn.
    Values: 'inbound_agent' | 'qualification_agent' | 'booking_agent'
    """

    should_handoff: bool
    """True when all qualification fields are captured and the Router has
    decided to transfer ownership to the next specialist agent."""

    # ------------------------------------------------------------------ #
    # Shared state (LangGraph node output)                                 #
    # ------------------------------------------------------------------ #
    state: dict = field(default_factory=dict)
    """
    Full snapshot of ConversationMemory.data at the end of this turn.
    Passed verbatim between agents / LangGraph nodes.
    """

    # ------------------------------------------------------------------ #
    # Supplementary metadata (consumed by logging / debug layer)          #
    # ------------------------------------------------------------------ #
    intent_confidence: float = 0.0
    missing_fields: list[str] = field(default_factory=list)
    recommendation: dict = field(default_factory=dict)
    qualification: dict = field(default_factory=dict)
    handoff_summary: dict | None = None
    booking_result: dict | None = None
    """Set by BookingAgent once a booking is confirmed."""
    booking: dict | None = None
    """Compatibility field with surabhi/main containing booking result."""
    sentiment_analysis: dict = field(default_factory=dict)
    """Current customer sentiment metadata; never spoken to the customer."""
    escalation: dict = field(default_factory=dict)
    """Escalation recommendation metadata; routing remains deterministic."""
    scoring: dict = field(default_factory=dict)
    """Numeric turn/session scoring for analytics; never spoken to the customer."""
    learning_metrics: dict = field(default_factory=dict)
    """Aggregated learning metrics accumulated across the session."""
    metrics_report: dict = field(default_factory=dict)
    """Detailed report snapshot for dashboards and API consumers."""
    debug: dict = field(default_factory=dict)
    """Compatibility field with surabhi/main containing state snapshot and entities."""

    # ------------------------------------------------------------------ #
    # Backward-compatible dict-style access                                #
    #                                                                      #
    # Existing tests and callers use result["response"] etc.               #
    # These dunder methods preserve that interface so no test changes are  #
    # needed when migrating from a plain dict return type.                 #
    # ------------------------------------------------------------------ #

    def _as_dict(self) -> dict:
        """Flat dict view that also includes the legacy 'route' key."""
        return {
            "response": self.response,
            "intent": self.intent,
            "intent_confidence": self.intent_confidence,
            "next_agent": self.next_agent,
            "should_handoff": self.should_handoff,
            "missing_fields": self.missing_fields,
            "recommendation": self.recommendation,
            "qualification": self.qualification,
            "handoff_summary": self.handoff_summary,
            "booking_result": self.booking_result,
            "booking": self.booking,
            "sentiment_analysis": self.sentiment_analysis,
            "escalation": self.escalation,
            "scoring": self.scoring,
            "learning_metrics": self.learning_metrics,
            "metrics_report": self.metrics_report,
            "debug": self.debug,
            "state": self.state,
            # Legacy key used by some older test assertions
            "route": {
                "next_agent": self.next_agent,
                "should_handoff": self.should_handoff,
                "reason": "",
            },
        }

    def __getitem__(self, key: str):
        return self._as_dict()[key]

    def __contains__(self, key: str) -> bool:
        return key in self._as_dict()

    def get(self, key: str, default=None):
        return self._as_dict().get(key, default)
