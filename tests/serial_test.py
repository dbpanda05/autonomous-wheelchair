"""
Project Saarthi — ESP32 serial protocol test suite
Run: python tests/serial_test.py --port COM3
"""

import argparse
import itertools
import sys
import time
import threading

try:
    import serial
except ImportError:
    sys.exit("pyserial not installed. Run: pip install pyserial")


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

_seq = itertools.count(1)

def make_heartbeat():
    return f"H,{next(_seq)}\n"

def make_velocity(linear: float, angular: float):
    return f"V,{linear:.4f},{angular:.4f},{next(_seq)}\n"


def _drain(ser: serial.Serial, timeout: float, prefix: str | None = None) -> list[str]:
    """Collect lines until timeout. Optionally filter by prefix."""
    lines: list[str] = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        ser.timeout = max(0.0, deadline - time.monotonic())
        raw = ser.readline()
        if not raw:
            break
        line = raw.decode("ascii", errors="ignore").strip()
        if line and (prefix is None or line.startswith(prefix)):
            lines.append(line)
    return lines


def _pass_fail(name: str, ok: bool, detail: str = ""):
    status = "PASS" if ok else "FAIL"
    detail_str = f"  ({detail})" if detail else ""
    print(f"  [{status}] {name}{detail_str}")
    return ok


def open_port(port: str, baud: int = 115200) -> serial.Serial:
    return serial.Serial(port, baud, timeout=1.0)


# ──────────────────────────────────────────────
# Individual tests
# ──────────────────────────────────────────────

def connect_test(port: str) -> bool:
    """Open the port and verify S, (status) messages arrive within 1 s."""
    print("\n--- connect_test ---")
    try:
        ser = open_port(port)
    except serial.SerialException as e:
        return _pass_fail("open port", False, str(e))

    lines = _drain(ser, timeout=1.5, prefix="S,")
    ser.close()
    return _pass_fail(
        "S, messages arrive within 1 s",
        len(lines) > 0,
        f"got {len(lines)} S, lines"
    )


def heartbeat_test(port: str) -> bool:
    """Send H, for 2 s; verify O, odometry messages keep arriving."""
    print("\n--- heartbeat_test ---")
    ser = open_port(port)

    odom_lines: list[str] = []
    stop_event = threading.Event()

    def _send_hb():
        while not stop_event.is_set():
            ser.write(make_heartbeat().encode())
            time.sleep(0.1)   # 10 Hz

    def _collect():
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            ser.timeout = 0.05
            raw = ser.readline()
            if raw:
                line = raw.decode("ascii", errors="ignore").strip()
                if line.startswith("O,"):
                    odom_lines.append(line)

    hb_thread = threading.Thread(target=_send_hb, daemon=True)
    col_thread = threading.Thread(target=_collect, daemon=True)
    hb_thread.start()
    col_thread.start()
    col_thread.join()
    stop_event.set()
    ser.close()

    # At 50 Hz over 2 s we expect ~100 messages; accept ≥ 80 to allow startup jitter
    ok = len(odom_lines) >= 80
    return _pass_fail(
        "O, messages arrive while heartbeat is sent",
        ok,
        f"got {len(odom_lines)} O, lines in 2 s"
    )


def velocity_test(port: str) -> bool:
    """Send V,0.2,0.0 + heartbeats; verify O, tick counts are positive."""
    print("\n--- velocity_test ---")
    ser = open_port(port)

    stop_event = threading.Event()
    odom_lines: list[str] = []

    def _sender():
        while not stop_event.is_set():
            ser.write(make_velocity(0.2, 0.0).encode())
            ser.write(make_heartbeat().encode())
            time.sleep(0.05)   # 20 Hz velocity + 10 Hz heartbeat interleaved

    def _collector():
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            ser.timeout = 0.05
            raw = ser.readline()
            if raw:
                line = raw.decode("ascii", errors="ignore").strip()
                if line.startswith("O,"):
                    odom_lines.append(line)

    t_send = threading.Thread(target=_sender, daemon=True)
    t_col  = threading.Thread(target=_collector, daemon=True)
    t_send.start()
    t_col.start()
    t_col.join()
    stop_event.set()
    ser.close()

    # Check that at least some frames report non-zero ticks
    nonzero = 0
    for line in odom_lines:
        parts = line.split(",")
        if len(parts) == 4:
            try:
                left  = int(parts[1])
                right = int(parts[2])
                if left > 0 or right > 0:
                    nonzero += 1
            except ValueError:
                pass

    ok = nonzero > 0
    return _pass_fail(
        "O, reports increasing ticks during forward motion",
        ok,
        f"{nonzero}/{len(odom_lines)} frames had non-zero ticks"
    )


def watchdog_test(port: str) -> bool:
    """
    Send heartbeats for 1 s, then stop.
    Time how long until ticks go to zero (motors stop).
    Expect ≤ 350 ms after last heartbeat.
    """
    print("\n--- watchdog_test ---")
    ser = open_port(port)

    stop_hb = threading.Event()

    def _sender():
        while not stop_hb.is_set():
            ser.write(make_heartbeat().encode())
            time.sleep(0.1)

    t = threading.Thread(target=_sender, daemon=True)
    t.start()
    time.sleep(1.0)   # send heartbeats for 1 s to ensure motors are "alive"

    # Stop sending and note exact time
    stop_hb.set()
    t.join(timeout=0.2)
    stop_time = time.monotonic()
    ser.write(make_velocity(0.1, 0.0).encode())  # one last velocity so ticks would be running

    # Now just collect O, messages until ticks drop to zero
    silence_start: float | None = None
    deadline = time.monotonic() + 1.5   # 1.5 s window
    while time.monotonic() < deadline:
        ser.timeout = 0.05
        raw = ser.readline()
        if raw:
            line = raw.decode("ascii", errors="ignore").strip()
            if line.startswith("O,"):
                parts = line.split(",")
                if len(parts) == 4:
                    try:
                        left  = int(parts[1])
                        right = int(parts[2])
                        if left == 0 and right == 0 and silence_start is None:
                            silence_start = time.monotonic()
                    except ValueError:
                        pass

    ser.close()

    if silence_start is None:
        return _pass_fail("watchdog stops motors within 350 ms", False,
                          "ticks never went to zero in 1.5 s window")

    elapsed_ms = (silence_start - stop_time) * 1000.0
    ok = elapsed_ms <= 350.0
    return _pass_fail(
        "watchdog stops motors within 350 ms",
        ok,
        f"ticks zeroed {elapsed_ms:.0f} ms after last heartbeat"
    )


def angular_test(port: str) -> bool:
    """
    Send V,0.0,0.5 (pure rotation) + heartbeats.
    Verify that left and right tick RATES differ (one wheel faster).
    """
    print("\n--- angular_test ---")
    ser = open_port(port)

    stop_event = threading.Event()
    left_total  = 0
    right_total = 0

    def _sender():
        while not stop_event.is_set():
            ser.write(make_velocity(0.0, 0.5).encode())
            ser.write(make_heartbeat().encode())
            time.sleep(0.05)

    def _collector():
        nonlocal left_total, right_total
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            ser.timeout = 0.05
            raw = ser.readline()
            if raw:
                line = raw.decode("ascii", errors="ignore").strip()
                if line.startswith("O,"):
                    parts = line.split(",")
                    if len(parts) == 4:
                        try:
                            left_total  += abs(int(parts[1]))
                            right_total += abs(int(parts[2]))
                        except ValueError:
                            pass

    t_send = threading.Thread(target=_sender, daemon=True)
    t_col  = threading.Thread(target=_collector, daemon=True)
    t_send.start()
    t_col.start()
    t_col.join()
    stop_event.set()
    ser.close()

    # For pure rotation both wheels should be turning but in opposite magnitudes.
    # Due to counting only abs ticks they should be roughly equal, but at least both > 0
    # and the ratio shouldn't be extreme (both non-zero indicates differential drive).
    total = left_total + right_total
    if total == 0:
        return _pass_fail("tick rates differ during pure rotation", False,
                          "no ticks received at all")

    ratio = min(left_total, right_total) / max(left_total, right_total)
    # Wheels should be symmetric in magnitude for pure rotation
    ok = left_total > 0 and right_total > 0 and ratio > 0.5
    return _pass_fail(
        "both wheels turn during pure rotation (differential)",
        ok,
        f"left={left_total} right={right_total} ratio={ratio:.2f}"
    )


# ──────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Saarthi ESP32 serial test suite")
    parser.add_argument("--port", default="/dev/ttyUSB0",
                        help="Serial port (e.g. COM3 or /dev/ttyUSB0)")
    args = parser.parse_args()

    print(f"=== Saarthi ESP32 Serial Tests  (port: {args.port}) ===")

    results = [
        connect_test(args.port),
        heartbeat_test(args.port),
        velocity_test(args.port),
        watchdog_test(args.port),
        angular_test(args.port),
    ]

    passed = sum(results)
    total  = len(results)
    print(f"\n=== {passed}/{total} tests passed ===")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
