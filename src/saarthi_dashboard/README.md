# Saarthi Dashboard

Phone dashboard for the Project Saarthi autonomous wheelchair.
A FastAPI WebSocket server runs on the Raspberry Pi; the caregiver's Android
phone connects via USB tethering and opens `http://localhost:8080` in Chrome.

---

## Quick start

### Laptop / mock mode (no ROS needed)

```bash
cd src/saarthi_dashboard
pip install -r requirements.txt
MOCK_ROS=1 uvicorn main:app --host 0.0.0.0 --port 8080
```

Open <http://localhost:8080> in Chrome.
The map shows a fake floor plan; the robot dot circles slowly.

### Raspberry Pi (real ROS 2 mode)

```bash
source /opt/ros/humble/setup.bash
source ~/ros2_ws/install/setup.bash
cd ~/ros2_ws/src/saarthi_dashboard
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8080
```

---

## Phone connection (USB tethering + ADB reverse)

1. Enable **USB tethering** on the Android phone:
   Settings → Network → Hotspot & tethering → USB tethering → ON.
2. The Pi now has internet through the phone, and both share a private network.
3. On a PC/Mac connected to the Pi (or directly on the Pi):
   ```bash
   adb reverse tcp:8080 tcp:8080
   ```
4. On the phone, open Chrome and go to `http://localhost:8080`.

> Why ADB reverse?  It makes port 8080 on the *phone's* localhost tunnel to
> port 8080 on the Pi, so Chrome's microphone permission (which requires a
> secure context) treats the page as localhost — no HTTPS certificate needed.

---

## Setting destination coordinates after mapping

Once you have saved a map with SLAM Toolbox / Nav2:

1. Use RViz's **2D Goal Pose** tool to drive the robot to each room entrance.
2. Note the `x`, `y`, `theta` values shown in the **Goal Pose** panel or read
   from `/amcl_pose`.
3. Edit the `DESTINATIONS` dict in `main.py`:

```python
DESTINATIONS: dict[str, dict] = {
    "kitchen":   {"x":  2.0,  "y":  1.0,  "theta": 0.0},
    "classroom": {"x": -1.5,  "y":  2.0,  "theta": 1.57},
    "bedroom":   {"x":  3.0,  "y": -1.0,  "theta": 3.14},
    "bathroom":  {"x":  0.5,  "y": -2.5,  "theta": -1.57},
}
```

4. Restart the server.

The mock_ros.py file has the same dict — update it too if you want mock
navigation to reach the correct positions.

---

## Testing voice commands

Voice (webkitSpeechRecognition) requires a **secure context**:
- `localhost` counts as secure — ADB reverse ensures the phone hits
  `http://localhost:8080`, so Chrome grants mic access automatically.
- If you serve over the local network IP (e.g. `http://192.168.x.x:8080`)
  Chrome will block the mic on Android. Use ADB reverse instead.

Supported spoken phrases (partial match):
- "Go to the **kitchen**"
- "Take me to the **classroom**"
- "**Bedroom** please"
- "**Bathroom**"

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| Mic permission denied on phone | Use ADB reverse + `http://localhost:8080` (not an IP) |
| Map canvas blank | Check `/map` topic is publishing; mock mode always shows a floor plan |
| WebSocket keeps reconnecting | Ensure `uvicorn` is running on the Pi and ADB reverse is active |
| `rclpy` not found | Source ROS 2 setup.bash before running uvicorn |
| Speech not recognised | Hold the button until you finish speaking; speak clearly in English |

---

## Architecture

```
Android Phone (Chrome)
    │  HTTP GET /          → index.html
    │  ws://localhost:8080/ws
    │  (ADB reverse tunnels to Pi:8080)
    ▼
Raspberry Pi — uvicorn / FastAPI (main.py)
    │
    ├── ros_bridge.py (or mock_ros.py)
    │       subscribes: /map, /amcl_pose, /cmd_vel, /esp32_status
    │       publishes:  /goal_pose, /cmd_vel
    │
    └── ROS 2 Navigation Stack (Nav2 + AMCL + SLAM Toolbox)
```
