"""spike-mind hub bridge — runs on SPIKE Prime with Pybricks firmware.

Receives binary commands via BLE stdin, executes motor/sensor actions,
returns sensor state via stdout.

Command format: struct.pack("!if", cmd_id, value) = 8 bytes
Response format: struct.pack("!fffff", dist_cm, heading, pitch, roll, left_angle) = 20 bytes

Port assignments:
  A = left wheel motor
  B = right wheel motor
  C = turret motor (for ultrasonic sweep)
  D = ultrasonic sensor
  E = color sensor
  F = force sensor (bumper)
"""

import ustruct
from micropython import kbd_intr
from pybricks.hubs import PrimeHub
from pybricks.parameters import Port, Color
from pybricks.pupdevices import Motor, UltrasonicSensor, ColorSensor, ForceSensor
from pybricks.robotics import DriveBase
from pybricks.tools import wait, StopWatch
from uselect import poll
from usys import stdin, stdout

# Disable Ctrl-C on stdin so binary 0x03 doesn't raise KeyboardInterrupt
kbd_intr(-1)

# Hardware setup
hub = PrimeHub()
left = Motor(Port.A)
right = Motor(Port.B)
turret = Motor(Port.C)
dist_sensor = UltrasonicSensor(Port.D)
color_sensor = ColorSensor(Port.E)
force_sensor = ForceSensor(Port.F)

drive = DriveBase(left, right, wheel_diameter=56, axle_track=112)
drive.use_gyro(True)

# Command IDs (must match host protocol.py)
CMD_STRAIGHT = 1
CMD_TURN = 2
CMD_STOP = 3
CMD_READ_DISTANCE = 4
CMD_READ_COLOR = 5
CMD_TURRET = 6

# Watchdog: stop motors if no command received within this many ms
WATCHDOG_TIMEOUT_MS = 2000

# Color name mapping (Pybricks Color → float ID for transmission)
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
        dist_sensor.distance() / 10,  # mm → cm
        float(hub.imu.heading()),
        float(hub.imu.tilt()[0]),      # pitch
        float(hub.imu.tilt()[1]),      # roll
        float(left.angle()),
    )


def handle_command(cmd, value):
    """Execute a command and return response bytes."""
    if cmd == CMD_STRAIGHT:
        drive.straight(value)  # value = distance in mm
    elif cmd == CMD_TURN:
        drive.turn(value)      # value = angle in degrees
    elif cmd == CMD_STOP:
        drive.stop()
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
        turret.run_angle(200, value)  # value = angle in degrees, moderate speed

    return read_sensor_state()


# Main loop
hub.light.on(Color.GREEN)  # Signal ready
timer.reset()

while True:
    # Watchdog: stop motors if no command received recently
    if timer.time() > WATCHDOG_TIMEOUT_MS:
        drive.stop()
        turret.stop()
        hub.light.on(Color.ORANGE)  # Visual warning

    if keyboard.poll(0):
        data = stdin.buffer.read(8)
        if data and len(data) == 8:
            timer.reset()
            hub.light.on(Color.GREEN)
            cmd, value = ustruct.unpack("!if", data)
            response = handle_command(cmd, value)
            stdout.buffer.write(response)
    else:
        wait(1)  # Yield to avoid busy-spinning
