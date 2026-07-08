import asyncio
import os
import sys
import datetime

from dotenv import load_dotenv
load_dotenv()

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from src.orchestration.booking_orchestrator import BookingOrchestrator
from src.integrations.kreeda.breakout_booking_provider import BreakoutBookingProvider
from src.integrations.kreeda.breakout_api import BreakoutAPI
from src.integrations.kreeda.agent_contract_provider import AgentContractProvider

def main():
    import logging
    logging.getLogger().handlers.clear()
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        stream=sys.stdout
    )
    print("Testing Kreeda Native WhatsApp Booking...")
    
    # Instantiate the live providers
    booking_provider = BreakoutBookingProvider(
        client=BreakoutAPI(timeout=BookingOrchestrator._live_booking_timeout())
    )
    contract_provider = AgentContractProvider()
    
    # Initialize orchestrator with live providers
    orchestrator = BookingOrchestrator(
        booking_provider=booking_provider,
        contract_provider=contract_provider
    )
    orchestrator.is_live = True  # Force live mode to hit Kreeda API
    
    tomorrow = (datetime.datetime.now() + datetime.timedelta(days=1)).strftime("%Y-%m-%d")
    
    # Fake memory representing the state right before a booking is finalized
    memory = {
        "phone": "8217008407",
        "customer_name": "Test User",
        "sendPaymentRequest": True,  # This tells Kreeda to send the WhatsApp message
        "location": "koramangala",
        "participants": 2,
        "date": tomorrow,
        "preferred_date": tomorrow,
        "age_group": "adults",
        "room": "murder_mystery", # Replace if different
    }
    
    print(f"1. Checking availability for Koramangala, {tomorrow}, 2 participants...")
    avail = orchestrator.check_availability(
        location=memory["location"],
        date=memory["date"],
        participants=memory["participants"],
        room=memory["room"]
    )
    
    if not avail.get("available") or not avail.get("slots"):
        print("No slots available. Availability response:", avail)
        return
        
    slots = avail["slots"]
    chosen_slot = slots[0]
    print(f"2. Selected slot: {chosen_slot}")
    
    print(f"3. Creating confirmed booking (This should trigger Kreeda's WhatsApp)...")
    booking_result = orchestrator.prepare_booking(memory, chosen_slot)
    
    if booking_result.get("confirmed"):
        print(f"✅ Booking Confirmed! Booking ID: {booking_result.get('booking_id')}")
        print("Please check 8217008407 on WhatsApp to see if Kreeda sent the payment link natively!")
    else:
        print(f"❌ Booking failed: {booking_result}")
        
    print("\n--- TRACE EVENTS ---")
    import json
    for event in orchestrator.booking_provider.client.trace_events:
        print(json.dumps(event, indent=2))

if __name__ == "__main__":
    main()
