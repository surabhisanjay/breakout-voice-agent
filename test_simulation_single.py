from pathlib import Path
import random
import sys
from unittest.mock import MagicMock

PROJECT_DIR = Path(__file__).resolve().parents[4] / "breakout-voice-agent"
sys.path.insert(0, str(PROJECT_DIR))

from src.agents.booking_agent import BookingAgent
from src.memory.conversation_memory import ConversationMemory

def run_debug():
    seed = 0
    rng = random.Random(seed)
    tmp_path = Path("/tmp/debug_sim")
    tmp_path.mkdir(exist_ok=True)
    memory = ConversationMemory(tmp_path / "simulation.json")
    memory.reset()
    memory.data.update({
        "intent": "escape_room_inquiry", "event_type": "Escape Room",
        "booking_started": True, "current_workflow": "booking",
        "participants": 4, "age_group": "adults", "location": "Whitefield",
        "preferred_date": "25 June", "room": "Murder Mystery",
    })
    memory.save()
    agent = BookingAgent(memory)
    agent.response_composer.enabled = False
    agent.availability_tool.check = MagicMock(return_value={
        "available": True, "verified": True, "capacity_supported": True,
        "slots": ["3:00 PM", "7:00 PM"],
    })
    agent.booking_tool.create = MagicMock(return_value={
        "booking_id": f"booking-{seed}", "booking_reference": f"reference-{seed}",
        "confirmed": True, "location": "Whitefield", "date": "25 June",
        "slot": "7:00 PM", "participants": 4, "event_type": "Escape Room",
        "customer_name": "Riya Patel", "phone": "9876543210",
    })

    print("--- Message: ready ---")
    res1 = agent.handle_message("ready")
    print("Response:", res1.response)
    print("State:", agent._state)
    print("Memory:", memory.data)

    print("\n--- Message: FAQ ---")
    msg = rng.choice(("Is parking available?", "How long is the game?", "What food do you have?"))
    res2 = agent.handle_message(msg)
    print("Message:", msg)
    print("Response:", res2.response)
    print("State:", agent._state)
    print("Memory:", memory.data)

    print("\n--- Message: Mutation ---")
    mutation = rng.choice(("participants", "date", "room", "none"))
    print("Mutation chosen:", mutation)
    if mutation == "participants":
        res_mut = agent.handle_message("We are actually 5 people")
        print("Response:", res_mut.response)
        print("State:", agent._state)
        print("Memory:", memory.data)
    elif mutation == "date":
        res_mut = agent.handle_message("Change the date to 26 June")
        print("Response:", res_mut.response)
        print("State:", agent._state)
        print("Memory:", memory.data)
    elif mutation == "room":
        res_mut = agent.handle_message("Switch the room to Hostage")
        print("Response:", res_mut.response)
        print("State:", agent._state)
        print("Memory:", memory.data)

    print("\n--- Message: 7 PM ---")
    res3 = agent.handle_message("7 PM")
    print("Response:", res3.response)
    print("State:", agent._state)
    print("Memory:", memory.data)

    print("\n--- Message: Riya Patel ---")
    res4 = agent.handle_message("Riya Patel")
    print("Response:", res4.response)
    print("State:", agent._state)
    print("Memory:", memory.data)

    print("\n--- Message: phone ---")
    res5 = agent.handle_message("9876543210")
    print("Response:", res5.response)
    print("State:", agent._state)
    print("Memory:", memory.data)

if __name__ == "__main__":
    run_debug()
