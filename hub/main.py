"""spike-mind hub bridge — runs on SPIKE Prime with Pybricks firmware.

Receives binary commands via BLE stdin, executes motor/sensor actions,
returns sensor state via stdout.

Command format: struct.pack("!if", cmd_id, value) = 8 bytes
Response format: struct.pack("!fffff", dist_cm, heading, pitch, roll, left_angle) = 20 bytes

IMPORTANT: Motor commands use run_angle(wait=False) so they don't block
the main loop. Blocking the loop kills BLE on macOS.

Port assignments:
  A = left wheel motor
  B = head rotation motor
  C = color sensor
  D = ultrasonic sensor
  E = right wheel motor
  F = head tilt + arm motor
"""

import ustruct
from micropython import kbd_intr
from pybricks.hubs import PrimeHub
from pybricks.parameters import Port, Color
from pybricks.pupdevices import Motor, UltrasonicSensor, ColorSensor
from pybricks.robotics import DriveBase
from pybricks.tools import wait, StopWatch
from uselect import poll
from usys import stdin, stdout

# Disable Ctrl-C on stdin so binary 0x03 doesn't raise KeyboardInterrupt
kbd_intr(-1)

# Hardware setup
hub = PrimeHub()
left = Motor(Port.A)
head_rotation = Motor(Port.B)
color_sensor = ColorSensor(Port.C)
dist_sensor = UltrasonicSensor(Port.D)
right = Motor(Port.E)
head_tilt = Motor(Port.F)

drive = DriveBase(left, right, wheel_diameter=56, axle_track=112)
drive.use_gyro(True)

# Command IDs (must match host protocol.py)
CMD_STRAIGHT = 1
CMD_TURN = 2
CMD_STOP = 3
CMD_READ_DISTANCE = 4
CMD_READ_COLOR = 5
CMD_TURRET = 6
CMD_HEAD_TILT = 7

# Watchdog: stop motors if no command received within this many ms
WATCHDOG_TIMEOUT_MS = 5000

# Color name mapping (Pybricks Color -> float ID for transmission)
COLOR_MAP = {
    Color.NONE: 0.0,
    Color.BLACK: 1.0,
    Color.BLUE: 2.0,
    Color.GREEN: 3.0,
    Color.YELLOW: 4.0,
    Color.RED: 5.0,
    Color.WHITE: 6.0,
    Color.ORANGE: 7.0,
    Color.VIOLET: 8.0,
}

# I/O setup
keyboard = poll()
keyboard.register(stdin)
timer = StopWatch()


def read_sensor_state():
    """Pack current sensor state into 20 bytes."""
    return ustruct.pack(
        "!fffff",
        dist_sensor.distance() / 10,  # mm -> cm
        float(hub.imu.heading()),
        float(hub.imu.tilt()[0]),      # pitch
        float(hub.imu.tilt()[1]),      # roll
        float(left.angle()),
    )


def handle_command(cmd, value):
    """Execute a command and return response bytes.

    Motor commands use wait=False so they don't block BLE.
    Sensor reads return immediately.
    """
    try:
        if cmd == CMD_STRAIGHT:
            # Non-blocking: start driving, return immediately
            drive.straight(value, wait=False)
            # Wait briefly for motion to start, then respond
            wait(100)
        elif cmd == CMD_TURN:
            drive.turn(value, wait=False)
            wait(100)
        elif cmd == CMD_STOP:
            drive.stop()
            left.stop()
            right.stop()
            head_rotation.stop()
            head_tilt.stop()
        elif cmd == CMD_READ_DISTANCE:
            pass  # just return sensor state
        elif cmd == CMD_READ_COLOR:
            # Override distance_cm field with color ID in response
            color = color_sensor.color()
            color_id = COLOR_MAP.get(color, 0.0)
            return ustruct.pack(
                "!fffff",
                color_id,  # color ID instead of distance
                float(hub.imu.heading()),
                float(hub.imu.tilt()[0]),
                float(hub.imu.tilt()[1]),
                float(left.angle()),
            )
        elif cmd == CMD_TURRET:
            head_rotation.run_angle(200, value, wait=False)
            wait(100)
        elif cmd == CMD_HEAD_TILT:
            head_tilt.run_angle(200, value, wait=False)
            wait(100)
    except Exception:
        drive.stop()
        hub.light.on(Color.RED)
        wait(200)
        hub.light.on(Color.GREEN)

    return read_sensor_state()


# Signal ready via text (PC uses read_line() for handshake)
print("OK")
hub.light.on(Color.GREEN)
timer.reset()

while True:
    # Watchdog: stop motors if no command received recently
    if timer.time() > WATCHDOG_TIMEOUT_MS:
        drive.stop()
        head_rotation.stop()
        head_tilt.stop()
        hub.light.on(Color.ORANGE)

    # Poll with 100ms timeout — keeps BLE stack alive
    if keyboard.poll(100):
        data = stdin.buffer.read(8)
        if data and len(data) == 8:
            timer.reset()
            hub.light.on(Color.GREEN)
            cmd, value = ustruct.unpack("!if", data)
            response = handle_command(cmd, value)
            stdout.buffer.write(response)
