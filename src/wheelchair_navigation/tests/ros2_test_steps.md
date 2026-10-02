# Project Saarthi — ROS2 Hardware Test Procedure

All commands assume the workspace is sourced:
```
source ~/saarthi_ws/install/setup.bash
```

---

## Stage A — Serial Bridge (no Nav2 needed)

This verifies the Pi ↔ ESP32 UART link and odometry pipeline in isolation.

**A1. Start the serial bridge node**
```bash
ros2 run wheelchair_navigation serial_bridge_node
```
Expected: no errors; you should see "Opened serial port /dev/ttyUSB1".

If the port name differs (e.g. `/dev/ttyUSB0` or `/dev/ttyACM0`):
```bash
ros2 run wheelchair_navigation serial_bridge_node --ros-args -p serial_port:=/dev/ttyACM0
```

**A2. Verify odometry is publishing**
```bash
ros2 topic echo /odom
```
Expected: messages at ~50 Hz with `header.frame_id = "odom"` and `child_frame_id = "base_link"`.
When the chair is stationary, `pose.pose.position.x/y` should remain stable.

**A3. Verify ESP32 status is publishing**
```bash
ros2 topic echo /esp32_status
```
Expected: JSON messages at ~10 Hz, e.g.:
```
data: '{"estop": 0, "cliff": 0, "batt_mv": 12600}'
```
Check that `batt_mv` is plausible (>10 000 for a charged 12 V pack).

**A4. Command a velocity and watch the wheels**
```bash
ros2 topic pub /cmd_vel geometry_msgs/msg/Twist "{linear: {x: 0.1}}" --once
```
Expected: drive wheels spin for ~300 ms then stop (ESP32 watchdog cuts power after ~500 ms without a new V, command).
Check that `/odom` position advances while they spin.

**A5. Verify the TF tree**
```bash
ros2 run tf2_tools view_frames
```
Expected tree: `map → odom → base_link → … (URDF joints) → lidar_link`

---

## Stage B — SLAM Mapping

Build a persistent map of the space before attempting autonomous navigation.

**B1. Launch in mapping mode**
```bash
ros2 launch wheelchair_navigation bringup.launch.py mode:=mapping
```
Open RViz2 and add:
- Map display  (topic: `/map`)
- LaserScan    (topic: `/scan`, frame: `lidar_link`)
- RobotModel
- TF

**B2. Drive the whole space**
```bash
ros2 run teleop_twist_keyboard teleop_twist_keyboard
```
Move slowly around every room and doorway. The map should fill in grey/black/white with no large grey blobs drifting.

**B3. Save the map**
```bash
mkdir -p ~/saarthi_ws/maps
ros2 run nav2_map_server map_saver_cli -f ~/saarthi_ws/maps/arena
```
Expected: `arena.pgm` and `arena.yaml` created.
Open `arena.pgm` in an image viewer and verify the walls are clean solid lines.

---

## Stage C — Navigation Test

**C1. Launch in navigation mode**
```bash
ros2 launch wheelchair_navigation bringup.launch.py \
  mode:=navigation \
  map:=$HOME/saarthi_ws/maps/arena.yaml
```

**C2. Set initial pose in RViz2**
Use the "2D Pose Estimate" tool. Click on the map where the chair currently is and drag in the direction it faces.
Expected: the particle cloud collapses to a tight cluster around the chair.

**C3. Send a navigation goal**
Use the "Nav2 Goal" tool in RViz2. Click a reachable location.
Expected:
- A path appears in the global costmap.
- The chair begins moving toward the goal.
- The chair slows and stops within ~25 cm of the goal.

**C4. Verify the TF tree is intact during navigation**
```bash
ros2 run tf2_tools view_frames
```
Expected: `map → odom → base_link` present and freshly timestamped.

---

## Stage D — Full Integration Test

**D1. Start everything**
```bash
# Terminal 1 — Nav stack
ros2 launch wheelchair_navigation bringup.launch.py \
  mode:=navigation \
  map:=$HOME/saarthi_ws/maps/arena.yaml

# Terminal 2 — Dashboard server (adapt to your project structure)
cd ~/saarthi_ws && python3 dashboard/server.py
```

**D2. Send a goal from the phone/dashboard**
Open the dashboard in a browser and tap a destination.
Expected:
- `/goal_pose` (geometry_msgs/PoseStamped) is published on the ROS network.
- bt_navigator picks it up.
- The chair navigates to the destination.

**D3. Confirm movement**
```bash
ros2 topic echo /odom | grep "position"
```
Position should change during travel and stabilise when the goal is reached.

---

## Common Failure Modes

### The chair does not move after a Nav2 goal

1. Check `/tf` for staleness:
   ```bash
   ros2 run tf2_ros tf2_echo map base_link
   ```
   If it says "no transform available", the odom → base_link TF is not being published.
   Confirm serial_bridge_node is running and receiving O, lines from the ESP32.

2. Check the local costmap for an inflated obstacle around the robot:
   In RViz2 add a Costmap display for `/local_costmap/costmap`. If the robot footprint is entirely red, the inflation layer thinks the robot itself is an obstacle — the `inflation_radius` in `nav2_params.yaml` may be too large for the space.

3. Check controller_server logs:
   ```bash
   ros2 lifecycle get /controller_server
   ros2 topic echo /cmd_vel
   ```

### AMCL delocalisations (robot suddenly jumps in the map)

- Increase `max_particles` in `nav2_params.yaml` to 3000.
- If the LiDAR scan looks correct in RViz but AMCL drifts, the `transform_tolerance` may need to be raised to 1.5 s.
- Use "2D Pose Estimate" to manually re-initialise AMCL.

### Serial port not found (`/dev/ttyUSB1`)

```bash
ls -l /dev/ttyUSB* /dev/ttyACM*
```
Identify the correct device, then pass it:
```bash
ros2 run wheelchair_navigation serial_bridge_node --ros-args -p serial_port:=/dev/ttyACM0
```
Ensure the Pi user is in the `dialout` group:
```bash
sudo usermod -aG dialout $USER
# Then log out and back in.
```

### Odometry drifts even on a straight path

- Verify `ticks_per_rev` matches the actual encoder resolution on the ESP32.
- Verify `wheel_radius` (0.127 m) and `wheel_separation` (0.738 m) match the physical hardware.
- Pass corrected values at runtime:
  ```bash
  ros2 run wheelchair_navigation serial_bridge_node --ros-args \
    -p ticks_per_rev:=20 \
    -p wheel_radius:=0.127 \
    -p wheel_separation:=0.738
  ```

### E-stop triggered (estop=1 in /esp32_status)

The ESP32 has asserted the emergency stop. Check:
1. Physical e-stop button — release it.
2. `cliff` field: if 1, the cliff sensors detected a drop (ramp edge). Back the chair up.
3. Power: `batt_mv` below ~10 500 mV indicates a low battery.

### Nav2 recovery behaviours loop forever

The `behavior_server` is configured with only `ClearCostmapRecovery` (Spin and BackUp are disabled as unsafe with a rider). If clearing the costmap does not resolve the blockage, the robot will stop. Manually reposition the chair and re-send the goal, or use teleop to back out of the blocked position.

### SLAM map has a "ghost" wall or mis-closes a loop

- Reduce speed during mapping (max ~0.2 m/s in tight spaces).
- Increase `scan_buffer_size` in `mapper_params_online.yaml` to 20 for slower CPUs.
- Re-map from scratch if the loop closure error is large.
