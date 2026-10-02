#!/usr/bin/env python3
"""
serial_bridge_node.py — Project Saarthi
ROS2 Jazzy node that bridges the Raspberry Pi to the ESP32 motor controller
over UART at 115200 baud.

Serial protocol
---------------
Pi → ESP32 (20 Hz):   V,<linear>,<angular>,<seq>\n
Pi → ESP32 (10 Hz):   H,<seq>\n  (heartbeat — keeps ESP32 watchdog alive)
ESP32 → Pi (50 Hz):   O,<left_ticks>,<right_ticks>,<dt_ms>\n
ESP32 → Pi (10 Hz):   S,<estop>,<cliff>,<batt_mv>\n
"""

import json
import math
import threading

import rclpy
from rclpy.node import Node

from geometry_msgs.msg import Twist, TransformStamped
from nav_msgs.msg import Odometry
from std_msgs.msg import String

import tf2_ros

try:
    import serial
except ImportError:
    serial = None  # handled at runtime with a clear error log


class SerialBridgeNode(Node):
    """Bridges /cmd_vel to ESP32 over UART and publishes /odom and /esp32_status."""

    def __init__(self):
        super().__init__('serial_bridge_node')

        # ── Parameters ────────────────────────────────────────────────────────
        self.declare_parameter('serial_port', '/dev/ttyUSB1')
        self.declare_parameter('baud_rate', 115200)
        self.declare_parameter('ticks_per_rev', 15)
        self.declare_parameter('wheel_radius', 0.127)
        self.declare_parameter('wheel_separation', 0.738)

        self._port_name = self.get_parameter('serial_port').get_parameter_value().string_value
        self._baud = self.get_parameter('baud_rate').get_parameter_value().integer_value
        self._ticks_per_rev = self.get_parameter('ticks_per_rev').get_parameter_value().integer_value
        self._wheel_radius = self.get_parameter('wheel_radius').get_parameter_value().double_value
        self._wheel_sep = self.get_parameter('wheel_separation').get_parameter_value().double_value

        # ── State ──────────────────────────────────────────────────────────────
        self._latest_linear = 0.0
        self._latest_angular = 0.0
        self._cmd_seq = 0
        self._hb_seq = 0

        # Odometry pose state
        self._x = 0.0
        self._y = 0.0
        self._theta = 0.0

        self._serial_lock = threading.Lock()
        self._ser = None

        # ── Publishers ────────────────────────────────────────────────────────
        self._odom_pub = self.create_publisher(Odometry, '/odom', 10)
        self._status_pub = self.create_publisher(String, '/esp32_status', 10)

        # ── TF broadcaster ────────────────────────────────────────────────────
        self._tf_broadcaster = tf2_ros.TransformBroadcaster(self)

        # ── Subscription ─────────────────────────────────────────────────────
        self.create_subscription(Twist, '/cmd_vel', self._cmd_vel_cb, 10)

        # ── Timers ────────────────────────────────────────────────────────────
        self.create_timer(1.0 / 20.0, self._send_velocity_timer)   # 20 Hz
        self.create_timer(1.0 / 10.0, self._heartbeat_timer)       # 10 Hz
        self.create_timer(1.0 / 50.0, self._read_serial_timer)     # 50 Hz (read + dispatch O & S)

        # ── Open serial port ──────────────────────────────────────────────────
        self._open_serial()

        self.get_logger().info(
            f'serial_bridge_node started — port={self._port_name} baud={self._baud}'
        )

    # ── Serial helpers ────────────────────────────────────────────────────────

    def _open_serial(self):
        if serial is None:
            self.get_logger().error(
                'pyserial is not installed. Run: pip install pyserial'
            )
            return
        try:
            self._ser = serial.Serial(
                port=self._port_name,
                baudrate=self._baud,
                timeout=0.0,   # non-blocking reads
            )
            self.get_logger().info(f'Opened serial port {self._port_name}')
        except Exception as exc:
            self._ser = None
            self.get_logger().warn(f'Could not open serial port {self._port_name}: {exc}')

    def _write_line(self, line: str):
        """Write a newline-terminated string to the serial port (thread-safe)."""
        if self._ser is None:
            return
        try:
            with self._serial_lock:
                self._ser.write((line + '\n').encode('ascii'))
        except Exception as exc:
            self.get_logger().warn(f'Serial write error: {exc}')

    def _read_pending_lines(self):
        """Return all complete lines currently waiting in the serial RX buffer."""
        lines = []
        if self._ser is None:
            return lines
        try:
            with self._serial_lock:
                waiting = self._ser.in_waiting
                if waiting > 0:
                    raw = self._ser.read(waiting).decode('ascii', errors='replace')
                    lines = raw.split('\n')
                    # Last element may be a partial line; discard it (it will
                    # be completed on the next read).
                    lines = [l.strip() for l in lines[:-1] if l.strip()]
        except Exception as exc:
            self.get_logger().warn(f'Serial read error: {exc}')
        return lines

    # ── ROS callbacks ─────────────────────────────────────────────────────────

    def _cmd_vel_cb(self, msg: Twist):
        """Cache the latest velocity command."""
        self._latest_linear = msg.linear.x
        self._latest_angular = msg.angular.z

    # ── Timer callbacks ───────────────────────────────────────────────────────

    def _send_velocity_timer(self):
        """20 Hz — send the cached velocity command to the ESP32."""
        self._cmd_seq += 1
        line = f'V,{self._latest_linear:.4f},{self._latest_angular:.4f},{self._cmd_seq}'
        self._write_line(line)

    def _heartbeat_timer(self):
        """10 Hz — send a heartbeat so the ESP32 watchdog does not trigger."""
        self._hb_seq += 1
        self._write_line(f'H,{self._hb_seq}')

    def _read_serial_timer(self):
        """50 Hz — parse all incoming lines; dispatches both O, and S, messages."""
        for line in self._read_pending_lines():
            if line.startswith('O,'):
                self._handle_odometry_line(line)
            elif line.startswith('S,'):
                self._handle_status_line(line)

    # ── Protocol parsers ──────────────────────────────────────────────────────

    def _handle_odometry_line(self, line: str):
        """Parse  O,<left_ticks>,<right_ticks>,<dt_ms>  and publish odometry."""
        try:
            parts = line.split(',')
            if len(parts) != 4:
                return
            left_ticks = int(parts[1])
            right_ticks = int(parts[2])
            dt_ms = float(parts[3])
        except (ValueError, IndexError):
            self.get_logger().warn(f'Malformed O line: {line!r}')
            return

        dt_s = dt_ms / 1000.0
        if dt_s <= 0.0:
            return

        circumference = 2.0 * math.pi * self._wheel_radius
        dist_left = (left_ticks / self._ticks_per_rev) * circumference
        dist_right = (right_ticks / self._ticks_per_rev) * circumference

        dist = (dist_left + dist_right) / 2.0
        dtheta = (dist_right - dist_left) / self._wheel_sep

        # Integrate pose
        self._theta += dtheta
        self._x += dist * math.cos(self._theta)
        self._y += dist * math.sin(self._theta)

        now = self.get_clock().now().to_msg()

        # ── Odometry message ──────────────────────────────────────────────────
        odom = Odometry()
        odom.header.stamp = now
        odom.header.frame_id = 'odom'
        odom.child_frame_id = 'base_link'

        odom.pose.pose.position.x = self._x
        odom.pose.pose.position.y = self._y
        odom.pose.pose.position.z = 0.0

        # Convert theta to quaternion (rotation around Z)
        qz = math.sin(self._theta / 2.0)
        qw = math.cos(self._theta / 2.0)
        odom.pose.pose.orientation.x = 0.0
        odom.pose.pose.orientation.y = 0.0
        odom.pose.pose.orientation.z = qz
        odom.pose.pose.orientation.w = qw

        vx = dist / dt_s
        vth = dtheta / dt_s
        odom.twist.twist.linear.x = vx
        odom.twist.twist.angular.z = vth

        self._odom_pub.publish(odom)

        # ── TF: odom → base_link ──────────────────────────────────────────────
        tf_msg = TransformStamped()
        tf_msg.header.stamp = now
        tf_msg.header.frame_id = 'odom'
        tf_msg.child_frame_id = 'base_link'
        tf_msg.transform.translation.x = self._x
        tf_msg.transform.translation.y = self._y
        tf_msg.transform.translation.z = 0.0
        tf_msg.transform.rotation.x = 0.0
        tf_msg.transform.rotation.y = 0.0
        tf_msg.transform.rotation.z = qz
        tf_msg.transform.rotation.w = qw

        self._tf_broadcaster.sendTransform(tf_msg)

    def _handle_status_line(self, line: str):
        """Parse  S,<estop>,<cliff>,<batt_mv>  and publish JSON to /esp32_status."""
        try:
            parts = line.split(',')
            if len(parts) != 4:
                return
            payload = {
                'estop': int(parts[1]),
                'cliff': int(parts[2]),
                'batt_mv': int(parts[3]),
            }
        except (ValueError, IndexError):
            self.get_logger().warn(f'Malformed S line: {line!r}')
            return

        msg = String()
        msg.data = json.dumps(payload)
        self._status_pub.publish(msg)


def main(args=None):
    rclpy.init(args=args)
    node = SerialBridgeNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
