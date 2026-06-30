import os
from celery import Celery
from src.integrations.whatsapp import WhatsAppClient

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

celery_app = Celery(
    "breakout_tasks",
    broker=REDIS_URL,
    backend=REDIS_URL
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True
)

@celery_app.task(name="send_whatsapp_message")
def send_whatsapp_message_task(phone: str, booking: dict) -> dict:
    """Asynchronously dispatches booking confirmation messages via WATI API."""
    client = WhatsAppClient()
    result = client.send_booking_confirmation(phone, booking)
    return result.to_dict()

@celery_app.task(name="sync_crm_data")
def sync_crm_data_task(session_id: str, payload: dict) -> str:
    """Asynchronously syncs lead lifecycle changes and call logs to CRM databases."""
    # Place out-of-band CRM webhook forwarding, syncs, or integrations here
    return f"Synced session {session_id} successfully."
