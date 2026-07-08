from __future__ import annotations

import sys
import base64
import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

import app as api_app  # noqa: E402
from src.core.agent_response import AgentResponse  # noqa: E402
from src.memory.conversation_memory import ConversationMemory  # noqa: E402


def make_client(tmp_path: Path, monkeypatch) -> TestClient:
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("BREAKOUT_GPT_REASONER", "false")
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.setenv("BOOKING_PROVIDER", "simulator")
    monkeypatch.setattr(api_app, "API_MEMORY_DIR", tmp_path / "api_sessions")
    api_app._sessions.clear()
    return TestClient(api_app.app)


def _token(role: str = "sales_manager", agent_id: int = 3, org_id: str = "org_test") -> str:
    header = {"alg": "none", "typ": "JWT"}
    payload = {"org_id": org_id, "role": role, "sub": agent_id, "agent_id": agent_id, "name": f"User {agent_id}"}

    def enc(value: dict[str, Any]) -> str:
        raw = json.dumps(value, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    return f"{enc(header)}.{enc(payload)}.sig"


def _headers(role: str = "sales_manager", agent_id: int = 3) -> dict[str, str]:
    return {"Authorization": f"Bearer {_token(role, agent_id)}"}


def test_web_chat_ui_is_served_without_frontend_framework(tmp_path: Path, monkeypatch) -> None:
    client = make_client(tmp_path, monkeypatch)

    page = client.get("/web-chat")
    script = client.get("/web-chat/assets/app.js")
    styles = client.get("/web-chat/assets/styles.css")

    assert page.status_code == 200
    assert script.status_code == 200
    assert styles.status_code == 200
    assert "Booking Assistant" in page.text
    assert 'fetch("/chat"' in script.text
    assert "Murder Mystery" not in script.text
    assert "Hostage" not in script.text
    assert "search_available_seats" not in script.text
    assert "create_booking" not in script.text


def test_chat_response_exposes_backend_media_booking_and_payment_metadata(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client = make_client(tmp_path, monkeypatch)

    def fake_dispatch(message, inbound, booking, active_agent):
        result = AgentResponse(
            response="Here are the details from the backend.",
            intent="escape_room_inquiry",
            next_agent="booking_agent",
            should_handoff=False,
            state={
                "media": [{"type": "image", "url": "https://cdn.example/room.jpg", "title": "Room"}],
                "booking_id": "bk_123",
                "booking_ref": "or_123",
                "bookingStatus": "RESERVED",
                "room": "Backend Room",
                "location": "Whitefield",
                "preferred_date": "Tomorrow",
                "selected_slot": "7:00 PM",
                "paymentStatus": "UNPAID",
                "paymentUrl": "https://pay.example/order?pr=true",
                "paymentDeadline": "2026-07-01T15:30:00+00:00",
                "price_breakdown": {
                    "base_price": 2800,
                    "discount": 280,
                    "discount_applied": True,
                    "discount_percent": 10.0,
                    "final_price": 2520,
                    "currency": "INR",
                    "source": "kreeda_payment_status",
                },
            },
        )
        return result, booking, active_agent

    monkeypatch.setattr(api_app, "dispatch", fake_dispatch)

    response = client.post("/chat", json={"session_id": "media-contract", "message": "Show me that room"})

    assert response.status_code == 200
    body = response.json()
    assert body["media"] == [{"type": "image", "url": "https://cdn.example/room.jpg", "title": "Room", "thumbnail_url": "", "mime_type": ""}]
    assert body["booking"]["booking_id"] == "bk_123"
    assert body["booking"]["status"] == "RESERVED"
    assert body["payment"]["payment_url"] == "https://pay.example/order?pr=true"
    assert body["payment"]["status"] == "UNPAID"
    assert body["price_breakdown"]["final_price"] == 2520
    assert body["booking"]["price_breakdown"] == body["price_breakdown"]
    assert body["conversation_summary"]["conversation_id"] == "media-contract"
    assert body["evaluation"]["score"] > 0
    assert body["metrics"]["turn_count"] >= 0
    assert body["csat"]["score"] > 0
    assert body["agent_score"] == body["evaluation"]["score"]
    assert body["sentiment_graph"]
    assert "observed_topics" in body["learning"]
    assert "recommendations" in body["follow_up"]
    assert isinstance(body["conversation_history"], list)


def test_price_breakdown_is_numeric_for_discounted_and_undiscounted_totals() -> None:
    from src.services.price_breakdown import price_breakdown_from_totals

    discounted = price_breakdown_from_totals(
        {"subtotal": 2800, "total": 2520, "currency": "INR"}
    )
    undiscounted = price_breakdown_from_totals(
        {"subtotal": 1600, "total": 1600, "currency": "INR"}
    )

    assert discounted == {
        "base_price": 2800,
        "discount": 280,
        "discount_applied": True,
        "discount_percent": 10.0,
        "final_price": 2520,
        "currency": "INR",
        "source": "kreeda_payment_status",
    }
    assert undiscounted["base_price"] == 1600
    assert undiscounted["discount"] == 0
    assert undiscounted["discount_applied"] is False
    assert undiscounted["discount_percent"] == 0.0
    assert undiscounted["final_price"] == 1600


def test_web_chat_transport_runs_50_conversations_through_dispatch(tmp_path: Path, monkeypatch) -> None:
    client = make_client(tmp_path, monkeypatch)
    conversations = [
        ["Hi", "We are a couple", "First time", "Whitefield"],
        ["Book Murder Mystery", "Tomorrow", "Any time works"],
        ["What do you recommend?", "Parking?", "Continue booking"],
        ["Six adults", "Actually four", "Koramangala", "Something difficult"],
        ["Birthday booking", "Kids and adults", "Food options?"],
        ["Corporate outing", "30 people", "Need food", "Human please"],
        ["I did not receive payment link", "My booking is not done yet"],
        ["Show me Murder Mystery", "Compare rooms", "Any other option?"],
        ["Tomorrow evening", "Around 7", "What slots do you have?"],
        ["This is frustrating", "I want a real person"],
    ]

    total = 0
    for index in range(50):
        session_id = f"webchat-{index}"
        turns = conversations[index % len(conversations)]
        for turn in turns:
            response = client.post("/chat", json={"session_id": session_id, "message": turn})
            total += 1
            assert response.status_code == 200
            body = response.json()
            assert isinstance(body["response"], str)
            assert body["response"].strip()
            assert "media" in body
            assert "booking" in body
            assert "payment" in body
        memory = client.get(f"/memory/{session_id}").json()["memory"]
        assert len(memory["conversation"]) >= len(turns) * 2

    assert total >= 50


def test_closiro_contract_reads_real_web_chat_session_data(tmp_path: Path, monkeypatch) -> None:
    client = make_client(tmp_path, monkeypatch)

    def fake_dispatch(message, inbound, booking, active_agent):
        inbound.memory.data.update(
            {
                "customer_name": "Siddharth Khandelwal",
                "first_name": "Siddharth",
                "last_name": "Khandelwal",
                "phone": "9982151357",
                "intent": "escape_room_inquiry",
                "sentiment": "frustrated",
                "booking_id": "bk_real_session",
                "booking_ref": "or_real_session",
                "bookingStatus": "RESERVED",
                "paymentStatus": "UNPAID",
                "paymentUrl": "https://pay.example/order?pr=true",
                "room": "Murder Mystery",
                "location": "Whitefield",
                "preferred_date": "Tomorrow",
                "selected_slot": "7:00 PM",
            }
        )
        inbound.memory.add_turn("customer", message)
        inbound.memory.add_turn("agent", "I will connect you to a human.")
        result = AgentResponse(
            response="I will connect you to a human.",
            intent="escape_room_inquiry",
            next_agent="escalation_agent",
            should_handoff=True,
            state=dict(inbound.memory.data),
            handoff_summary={
                "summary": "Customer requested human after payment confusion.",
                "action_items": ["Call Siddharth", "Verify payment link"],
            },
            escalation={"escalate": True, "reason": "Customer requested human"},
            ai_summary={
                "summary": "Customer has a reserved booking and requested a human.",
                "action_items": ["Call Siddharth"],
            },
            customer_profile={"customer_name": "Siddharth Khandelwal", "phone": "9982151357"},
            timeline_events=[{"event": "Human handoff requested", "time": "00:24"}],
            transcript=[
                {"sequence": 1, "speaker_type": "customer", "text": message, "spoken_at_second": 1.0},
                {"sequence": 2, "speaker_type": "agent", "text": "I will connect you to a human.", "spoken_at_second": 2.0},
            ],
        )
        return result, booking, "escalation_agent"

    monkeypatch.setattr(api_app, "dispatch", fake_dispatch)

    chat = client.post("/chat", json={"session_id": "closiro-live", "message": "I want a human"})
    assert chat.status_code == 200

    calls = client.get("/api/v1/calls?session_id=closiro-live", headers=_headers()).json()["items"]
    call = next(item for item in calls if item.get("session_id") == "closiro-live")
    summary = client.get(f"/api/v1/calls/{call['id']}/summary", headers=_headers()).json()
    transcript = client.get(f"/api/v1/calls/{call['id']}/transcript", headers=_headers()).json()
    activity = client.get(f"/api/v1/contacts/{call['contact_id']}/activity", headers=_headers()).json()
    escalations = client.get("/api/v1/escalations?session_id=closiro-live", headers=_headers()).json()["items"]
    escalation = next(item for item in escalations if item.get("session_id") == "closiro-live")

    assert summary["summary"]["summary"] == "Customer has a reserved booking and requested a human."
    assert transcript["lines"][0]["text"] == "I want a human"
    assert any(item["summary"] == "Customer has a reserved booking and requested a human." for item in activity["items"])
    assert escalation["handoff_summary"]["summary"] == "Customer requested human after payment confusion."
    assert escalation["action_items"] == ["Call Siddharth"]


def test_first_time_answer_is_not_reasked_after_location(tmp_path: Path, monkeypatch) -> None:
    client = make_client(tmp_path, monkeypatch)
    session_id = "state-loop-literal"

    first = client.post(
        "/chat",
        json={"session_id": session_id, "message": "Hi, I want to book an escape room for 2 people."},
    ).json()
    assert "done an escape room" in first["response"].lower()

    second = client.post(
        "/chat",
        json={"session_id": session_id, "message": "No, first time."},
    ).json()
    assert "murder mystery" in second["response"].lower() or "beginner" in second["response"].lower()

    third = client.post(
        "/chat",
        json={"session_id": session_id, "message": "Whitefield."},
    ).json()
    memory = client.get(f"/memory/{session_id}").json()["memory"]

    assert memory["experience_level"] == "beginner"
    assert memory["location"] == "Whitefield"
    assert "done an escape room" not in third["response"].lower()
    assert "first one" not in third["response"].lower()


def test_web_chat_asr_first_one_shorthand_does_not_loop(tmp_path: Path, monkeypatch) -> None:
    client = make_client(tmp_path, monkeypatch)
    session_id = "state-loop-asr"

    first = client.post("/chat", json={"session_id": session_id, "message": "I want to book for 2 people"}).json()
    second = client.post("/chat", json={"session_id": session_id, "message": "no first one"}).json()
    third = client.post("/chat", json={"session_id": session_id, "message": "whitefield"}).json()
    fourth = client.post("/chat", json={"session_id": session_id, "message": "first one"}).json()
    memory = client.get(f"/memory/{session_id}").json()["memory"]

    assert "done an escape room" in first["response"].lower()
    assert memory["experience_level"] == "beginner"
    assert memory["location"] == "Whitefield"
    assert "done an escape room" not in third["response"].lower()
    assert "done an escape room" not in fourth["response"].lower()
    assert second["response"] != third["response"]


def test_recommendation_explanation_answers_before_qualification(tmp_path: Path, monkeypatch) -> None:
    client = make_client(tmp_path, monkeypatch)
    session_id = "recommendation-explanation"

    first = client.post(
        "/chat",
        json={
            "session_id": session_id,
            "message": "Hi, my friends and I are planning to try an escape room for the first time.",
        },
    ).json()
    second = client.post(
        "/chat",
        json={"session_id": session_id, "message": "Why do you recommend that room?"},
    ).json()

    assert "murder mystery" in first["response"].lower()
    text = second["response"].lower()
    assert "recommend murder mystery" in text
    assert "beginner" in text
    assert "which branch" in text


def test_faq_before_qualification_answers_then_resumes(tmp_path: Path, monkeypatch) -> None:
    client = make_client(tmp_path, monkeypatch)
    session_id = "faq-before-qualification"

    client.post("/chat", json={"session_id": session_id, "message": "I want to book for six people"}).json()
    food = client.post("/chat", json={"session_id": session_id, "message": "What food options are there?"}).json()
    duration = client.post("/chat", json={"session_id": session_id, "message": "How long does a game last?"}).json()
    memory = client.get(f"/memory/{session_id}").json()["memory"]

    assert "food options" in food["response"].lower()
    assert "which" in food["response"].lower() or "branch" in food["response"].lower()
    assert "50 minutes" in duration["response"].lower()
    assert memory["participants"] == 6


def test_recommendation_without_location_uses_global_recommendation(tmp_path: Path, monkeypatch) -> None:
    client = make_client(tmp_path, monkeypatch)

    response = client.post(
        "/chat",
        json={"session_id": "global-rec", "message": "What room do you recommend for first timers?"},
    ).json()
    memory = client.get("/memory/global-rec").json()["memory"]

    text = response["response"].lower()
    assert "across all branches" in text
    assert "murder mystery" in text
    assert "once i know your preferred branch" in text
    assert memory["recommended_option"] == "Murder Mystery"


def test_booking_agent_offers_recommendation_instead_of_room_loop(tmp_path: Path, monkeypatch) -> None:
    client = make_client(tmp_path, monkeypatch)
    session_id = "booking-no-room-loop"

    client.post("/chat", json={"session_id": session_id, "message": "I want to book tomorrow."}).json()
    client.post("/chat", json={"session_id": session_id, "message": "Whitefield"}).json()
    participant = client.post("/chat", json={"session_id": session_id, "message": "There are 4 of us."}).json()
    book = client.post("/chat", json={"session_id": session_id, "message": "Okay, let's book."}).json()

    assert (participant["escalation"] or {}).get("escalate") is False
    assert (book["escalation"] or {}).get("escalate") is False
    assert "which escape room would you like to book" not in book["response"].lower()
    assert "recommend" in book["response"].lower()
    assert "murder mystery" in book["response"].lower()


def test_time_preference_period_persists_in_booking_flow(tmp_path: Path, monkeypatch) -> None:
    client = make_client(tmp_path, monkeypatch)
    session_id = "time-period-persistence"

    client.post(
        "/chat",
        json={"session_id": session_id, "message": "Book Murder Mystery at Whitefield tomorrow for 4 people."},
    ).json()
    response = client.post("/chat", json={"session_id": session_id, "message": "Evening works."}).json()
    memory = client.get(f"/memory/{session_id}").json()["memory"]

    assert (response["escalation"] or {}).get("escalate") is False
    assert memory["preferred_period"] == "evening"
    assert memory["time_preference"] == "period"


def test_participant_count_with_evening_is_not_parsed_as_time(tmp_path: Path, monkeypatch) -> None:
    client = make_client(tmp_path, monkeypatch)

    response = client.post(
        "/chat",
        json={
            "session_id": "participant-not-time",
            "message": "Book Murder Mystery for two adults at Whitefield tomorrow evening.",
        },
    ).json()
    memory = client.get("/memory/participant-not-time").json()["memory"]

    assert memory["participants"] == 2
    assert memory.get("preferred_time") in ("", None)
    assert "2:00 AM" not in response["response"]


def test_search_for_after_hours_slot_is_not_rerouted_to_recommendation(tmp_path: Path, monkeypatch) -> None:
    client = make_client(tmp_path, monkeypatch)

    response = client.post(
        "/chat",
        json={
            "session_id": "cold-start-search-130am",
            "message": "Can you search for a 1:30 AM slot tomorrow at Whitefield?",
        },
    ).json()
    memory = client.get("/memory/cold-start-search-130am").json()["memory"]
    text = response["response"].lower()

    assert "1:30 am" in text
    assert "outside" in text or "won't be available" in text
    assert "recommend" not in text
    assert memory["location"] == "Whitefield"
    assert memory.get("preferred_time") in ("", None)
    assert memory["booking_id"] == ""


def test_safety_breathing_concern_escalates_immediately_during_booking(tmp_path: Path, monkeypatch) -> None:
    client = make_client(tmp_path, monkeypatch)
    session_id = "safety-breathing"

    client.post(
        "/chat",
        json={
            "session_id": session_id,
            "message": "I want to book Murder Mystery for 4 people at Whitefield tomorrow evening.",
        },
    )
    response = client.post(
        "/chat",
        json={
            "session_id": session_id,
            "message": "Wait, someone in our group is inside a room right now and is having trouble breathing.",
        },
    ).json()
    memory = client.get(f"/memory/{session_id}").json()["memory"]

    assert response["next_agent"] == "escalation_agent"
    assert response["escalation"]["escalate"] is True
    assert response["escalation"]["trigger"] == "safety_concern"
    assert "emergency" in response["response"].lower() or "on-site staff" in response["response"].lower()
    assert memory["booking_id"] == ""


def test_other_option_pronoun_does_not_become_fake_one_am_time(tmp_path: Path, monkeypatch) -> None:
    client = make_client(tmp_path, monkeypatch)
    session_id = "other-option-not-time"

    client.post(
        "/chat",
        json={
            "session_id": session_id,
            "message": "Uh I want that other beginner one maybe tomorrow evening, actually no maybe Whitefield.",
        },
    )
    client.post(
        "/chat",
        json={
            "session_id": session_id,
            "message": "Can you make it three people, not scary, same kind of room as before?",
        },
    )
    response = client.post(
        "/chat",
        json={"session_id": session_id, "message": "Actually Koramangala instead, maybe that other option."},
    ).json()
    memory = client.get(f"/memory/{session_id}").json()["memory"]

    assert memory["preferred_time"] != "1:00 AM"
    assert memory["requested_time"] != "1:00 AM"
    assert "1:00 AM" not in response["response"]


def test_unknown_certification_question_is_answered_honestly_and_resumes_booking(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client = make_client(tmp_path, monkeypatch)
    session_id = "unknown-certification"

    client.post(
        "/chat",
        json={
            "session_id": session_id,
            "message": "Book Murder Mystery for three adults at Whitefield tomorrow evening.",
        },
    )
    response = client.post(
        "/chat",
        json={
            "session_id": session_id,
            "message": "Before we go on, do you have a certified VR headset allergy report for every prop in the room?",
        },
    ).json()
    memory = client.get(f"/memory/{session_id}").json()["memory"]
    text = response["response"].lower()

    assert "don't have" in text or "do not have" in text
    assert "certified" in text or "documentation" in text
    assert "available evening slots" in text or "which time" in text
    assert memory["location"] == "Whitefield"
    assert memory["room"] == "Murder Mystery"
    assert memory["participants"] == 3
    assert memory["booking_id"] == ""


def test_locked_inside_faq_is_answered_during_slot_selection(tmp_path: Path, monkeypatch) -> None:
    client = make_client(tmp_path, monkeypatch)
    session_id = "locked-faq-during-booking"

    client.post(
        "/chat",
        json={
            "session_id": session_id,
            "message": "Hi, we're two adults and this is our first escape room. We want to book Murder Mystery at Whitefield tomorrow evening.",
        },
    )
    response = client.post(
        "/chat",
        json={"session_id": session_id, "message": "Quick question, are we actually locked inside the room?"},
    ).json()
    memory = client.get(f"/memory/{session_id}").json()["memory"]

    text = response["response"].lower()
    assert "not actually locked" in text or "can open" in text
    assert "which time" in text or "available slots" in text
    assert memory["room"] == "Murder Mystery"
    assert memory["location"] == "Whitefield"
    assert memory["participants"] == 2


def test_repeated_slot_while_waiting_for_name_is_not_saved_as_name(tmp_path: Path, monkeypatch) -> None:
    client = make_client(tmp_path, monkeypatch)
    session_id = "slot-not-name"

    first = client.post(
        "/chat",
        json={
            "session_id": session_id,
            "message": "Book Murder Mystery for two adults at Whitefield tomorrow evening.",
        },
    ).json()
    slot = "5:20 PM" if "5:20 PM" in first["response"] else "6:30 PM"
    client.post("/chat", json={"session_id": session_id, "message": slot})
    response = client.post("/chat", json={"session_id": session_id, "message": slot}).json()
    memory = client.get(f"/memory/{session_id}").json()["memory"]

    assert memory["customer_name"] == ""
    assert memory["first_name"] == ""
    assert "may i have your name" in response["response"].lower()


def test_escalation_is_sticky_after_handoff(tmp_path: Path, monkeypatch) -> None:
    client = make_client(tmp_path, monkeypatch)
    session_id = "sticky-escalation"

    first = client.post("/chat", json={"session_id": session_id, "message": "I want a human."}).json()
    second = client.post("/chat", json={"session_id": session_id, "message": "Transfer me."}).json()
    third = client.post("/chat", json={"session_id": session_id, "message": "I don't want AI."}).json()

    for response in (first, second, third):
        assert response["next_agent"] == "escalation_agent"
        assert (response["escalation"] or {}).get("escalate") is True
        assert "connect you with our team" in response["response"].lower()
        assert "which location" not in response["response"].lower()

    memory = client.get(f"/memory/{session_id}").json()["memory"]
    turns = memory["conversation"]
    assert len(turns) == 6
    assert [turn["content"] for turn in turns if turn["role"] == "customer"] == [
        "I want a human.",
        "Transfer me.",
        "I don't want AI.",
    ]


def test_positive_booking_signals_do_not_false_escalate(tmp_path: Path, monkeypatch) -> None:
    client = make_client(tmp_path, monkeypatch)
    session_id = "positive-no-escalation"

    client.post("/chat", json={"session_id": session_id, "message": "I want to book tomorrow."}).json()
    client.post("/chat", json={"session_id": session_id, "message": "Whitefield"}).json()
    participants = client.post("/chat", json={"session_id": session_id, "message": "There are four of us."}).json()
    book = client.post("/chat", json={"session_id": session_id, "message": "Let's book."}).json()
    back = client.post("/chat", json={"session_id": session_id, "message": "Back to booking."}).json()

    assert (participants["escalation"] or {}).get("escalate") is False
    assert (book["escalation"] or {}).get("escalate") is False
    assert (back["escalation"] or {}).get("escalate") is False


def test_cold_start_outside_hours_availability_answers_directly(tmp_path: Path, monkeypatch) -> None:
    client = make_client(tmp_path, monkeypatch)

    response = client.post(
        "/chat",
        json={"session_id": "cold-start-a", "message": "Do you have a 1:30 AM slot tomorrow available?"},
    ).json()

    text = response["response"].lower()
    assert "1:30 am" in text
    assert "outside" in text or "won't be available" in text
    assert "which location" not in text
    assert "have you done" not in text


def test_cold_start_specific_room_location_time_checks_availability(tmp_path: Path, monkeypatch) -> None:
    client = make_client(tmp_path, monkeypatch)

    response = client.post(
        "/chat",
        json={"session_id": "cold-start-b", "message": "Is Murder Mystery available at Whitefield tomorrow 1 PM?"},
    ).json()
    memory = client.get("/memory/cold-start-b").json()["memory"]

    text = response["response"].lower()
    assert "checked availability" in text
    assert "1:00 pm" in text
    assert "closest available slots" in text
    assert "have you done" not in text
    assert "which location" not in text
    assert memory["location"] == "Whitefield"
    assert memory["room"] == "Murder Mystery"
    assert memory["preferred_time"] == "1:00 PM"
    assert memory["last_suggested_slots"]


def test_cold_start_room_comparison_answers_without_qualification(tmp_path: Path, monkeypatch) -> None:
    client = make_client(tmp_path, monkeypatch)

    response = client.post(
        "/chat",
        json={
            "session_id": "cold-start-c",
            "message": "What's the difference between Murder Mystery and Prison Break, which is harder?",
        },
    ).json()

    text = response["response"].lower()
    assert "murder mystery" in text
    assert "prison break" in text
    assert "harder" in text or "stronger pressure" in text
    assert "which location" not in text
    assert "how many people" not in text


def test_mid_flow_parking_interruption_preserves_booking_state(tmp_path: Path, monkeypatch) -> None:
    client = make_client(tmp_path, monkeypatch)
    session_id = "cold-start-d"

    first = client.post(
        "/chat",
        json={"session_id": session_id, "message": "Book me Hostage for 4 people, Koramangala."},
    ).json()
    assert "date" in first["response"].lower()

    second = client.post(
        "/chat",
        json={"session_id": session_id, "message": "Wait — is there parking there?"},
    ).json()
    memory = client.get(f"/memory/{session_id}").json()["memory"]

    assert "parking" in second["response"].lower()
    assert "date" in second["response"].lower()
    assert memory["room"] == "Hostage"
    assert memory["location"] == "Koramangala"
    assert memory["participants"] == 4


def test_core_booking_field_change_clears_derived_payment_state(tmp_path: Path) -> None:
    memory = ConversationMemory(tmp_path / "derived-state.json")
    memory.data.update(
        {
            "location": "Whitefield",
            "room": "Murder Mystery",
            "selected_slot": "5:20 PM",
            "venue_id": "v_whitefield",
            "venueId": "v_whitefield",
            "_kreeda_cart_id": "cart-whitefield",
            "_kreeda_cart_signature": "v_whitefield|game|date|time|people",
            "booking_id": "bk_old",
            "orderId": "or_old",
            "paymentUrl": "https://pay.example/old?pr=true",
            "whatsapp_payload": {"payment_link": "https://pay.example/old?pr=true"},
        }
    )
    memory.save()

    memory.set_field("location", "Koramangala", "Actually change location to Koramangala.", expected_field="location")

    assert memory.data["location"] == "Koramangala"
    assert memory.data["selected_slot"] == ""
    assert memory.data["venue_id"] == ""
    assert memory.data["venueId"] == ""
    assert memory.data["_kreeda_cart_id"] == ""
    assert memory.data["_kreeda_cart_signature"] == ""
    assert memory.data["booking_id"] == ""
    assert memory.data["orderId"] == ""
    assert memory.data["paymentUrl"] == ""
    assert memory.data["whatsapp_payload"] == ""


def test_raw_transcript_style_opening_does_not_force_experience_first(tmp_path: Path, monkeypatch) -> None:
    client = make_client(tmp_path, monkeypatch)
    session_id = "cold-start-e"

    first = client.post(
        "/chat",
        json={
            "session_id": session_id,
            "message": "Yeah, I wanted to know what slots are available today for 2 people.",
        },
    ).json()
    second = client.post(
        "/chat",
        json={"session_id": session_id, "message": "Koramangala."},
    ).json()
    third = client.post(
        "/chat",
        json={
            "session_id": session_id,
            "message": "I think in Koramangala, I mean this breakout room, this is first time only.",
        },
    ).json()
    fourth = client.post(
        "/chat",
        json={"session_id": session_id, "message": "Okay, and what is the charges?"},
    ).json()
    fifth = client.post(
        "/chat",
        json={"session_id": session_id, "message": "I'll let you know in some time."},
    ).json()
    memory = client.get(f"/memory/{session_id}").json()["memory"]

    first_text = first["response"].lower()
    assert "koramangala" in first_text or "which branch" in first_text or "which location" in first_text
    assert "first time" not in first["response"].lower()
    assert "first time" not in second["response"].lower()
    assert memory["participants"] == 2
    assert memory["location"] == "Koramangala"
    assert memory["experience_level"] == "beginner"
    assert "which location" not in third["response"].lower()
    assert "pricing" in fourth["response"].lower() or "price" in fourth["response"].lower()
    assert "name" not in fifth["response"].lower()
    assert "ready" in fifth["response"].lower()
