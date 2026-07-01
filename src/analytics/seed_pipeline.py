import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parents[2] / "memory" / "analytics.db"

def seed_db():
    if not DB_PATH.exists():
        print("Database does not exist yet. Run the FastAPI application to initialize it first.")
        return
        
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    
    try:
        # Check if leads table is empty
        cursor.execute("SELECT COUNT(*) FROM leads")
        if cursor.fetchone()[0] == 0:
            print("Seeding initial Leads...")
            # We seed a few Lakhan Pandey cards across stages to match the screenshots
            stages = ["New", "New", "New", "Qualified", "Qualified", "Booking", "Booking", "Payment", "Payment"]
            for idx, stage in enumerate(stages):
                cursor.execute(
                    """
                    INSERT INTO leads (
                        lead_id, first_name, last_name, email, phone, company_name,
                        location, escape_room, value, group_size, stage, priority
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        f"lead_seed_{idx}",
                        "Lakhan",
                        "Pandey",
                        "lakhan@stark.com",
                        "8217008407",
                        "Stark Industries",
                        "JP Nagar",
                        "Murder Mystery",
                        10320.0,
                        10,
                        stage,
                        "High"
                    )
                )
            
        # Check if agents table is empty
        cursor.execute("SELECT COUNT(*) FROM agents")
        if cursor.fetchone()[0] == 0:
            print("Seeding initial Agents...")
            cursor.execute(
                """
                INSERT INTO agents (agent_id, agent_name, description, department, role)
                VALUES (?, ?, ?, ?, ?)
                """,
                ("rajesh_agrawal", "Rajesh Agrawal", "Senior Sales Representative", "Sales", "Qualifier")
            )
            
        conn.commit()
        print("Seeding completed successfully.")
    except Exception as e:
        print(f"Error seeding database: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    seed_db()
