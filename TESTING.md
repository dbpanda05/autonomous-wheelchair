# Project Saarthi — Testing Checkpoints

Cross-verification guide. Work through these in order. Each stage gates the next.

---

## Stage 1 — ESP32 Standalone (no Pi needed)

**What:** Verify firmware runs and serial protocol works before connecting to ROS.

**How:**
1. Flash `src/esp32_firmware/saarthi.ino` via Arduino IDE
2. Open Serial Monitor at 115200 baud
3. You should see `S,0,0,0` messages every 100ms immediately on boot
4. Type `H,1` and press Enter → watchdog resets, S messages keep coming
5. Type `V,0.2,0.0,1` + keep sending `H,<seq>` → DAC should output ~1.5V on GPIO25/26 (measure with multimeter)
6. Stop sending `H,` → within 300ms motors should cut (DAC drops to 0)

**Automated test:**
```
python tests/serial_test.py --port COM3   # Windows
python tests/serial_test.py --port /dev/ttyUSB0   # Linux/Pi
```
All 5 tests should print PASS.

**Pass criteria:**
- [ ] S, messages arrive on boot
- [ ] Watchdog stops output within 350ms of last heartbeat
- [ ] DAC voltage measurable on GPIO25/26 during V, command
- [ ] serial_test.py: all 5 PASS

---

## Stage 2 — Serial Bridge on Pi (ESP32 + Pi, no Nav2)

**What:** Verify the ROS2 bridge node talks to ESP32 and publishes odometry.

**How:**
```bash
# Terminal 1: source and run bridge
source ~/saarthi_ws/install/setup.bash
ros2 run wheelchair_navigation serial_bridge_node

# Terminal 2: watch odometry
ros2 topic echo /odom

# Terminal 3: watch status
ros2 topic echo /esp32_status

# Terminal 4: send a velocity command
ros2 topic pub /cmd_vel geometry_msgs/msg/Twist \
  "{linear: {x: 0.1, y: 0.0, z: 0.0}, angular: {x: 0.0, y: 0.0, z: 0.0}}" \
  --rate 20
```

Wheels should spin. Kill the publisher → wheels stop within 300ms (watchdog).

**Check TF:**
```bash
ros2 run tf2_tools view_frames   # generates frames.pdf
# Must show: odom → base_link published by serial_bridge_node
```

**Pass criteria:**
- [ ] /odom publishing at ~50Hz
- [ ] /esp32_status publishing at ~10Hz
- [ ] Wheels physically spin when /cmd_vel is published
- [ ] Wheels stop within 300ms when /cmd_vel stops
- [ ] odom → base_link TF exists in tf tree

---

## Stage 3 — SLAM Mapping (full bringup, mapping mode)

**What:** Drive the chair around and build a map.

**How:**
```bash
# Launch full stack in mapping mode
ros2 launch wheelchair_navigation bringup.launch.py mode:=mapping

# On laptop: visualize in RViz (same ROS_DOMAIN_ID=42)
rviz2   # Add: Map topic /map, LaserScan topic /scan, TF

# Drive manually
ros2 run teleop_twist_keyboard teleop_twist_keyboard

# When map looks good, save it
mkdir -p ~/saarthi_ws/maps
ros2 run nav2_map_server map_saver_cli -f ~/saarthi_ws/maps/arena
# Creates arena.pgm and arena.yaml
```

**Pass criteria:**
- [ ] /scan topic has LIDAR data (non-empty)
- [ ] /map topic shows occupancy grid building in RViz
- [ ] Driving manually causes map to expand correctly
- [ ] arena.pgm and arena.yaml saved successfully
- [ ] Map clearly shows the room layout

---

## Stage 4 — Navigation (localization + Nav2 goal)

**What:** Chair localizes on the saved map and navigates to a goal.

**How:**
```bash
# Launch in navigation mode with saved map
ros2 launch wheelchair_navigation bringup.launch.py \
  mode:=navigation \
  map:=$HOME/saarthi_ws/maps/arena.yaml

# In RViz:
# 1. Click "2D Pose Estimate" → click where the chair is on the map
# 2. Watch AMCL particles converge
# 3. Click "Nav2 Goal" → click a destination on the map
# 4. Chair should plan a path and drive there
```

**If navigation fails — diagnose in order:**
1. Check TF tree: `ros2 run tf2_tools view_frames`
   - Must have: map → odom → base_link → lidar_link
2. Check costmap: In RViz, add topic /local_costmap/costmap and /global_costmap/costmap
3. Check AMCL: `ros2 topic echo /amcl_pose` — is pose updating?
4. Check /scan: Is LIDAR publishing? `ros2 topic hz /scan`

**Pass criteria:**
- [ ] AMCL particles visible in RViz after pose estimate
- [ ] /amcl_pose updates as chair drives
- [ ] Nav2 goal accepted (green path visible in RViz)
- [ ] Chair physically drives to goal and stops
- [ ] Chair avoids obstacles (step in front of it)

---

## Stage 5 — Dashboard (phone connected)

**What:** Phone UI controls the chair.

**Setup:**
```bash
# On Pi: start the dashboard server (alongside Nav2)
cd ~/saarthi_ws/src/saarthi_dashboard
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8080

# On laptop first (mock mode):
MOCK_ROS=1 uvicorn main:app --host 0.0.0.0 --port 8080
# Open http://localhost:8080 in Chrome — verify UI works

# Phone connection:
# 1. Enable USB Tethering: Settings → Hotspot & Tethering → USB Tethering
# 2. On Pi: adb reverse tcp:8080 tcp:8080
# 3. On phone: open Chrome → http://localhost:8080
```

**Test sequence:**
1. Map appears on phone screen (even if low-res)
2. Tap "Kitchen" button → check Pi terminal for goal published
3. Chair navigates to kitchen
4. Tap "✋ STOP" → chair stops immediately
5. Hold voice button → say "kitchen" → chair navigates

**Pass criteria:**
- [ ] Dashboard opens on phone at localhost:8080
- [ ] Map renders (shows rooms)
- [ ] Robot dot visible on map and moves as chair drives
- [ ] Tapping a destination publishes /goal_pose (verify: `ros2 topic echo /goal_pose`)
- [ ] Chair navigates when destination tapped from phone
- [ ] Stop button works
- [ ] Voice recognizes at least "kitchen" and "classroom"
- [ ] Phone speaks confirmation ("Navigating to Kitchen")

---

## Stage 6 — Demo Rehearsal

Run this sequence 5 times clean before demo day:

1. Boot Pi, launch full stack
2. Set initial pose in RViz (or phone)
3. Say "go to kitchen" on phone → chair drives there
4. Tap classroom button → chair rerouts
5. Step in front of chair → it stops or replans
6. Press E-stop → verify contactor cuts
7. Restart ROS mid-drive → verify watchdog stops chair in <350ms

**Time each run. Target: under 6 minutes total.**

Record a backup video of a successful run on Day 2.

---

## Quick Diagnostic Commands

```bash
# Check all topics are live
ros2 topic list
ros2 topic hz /scan /odom /amcl_pose /map /cmd_vel

# Check TF tree
ros2 run tf2_tools view_frames && evince frames.pdf

# Manual goal from terminal (bypass phone)
ros2 action send_goal /navigate_to_pose nav2_msgs/action/NavigateToPose \
  "{pose: {header: {frame_id: map}, pose: {position: {x: 2.0, y: 1.0}, orientation: {w: 1.0}}}}"

# Kill Nav2 mid-drive (watchdog test)
ros2 lifecycle set /bt_navigator shutdown

# Check serial port available
ls -la /dev/ttyUSB*   # LIDAR usually USB0, ESP32 usually USB1
```
