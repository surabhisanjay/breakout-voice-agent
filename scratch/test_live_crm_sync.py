import os
import sys

# Ensure import path includes src
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.integrations.closira.client import ClosiraCRMClient
import httpx

def test_live_sync():
    print("Initializing Closira CRM Client...")
    client = ClosiraCRMClient(
        base_url="http://139.84.146.101/api/v1",
        email="agent@closiro.com",
        password="Agent@Secure123!"
    )
    
    print("\n1. Testing Login...")
    if not client._login():
        print("  ✗ Login FAILED.")
        return
    print("  ✓ Login SUCCESSFUL.")
    print(f"  Token: {client._token}")
    print(f"  Headers: {client._get_headers()}")
    
    print("\n2. Creating a test Contact...")
    import random
    suffix = random.randint(1000, 9999)
    first_name = "LiveTest"
    last_name = f"User-{suffix}"
    phone = f"999999{suffix}"
    
    contact_id = client.create_contact(first_name, last_name, phone)
    if not contact_id:
        print("  ✗ Contact Creation FAILED.")
        return
    print(f"  ✓ Contact Created. ID: {contact_id}")
    
    print("\n3. Creating a test Lead...")
    lead_id = client.create_lead(
        contact_id=contact_id,
        first_name=first_name,
        last_name=last_name,
        phone=phone,
        location="Whitefield",
        event_type="Escape Room",
        party_size=6
    )
    if not lead_id:
        print("  ✗ Lead Creation FAILED.")
        return
    print(f"  ✓ Lead Created. ID: {lead_id}")
    
    print("\n4. Fetching all available pipeline stages...")
    headers = client._get_headers()
    response = httpx.get("http://139.84.146.101/api/v1/pipeline/stages", headers=headers, timeout=10.0)
    if response.status_code == 200:
        stages_data = response.json().get("data", [])
        print("  Available Stages:")
        for stage in stages_data:
            print(f"    - ID: {stage.get('id')}, Name: '{stage.get('name')}', Code/Label: '{stage.get('label')}'")
    else:
        print(f"  ✗ Failed to fetch stages. Status: {response.status_code}, Response: {response.text}")
        return
        
    # Try updating stage to the first available or 'booking'
    target_stage = "booking"
    print(f"\n4b. Updating Lead Stage to '{target_stage}'...")
    success = client.update_lead_stage(lead_id, target_stage)
    if not success:
        print("  ✗ Lead Stage Update FAILED.")
        return
    print(f"  ✓ Lead Stage Updated to '{target_stage}'.")
    
    print("\n5. Adding a CRM Note...")
    note_success = client.add_contact_note(contact_id, "Integration verified successfully from local Breakout AI backend!")
    if not note_success:
        print("  ✗ Note addition FAILED.")
        return
    print("  ✓ Note added to contact.")
    
    print(f"\n==================================================")
    print(f"  SUCCESS! Check your CRM dashboard for:")
    print(f"  - Contact: '{first_name} {last_name}'")
    print(f"  - Lead ID: {lead_id} (marked as 'booked')")
    print(f"==================================================")

if __name__ == "__main__":
    test_live_sync()
