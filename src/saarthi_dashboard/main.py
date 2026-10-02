"""
Saarthi Dashboard — FastAPI server running on the Raspberry Pi.
Phone connects via USB tethering; caregiver runs:
    adb reverse tcp:8080 tcp:8080
then opens http://localhost:8080 in Chrome.

Start (mock mode):  MOCK_ROS=1 uvicorn main:app --host 0.0.0.0 --port 8080
Start (real mode):  uvicorn main:app --host 0.0.0.0 --port 8080
"""

import asyncio
import json
import logging
import os
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Select real vs mock ROS bridge
# ---------------------------------------------------------------------------
if os.environ.get("MOCK_ROS", "0") == "1":
    log.info("MOCK_ROS=1 — loading mock_ros bridge (no ROS needed)")
    from mock_ros import RosBridge
else:
    log.info("Loading real ros_bridge (ROS 2 required)")
    from ros_bridge import RosBridge

STATIC_DIR = Path(__file__).parent / "static"

# ---------------------------------------------------------------------------
# Destinations — edit x/y/theta after saving your real map
# ---------------------------------------------------------------------------
DESTINATIONS: dict[str, dict] = {
    # Sim arena coords (saarthi_arena.sdf); update after real map save
    "kitchen":   {"x": 1.5, "y": 0.8, "theta": 0.0},
    "classroom": {"x": 1.5, "y": 4.2, "theta": 0.0},
    "bedroom":   {"x": 5.5, "y": 4.2, "theta": 3.14},
    "bathroom":  {"x": 5.5, "y": 0.8, "theta": -1.57},
}

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------
app = FastAPI(title="Saarthi Dashboard")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

ros_bridge = RosBridge()

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/")
async def serve_index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    log.info("Phone connected via WebSocket")
    try:
        await _handle_ws(ws)
    except WebSocketDisconnect:
        log.info("Phone disconnected")
    except Exception as exc:
        log.error("WebSocket error: %s", exc)


async def _handle_ws(ws: WebSocket) -> None:
    """Send state at 5 Hz; receive commands from the phone."""
    send_task = asyncio.create_task(_state_sender(ws))
    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                log.warning("Received non-JSON message: %s", raw)
                continue
            await _dispatch(msg)
    finally:
        send_task.cancel()
        try:
            await send_task
        except asyncio.CancelledError:
            pass


async def _state_sender(ws: WebSocket) -> None:
    """Push robot state to the phone every 200 ms."""
    while True:
        state = ros_bridge.get_state()
        payload = {
            "type": "state",
            "pose": state["pose"],
            "speed": state["speed"],
            "status": state["status"],
            "map_png": state["map_png"],
            "map_meta": state.get("map_meta", {
                "origin_x": 0.0,
                "origin_y": 0.0,
                "resolution": 0.05,
                "width": 200,
                "height": 200,
            }),
        }
        await ws.send_text(json.dumps(payload))
        await asyncio.sleep(0.2)


async def _dispatch(msg: dict) -> None:
    """Route an incoming phone message to the appropriate ROS action."""
    mtype = msg.get("type")

    if mtype == "goal":
        dest = msg.get("destination", "").lower()
        coords = DESTINATIONS.get(dest)
        if coords is None:
            log.warning("Unknown destination: %s", dest)
            return
        log.info("Goal → %s  %s", dest, coords)
        await asyncio.get_event_loop().run_in_executor(
            None, ros_bridge.send_goal, dest, coords
        )

    elif mtype == "stop":
        log.info("STOP command received")
        await asyncio.get_event_loop().run_in_executor(
            None, ros_bridge.send_stop
        )

    elif mtype == "cmd_vel":
        linear = float(msg.get("linear", 0.0))
        angular = float(msg.get("angular", 0.0))
        await asyncio.get_event_loop().run_in_executor(
            None, ros_bridge.send_cmd_vel, linear, angular
        )

    else:
        log.warning("Unknown message type: %s", mtype)
