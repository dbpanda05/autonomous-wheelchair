#pragma once

// ──────────────────────────────────────────────
// Pin definitions — change only here, never in .ino
// ──────────────────────────────────────────────

// Motor control output
#define DAC_LEFT        25   // ESP32 DAC1
#define DAC_RIGHT       26   // ESP32 DAC2
#define DIR_LEFT        27   // optocoupler → controller DIR
#define DIR_RIGHT       12
#define BRAKE_LEFT      14   // optocoupler → controller BRAKE
#define BRAKE_RIGHT     13

// Encoder inputs (level-shifted to 3.3 V)
#define HALL_LEFT_A     34
#define HALL_LEFT_B     35
#define HALL_RIGHT_A    33
#define HALL_RIGHT_B    36

// Safety & UI
#define ESTOP_PIN       4    // active LOW, INPUT_PULLUP
#define BUZZER_PIN      2

// ──────────────────────────────────────────────
// Robot geometry
// ──────────────────────────────────────────────
#define WHEEL_RADIUS_M      0.127f   // metres
#define HALF_SEPARATION_M   0.369f   // half wheel-to-wheel distance

// ──────────────────────────────────────────────
// Tunable constants
// ──────────────────────────────────────────────
#define TICKS_PER_REV   15           // Hall pulses per full wheel revolution (calibrate)
#define MAX_RPM         120.0f       // clamp before DAC scaling
#define WATCHDOG_MS     300UL        // ms without heartbeat → emergency stop

// ──────────────────────────────────────────────
// Serial
// ──────────────────────────────────────────────
#define SERIAL_BAUD     115200
#define PI_SERIAL       Serial2      // GPIO 16 RX, 17 TX
#define DBG_SERIAL      Serial

// ──────────────────────────────────────────────
// Loop timing (ms)
// ──────────────────────────────────────────────
#define MOTOR_INTERVAL_MS   20   // 50 Hz motor + odometry update
#define STATUS_INTERVAL_MS  100  // 10 Hz status message
