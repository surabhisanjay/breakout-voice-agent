from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch
import pytest

from src.integrations.closira.client import ClosiraCRMClient
from src.core.agent_response import AgentResponse
from main import dispatch


class TestClosiraCRMClient(unittest.TestCase):
    @patch("httpx.post")
    def test_login_success(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": {
                "access_token": "mock_jwt_token",
                "expires_in": 3600
            }
        }
        mock_post.return_value = mock_response

        client = ClosiraCRMClient(
            base_url="http://mock-crm/api/v1",
            email="test@closiro.com",
            password="Password123!"
        )
        success = client._login()
        self.assertTrue(success)
        self.assertEqual(client._token, "mock_jwt_token")

    @patch("httpx.post")
    def test_create_contact(self, mock_post):
        # First post is for login, second for create_contact
        mock_login = MagicMock()
        mock_login.status_code = 200
        mock_login.json.return_value = {"access_token": "mock_jwt_token", "expires_in": 3600}

        mock_create = MagicMock()
        mock_create.status_code = 201
        mock_create.json.return_value = {"data": {"id": 123}}

        mock_post.side_effect = [mock_login, mock_create]

        client = ClosiraCRMClient()
        contact_id = client.create_contact("John", "Doe", "9876543210")
        self.assertEqual(contact_id, 123)

    @patch("httpx.post")
    def test_create_lead(self, mock_post):
        mock_login = MagicMock()
        mock_login.status_code = 200
        mock_login.json.return_value = {"access_token": "mock_jwt_token", "expires_in": 3600}

        mock_create = MagicMock()
        mock_create.status_code = 201
        mock_create.json.return_value = {"data": {"id": 456}}

        mock_post.side_effect = [mock_login, mock_create]

        client = ClosiraCRMClient()
        lead_id = client.create_lead(
            contact_id=123,
            first_name="John",
            last_name="Doe",
            phone="9876543210",
            location="Whitefield",
            event_type="Escape Room",
            party_size=4
        )
        self.assertEqual(lead_id, 456)

    @patch("httpx.post")
    @patch("httpx.patch")
    def test_update_lead_stage(self, mock_patch, mock_post):
        mock_login = MagicMock()
        mock_login.status_code = 200
        mock_login.json.return_value = {"access_token": "mock_jwt_token", "expires_in": 3600}
        mock_post.return_value = mock_login

        mock_update = MagicMock()
        mock_update.status_code = 200
        mock_patch.return_value = mock_update

        client = ClosiraCRMClient()
        success = client.update_lead_stage(456, "booked")
        self.assertTrue(success)


class TestClosiraIntegrationWorkflow(unittest.TestCase):
    @patch("src.integrations.closira.client.ClosiraCRMClient.create_contact")
    @patch("src.integrations.closira.client.ClosiraCRMClient.create_lead")
    @patch("main._dispatch_impl")
    def test_dispatch_syncs_contact_and_lead(self, mock_dispatch_impl, mock_create_lead, mock_create_contact):
        # Set up mocks
        mock_inbound = MagicMock()
        mock_inbound.memory.data = {
            "customer_name": "John Doe",
            "phone": "9876543210",
            "location": "Whitefield",
            "event_type": "Escape Room",
            "participants": 4
        }
        
        mock_response = MagicMock(spec=AgentResponse)
        mock_response.next_agent = "inbound_agent"
        mock_dispatch_impl.return_value = (mock_response, None, "inbound_agent")

        mock_create_contact.return_value = 123
        mock_create_lead.return_value = 456

        # Run dispatch
        res, booking, agent = dispatch("my message", mock_inbound, None, "inbound_agent", "sess-1")

        # Verify calls
        mock_create_contact.assert_called_once_with(first_name="John", last_name="Doe", phone="9876543210")
        mock_create_lead.assert_called_once()
        self.assertEqual(mock_inbound.memory.data["closira_contact_id"], 123)
        self.assertEqual(mock_inbound.memory.data["closira_lead_id"], 456)
        self.assertEqual(mock_inbound.memory.data["closira_lead_status"], "new")
