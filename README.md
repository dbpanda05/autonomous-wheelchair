# Project Saarthi — Autonomous Assistive Wheelchair

An autonomous wheelchair for children with motor and cognitive impairments. A caregiver taps a destination on a phone dashboard; the wheelchair navigates there independently using onboard SLAM and obstacle avoidance.

---

## Architecture

```
┌─────────────────────────┐        USB tethering / Wi-Fi
│   Android Phone          │◄──────────────────────────────┐
│   (Saarthi Dashboard)    │   WebSocket  ws://localhost:8080│
└─────────────────────────┘                                 │
                                                            │
                                              ┌─────────────▼──────────────┐
                                              │  Raspberry Pi 5             │
                                              │  ROS2 Jazzy                 │
                                              │  ├─ serial_bridge_node      │
                                              │  ├─ SLAM Toolbox / Nav2     │
                                              │  ├─ robot_state_publisher   │
                                              │  └─ saarthi_dashboard       │
                                              └─────────────┬──────────────┘
                                                            │ UART 115200 baud
                                              ┌─────────────▼──────────────┐
                                              │  ESP32-WROOM-32             │
                                              │  ├─ DAC motor control       │
                                              │  ├─ Hall sensor odometry    │
                                              │  └─ Watchdog / E-Stop       │
                                              └────────────────────────────┘
```

---

## Repository Structure

```
autonomous-wheelchair/
├── README.md                          ← you are here
└── src/
    ├── esp32_firmware/                ← motor controller (Arduino C)
    ├── saarthi_dashboard/             ← caregiver phone UI (FastAPI + WebSocket)
    └── wheelchair_navigation/         ← ROS2 navigation package (Raspberry Pi)
```

---

## `src/esp32_firmware/`

Runs on the **ESP32-WROOM-32**. Flashed via Arduino IDE or PlatformIO.

| File | Purpose |
|------|---------|
| `saarthi.ino` | Main firmware — motor control loop, odometry, serial protocol, watchdog |
| `config.h` | All pin assignments and tunable constants in one place |

### Pin Map (`config.h`)

| Constant | GPIO | Role |
|----------|------|------|
| `DAC_LEFT` / `DAC_RIGHT` | 25 / 26 | Analog speed signal to motor controllers |
| `DIR_LEFT` / `DIR_RIGHT` | 27 / 12 | Direction signal (via optocoupler) |
| `BRAKE_LEFT` / `BRAKE_RIGHT` | 14 / 13 | Brake signal (via optocoupler) |
| `HALL_LEFT_A/B` | 34 / 35 | Left wheel Hall encoder (level-shifted to 3.3 V) |
| `HALL_RIGHT_A/B` | 33 / 36 | Right wheel Hall encoder |
| `ESTOP_PIN` | 4 | Active-LOW emergency stop button |
| `BUZZER_PIN` | 2 | Audio feedback buzzer |
| `PI_SERIAL` (Serial2) | GPIO 16 RX / 17 TX | UART link to Raspberry Pi |

### Serial Protocol (115200 baud)

**Pi → ESP32**
```
V,<linear_m_s>,<angular_rad_s>,<seq>\n   @ 20 Hz  — velocity command
H,<seq>\n                                 @ 10 Hz  — heartbeat (resets watchdog)
```

**ESP32 → Pi**
```
O,<left_ticks>,<right_ticks>,<dt_ms>\n   @ 50 Hz  — wheel odometry
S,<estop>,<cliff>,<batt_mv>\n            @ 10 Hz  — system status
```

### Key Constants to Calibrate

| Constant | Default | How to calibrate |
|----------|---------|-----------------|
| `TICKS_PER_REV` | 15 | Mark a wheel, spin one full turn, count Hall pulses |
| `MAX_RPM` | 120 | Set to your motor's no-load speed |
| `WATCHDOG_MS` | 300 | Time without heartbeat before E-Stop triggers |

---

## `src/saarthi_dashboard/`

A **FastAPI** server that runs on the Raspberry Pi. The phone connects to it over USB tethering or Wi-Fi, receives live robot state over WebSocket, and sends navigation goals.

| File | Purpose |
|------|---------|
| `main.py` | FastAPI app — WebSocket endpoint, goal dispatch, 5 Hz state push |
| `ros_bridge.py` | rclpy node running in a background thread; subscribes to ROS2 topics and publishes goals |
| `mock_ros.py` | Drop-in replacement for `ros_bridge.py` when `MOCK_ROS=1`; simulates realistic state without ROS |
| `requirements.txt` | Python dependencies (`fastapi`, `uvicorn`, `pillow`, `numpy`) |
| `static/index.html` | Phone UI — dark Tailwind design, live map canvas, 4 destination buttons, voice input, emergency stop |
| `static/app.js` | WebSocket client — state rendering, map drawing, voice recognition, speech synthesis |

### ROS2 Topics

| Topic | Direction | Message Type | Purpose |
|-------|-----------|-------------|---------|
| `/map` | Subscribe | `nav_msgs/OccupancyGrid` | Live SLAM map → rendered as PNG on the phone |
| `/amcl_pose` | Subscribe | `geometry_msgs/PoseWithCovarianceStamped` | Robot position in navigation mode |
| `/cmd_vel` | Subscribe | `geometry_msgs/Twist` | Speed readout for the dashboard |
| `/esp32_status` | Subscribe | `std_msgs/String` (JSON) | E-Stop / cliff / battery status |
| `/cmd_vel` | Publish | `geometry_msgs/Twist` | Manual velocity from dashboard (future) |
| `/goal_pose` | Publish | `geometry_msgs/PoseStamped` | Navigation goal sent to Nav2 |

### Destination Coordinates

Defined in `main.py` — update these after saving your real map:

```python
DESTINATIONS = {
    "kitchen":   {"x": ..., "y": ..., "theta": ...},
    "classroom": {"x": ..., "y": ..., "theta": ...},
    "bedroom":   {"x": ..., "y": ..., "theta": ...},
    "bathroom":  {"x": ..., "y": ..., "theta": ...},
}
```

### Running the Dashboard

```bash
# On the Raspberry Pi (real hardware):
cd src/saarthi_dashboard
pip install -r requirements.txt
python3 -m uvicorn main:app --host 0.0.0.0 --port 8080

# On Windows / any machine without ROS (mock mode):
MOCK_ROS=1 python3 -m uvicorn main:app --host 0.0.0.0 --port 8080
# PowerShell: $env:MOCK_ROS="1"; python -m uvicorn main:app --host 0.0.0.0 --port 8080

# Phone: open http://<pi-ip>:8080  (or use adb reverse tcp:8080 tcp:8080 for USB)
```

---

## `src/wheelchair_navigation/`

A **ROS2 Jazzy** package (`ament_cmake`) that runs on the Raspberry Pi. Handles SLAM, navigation, hardware I/O, and robot description.

```
wheelchair_navigation/
├── CMakeLists.txt
├── package.xml
├── config/
│   ├── mapper_params_online.yaml    ← SLAM Toolbox tuning
│   └── nav2_params.yaml             ← Nav2 stack parameters
├── launch/
│   └── bringup.launch.py            ← main Pi launch file
├── urdf/
│   └── wheelchair.urdf              ← robot geometry + Gazebo extensions
├── worlds/                          ← Gazebo simulation worlds (sim branch only)
│   └── saarthi_arena.sdf
└── wheelchair_navigation/
    ├── __init__.py
    └── serial_bridge_node.py        ← Pi ↔ ESP32 UART bridge node
```

### Key Files

#### `launch/bringup.launch.py`
The **main launch file** for the real robot. Always starts:
- `robot_state_publisher` — broadcasts URDF joint transforms
- `sllidar_ros2` — drives the RPLidar A1 sensor → `/scan`

Conditionally adds:
- `serial_bridge_node` — when `use_hardware:=true` (default)
- SLAM Toolbox — when `mode:=mapping` (default)
- Nav2 + AMCL — when `mode:=navigation map:=<path>`

#### `wheelchair_navigation/serial_bridge_node.py`
Translates between ROS2 and the ESP32 serial protocol:
- Subscribes to `/cmd_vel` → sends `V,` commands at 20 Hz
- Sends `H,` heartbeat at 10 Hz to keep the ESP32 watchdog alive
- Reads `O,` odometry lines at 50 Hz → publishes `/odom` + `odom→base_link` TF
- Reads `S,` status lines → publishes `/esp32_status`

#### `urdf/wheelchair.urdf`
Defines the robot geometry (links, joints, inertias). The Gazebo plugin section at the bottom is **ignored on real hardware** — only used in simulation.

Key dimensions:
- Wheel radius: **0.127 m**
- Wheel separation: **0.738 m**
- Base height from ground: **0.3262 m**

#### `config/mapper_params_online.yaml`
SLAM Toolbox configuration:
- Resolution: 0.05 m/cell
- Max laser range: 12 m
- Map update interval: 5 s
- Base frame: `base_link`, Odom frame: `odom`

#### `config/nav2_params.yaml`
Nav2 stack parameters including DWB local planner, AMCL, costmap inflation radius (0.3 m), and lifecycle manager config.

---

## Raspberry Pi Setup

### 1. Install ROS2 Jazzy

```bash
sudo apt update && sudo apt install -y software-properties-common
sudo add-apt-repository universe
sudo curl -sSL https://raw.githubusercontent.com/ros/rosdistro/master/ros.key \
  -o /usr/share/keyrings/ros-archive-keyring.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/ros-archive-keyring.gpg] \
  http://packages.ros.org/ros2/ubuntu $(. /etc/os-release && echo $UBUNTU_CODENAME) main" \
  | sudo tee /etc/apt/sources.list.d/ros2.list
sudo apt update
sudo apt install -y ros-jazzy-desktop ros-jazzy-slam-toolbox ros-jazzy-nav2-bringup \
  ros-jazzy-sllidar-ros2 python3-colcon-common-extensions python3-pip
echo "source /opt/ros/jazzy/setup.bash" >> ~/.bashrc
source ~/.bashrc
```

### 2. Build the workspace

```bash
mkdir -p ~/saarthi_ws/src
cd ~/saarthi_ws/src
git clone https://github.com/dbpanda05/autonomous-wheelchair.git
cp -r autonomous-wheelchair/src/wheelchair_navigation .
cd ~/saarthi_ws
colcon build --packages-select wheelchair_navigation
source install/setup.bash
```

### 3. Flash the ESP32

Open `src/esp32_firmware/saarthi.ino` in Arduino IDE.  
Install board: **ESP32 by Espressif** via Boards Manager.  
Select board: **ESP32 Dev Module**, Port: `/dev/ttyUSB0`.  
Calibrate `TICKS_PER_REV` in `config.h` before first run.

### 4. Run — Mapping mode (first time)

```bash
# Terminal 1: navigation stack
ros2 launch wheelchair_navigation bringup.launch.py mode:=mapping

# Terminal 2: dashboard
cd ~/saarthi_dashboard
python3 -m uvicorn main:app --host 0.0.0.0 --port 8080

# Drive the wheelchair around the full space using the dashboard or teleop
ros2 run teleop_twist_keyboard teleop_twist_keyboard

# Save the map when done
ros2 run nav2_map_server map_saver_cli -f ~/saarthi_map
```

### 5. Run — Navigation mode (daily use)

```bash
ros2 launch wheelchair_navigation bringup.launch.py \
  mode:=navigation \
  map:=/home/pi/saarthi_map.yaml

cd ~/saarthi_dashboard
python3 -m uvicorn main:app --host 0.0.0.0 --port 8080
```

---

## Robot Geometry Reference

| Parameter | Value |
|-----------|-------|
| Wheel radius | 0.127 m |
| Wheel separation (centre-to-centre) | 0.738 m |
| Caster wheel radius | 0.0375 m |
| Max linear speed | ~0.5 m/s |
| Lidar model | RPLidar A1 (360°, 12 m range) |
| Baud rate (Pi ↔ ESP32) | 115200 |

---

## Branches

| Branch | Purpose |
|--------|---------|
| `main` | Production code — deploy this to the Raspberry Pi |
| `simulation` | Gazebo Harmonic simulation — test on a PC without hardware. See the [simulation README](https://github.com/dbpanda05/autonomous-wheelchair/blob/simulation/README.md) |
