#!/bin/bash
# Closira Voice CRM Deploy Script

echo "=== 1. Building Vite React Frontend Assets ==="
cd frontend && npm run build
cd ..

echo "=== 2. Terminating Existing Services on Port 8010 ==="
PID=$(lsof -t -i:8010)
if [ ! -z "$PID" ]; then
  echo "Killing process $PID on port 8010"
  kill -9 $PID
fi
echo "Killing background tunnels targeting port 8010"
pkill -f "cloudflared.*8010" || true
pkill -f "ngrok.*8010" || true
sleep 1

echo "=== 3. Starting FastAPI Uvicorn Server ==="
python -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8010 > /tmp/uvicorn.log 2>&1 &

echo "=== 4. Starting Ngrok Tunnel ==="
ngrok http 8010 > /tmp/ngrok.log 2>&1 &

echo "Waiting for tunnel initialization..."
sleep 4

TUNNEL_URL=$(python3 -c "import urllib.request, json; print(json.loads(urllib.request.urlopen('http://127.0.0.1:4040/api/tunnels').read().decode())['tunnels'][0]['public_url'])" 2>/dev/null || echo "")

echo "============================================="
echo "Services deployed successfully via Ngrok!"
echo "Uvicorn logs: tail -f /tmp/uvicorn.log"
echo "Ngrok logs: tail -f /tmp/ngrok.log"
echo "Public Tunnel URL: $TUNNEL_URL"
echo "============================================="
