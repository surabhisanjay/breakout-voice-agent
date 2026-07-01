import sqlite3
from pathlib import Path
from datetime import datetime, timedelta

DB_PATH = Path(__file__).resolve().parents[2] / "memory" / "analytics.db"

def seed_conversations():
    if not DB_PATH.exists():
        print("Database not found. Initialize first.")
        return
        
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    
    try:
        # Check if test conversation already exists
        cursor.execute("SELECT COUNT(*) FROM calls WHERE session_id = 'session_test_123'")
        if cursor.fetchone()[0] == 0:
            print("Seeding test conversation session_test_123...")
            
            # 1. Insert Call log
            started = (datetime.utcnow() - timedelta(minutes=5)).isoformat()
            ended = datetime.utcnow().isoformat()
            cursor.execute(
                """
                INSERT INTO calls (session_id, direction, status, started_at, ended_at, duration, latency, ttfr, silence_percent, interruptions, speech_speed, speaking_ratio)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("session_test_123", "inbound", "connected", started, ended, 300, 0.45, 1.1, 8.5, 2, 138.5, 0.42)
            )
            
            # 2. Insert Agent Performance
            cursor.execute(
                """
                INSERT INTO agent_performance (session_id, agent_id, team_id, call_date, handle_time, talk_time, talk_listen_ratio, quality_score, csat)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("session_test_123", "inbound_agent", "front_desk", datetime.utcnow().strftime("%Y-%m-%d"), 300, 180, 0.6, 13, 5)
            )
            
            # 3. Insert Escalation
            cursor.execute(
                """
                INSERT INTO escalations (session_id, timestamp, reason, priority, status, handoff_to)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                ("session_test_123", started, "Inquiry about group sizes and payment plans", "High", "Unresolved", "support")
            )
            
            # 4. Insert Booking Analytics
            cursor.execute(
                """
                INSERT INTO bookings_analytics (booking_id, session_id, booking_reference, status, room, location, booking_date, slot, participants, revenue, payment_received)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("book_seed_123", "session_test_123", "REF_ANITA_MEHTA", "Completed", "Murder Mystery", "Indiranagar", "2026-07-04", "10:00 AM", 10, 120.0, 1)
            )
            
            # 5. Insert Sentiment timeline points
            sentiment_turns = [
                (-1, "negative"),
                (0, "neutral"),
                (1, "positive"),
                (2, "delighted")
            ]
            for idx, (score, label) in enumerate(sentiment_turns):
                ts = (datetime.utcnow() - timedelta(minutes=5 - idx)).isoformat()
                cursor.execute(
                    """
                    INSERT INTO sentiment_timeline (session_id, timestamp, score, label)
                    VALUES (?, ?, ?, ?)
                    """,
                    ("session_test_123", ts, score, label)
                )
                
            # 6. Insert Tasks
            cursor.execute(
                """
                INSERT INTO tasks (id, session_id, description, priority, due_date, status)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                ("task_seed_1", "session_test_123", "Answer about booking slots", "High", "Today", "Pending")
            )
            cursor.execute(
                """
                INSERT INTO tasks (id, session_id, description, priority, due_date, status)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                ("task_seed_2", "session_test_123", "Tell about pricing policies", "Medium", "Today", "Pending")
            )
            
            # 7. Insert Notes
            cursor.execute(
                """
                INSERT INTO notes (id, session_id, content)
                VALUES (?, ?, ?)
                """,
                ("note_seed_1", "session_test_123", "Mentioned interest in the Wrigleyville area")
            )
            cursor.execute(
                """
                INSERT INTO notes (id, session_id, content)
                VALUES (?, ?, ?)
                """,
                ("note_seed_2", "session_test_123", "Asks about seasonal promotions and bundles")
            )
            
            # 8. Insert Activities
            cursor.execute(
                """
                INSERT INTO activities (id, session_id, type, description)
                VALUES (?, ?, ?, ?)
                """,
                ("act_seed_1", "session_test_123", "call", "Inbound call answered by Rajesh Agrawal")
            )
            cursor.execute(
                """
                INSERT INTO activities (id, session_id, type, description)
                VALUES (?, ?, ?, ?)
                """,
                ("act_seed_2", "session_test_123", "task", "Created action item: Answer about booking slots")
            )
            
            # 9. Insert Transcript Turns
            dialogue = [
                ("Customer", "Hey Anita, I'm calling to inquire about booking a slot for my corporate event."),
                ("Agent", "Hello! I can definitely help with that. We have our popular Murder Mystery theme available."),
                ("Customer", "Perfect. What are the pricing options and details? We have a large group of around 10 people."),
                ("Agent", "For 10 players, the rate is Rs. 10,320. Does that fit your budget?"),
                ("Customer", "That sounds reasonable, but I'll need to check on our custom payment and contract terms first.")
            ]
            for idx, (speaker, text) in enumerate(dialogue):
                cursor.execute(
                    """
                    INSERT INTO transcripts (session_id, speaker, text)
                    VALUES (?, ?, ?)
                    """,
                    ("session_test_123", speaker, text)
                )
                
            conn.commit()
            print("Conversation seeding completed successfully.")
        else:
            print("Test conversation session_test_123 already exists.")
    except Exception as e:
        print(f"Error seeding conversation: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    seed_conversations()
