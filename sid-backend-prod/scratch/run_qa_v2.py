"""
QA Runner v2 — Vapi voice pipeline
Sends every turn to POST /chat and captures:
  - agent response text
  - next_agent / sentiment / escalation from JSON
  - memory slots from GET /memory/<session>
Stores per-scenario JSON log in scratch/.
"""
import requests, uuid, json, sys, time

BASE = "http://localhost:8000"
SLOT_KEYS = ("intent","participants","location","age_group",
             "preferred_date","room","customer_name","phone",
             "current_workflow","booking_started")

def slots(session_id):
    try:
        r = requests.get(f"{BASE}/memory/{session_id}", timeout=10)
        if r.status_code == 200:
            m = r.json().get("memory", {})
            return {k: m.get(k, "") for k in SLOT_KEYS}
    except Exception:
        pass
    return {}

def run(name, messages):
    sid = f"vapi2-{name.lower().replace(' ','-')}-{uuid.uuid4().hex[:4]}"
    print(f"\n{'='*60}\nSCENARIO: {name}\nSession : {sid}\n{'='*60}")
    log = []
    for i, msg in enumerate(messages, 1):
        print(f"\nT{i:02d}  Customer : {msg}")
        try:
            r = requests.post(f"{BASE}/chat",
                              json={"session_id": sid, "message": msg},
                              timeout=30)
            if r.status_code != 200:
                print(f"     ERROR {r.status_code}: {r.text[:200]}")
                break
            d = r.json()
            resp      = d.get("response", "")
            na        = d.get("next_agent", "")
            sent      = d.get("sentiment_analysis", {})
            esc       = d.get("escalation", {})
            mem       = slots(sid)
            print(f"     Agent    : {resp}")
            print(f"     Trace    : agent={na}  "
                  f"sent={sent.get('sentiment','?')}({sent.get('confidence',0):.2f})  "
                  f"esc={esc.get('escalate','?')}")
            print(f"     Memory   : {mem}")
            log.append({"turn": i, "customer": msg, "agent": resp,
                        "next_agent": na, "sentiment": sent,
                        "escalation": esc, "slots": mem})
        except Exception as e:
            print(f"     FAIL: {e}")
            break
        time.sleep(0.3)

    out = f"scratch/log_v2_{name.lower().replace(' ','_')}.json"
    with open(out, "w") as f:
        json.dump(log, f, indent=2)
    print(f"\nLog saved → {out}")
    return log

# ─── Scenarios ────────────────────────────────────────────
S1 = [
    "Hi, my friends and I are planning to try an escape room for the first time.",
    "Why do you recommend that room?",
    "Is it difficult?",
    "Can beginners finish it?",
    "Is there parking at the branch?",
    "What about food options?",
    "Can children play in these rooms?",
    "Is it scary?",
    "Do you celebrate birthdays there?",
    "Okay, let's go ahead and book.",
]

S2 = [
    "I want to book tomorrow.",
    "Actually, make it Friday instead.",
    "We want Whitefield.",
    "Wait, change that to Koramangala.",
    "There are 4 of us.",
    "Actually, 6 people.",
    "We'd like the afternoon slot.",
    "Can we switch to evening?",
    "Actually let's do morning.",
    "Okay, let's book.",
]

S3 = [
    "Is there parking?",
    "Is food available?",
    "What is the cancellation policy?",
    "Can we reschedule?",
    "What if we arrive late?",
    "How difficult are the rooms?",
    "Do you host birthday parties?",
    "Do you do corporate events?",
    "How much does it cost?",
    "How long does a game last?",
    "Okay, let's make a booking.",
]

S4 = [
    "Hi, I need help with a booking.",
    "This isn't helping.",
    "You keep repeating yourself.",
    "I already answered that.",
    "I want a human.",
    "Transfer me.",
    "I'm very angry.",
    "I don't want AI.",
    "Connect me right now.",
]

S5 = [
    "I want to book for six people.",
    "Wait, is there parking?",
    "Okay, back to booking.",
    "What food options are there?",
    "Back to booking.",
    "How do I get directions to the venue?",
    "Back to booking.",
    "What room do you recommend?",
    "Can you suggest a different room?",
    "Let's change the date to next Saturday.",
    "How much does it cost?",
    "Okay, let's book it.",
]

if __name__ == "__main__":
    run("Scenario 1", S1)
    run("Scenario 2", S2)
    run("Scenario 3", S3)
    run("Scenario 4", S4)
    run("Scenario 5", S5)
    print("\n\nAll scenarios complete.")
