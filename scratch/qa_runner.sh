#!/usr/bin/env bash
# QA conversation runner — sends messages sequentially and captures responses
# Usage: bash qa_runner.sh <session_id> "message1" "message2" ...

BASE="http://localhost:8000"
SESSION="$1"; shift
TURN=0

chat() {
  TURN=$((TURN+1))
  local msg="$1"
  echo ""
  echo "──────────────────────────────────────────────"
  echo "TURN $TURN | CUSTOMER: $msg"
  echo "──────────────────────────────────────────────"
  RESPONSE=$(curl -s -X POST "$BASE/chat" \
    -H "Content-Type: application/json" \
    -d "{\"session_id\":\"$SESSION\",\"message\":$(echo "$msg" | python3 -c 'import sys,json; print(json.dumps(sys.stdin.read().rstrip()))')}" )

  AGENT_REPLY=$(echo "$RESPONSE" | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d.get("response","[NO RESPONSE]"))')
  NEXT_AGENT=$(echo "$RESPONSE" | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d.get("next_agent","?"))')
  ESC=$(echo "$RESPONSE" | python3 -c 'import sys,json; d=json.load(sys.stdin); e=d.get("escalation",{}); print(e.get("escalate","?") if isinstance(e,dict) else "?")')
  SENTIMENT=$(echo "$RESPONSE" | python3 -c 'import sys,json; d=json.load(sys.stdin); s=d.get("sentiment_analysis",{}); print(s.get("sentiment","?") if isinstance(s,dict) else "?")')
  MEM_LOC=$(echo "$RESPONSE" | python3 -c 'import sys,json; d=json.load(sys.stdin); cs=d.get("conversation_summary",{}); print(cs.get("venue","?") if isinstance(cs,dict) else "?")')
  MEM_ROOM=$(echo "$RESPONSE" | python3 -c 'import sys,json; d=json.load(sys.stdin); cs=d.get("conversation_summary",{}); print(cs.get("room","?") if isinstance(cs,dict) else "?")')

  echo "AGENT: $AGENT_REPLY"
  echo "[next_agent=$NEXT_AGENT | escalation=$ESC | sentiment=$SENTIMENT | mem_location=$MEM_LOC | mem_room=$MEM_ROOM]"
}

for msg in "$@"; do
  chat "$msg"
  sleep 1
done
