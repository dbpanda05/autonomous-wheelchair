"""
mock_ros.py — Drop-in replacement for ros_bridge.py.

Works WITHOUT ROS installed. Use for testing the dashboard UI on a laptop.

Usage:
    MOCK_ROS=1 uvicorn main:app --host 0.0.0.0 --port 8080

The RosBridge class has exactly the same interface as the real ros_bridge.RosBridge.
"""

import base64
import io
import logging
import math
import threading
import time
from typing import Any

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Map generation
# ---------------------------------------------------------------------------

def _make_fake_map_png() -> tuple[str, dict]:
    """
    Generate a simple occupancy-grid-style floor plan (rooms as rectangles)
    and return (base64_png_string, map_meta_dict).
    """
    try:
        import numpy as np
        from PIL import Image, ImageDraw
    except ImportError:
        log.warning("numpy/Pillow not available — map will be empty")
        return "", {}

    W, H = 200, 200          # pixels == grid cells
    RESOLUTION = 0.05        # metres per cell
    ORIGIN_X = -5.0
    ORIGIN_Y = -5.0

    # Start with unknown (128 = mid-grey)
    arr = np.full((H, W), 128, dtype=np.uint8)

    # Paint free space (230 = light grey) for several rooms
    rooms = [
        (20, 20, 90, 90),    # kitchen / living area
        (100, 20, 180, 90),  # classroom
        (20, 100, 90, 180),  # bedroom
        (100, 100, 180, 180),# bathroom
    ]
    for x0, y0, x1, y1 in rooms:
        arr[y0:y1, x0:x1] = 230

    # Paint walls (0 = black) — outer border + room walls
    img = Image.fromarray(arr, mode="L")
    draw = ImageDraw.Draw(img)

    # Outer walls
    draw.rectangle([19, 19, 90, 90],   outline=0, width=2)
    draw.rectangle([99, 19, 180, 90],  outline=0, width=2)
    draw.rectangle([19, 99, 90, 180],  outline=0, width=2)
    draw.rectangle([99, 99, 180, 180], outline=0, width=2)

    # Doorways (3-cell gaps on inner walls — just leave them open)
    # Corridor connecting rooms
    arr[90:100, 40:160] = 230   # horizontal corridor
    arr[40:160, 90:100] = 230   # vertical corridor
    img = Image.fromarray(arr, mode="L")

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")

    meta = {
        "origin_x":  ORIGIN_X,
        "origin_y":  ORIGIN_Y,
        "resolution": RESOLUTION,
        "width":     W,
        "height":    H,
    }
    return b64, meta


# ---------------------------------------------------------------------------
# Destination coordinates (mirrors DESTINATIONS in main.py)
# ---------------------------------------------------------------------------
_DESTINATIONS: dict[str, dict] = {
    "kitchen":   {"x":  2.0,  "y":  1.0,  "theta": 0.0},
    "classroom": {"x": -1.5,  "y":  2.0,  "theta": 1.57},
    "bedroom":   {"x":  3.0,  "y": -1.0,  "theta": 3.14},
    "bathroom":  {"x":  0.5,  "y": -2.5,  "theta": -1.57},
}

_RADIUS = 1.5          # metres — radius of idle circular path
_CIRCLE_PERIOD = 20.0  # seconds for one full idle circle


class RosBridge:
    """Mock RosBridge — same public interface as ros_bridge.RosBridge."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._start_time = time.time()

        map_png, map_meta = _make_fake_map_png()
        self._map_png = map_png
        self._map_meta = map_meta if map_meta else {
            "origin_x":   0.0,
            "origin_y":   0.0,
            "resolution": 0.05,
            "width":      200,
            "height":     200,
        }

        self._pose   = {"x": 0.0, "y": 0.0, "theta": 0.0}
        self._speed  = 0.0
        self._status = "ok"

        # Navigation state
        self._goal_pose: dict | None = None
        self._navigating = False

        t = threading.Thread(target=self._sim_loop, daemon=True, name="mock_sim")
        t.start()
        log.info("MockRosBridge started")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_state(self) -> dict[str, Any]:
        with self._lock:
            return {
                "pose":    dict(self._pose),
                "speed":   self._speed,
                "status":  self._status,
                "map_png": self._map_png,
                "map_meta": dict(self._map_meta),
            }

    def send_goal(self, name: str, coords: dict) -> None:
        log.info("[MOCK] Goal → %s  %s", name, coords)
        with self._lock:
            self._goal_pose = dict(coords)
            self._navigating = True
            self._status = "ok"
            self._speed = 0.3

    def send_stop(self) -> None:
        log.info("[MOCK] STOP")
        with self._lock:
            self._navigating = False
            self._goal_pose = None
            self._speed = 0.0
            self._status = "estop"

    def send_cmd_vel(self, linear: float, angular: float) -> None:
        log.info("[MOCK] cmd_vel linear=%.2f angular=%.2f", linear, angular)
        with self._lock:
            self._speed = linear

    # ------------------------------------------------------------------
    # Simulation loop
    # ------------------------------------------------------------------

    def _sim_loop(self) -> None:
        """Update fake robot pose at ~10 Hz."""
        while True:
            time.sleep(0.1)
            self._tick()

    def _tick(self) -> None:
        with self._lock:
            if self._navigating and self._goal_pose is not None:
                # Step toward goal
                gx = self._goal_pose["x"]
                gy = self._goal_pose["y"]
                dx = gx - self._pose["x"]
                dy = gy - self._pose["y"]
                dist = math.hypot(dx, dy)
                if dist < 0.05:
                    # Arrived
                    self._pose = {"x": gx, "y": gy, "theta": self._goal_pose.get("theta", 0.0)}
                    self._navigating = False
                    self._speed = 0.0
                    self._goal_pose = None
                else:
                    step = min(0.03, dist)
                    theta = math.atan2(dy, dx)
                    self._pose["x"] += step * math.cos(theta)
                    self._pose["y"] += step * math.sin(theta)
                    self._pose["theta"] = theta
                    self._speed = 0.3
            elif self._status != "estop":
                # Idle: slow circle
                t = time.time() - self._start_time
                angle = (2.0 * math.pi * t) / _CIRCLE_PERIOD
                self._pose = {
                    "x":     _RADIUS * math.cos(angle),
                    "y":     _RADIUS * math.sin(angle),
                    "theta": angle + math.pi / 2.0,
                }
                self._speed = 0.1
