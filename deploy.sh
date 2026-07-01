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

echo "=== 5. Updating Vapi Webhook URLs ==="
python3 -c "
import os, json, urllib.request, urllib.error
try:
    req = urllib.request.Request('http://127.0.0.1:4040/api/tunnels')
    with urllib.request.urlopen(req) as resp:
        tunnels = json.loads(resp.read().decode())['tunnels']
        ngrok_url = [t['public_url'] for t in tunnels if t['proto'] == 'https'][0]
except Exception:
    ngrok_url = None

if ngrok_url:
    env = {}
    if os.path.exists('.env'):
        with open('.env') as f:
            for line in f:
                if '=' in line and not line.strip().startswith('#'):
                    k, v = line.strip().split('=', 1)
                    env[k.strip()] = v.strip()
    api_key = env.get('VAPI_API_KEY')
    assistant_id = env.get('VAPI_ASSISTANT_ID')
    tool_id = env.get('VAPI_TOOL_ID')
    if api_key:
        headers = {'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'}
        if assistant_id:
            try:
                req = urllib.request.Request(f'https://api.vapi.ai/assistant/{assistant_id}', data=json.dumps({'serverUrl': f'{ngrok_url}/vapi/webhook'}).encode(), headers=headers, method='PATCH')
                with urllib.request.urlopen(req):
                    print('Vapi Assistant webhook updated to:', ngrok_url)
            except Exception:
                print('Note: Could not auto-patch Vapi Assistant webhook (check key/ID)')
        if tool_id:
            try:
                req = urllib.request.Request(f'https://api.vapi.ai/tool/{tool_id}', data=json.dumps({'server': {'url': f'{ngrok_url}/vapi/tool'}}).encode(), headers=headers, method='PATCH')
                with urllib.request.urlopen(req):
                    print('Vapi Tool webhook updated to:', ngrok_url)
            except Exception:
                print('Note: Could not auto-patch Vapi Tool webhook (check key/ID)')
" || true

echo "============================================="
echo "Services deployed successfully via Ngrok!"
echo "Uvicorn logs: tail -f /tmp/uvicorn.log"
echo "Ngrok logs: tail -f /tmp/ngrok.log"
echo "Public Tunnel URL: $TUNNEL_URL"
echo "============================================="
