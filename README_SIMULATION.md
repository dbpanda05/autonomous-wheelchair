# Project Saarthi — Simulation Guide (`simulation` branch)

This branch extends `main` with a full **Gazebo Harmonic** simulation so you can develop and test the entire software stack — SLAM, Nav2, dashboard — without any physical hardware.

> **Pi deployment:** use the `main` branch. This branch is for PC-based simulation only.

---

## What the Simulation Adds

| Added in `simulation` | Not in `main` |
|-----------------------|---------------|
| `launch/sim_launch.py` | Gazebo launch, bridge, robot spawner |
| `worlds/saarthi_arena.sdf` | 7 × 5 m test arena with 4 labelled rooms |
| URDF Gazebo plugins | Diff-drive, lidar sensor, joint state publisher |
| Static TF alias | Bridges Gazebo-scoped frame names to URDF names |
| `ros_bridge.py` `/pose` + `/odom` subscriptions | Handles SLAM pose instead of AMCL |
| Sim arena destination coords in `main.py` | Matches the SDF world |

---

## System Requirements

- **OS:** Windows 11 with WSL2 (Ubuntu 24.04) — or native Ubuntu 24.04
- **RAM:** 8 GB minimum, 16 GB recommended
- **Disk:** ~5 GB free in WSL2
- **Display:** Gazebo GUI renders headlessly in WSL2 (no GPU required for logic testing); for the visual sim, WSLg or X11 forwarding is needed

---

## One-Time Setup (WSL2)

### 1. Fresh WSL2 Ubuntu 24.04

```powershell
# PowerShell (Windows)
wsl --install -d Ubuntu-24.04
```

### 2. Add ROS2 Jazzy + Gazebo Harmonic apt repo

```bash
# Inside WSL2 (run as root or with sudo)
apt update && apt install -y software-properties-common curl gnupg lsb-release
add-apt-repository universe -y

curl -sSL https://raw.githubusercontent.com/ros/rosdistro/master/ros.key \
  -o /usr/share/keyrings/ros-archive-keyring.gpg

echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/ros-archive-keyring.gpg] \
  http://packages.ros.org/ros2/ubuntu $(. /etc/os-release && echo $UBUNTU_CODENAME) main" \
  | tee /etc/apt/sources.list.d/ros2.list

apt update
```

### 3. Install packages

```bash
DEBIAN_FRONTEND=noninteractive apt install -y \
  ros-jazzy-desktop \
  ros-jazzy-slam-toolbox \
  ros-jazzy-nav2-bringup \
  ros-jazzy-ros-gz \
  ros-jazzy-ros-gz-bridge \
  ros-jazzy-teleop-twist-keyboard \
  python3-colcon-common-extensions \
  python3-pip \
  tmux

echo "source /opt/ros/jazzy/setup.bash" >> ~/.bashrc
source ~/.bashrc
```

### 4. Build the workspace

```bash
mkdir -p ~/saarthi_ws/src
cd ~/saarthi_ws/src

git clone -b simulation https://github.com/dbpanda05/autonomous-wheelchair.git
cp -r autonomous-wheelchair/src/wheelchair_navigation .

cd ~/saarthi_ws
colcon build --packages-select wheelchair_navigation
source install/setup.bash
echo "source ~/saarthi_ws/install/setup.bash" >> ~/.bashrc
```

### 5. Install dashboard Python deps

```bash
pip install fastapi uvicorn websockets pillow numpy --break-system-packages --ignore-installed typing-extensions
cp -r ~/saarthi_ws/src/autonomous-wheelchair/src/saarthi_dashboard ~/saarthi_dashboard
```

---

## Running the Simulation

You need **two terminal sessions** — use tmux or two separate WSL2 windows.

### Session 1 — Gazebo + SLAM

```bash
source /opt/ros/jazzy/setup.bash
source ~/saarthi_ws/install/setup.bash
ros2 launch wheelchair_navigation sim_launch.py
```

Wait ~10 seconds for Gazebo to load and the robot to spawn.  
You will see log lines like:
```
[ros_gz_bridge]: Creating GZ->ROS Bridge: [/scan ...]
[slam_toolbox]: Activating
[ros_gz_sim]: Entity creation successful.
```

### Session 2 — Dashboard

```bash
source /opt/ros/jazzy/setup.bash
source ~/saarthi_ws/install/setup.bash
cd ~/saarthi_dashboard
python3 -m uvicorn main:app --host 0.0.0.0 --port 8080
```

### Browser

Open **http://localhost:8080** in Chrome on Windows.  
WSL2 automatically forwards the port — no extra config needed.

You should see:
- **Green "Ready" pill** — dashboard WebSocket connected
- **Speed readout** — live from Gazebo odometry
- **Live Map** — builds as you drive (starts blank, fills in progressively)

---

## Driving the Robot

### Option A — Teleop keyboard (WSL2 terminal)

```bash
source /opt/ros/jazzy/setup.bash && source ~/saarthi_ws/install/setup.bash
ros2 run teleop_twist_keyboard teleop_twist_keyboard
```

| Key | Action |
|-----|--------|
| `i` | Forward |
| `,` | Backward |
| `j` | Turn left |
| `l` | Turn right |
| `k` | Stop |
| `u` / `o` | Forward-left / forward-right arc |
| `q` / `z` | Increase / decrease max speed |

### Option B — Single velocity command

```bash
# Drive forward 0.3 m/s for 3 seconds
ros2 topic pub --times 30 /cmd_vel geometry_msgs/msg/Twist \
  '{linear: {x: 0.3}, angular: {z: 0.0}}' --rate 10

# Turn in place
ros2 topic pub --times 20 /cmd_vel geometry_msgs/msg/Twist \
  '{linear: {x: 0.0}, angular: {z: 0.5}}' --rate 10
```

### Option C — Dashboard destination buttons

Once a map is built, tap **Kitchen / Classroom / Bedroom / Bathroom** in the browser. This publishes a `/goal_pose` to Nav2. *Requires navigation mode — see below.*

---

## Simulation Modes

### Mapping mode (default)

Builds a map of the arena as you drive. SLAM Toolbox runs online.

```bash
ros2 launch wheelchair_navigation sim_launch.py
# (mode:=mapping is the default)
```

Drive the robot around the full arena. Watch the map fill in on the dashboard. When the map looks complete:

```bash
ros2 run nav2_map_server map_saver_cli -f ~/saarthi_map
# Creates: ~/saarthi_map.pgm  and  ~/saarthi_map.yaml
```

### Navigation mode

Uses a saved map + AMCL localisation + full Nav2 path planning. The dashboard destination buttons work in this mode.

```bash
ros2 launch wheelchair_navigation sim_launch.py \
  mode:=navigation \
  map:=/root/saarthi_map.yaml
```

Then tap a destination in the browser — Nav2 plans a path and drives the robot autonomously.

---

## What to Test

### ✅ Sensor pipeline
```bash
# Confirm lidar is flowing
ros2 topic hz /scan          # should be ~10 Hz
ros2 topic hz /odom          # should be ~50 Hz
ros2 topic echo /odom --once # check position values make sense
```

### ✅ TF tree integrity
```bash
ros2 run tf2_tools view_frames
# Open /tmp/frames.pdf — should show:
# map → odom → base_footprint → base_link → lidar_link
```

### ✅ SLAM map building
```bash
ros2 topic hz /map           # publishes every ~5 s when robot moves
ros2 topic echo /map --once  # check info.width / height grow as you explore
```

### ✅ cmd_vel → robot response
```bash
# Send one forward burst, then check odom moved
ros2 topic pub --once /cmd_vel geometry_msgs/msg/Twist '{linear: {x: 0.2}}'
sleep 1
ros2 topic echo /odom --once | grep "position"
# x should be ~0.2 m
```

### ✅ Dashboard end-to-end
1. Open http://localhost:8080
2. Status pill shows **Ready** (green)
3. Drive with teleop — speed counter updates
4. SLAM map renders on the dashboard Live Map panel
5. Press **Emergency Stop** — robot halts, status turns red

### ✅ Goal navigation (navigation mode only)
1. Launch with `mode:=navigation map:=<path>`
2. Open dashboard
3. Tap **Kitchen** — Nav2 should plan and execute a path
4. Robot arrives at kitchen coordinates (1.5, 0.8)

---

## Sim Arena Layout

The world (`worlds/saarthi_arena.sdf`) is a **7 × 5 m** enclosed space split into two zones by a central divider with a 1.2 m doorway.

```
┌──────────────────────────┬──────────────────────────┐
│                          │                          │
│   KITCHEN  (1.5, 0.8)    │   BEDROOM  (5.5, 4.2)   │
│                          │                          │
│        Zone A            ║        Zone B            │
│                          ║                          │
│  CLASSROOM (1.5, 4.2)    │  BATHROOM  (5.5, 0.8)   │
│                          │                          │
└──────────────────────────┴──────────────────────────┘
  ↑ Robot spawns at (3.5, 2.5) — centre of the arena
```

Each room has a coloured floor marker visible in Gazebo:
- Kitchen — cyan
- Classroom — green
- Bedroom — purple
- Bathroom — blue

---

## Active ROS2 Topics (sim)

| Topic | Hz | Publisher | Subscriber |
|-------|----|-----------|-----------|
| `/clock` | sim time | Gazebo | all nodes |
| `/scan` | 10 | Gazebo lidar | SLAM Toolbox |
| `/odom` | 50 | Gazebo diff-drive | serial_bridge (replaced), dashboard |
| `/cmd_vel` | on demand | dashboard / teleop | Gazebo diff-drive |
| `/tf` | 50 | Gazebo diff-drive + RSP | SLAM, Nav2 |
| `/map` | ~0.2 | SLAM Toolbox | dashboard |
| `/goal_pose` | on demand | dashboard | Nav2 (nav mode) |
| `/amcl_pose` | 1 | Nav2 (nav mode) | dashboard |

---

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| Map never appears on dashboard | SLAM not receiving scans | Run `ros2 topic hz /scan` — should be ~10 Hz. If 0, TF chain is broken. Run `view_frames`. |
| `TF_OLD_DATA` spam in logs | Sim restarted without killing old nodes | Kill both tmux sessions, restart fresh |
| Dashboard shows "Disconnected" | Dashboard backend not running | Check `tmux attach -t dashboard` for errors |
| `dpkg lock` error in WSL2 | Previous apt interrupted | Run `rm -f /var/lib/dpkg/lock-frontend && dpkg --configure -a` |
| Robot doesn't move with teleop | Wrong terminal — ROS not sourced | Run `source /opt/ros/jazzy/setup.bash && source ~/saarthi_ws/install/setup.bash` first |
| Speed shows 0 but robot moves | `/pose` not subscribed yet | Wait 3–5 s after launch for all nodes to connect |

---

## tmux Quick Reference

The simulation runs persistently in tmux sessions inside WSL2.

```bash
# Attach to running sessions
tmux attach -t saarthi     # Gazebo + SLAM logs
tmux attach -t dashboard   # Dashboard server logs

# Detach without killing (Ctrl+B then D)

# Kill and restart everything
tmux kill-session -t saarthi
tmux kill-session -t dashboard
```

---

## Differences vs Real Hardware

| Aspect | Simulation | Real Hardware |
|--------|-----------|--------------|
| Odometry source | Gazebo diff-drive plugin | `serial_bridge_node` reading ESP32 Hall sensors |
| Lidar | Gazebo GPU lidar (perfect, no noise tuning needed) | RPLidar A1 physical sensor |
| Motor control | Gazebo physics | ESP32 DAC + motor controllers |
| Clock | Gazebo sim time (`/clock` topic) | Wall clock |
| E-Stop | Dashboard button only | Hardware button on `ESTOP_PIN` (GPIO 4) |
| Status topic | Not published (defaults to "ok") | `/esp32_status` from `serial_bridge_node` |
| TICKS_PER_REV | Not applicable | Must be calibrated physically |

When you move to real hardware, switch to the `main` branch and use `bringup.launch.py` instead of `sim_launch.py`. Everything else — SLAM config, Nav2 params, dashboard — is identical.
