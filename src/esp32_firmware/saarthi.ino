/*
 * Project Saarthi — ESP32-WROOM-32 wheelchair firmware
 *
 * Serial protocol (115200 baud, \n-terminated ASCII):
 *   Pi  → ESP32:  V,<linear_m_s>,<angular_rad_s>,<seq>\n
 *                 H,<seq>\n
 *   ESP32 → Pi:   O,<left_ticks>,<right_ticks>,<dt_ms>\n  @ 50 Hz
 *                 S,<estop>,<cliff>,<batt_mv>\n            @ 10 Hz
 */

#include <Arduino.h>
#include "config.h"

// ──────────────────────────────────────────────
// Interrupt-driven tick counters (volatile, 32-bit)
// ──────────────────────────────────────────────
volatile int32_t g_ticks_left  = 0;
volatile int32_t g_ticks_right = 0;

void IRAM_ATTR isr_left()  { g_ticks_left++;  }
void IRAM_ATTR isr_right() { g_ticks_right++; }

// ──────────────────────────────────────────────
// Motor state
// ──────────────────────────────────────────────
struct MotorCmd {
    uint8_t dac;      // 0–255
    bool    forward;  // true = positive direction
};

static MotorCmd g_cmd_left  = {0, true};
static MotorCmd g_cmd_right = {0, true};
static bool     g_stopped   = false;   // watchdog or e-stop active

// ──────────────────────────────────────────────
// Timing
// ──────────────────────────────────────────────
static uint32_t g_last_heartbeat_ms = 0;
static uint32_t g_last_motor_ms     = 0;
static uint32_t g_last_status_ms    = 0;
static uint32_t g_last_odom_ms      = 0;   // tracks dt for odometry message

// ──────────────────────────────────────────────
// Serial input buffer
// ──────────────────────────────────────────────
#define RX_BUF_LEN 64
static char  g_rx_buf[RX_BUF_LEN];
static uint8_t g_rx_pos = 0;

// ──────────────────────────────────────────────
// Helper: convert wheel speed (m/s) → MotorCmd
// ──────────────────────────────────────────────
static MotorCmd speed_to_cmd(float v_wheel_ms)
{
    MotorCmd cmd;
    cmd.forward = (v_wheel_ms >= 0.0f);

    float rpm     = (fabsf(v_wheel_ms) / (2.0f * M_PI * WHEEL_RADIUS_M)) * 60.0f;
    float clamped = min(rpm, (float)MAX_RPM);
    cmd.dac       = (uint8_t)(clamped / MAX_RPM * 255.0f);
    return cmd;
}

// ──────────────────────────────────────────────
// Apply motor outputs to hardware
// ──────────────────────────────────────────────
static void apply_motors()
{
    if (g_stopped) {
        dacWrite(DAC_LEFT,  0);
        dacWrite(DAC_RIGHT, 0);
        digitalWrite(BRAKE_LEFT,  HIGH);
        digitalWrite(BRAKE_RIGHT, HIGH);
        return;
    }

    // Release brakes first, then set direction and speed
    digitalWrite(BRAKE_LEFT,  LOW);
    digitalWrite(BRAKE_RIGHT, LOW);
    digitalWrite(DIR_LEFT,  g_cmd_left.forward  ? HIGH : LOW);
    digitalWrite(DIR_RIGHT, g_cmd_right.forward ? HIGH : LOW);
    dacWrite(DAC_LEFT,  g_cmd_left.dac);
    dacWrite(DAC_RIGHT, g_cmd_right.dac);
}

// ──────────────────────────────────────────────
// Emergency stop — brakes on, DAC zeroed, single buzz
// ──────────────────────────────────────────────
static bool g_buzz_done = false;

static void emergency_stop()
{
    dacWrite(DAC_LEFT,  0);
    dacWrite(DAC_RIGHT, 0);
    digitalWrite(BRAKE_LEFT,  HIGH);
    digitalWrite(BRAKE_RIGHT, HIGH);

    // One buzz per stop event so the buzzer doesn't loop continuously
    if (!g_buzz_done) {
        digitalWrite(BUZZER_PIN, HIGH);
        delay(80);
        digitalWrite(BUZZER_PIN, LOW);
        g_buzz_done = true;
    }
}

// ──────────────────────────────────────────────
// Parse a complete line from the Pi
// ──────────────────────────────────────────────
static void process_line(const char *line)
{
    char type = line[0];

    if (type == 'V' && line[1] == ',') {
        // V,<linear>,<angular>,<seq>
        float linear, angular;
        int   seq;
        if (sscanf(line + 2, "%f,%f,%d", &linear, &angular, &seq) == 3) {
            float v_left  = linear - angular * HALF_SEPARATION_M;
            float v_right = linear + angular * HALF_SEPARATION_M;
            g_cmd_left    = speed_to_cmd(v_left);
            g_cmd_right   = speed_to_cmd(v_right);
        }
    } else if (type == 'H' && line[1] == ',') {
        // H,<seq>
        g_last_heartbeat_ms = millis();
        // A valid heartbeat clears the stopped flag (if e-stop is not asserted)
        if (g_stopped && digitalRead(ESTOP_PIN) == HIGH) {
            g_stopped   = false;
            g_buzz_done = false;
        }
    }
}

// ──────────────────────────────────────────────
// Accumulate bytes into line buffer, call process_line on '\n'
// ──────────────────────────────────────────────
static void read_serial()
{
    while (PI_SERIAL.available()) {
        char c = (char)PI_SERIAL.read();
        if (c == '\n') {
            g_rx_buf[g_rx_pos] = '\0';
            if (g_rx_pos > 0) process_line(g_rx_buf);
            g_rx_pos = 0;
        } else {
            if (g_rx_pos < RX_BUF_LEN - 1) g_rx_buf[g_rx_pos++] = c;
            // Silently drop if buffer full (malformed/oversized packet)
        }
    }
}

// ──────────────────────────────────────────────
// Send odometry message; snaps and resets tick counters atomically
// ──────────────────────────────────────────────
static void send_odometry()
{
    uint32_t now = millis();
    uint32_t dt  = now - g_last_odom_ms;
    g_last_odom_ms = now;

    // Atomic snapshot — disable interrupts only long enough to copy
    noInterrupts();
    int32_t left  = g_ticks_left;
    int32_t right = g_ticks_right;
    g_ticks_left  = 0;
    g_ticks_right = 0;
    interrupts();

    PI_SERIAL.printf("O,%d,%d,%u\n", left, right, dt);
}

// ──────────────────────────────────────────────
// Send status message
// ──────────────────────────────────────────────
static void send_status()
{
    uint8_t estop = (digitalRead(ESTOP_PIN) == LOW) ? 1 : 0;
    uint8_t cliff = 0;   // cliff sensor not fitted in v1; placeholder

    // Battery: ESP32 ADC on GPIO35 with a voltage divider would give mV.
    // Without that circuit, report 0 as a sentinel for "not measured".
    uint16_t batt_mv = 0;

    PI_SERIAL.printf("S,%u,%u,%u\n", estop, cliff, batt_mv);
}

// ──────────────────────────────────────────────
// setup()
// ──────────────────────────────────────────────
void setup()
{
    DBG_SERIAL.begin(SERIAL_BAUD);
    PI_SERIAL.begin(SERIAL_BAUD, SERIAL_8N1, 16, 17);  // RX=16, TX=17

    // Safe initial state: DAC to 0, brakes on
    dacWrite(DAC_LEFT,  0);
    dacWrite(DAC_RIGHT, 0);
    pinMode(DIR_LEFT,    OUTPUT); digitalWrite(DIR_LEFT,    LOW);
    pinMode(DIR_RIGHT,   OUTPUT); digitalWrite(DIR_RIGHT,   LOW);
    pinMode(BRAKE_LEFT,  OUTPUT); digitalWrite(BRAKE_LEFT,  HIGH);
    pinMode(BRAKE_RIGHT, OUTPUT); digitalWrite(BRAKE_RIGHT, HIGH);

    // Hall-effect encoder interrupts (RISING only; quadrature not needed for speed/distance)
    pinMode(HALL_LEFT_A,  INPUT);
    pinMode(HALL_LEFT_B,  INPUT);
    pinMode(HALL_RIGHT_A, INPUT);
    pinMode(HALL_RIGHT_B, INPUT);
    attachInterrupt(digitalPinToInterrupt(HALL_LEFT_A),  isr_left,  RISING);
    attachInterrupt(digitalPinToInterrupt(HALL_RIGHT_A), isr_right, RISING);

    // E-stop and buzzer
    pinMode(ESTOP_PIN,  INPUT_PULLUP);
    pinMode(BUZZER_PIN, OUTPUT);

    // Boot beep — signals MCU is alive to the user
    digitalWrite(BUZZER_PIN, HIGH);
    delay(100);
    digitalWrite(BUZZER_PIN, LOW);

    uint32_t now = millis();
    g_last_heartbeat_ms = now;   // avoid immediate watchdog trip on first boot
    g_last_motor_ms     = now;
    g_last_status_ms    = now;
    g_last_odom_ms      = now;

    DBG_SERIAL.println("Saarthi ESP32 firmware ready");
}

// ──────────────────────────────────────────────
// loop()
// ──────────────────────────────────────────────
void loop()
{
    uint32_t now = millis();

    // 1. Drain incoming serial
    read_serial();

    // 2. Watchdog check — runs every iteration for minimum latency
    bool watchdog_tripped = (now - g_last_heartbeat_ms) > WATCHDOG_MS;
    bool estop_asserted   = (digitalRead(ESTOP_PIN) == LOW);

    if (watchdog_tripped || estop_asserted) {
        if (!g_stopped) {
            g_stopped   = true;
            g_buzz_done = false;
        }
        emergency_stop();
    }

    // 3. 50 Hz: apply motors + send odometry
    if (now - g_last_motor_ms >= MOTOR_INTERVAL_MS) {
        g_last_motor_ms = now;
        apply_motors();
        send_odometry();
    }

    // 4. 10 Hz: send status
    if (now - g_last_status_ms >= STATUS_INTERVAL_MS) {
        g_last_status_ms = now;
        send_status();
    }
}
