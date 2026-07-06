from __future__ import annotations

import base64
import json
import sys
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

import app as api_app  # noqa: E402


def _token(role: str, agent_id: int, org_id: str = "org_test") -> str:
    header = {"alg": "none", "typ": "JWT"}
    payload = {"org_id": org_id, "role": role, "sub": agent_id, "agent_id": agent_id, "name": f"User {agent_id}"}

    def enc(value: dict[str, Any]) -> str:
        raw = json.dumps(value, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    return f"{enc(header)}.{enc(payload)}.sig"


def _headers(role: str = "sales_agent", agent_id: int = 1, org_id: str = "org_test") -> dict[str, str]:
    return {"Authorization": f"Bearer {_token(role, agent_id, org_id)}"}


def _client(monkeypatch) -> TestClient:
    monkeypatch.setattr(api_app, "closira_store", api_app.ClosiraStore())
    return TestClient(api_app.app)


def test_api_v1_requires_jwt_and_returns_me(monkeypatch) -> None:
    client = _client(monkeypatch)

    missing = client.get("/api/v1/me")
    present = client.get("/api/v1/me", headers=_headers())

    assert missing.status_code == 401
    assert present.status_code == 200
    assert present.json()["org_id"] == "org_test"
    assert present.json()["role"] == "sales_agent"


def test_contacts_are_agent_scoped_and_manager_can_see_all(monkeypatch) -> None:
    client = _client(monkeypatch)

    agent = client.get("/api/v1/contacts", headers=_headers("sales_agent", 1)).json()
    denied = client.get("/api/v1/contacts/2", headers=_headers("sales_agent", 1))
    manager = client.get("/api/v1/contacts", headers=_headers("sales_manager", 3)).json()

    assert [item["id"] for item in agent["items"]] == [1]
    assert denied.status_code == 404
    assert {item["id"] for item in manager["items"]} == {1, 2}


def test_contact_child_endpoints_and_voice_activity_shape(monkeypatch) -> None:
    client = _client(monkeypatch)
    headers = _headers("sales_agent", 1)

    assert client.get("/api/v1/contacts/1/leads", headers=headers).status_code == 200
    assert client.get("/api/v1/contacts/1/calls", headers=headers).status_code == 200
    assert client.get("/api/v1/contacts/1/bookings", headers=headers).status_code == 200
    stats = client.get("/api/v1/contacts/1/stats", headers=headers).json()
    activity = client.get("/api/v1/contacts/1/activity", headers=headers).json()
    note = client.post("/api/v1/contacts/1/notes", headers=headers, json={"body": "Call back"}).json()
    patched = client.patch(f"/api/v1/contacts/1/notes/{note['id']}", headers=headers, json={"body": "Called back"})
    deleted = client.delete(f"/api/v1/contacts/1/notes/{note['id']}", headers=headers)

    assert stats == {"total_calls": 1, "total_bookings": 1, "conversion_rate": 1.0, "lifetime_spend": 12000.0}
    assert activity["items"][0]["type"] == "call"
    assert activity["items"][0]["summary"] == "Customer booked Murder Mystery."
    assert patched.status_code == 200
    assert patched.json()["body"] == "Called back"
    assert deleted.status_code == 200


def test_lead_contract_and_manager_only_lost_leads(monkeypatch) -> None:
    client = _client(monkeypatch)

    agent_headers = _headers("sales_agent", 1)
    manager_headers = _headers("sales_manager", 3)
    created = client.post("/api/v1/leads", headers=agent_headers, json={"first_name": "Asha", "phone": "9000000000"}).json()
    patched = client.patch(f"/api/v1/leads/{created['id']}", headers=agent_headers, json={"pipeline_stage": "qualified", "ignored": True})
    staged = client.patch(f"/api/v1/leads/{created['id']}/stage", headers=agent_headers, json={"stage_id": 2, "pipeline_stage": "qualified"})

    assert client.get("/api/v1/leads/lost", headers=agent_headers).status_code == 403
    assert client.get("/api/v1/leads/lost", headers=manager_headers).status_code == 200
    assert client.post("/api/v1/leads/lost", headers=manager_headers, json={"lead_id": created["id"], "reason": "No fit"}).status_code == 200
    assert client.delete("/api/v1/leads/lost/1", headers=manager_headers).status_code == 200
    assert patched.status_code == 200
    assert "ignored" not in patched.json()
    assert staged.json()["stage_id"] == 2


def test_calls_contract_live_manager_gate_and_transcript_shapes(monkeypatch) -> None:
    client = _client(monkeypatch)
    headers = _headers("sales_agent", 1)

    assert client.get("/api/v1/calls/live", headers=headers).status_code == 403
    assert client.get("/api/v1/calls/live", headers=_headers("admin", 4)).json()["items"][0]["agent"]["id"] == 1
    assert client.get("/api/v1/calls", headers=headers).json()["items"][0]["id"] == 1
    assert client.get("/api/v1/calls/1", headers=headers).status_code == 200
    assert client.get("/api/v1/calls/1/summary", headers=headers).json()["summary"]["summary"] == "Customer booked Murder Mystery."
    assert client.get("/api/v1/calls/1/transcript", headers=headers).json()["lines"][0]["speaker_type"] == "customer"
    assert client.get("/api/v1/calls/1/transcript/live", headers=headers).json()["call_id"] == 1
    assert client.get("/api/v1/calls/1/recording", headers=headers).json()["available"] is False
    assert client.post("/api/v1/calls/1/csat", headers=headers, json={"score": 5}).json()["saved"] is True


def test_escalations_are_role_scoped_and_patchable(monkeypatch) -> None:
    client = _client(monkeypatch)

    assert client.get("/api/v1/escalations", headers=_headers("sales_agent", 1)).json()["items"] == []
    assert client.get("/api/v1/escalations/1", headers=_headers("sales_agent", 1)).status_code == 404
    patched = client.patch(
        "/api/v1/escalations/1",
        headers=_headers("sales_manager", 3),
        json={"escalation_status": "resolved", "resolution_notes": "Called customer", "ignored": True},
    )

    assert patched.status_code == 200
    assert patched.json()["escalation_status"] == "resolved"
    assert "ignored" not in patched.json()


def test_pipeline_stage_deletion_migrates_leads(monkeypatch) -> None:
    client = _client(monkeypatch)
    headers = _headers("sales_manager", 3)

    create = client.post("/api/v1/pipeline/stages", headers=headers, json={"name": "demo"}).json()
    lead = client.post("/api/v1/leads", headers=headers, json={"first_name": "Mira", "stage_id": create["id"]}).json()
    deleted = client.request("DELETE", f"/api/v1/pipeline/stages/{create['id']}", headers=headers, json={"target_stage_id": 2})
    migrated = client.get(f"/api/v1/leads/{lead['id']}", headers=headers).json()

    assert client.post("/api/v1/pipeline/stages", headers=_headers("sales_agent", 1), json={"name": "blocked"}).status_code == 403
    assert client.patch("/api/v1/pipeline/stages/reorder", headers=headers, json={"stage_ids": [3, 2, 1]}).status_code == 200
    assert deleted.status_code == 200
    assert migrated["stage_id"] == 2
    assert client.request("DELETE", "/api/v1/pipeline/stages/999", headers=headers, json={"target_stage_id": 2}).status_code == 404


def test_booking_dual_write_creates_contact_and_balance(monkeypatch) -> None:
    client = _client(monkeypatch)
    payload = {
        "first_name": "Dev",
        "last_name": "Rao",
        "phone": "9111111111",
        "event_type": "Escape Room",
        "location": "Whitefield",
        "party_size": 3,
        "event_date": "2026-06-30",
        "total_amount": 9000,
        "paid_amount": 3000,
        "payment_status": "PAYMENT_PENDING",
        "source": "crm",
        "channel": "voice",
        "notes": "Demo booking",
    }

    booking = client.post("/api/v1/bookings", headers=_headers("sales_agent", 1), json=payload).json()
    fetched = client.get(f"/api/v1/bookings/{booking['id']}", headers=_headers("sales_agent", 1)).json()
    contacts = client.get("/api/v1/contacts", headers=_headers("sales_agent", 1)).json()["items"]

    assert booking["contact_id"] != 0
    assert fetched["balance_amount"] == 6000.0
    assert any(contact["phone"] == "9111111111" for contact in contacts)


def test_agents_teams_communications_user_layout_search_notifications_and_exports(monkeypatch) -> None:
    client = _client(monkeypatch)
    agent_headers = _headers("sales_agent", 1)
    manager_headers = _headers("sales_manager", 3)

    assert client.get("/api/v1/agents", headers=agent_headers).status_code == 403
    assert client.get("/api/v1/agents/assignable", headers=agent_headers).status_code == 200
    assert client.get("/api/v1/agents/1", headers=agent_headers).status_code == 200
    assert client.post("/api/v1/agents", headers=manager_headers, json={"name": "New Agent"}).status_code == 200
    assert client.patch("/api/v1/agents/1", headers=manager_headers, json={"active": False}).status_code == 200
    assert client.get("/api/v1/teams", headers=agent_headers).status_code == 403
    team = client.post("/api/v1/teams", headers=manager_headers, json={"name": "Demo Team"}).json()
    assert client.get(f"/api/v1/teams/{team['id']}", headers=manager_headers).status_code == 200
    assert client.patch(f"/api/v1/teams/{team['id']}", headers=manager_headers, json={"name": "Demo Team 2"}).status_code == 200

    layout_payload = {"role": "sales_agent", "layout": [{"i": "calls-today", "x": 0, "y": 0, "w": 4, "h": 2}], "widgets": []}
    assert client.put("/api/v1/me/layout/dashboard", headers=agent_headers, json=layout_payload).status_code == 200
    assert client.get("/api/v1/me/layout/dashboard", headers=agent_headers).json()["layout"][0]["i"] == "calls-today"
    assert client.get("/api/v1/conversations/1", headers=agent_headers).status_code == 200
    assert client.post("/api/v1/conversations/1/messages", headers=agent_headers, json={"channel": "whatsapp", "body": "Hi"}).status_code == 200
    assert client.get("/api/v1/message-templates", headers=agent_headers).status_code == 200
    assert client.post("/api/v1/follow-ups", headers=agent_headers, json={"lead_id": 1, "due_at": "2026-06-30"}).status_code == 200
    assert client.get("/api/v1/follow-ups", headers=agent_headers).status_code == 200
    assert client.get("/api/v1/search?q=Priya&type=all", headers=agent_headers).json()["items"][0]["type"] == "contact"
    assert client.get("/api/v1/notifications", headers=agent_headers).status_code == 200
    assert client.patch("/api/v1/notifications/1/read", headers=agent_headers).json()["read"] is True
    assert client.get("/api/v1/export/lost-leads", headers=agent_headers).status_code == 403
    assert client.get("/api/v1/export/lost-leads", headers=manager_headers).headers["content-type"].startswith("text/csv")
    assert client.get("/api/v1/export/calls", headers=agent_headers).headers["content-type"].startswith("text/csv")
    assert client.get("/api/v1/export/contacts", headers=agent_headers).headers["content-type"].startswith("text/csv")


def test_analytics_support_role_differentiation_and_scoping(monkeypatch) -> None:
    client = _client(monkeypatch)

    agent_dashboard = client.get("/api/v1/analytics/dashboard?period=7d", headers=_headers("sales_agent", 1)).json()
    manager_dashboard = client.get("/api/v1/analytics/dashboard?period=30d&agent_id=1", headers=_headers("sales_manager", 3)).json()
    admin_dashboard = client.get("/api/v1/analytics/dashboard?period=this_year", headers=_headers("admin", 4)).json()

    assert "calls_today" in agent_dashboard
    assert "booking_rate_by_room" in manager_dashboard
    assert "revenue_this_month" in admin_dashboard
    assert client.get("/api/v1/analytics/live", headers=_headers("sales_agent", 1)).status_code == 403
    assert client.get("/api/v1/analytics/live", headers=_headers("sales_manager", 3)).status_code == 200
    assert client.get("/api/v1/analytics/information?from=2026-01-01T00:00:00Z&to=2026-01-31T00:00:00Z", headers=_headers("sales_agent", 1)).status_code == 200
    assert client.get("/api/v1/analytics/data?tab=all", headers=_headers("sales_agent", 1)).status_code == 200
    assert client.get("/api/v1/analytics/team-performance", headers=_headers("sales_agent", 1)).status_code == 403
    assert client.get("/api/v1/analytics/team-performance", headers=_headers("sales_manager", 3)).status_code == 200
    assert client.get("/api/v1/analytics/agent/2", headers=_headers("sales_agent", 1)).status_code == 403
    assert client.get("/api/v1/analytics/agent/1", headers=_headers("sales_agent", 1)).status_code == 200
    assert client.get("/api/v1/analytics/team/1", headers=_headers("sales_manager", 3)).status_code == 200
    assert client.get("/api/v1/analytics/dashboard?period=forever", headers=_headers("sales_agent", 1)).status_code == 400
