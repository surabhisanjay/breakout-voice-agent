from fastapi import FastAPI, Request, Body, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from backend.app.routers.analytics import router as analytics_router
from src.analytics.db import init_db
from src.analytics.websocket_server import ws_server, CLIENTS
from src.config.env_loader import load_project_env
from pathlib import Path
from typing import Any
import asyncio

# Load environment configurations
BASE_DIR = Path(__file__).resolve().parents[2]
load_project_env(BASE_DIR)

app = FastAPI(
    title="Voice CRM / Sales OS Production API",
    description="Enterprise API engine powering realtime call processing and dashboards.",
    version="1.0.0"
)

# Enable CORS for the Vite frontend client
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(analytics_router)

# Native WebSocket endpoint — served through port 8010 (works through any tunnel)
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """Accepts WebSocket connections and joins the ws_server broadcast pool."""
    await websocket.accept()
    # Register this native FastAPI WS client in the shared CLIENTS set
    # We wrap it in an adapter so ws_server.broadcast() can use .send()
    class _WSAdapter:
        def __init__(self, ws):
            self._ws = ws
        async def send(self, data):
            await self._ws.send_text(data)
        @property
        def remote_address(self):
            client = self._ws.client
            return (client.host, client.port) if client else ("unknown", 0)

    adapter = _WSAdapter(websocket)
    CLIENTS.add(adapter)
    import logging
    logging.getLogger(__name__).info(
        "Dashboard WS client connected via /ws: %s", websocket.client
    )
    try:
        while True:
            # Keep connection alive; we only push — no inbound messages expected
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        CLIENTS.discard(adapter)
        logging.getLogger(__name__).info(
            "Dashboard WS client disconnected: %s", websocket.client
        )


# Health endpoint (must be registered before frontend catch-all)
@app.get("/health")
def health():
    return {"status": "healthy", "service": "FastAPI Backend Engine"}

# Agent/Vapi integration route forwarding
@app.post("/vapi/webhook")
async def forward_vapi_webhook(request: Request):
    from app import vapi_webhook
    return await vapi_webhook(request)

@app.post("/vapi/tool")
async def forward_vapi_tool(request: Request):
    from app import vapi_tool
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    return await vapi_tool(payload)

@app.post("/vapi/llm/chat/completions")
async def forward_vapi_custom_llm(request: Request):
    from app import vapi_custom_llm
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    return await vapi_custom_llm(payload)

@app.get("/whatsapp/health")
def forward_whatsapp_health():
    from app import whatsapp_health
    return whatsapp_health()

@app.post("/whatsapp/messages")
async def forward_whatsapp_message(request: Request):
    from app import WhatsAppMessageRequest, whatsapp_message
    payload = await request.json()
    return whatsapp_message(
        WhatsAppMessageRequest(**payload),
        x_whatsapp_api_key=request.headers.get("x-whatsapp-api-key", ""),
    )

@app.post("/wati/webhook")
async def forward_wati_webhook(request: Request):
    from app import wati_webhook
    return await wati_webhook(request)

@app.get("/vapi/events")
def forward_vapi_events(limit: int = 50):
    from app import vapi_events
    return vapi_events(limit)

@app.get("/vapi/calls/live")
def forward_vapi_live_calls():
    from app import vapi_live_calls
    return vapi_live_calls()

@app.get("/vapi/diagnostics")
def forward_vapi_diagnostics():
    from app import vapi_diagnostics
    return vapi_diagnostics()

@app.post("/chat")
async def forward_chat(request: Request):
    from app import chat, ChatRequest
    body = await request.json()
    return chat(ChatRequest(**body))

@app.post("/reset")
async def forward_reset(request: Request):
    from app import reset, ResetRequest
    body = await request.json()
    return reset(ResetRequest(**body))

@app.get("/memory/{session_id}")
def forward_memory(session_id: str):
    from app import memory
    return memory(session_id)

@app.get("/metrics/{session_id}")
def forward_metrics(session_id: str):
    from app import metrics
    return metrics(session_id)

@app.api_route("/observability", methods=["GET", "POST"])
async def forward_observability(request: Request):
    from app import observability
    return await observability(request)

# Mount frontend assets
import os
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

FRONTEND_DIST = str(BASE_DIR / "frontend" / "dist")
if os.path.exists(FRONTEND_DIST):
    app.mount("/assets", StaticFiles(directory=os.path.join(FRONTEND_DIST, "assets")), name="assets")

    @app.get("/{catchall:path}")
    def serve_frontend(catchall: str):
        if catchall.startswith("api/") or catchall.startswith("vapi/") or catchall.startswith("chat") or catchall.startswith("reset") or catchall.startswith("memory/") or catchall.startswith("metrics/"):
            return None # FastAPI routes take care of API
        return FileResponse(os.path.join(FRONTEND_DIST, "index.html"))

@app.on_event("startup")
async def startup_event():
    """Initialize SQLite and bind broadcasts to FastAPI's native event loop."""
    init_db()
    ws_server.attach_loop(asyncio.get_running_loop())

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.app.main:app", host="127.0.0.1", port=8010, reload=True)
