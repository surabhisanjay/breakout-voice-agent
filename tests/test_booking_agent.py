from __future__ import annotations

from src.booking_agent import BookingAgent, BookingError


def test_create_and_confirm_booking():
    agent = BookingAgent()
    slot = agent.add_slot("2026-07-15", "18:00", "escape_room", capacity=6)
    available = agent.check_availability("escape_room", "2026-07-15")
    assert any(s.slot_id == slot for s in available)

    owner = "owner-123"
    locked = agent.lock_slot(slot, owner, ttl_seconds=1)
    assert locked is True

    customer = {"customer_name": "Alex", "phone": "9876543210", "participants": 4, "owner_id": owner}
    booking = agent.create_booking(slot, customer, require_payment=True)
    assert booking.reference.startswith("BK-")
    assert booking.payment_required is True
    assert booking.payment_payload and booking.payment_payload["booking_ref"] == booking.reference

    fetched = agent.get_booking(booking.reference)
    assert fetched is not None and fetched.reference == booking.reference


def test_prevent_double_booking():
    agent = BookingAgent()
    slot = agent.add_slot("2026-07-16", "12:00", "escape_room", capacity=6)
    owner1 = "o1"
    agent.lock_slot(slot, owner1, ttl_seconds=1)
    customer1 = {"customer_name": "A", "participants": 2, "owner_id": owner1}
    booking1 = agent.create_booking(slot, customer1)
    assert booking1 is not None

    # second booking attempt should fail
    try:
        customer2 = {"customer_name": "B", "participants": 2, "owner_id": "o2"}
        agent.create_booking(slot, customer2)
        assert False, "expected BookingError"
    except BookingError:
        pass


def test_reschedule_and_cancel():
    agent = BookingAgent()
    slot1 = agent.add_slot("2026-07-20", "10:00", "escape_room")
    slot2 = agent.add_slot("2026-07-21", "12:00", "escape_room")
    owner = "o"
    agent.lock_slot(slot1, owner)
    booking = agent.create_booking(slot1, {"customer_name": "C", "participants": 3, "owner_id": owner})

    res = agent.reschedule(booking.reference, slot2)
    assert res.slot_id == slot2

    cancelled = agent.cancel(booking.reference, reason="customer_request")
    assert cancelled.status == "cancelled"
