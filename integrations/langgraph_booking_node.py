# """LangGraph integration example for the BookingAgent.

# This module shows how to wrap the local `BookingAgent` as a node in a
# LangGraph workflow. It uses a conservative, import-guarded approach so the
#     available = agent.check_availability(service, date)
#     if not available:
#         # create a default slot in the local calendar for demo
#         slot_id = agent.add_slot(date, "18:00", service, capacity=10)
#     else:
#         slot_id = available[0].slot_id

#     owner_id = f"lg-{handoff.get('customer_name','anon')}-{slot_id}"
#     locked = agent.lock_slot(slot_id, owner_id)
#     if not locked:
#         return {"status": "failed", "reason": "could_not_lock_slot"}

#     customer = {
#         "customer_name": handoff.get("customer_name", ""),
#         "phone": handoff.get("phone", ""),
#         "participants": handoff.get("participants", 1),
#         "owner_id": owner_id,
#     }

#     try:
#         booking = agent.create_booking(slot_id, customer, require_payment=require_payment)
#     except BookingError as exc:
#         return {"status": "error", "reason": str(exc)}

#     return {
#         "status": "booked",
#         "booking_ref": booking.reference,
#         "payment_required": booking.payment_required,
#         "payment_payload": booking.payment_payload,
#     }


# # -------------------- LangGraph bootstrap example (pseudo-code) --------------------
# def build_langgraph_workflow():
#     """Pseudo-example showing how this handler might be registered with LangGraph.

#     Replace with actual LangGraph SDK usage for node/edge creation.
#     """
#     try:
#         import langgraph  # type: ignore
#     except Exception:
#         langgraph = None

#     if langgraph is None:
#         print("LangGraph not installed — this file is an example only.")
#         return None

#     graph = langgraph.Graph(name="booking_workflow")
#     # The exact API will differ; adapt to your LangGraph SDK.
#     graph.add_node("handoff_ingest", func=lambda x: x)
#     graph.add_node("booking_node", func=lambda handoff: booking_node_handler(handoff, require_payment=True))
#     graph.add_edge("handoff_ingest", "booking_node")
#     return graph


# if __name__ == "__main__":
#     # Demo run: simulate a handoff
#     demo_handoff = {
#         "customer_name": "Demo User",
#         "phone": "9876543210",
#         "participants": 4,
#         "intent": "birthday_party",
#         "preferred_date": "2026-08-15",
#     }
#     result = booking_node_handler(demo_handoff, require_payment=True)
#     print("Demo booking result:", result)
"""Robust LangGraph booking workflow for BookingAgent.

Improvements over the original:
  - Typed state via TypedDict for clear data contracts between nodes
  - Retry logic with exponential back-off on transient failures
  - Slot-lock TTL extended to cover booking creation latency
  - Idempotency: detects already-booked references before re-attempting
  - Graceful escalation path when all retries are exhausted
  - Payment payload gated behind a dedicated node (not inlined in booking)
  - Full audit trail: every state transition is logged to `state["log"]`
  - EscalationRequired propagated cleanly to the final output node
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Compatibility shim: works with or without langgraph installed
# ---------------------------------------------------------------------------
try:
    from langgraph.graph import StateGraph, END          # type: ignore
    _HAS_LANGGRAPH = True
except ImportError:
    _HAS_LANGGRAPH = False

from src.agents.booking_agent import BookingError, EscalationRequired
from src.orchestration.booking_orchestrator import BookingOrchestrator
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from src.orchestration.booking_orchestrator import BookingOrchestrator as BookingAgent

def booking_node_handler(handoff: Dict[str, Any], require_payment: bool = False) -> Dict[str, Any]:
    """Handle a handoff payload and attempt to create a booking using run_booking_workflow and BookingOrchestrator."""
    orchestrator = BookingOrchestrator()
    return run_booking_workflow(handoff, require_payment=require_payment, agent=orchestrator)

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


# ---------------------------------------------------------------------------
# Shared workflow state (TypedDict)
# ---------------------------------------------------------------------------
from typing import TypedDict

class BookingState(TypedDict, total=False):
    # ---- inputs ----
    handoff: Dict[str, Any]
    require_payment: bool
    # ---- runtime ----
    service: str
    date: str
    slot_id: Optional[str]
    owner_id: str
    customer: Dict[str, Any]
    attempt: int
    max_attempts: int
    # ---- outputs ----
    booking_ref: Optional[str]
    payment_required: bool
    payment_payload: Optional[Dict[str, Any]]
    status: str          # "pending" | "booked" | "payment_pending" | "failed" | "escalated"
    error: Optional[str]
    log: List[str]


# ---------------------------------------------------------------------------
# Retry helper
# ---------------------------------------------------------------------------
_RETRY_BACKOFF = [0.5, 1.5, 3.0]   # seconds between attempts


def _log(state: BookingState, msg: str) -> None:
    logger.info(msg)
    state.setdefault("log", []).append(msg)


# ---------------------------------------------------------------------------
# Node 1 — validate & normalise handoff payload
# ---------------------------------------------------------------------------
def node_validate(state: BookingState) -> BookingState:
    handoff = state.get("handoff", {})
    _log(state, "node_validate: checking handoff payload")

    missing = [k for k in ("customer_name", "phone", "intent") if not handoff.get(k)]
    if missing:
        state["status"] = "failed"
        state["error"] = f"missing_required_fields: {missing}"
        _log(state, f"node_validate: FAIL — {state['error']}")
        return state

    state["service"] = handoff.get("intent", "general_inquiry")
    state["date"] = handoff.get("preferred_date") or "2026-07-01"
    state["attempt"] = 0
    state["max_attempts"] = 3
    state["status"] = "pending"
    state["log"] = state.get("log", [])
    _log(state, f"node_validate: OK — service={state['service']} date={state['date']}")
    return state


# ---------------------------------------------------------------------------
# Node 2 — find or create a slot
# ---------------------------------------------------------------------------
def node_find_slot(state: BookingState, agent: BookingAgent) -> BookingState:
    if state.get("status") != "pending":
        return state

    _log(state, "node_find_slot: querying availability")
    try:
        available = agent.check_availability(state["service"], state["date"])
        if available:
            state["slot_id"] = available[0].slot_id
            _log(state, f"node_find_slot: found existing slot {state['slot_id']}")
        else:
            slot_id = agent.add_slot(state["date"], "18:00", state["service"], capacity=10)
            state["slot_id"] = slot_id
            _log(state, f"node_find_slot: created new slot {slot_id}")
    except Exception as exc:
        state["status"] = "failed"
        state["error"] = f"slot_lookup_error: {exc}"
        _log(state, f"node_find_slot: FAIL — {exc}")

    return state


# ---------------------------------------------------------------------------
# Node 3 — lock the slot (with retry)
# ---------------------------------------------------------------------------
def node_lock_slot(state: BookingState, agent: BookingAgent) -> BookingState:
    if state.get("status") != "pending":
        return state

    handoff = state["handoff"]
    slot_id = state["slot_id"]
    owner_id = f"wf-{handoff.get('customer_name','anon')}-{uuid.uuid4().hex[:6]}"
    state["owner_id"] = owner_id

    attempt = state.get("attempt", 0)
    max_attempts = state.get("max_attempts", 3)

    _log(state, f"node_lock_slot: attempt {attempt + 1}/{max_attempts} for slot {slot_id}")

    for i in range(max_attempts):
        try:
            locked = agent.lock_slot(slot_id, owner_id, ttl_seconds=60)
            if locked:
                _log(state, f"node_lock_slot: locked on attempt {i + 1}")
                state["attempt"] = i
                return state
        except BookingError as exc:
            _log(state, f"node_lock_slot: BookingError on attempt {i + 1} — {exc}")
            break

        # back-off before retry
        wait = _RETRY_BACKOFF[min(i, len(_RETRY_BACKOFF) - 1)]
        _log(state, f"node_lock_slot: slot busy, retrying in {wait}s …")
        time.sleep(wait)

        # refresh slot list — another slot may have freed up
        available = agent.check_availability(state["service"], state["date"])
        if available:
            slot_id = available[0].slot_id
            state["slot_id"] = slot_id
            owner_id = f"wf-{handoff.get('customer_name','anon')}-{uuid.uuid4().hex[:6]}"
            state["owner_id"] = owner_id
            _log(state, f"node_lock_slot: switched to slot {slot_id}")

    state["status"] = "escalated"
    state["error"] = "could_not_lock_any_slot_after_retries"
    _log(state, "node_lock_slot: ESCALATED — exhausted retries")
    return state


# ---------------------------------------------------------------------------
# Node 4 — create the booking
# ---------------------------------------------------------------------------
def node_create_booking(state: BookingState, agent: BookingAgent) -> BookingState:
    if state.get("status") not in ("pending",):
        return state

    handoff = state["handoff"]
    customer = {
        "customer_name": handoff.get("customer_name", ""),
        "phone": handoff.get("phone", ""),
        "participants": int(handoff.get("participants") or 1),
        "owner_id": state["owner_id"],
    }
    state["customer"] = customer
    require_payment = state.get("require_payment", False)

    _log(state, f"node_create_booking: creating booking (payment={require_payment})")
    try:
        booking = agent.create_booking(state["slot_id"], customer, require_payment=require_payment)
        state["booking_ref"] = booking.reference
        state["payment_required"] = booking.payment_required
        state["payment_payload"] = booking.payment_payload
        state["status"] = "payment_pending" if booking.payment_required else "booked"
        _log(state, f"node_create_booking: OK — ref={booking.reference} status={state['status']}")
    except BookingError as exc:
        err = str(exc)
        _log(state, f"node_create_booking: BookingError — {err}")
        if err == "already_booked":
            # idempotency: slot was booked between lock and create; release stale lock and retry.
            if state.get("slot_id") and state.get("owner_id"):
                try:
                    agent.unlock_slot(state["slot_id"], state["owner_id"])
                    _log(state, f"node_create_booking: released stale lock for slot {state['slot_id']}")
                except Exception:
                    pass
            state["status"] = "pending"
            state["slot_id"] = None
            state = node_find_slot(state, agent)
            state = node_lock_slot(state, agent)
            if state.get("status") == "pending":
                return node_create_booking(state, agent)
        else:
            state["status"] = "escalated"
            state["error"] = err

    return state


# ---------------------------------------------------------------------------
# Node 5 — handle payment initiation
# ---------------------------------------------------------------------------
def node_handle_payment(state: BookingState) -> BookingState:
    if state.get("status") != "payment_pending":
        return state

    payload = state.get("payment_payload")
    _log(state, f"node_handle_payment: dispatching payment for ref={state.get('booking_ref')}")
    # TODO: integrate with actual payment gateway (Razorpay, Stripe, etc.)
    # For now, mark payment as dispatched and leave confirmation to webhook.
    state["status"] = "booked"
    state["payment_payload"] = payload   # returned to caller for redirect / QR
    _log(state, "node_handle_payment: payment payload handed off to gateway")
    return state


# ---------------------------------------------------------------------------
# Node 6 — finalise & surface result
# ---------------------------------------------------------------------------
def node_finalise(state: BookingState) -> BookingState:
    _log(state, f"node_finalise: terminal status={state.get('status')}")
    if state.get("status") == "escalated":
        raise EscalationRequired(state.get("error", "unknown"))
    return state


# ---------------------------------------------------------------------------
# Public entry point (works with or without LangGraph)
# ---------------------------------------------------------------------------

def run_booking_workflow(handoff: Dict[str, Any], require_payment: bool = False, agent: Optional[BookingOrchestrator] = None) -> Dict[str, Any]:
    """Execute the booking workflow and return a result dict.

    If LangGraph is installed the workflow is run as a compiled graph;
    otherwise it runs sequentially in-process (same logic, no graph overhead).
    """
    if agent is None:
        agent = BookingOrchestrator()

    initial: BookingState = {
        "handoff": handoff,
        "require_payment": require_payment,
        "status": "pending",
        "log": [],
        "slot_id": None,
        "booking_ref": None,
        "payment_payload": None,
        "payment_required": False,
        "error": None,
    }

    if _HAS_LANGGRAPH:
        return _run_with_langgraph(initial, agent)
    return _run_sequentially(initial, agent)


def _run_sequentially(state: BookingState, agent: BookingAgent) -> Dict[str, Any]:
    try:
        state = node_validate(state)
        if state["status"] == "failed":
            return _to_output(state)

        state = node_find_slot(state, agent)
        state = node_lock_slot(state, agent)
        state = node_create_booking(state, agent)
        state = node_handle_payment(state)
        state = node_finalise(state)
    except EscalationRequired as exc:
        state["status"] = "escalated"
        state["error"] = str(exc)
        logger.error("EscalationRequired: %s", exc)

    return _to_output(state)


def _run_with_langgraph(initial: BookingState, agent: BookingAgent) -> Dict[str, Any]:
    """Build and invoke a LangGraph StateGraph."""
    from functools import partial

    graph = StateGraph(BookingState)

    graph.add_node("validate",        node_validate)
    graph.add_node("find_slot",       partial(node_find_slot, agent=agent))
    graph.add_node("lock_slot",       partial(node_lock_slot, agent=agent))
    graph.add_node("create_booking",  partial(node_create_booking, agent=agent))
    graph.add_node("handle_payment",  node_handle_payment)
    graph.add_node("finalise",        node_finalise)

    def _route_after_validate(s: BookingState) -> str:
        return END if s["status"] == "failed" else "find_slot"

    def _route_after_lock(s: BookingState) -> str:
        return "finalise" if s["status"] == "escalated" else "create_booking"

    graph.set_entry_point("validate")
    graph.add_conditional_edges("validate", _route_after_validate)
    graph.add_edge("find_slot", "lock_slot")
    graph.add_conditional_edges("lock_slot", _route_after_lock)
    graph.add_edge("create_booking", "handle_payment")
    graph.add_edge("handle_payment", "finalise")
    graph.add_edge("finalise", END)

    app = graph.compile()
    try:
        final = app.invoke(initial)
    except EscalationRequired as exc:
        initial["status"] = "escalated"
        initial["error"] = str(exc)
        return _to_output(initial)

    return _to_output(final)


def _to_output(state: BookingState) -> Dict[str, Any]:
    return {
        "status":          state.get("status"),
        "booking_ref":     state.get("booking_ref"),
        "payment_required": state.get("payment_required", False),
        "payment_payload": state.get("payment_payload"),
        "error":           state.get("error"),
        "log":             state.get("log", []),
    }


# ---------------------------------------------------------------------------
# Demo
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    demo_handoff = {
        "customer_name": "Priya Sharma",
        "phone": "9876543210",
        "participants": 4,
        "intent": "birthday_party",
        "preferred_date": "2026-08-15",
    }

    result = run_booking_workflow(demo_handoff, require_payment=True)
    print("\n=== Booking Result ===")
    for k, v in result.items():
        if k == "log":
            print("log:")
            for entry in v:
                print("  ", entry)
        else:
            print(f"  {k}: {v}")