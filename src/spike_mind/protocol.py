"""Binary command protocol for SPIKE Prime communication.

Wire format:
  Command (host → hub): struct.pack("!if", cmd_id, value) = 8 bytes
  Response (hub → host): struct.pack("!fffff", dist, heading, pitch, roll, left_angle) = 20 bytes

Over BLE, commands are prefixed with 0x06 (Pybricks WRITE_STDIN).
Hub responses arrive as notifications prefixed with 0x01 (WRITE_STDOUT).
"""

import struct
from dataclasses import dataclass
from enum import IntEnum

# Pybricks BLE constants
SERVICE_UUID = "c5f50001-8280-46da-89f4-6d8051e4aeef"
CHAR_UUID = "c5f50002-8280-46da-89f4-6d8051e4aeef"
PYBRICKS_WRITE_STDIN = b"\x06"
PYBRICKS_WRITE_STDOUT = 0x01

COMMAND_FMT = "!if"  # big-endian: int32 cmd + float32 value
COMMAND_SIZE = struct.calcsize(COMMAND_FMT)  # 8 bytes

RESPONSE_FMT = "!fffff"  # 5 × float32
RESPONSE_SIZE = struct.calcsize(RESPONSE_FMT)  # 20 bytes


class Command(IntEnum):
    """Command IDs matching hub/main.py dispatch table."""
    STRAIGHT = 1    # value: distance in mm
    TURN = 2        # value: angle in degrees
    STOP = 3        # value: ignored (send 0.0)
    READ_DISTANCE = 4  # value: ignored
    READ_COLOR = 5     # value: ignored
    TURRET = 6         # value: angle in degrees


@dataclass(frozen=True, slots=True)
class SensorState:
    """Decoded hub response."""
    distance_cm: float    # ultrasonic, 0-200
    heading: float        # gyro heading in degrees
    tilt_pitch: float     # IMU pitch
    tilt_roll: float      # IMU roll
    left_angle: float     # left motor encoder degrees


def encode_command(cmd: Command, value: float = 0.0) -> bytes:
    """Encode a command for transmission to the hub."""
    return struct.pack(COMMAND_FMT, int(cmd), value)


def decode_response(data: bytes) -> SensorState:
    """Decode a sensor state response from the hub."""
    if len(data) != RESPONSE_SIZE:
        raise ValueError(f"Expected {RESPONSE_SIZE} bytes, got {len(data)}")
    fields = struct.unpack(RESPONSE_FMT, data)
    return SensorState(*fields)


def encode_ble_command(cmd: Command, value: float = 0.0) -> bytes:
    """Encode a command with the Pybricks BLE WRITE_STDIN prefix."""
    return PYBRICKS_WRITE_STDIN + encode_command(cmd, value)


def decode_ble_response(data: bytes) -> SensorState:
    """Decode a BLE notification, stripping the Pybricks prefix."""
    if not data or data[0] != PYBRICKS_WRITE_STDOUT:
        raise ValueError(f"Expected WRITE_STDOUT prefix (0x01), got 0x{data[0]:02x}" if data else "Empty data")
    return decode_response(data[1:])
