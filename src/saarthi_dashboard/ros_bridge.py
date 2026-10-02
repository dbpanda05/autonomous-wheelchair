"""
ros_bridge.py — rclpy node running in a daemon thread.

The FastAPI / asyncio event loop owns the main thread.
This module starts rclpy in a secondary thread so both can coexist.

Exposes:
    RosBridge()
        .get_state()    → dict with pose, speed, status, map_png, map_meta
        .send_goal(name, coords)
        .send_stop()
        .send_cmd_vel(linear, angular)
"""

import base64
import io
import json
import logging
import math
import threading
from typing import Any

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default state returned when ROS is unavailable or no data received yet
# ---------------------------------------------------------------------------
_DEFAULT_STATE: dict[str, Any] = {
    "pose":    {"x": 0.0, "y": 0.0, "theta": 0.0},
    "speed":   0.0,
    "status":  "ok",
    "map_png": "",
    "map_meta": {
        "origin_x":  0.0,
        "origin_y":  0.0,
        "resolution": 0.05,
        "width":     200,
        "height":    200,
    },
}


def _quaternion_to_yaw(x: float, y: float, z: float, w: float) -> float:
    """Convert quaternion to yaw angle in radians."""
    siny_cosp = 2.0 * (w * z + x * y)
    cosy_cosp = 1.0 - 2.0 * (y * y + z * z)
    return math.atan2(siny_cosp, cosy_cosp)


def _occupancy_grid_to_png_b64(grid_msg) -> tuple[str, dict]:
    """
    Convert a nav_msgs/OccupancyGrid message to a grayscale PNG (base64).

    Returns (b64_string, map_meta_dict).
    """
    try:
        import numpy as np
        from PIL import Image
    except ImportError:
        log.warning("numpy/Pillow not installed — cannot render map")
        return "", {}

    w = grid_msg.info.width
    h = grid_msg.info.height
    data = grid_msg.data  # flat list, row-major, -1=unknown, 0=free, 100=occupied

    arr = np.array(data, dtype=np.int16).reshape((h, w))

    # Map occupancy values to grayscale:
    #   -1 (unknown)  → 128 (mid grey)
    #    0 (free)     → 230 (light grey)
    #  100 (occupied) →   0 (black)
    img_arr = np.full((h, w), 128, dtype=np.uint8)
    img_arr[arr == 0]   = 230
    img_arr[arr == 100] = 0

    # ROS maps are stored bottom-up; flip vertically so y increases upward on canvas
    img_arr = np.flipud(img_arr)

    img = Image.fromarray(img_arr, mode="L")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")

    meta = {
        "origin_x":  grid_msg.info.origin.position.x,
        "origin_y":  grid_msg.info.origin.position.y,
        "resolution": grid_msg.info.resolution,
        "width":     w,
        "height":    h,
    }
    return b64, meta


class RosBridge:
    """Thread-safe ROS 2 bridge that runs rclpy in a daemon thread."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state: dict[str, Any] = {
            "pose":    {"x": 0.0, "y": 0.0, "theta": 0.0},
            "speed":   0.0,
            "status":  "ok",
            "map_png": "",
            "map_meta": dict(_DEFAULT_STATE["map_meta"]),
        }
        self._last_map_seq = -1
        self._node = None
        self._executor = None

        self._start_ros_thread()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_state(self) -> dict:
        with self._lock:
            return {
                "pose":    dict(self._state["pose"]),
                "speed":   self._state["speed"],
                "status":  self._state["status"],
                "map_png": self._state["map_png"],
                "map_meta": dict(self._state["map_meta"]),
            }

    def send_goal(self, name: str, coords: dict) -> None:
        if self._node is None:
            log.warning("ROS not ready — cannot send goal")
            return
        try:
            from geometry_msgs.msg import PoseStamped
            from std_msgs.msg import Header
            msg = PoseStamped()
            msg.header = Header()
            msg.header.frame_id = "map"
            msg.header.stamp = self._node.get_clock().now().to_msg()
            msg.pose.position.x = coords["x"]
            msg.pose.position.y = coords["y"]
            msg.pose.position.z = 0.0
            # Convert yaw to quaternion
            theta = coords.get("theta", 0.0)
            msg.pose.orientation.z = math.sin(theta / 2.0)
            msg.pose.orientation.w = math.cos(theta / 2.0)
            self._goal_pub.publish(msg)
            log.info("Published goal: %s → %s", name, coords)
        except Exception as exc:
            log.error("send_goal failed: %s", exc)

    def send_stop(self) -> None:
        self._publish_twist(0.0, 0.0)

    def send_cmd_vel(self, linear: float, angular: float) -> None:
        self._publish_twist(linear, angular)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _publish_twist(self, linear: float, angular: float) -> None:
        if self._node is None:
            log.warning("ROS not ready — cannot publish cmd_vel")
            return
        try:
            from geometry_msgs.msg import Twist
            msg = Twist()
            msg.linear.x = linear
            msg.angular.z = angular
            self._cmd_vel_pub.publish(msg)
        except Exception as exc:
            log.error("_publish_twist failed: %s", exc)

    def _start_ros_thread(self) -> None:
        t = threading.Thread(target=self._ros_spin, daemon=True, name="ros_spin")
        t.start()

    def _ros_spin(self) -> None:
        try:
            import rclpy
            from rclpy.executors import SingleThreadedExecutor
            from rclpy.node import Node

            rclpy.init()
            node = rclpy.create_node("saarthi_dashboard")
            self._node = node

            # Publishers
            from geometry_msgs.msg import PoseStamped, Twist
            self._goal_pub = node.create_publisher(PoseStamped, "/goal_pose", 10)
            self._cmd_vel_pub = node.create_publisher(Twist, "/cmd_vel", 10)

            # Subscribers
            from geometry_msgs.msg import PoseWithCovarianceStamped
            from nav_msgs.msg import OccupancyGrid
            from std_msgs.msg import String

            node.create_subscription(OccupancyGrid, "/map", self._on_map, 1)
            # Real hardware: AMCL pose; Simulation: SLAM Toolbox pose; both same type
            node.create_subscription(
                PoseWithCovarianceStamped, "/amcl_pose", self._on_amcl_pose, 10
            )
            node.create_subscription(
                PoseWithCovarianceStamped, "/pose", self._on_amcl_pose, 10
            )
            from nav_msgs.msg import Odometry
            node.create_subscription(Odometry, "/odom", self._on_odom, 10)
            node.create_subscription(Twist, "/cmd_vel", self._on_cmd_vel, 10)
            node.create_subscription(String, "/esp32_status", self._on_esp32_status, 10)

            log.info("ROS 2 node started — spinning")
            executor = SingleThreadedExecutor()
            executor.add_node(node)
            self._executor = executor
            executor.spin()
        except Exception as exc:
            log.warning("ROS 2 not available (%s) — bridge disabled", exc)

    # ------------------------------------------------------------------
    # Subscription callbacks (called from ROS spin thread)
    # ------------------------------------------------------------------

    def _on_map(self, msg) -> None:
        # ROS 2 dropped header.seq — compare data length as a cheap change check
        new_seq = len(msg.data)
        if new_seq == self._last_map_seq:
            return
        self._last_map_seq = new_seq
        try:
            b64, meta = _occupancy_grid_to_png_b64(msg)
            with self._lock:
                self._state["map_png"] = b64
                self._state["map_meta"] = meta
        except Exception as exc:
            log.error("_on_map error: %s", exc)

    def _on_amcl_pose(self, msg) -> None:
        try:
            p = msg.pose.pose
            theta = _quaternion_to_yaw(
                p.orientation.x, p.orientation.y,
                p.orientation.z, p.orientation.w,
            )
            with self._lock:
                self._state["pose"] = {
                    "x": p.position.x,
                    "y": p.position.y,
                    "theta": theta,
                }
        except Exception as exc:
            log.error("_on_amcl_pose error: %s", exc)

    def _on_cmd_vel(self, msg) -> None:
        with self._lock:
            self._state["speed"] = msg.linear.x

    def _on_odom(self, msg) -> None:
        """Fallback pose from odometry when no AMCL/SLAM pose is available."""
        try:
            p = msg.pose.pose
            theta = _quaternion_to_yaw(
                p.orientation.x, p.orientation.y,
                p.orientation.z, p.orientation.w,
            )
            with self._lock:
                # Only update from odom if SLAM/AMCL haven't published yet
                if self._state["pose"]["x"] == 0.0 and self._state["pose"]["y"] == 0.0:
                    self._state["pose"] = {"x": p.position.x, "y": p.position.y, "theta": theta}
            with self._lock:
                self._state["speed"] = msg.twist.twist.linear.x
        except Exception as exc:
            log.error("_on_odom error: %s", exc)

    def _on_esp32_status(self, msg) -> None:
        try:
            data = json.loads(msg.data)
            # serial_bridge publishes {"estop": 0, "cliff": 0, "batt_mv": 0}
            if data.get("estop", 0):
                status = "estop"
            elif data.get("cliff", 0):
                status = "cliff"
            else:
                status = "ok"
            with self._lock:
                self._state["status"] = status
        except Exception as exc:
            log.error("_on_esp32_status error: %s", exc)
