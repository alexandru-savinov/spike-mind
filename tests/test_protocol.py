"""Tests for the binary command protocol."""

import struct
import pytest
from spike_mind.protocol import (
    Command,
    SensorState,
    encode_command,
    decode_response,
    encode_ble_command,
    decode_ble_response,
    COMMAND_SIZE,
    RESPONSE_SIZE,
    PYBRICKS_WRITE_STDIN,
    PYBRICKS_WRITE_STDOUT,
)


class TestEncodeCommand:
    def test_straight_format(self):
        data = encode_command(Command.STRAIGHT, 150.0)
        assert len(data) == COMMAND_SIZE
        cmd, val = struct.unpack("!if", data)
        assert cmd == 1
        assert val == pytest.approx(150.0)

    def test_turn(self):
        data = encode_command(Command.TURN, -90.0)
        cmd, val = struct.unpack("!if", data)
        assert cmd == 2
        assert val == pytest.approx(-90.0)

    def test_stop_ignores_value(self):
        data = encode_command(Command.STOP)
        cmd, val = struct.unpack("!if", data)
        assert cmd == 3
        assert val == pytest.approx(0.0)

    def test_all_commands_produce_8_bytes(self):
        for cmd in Command:
            assert len(encode_command(cmd, 42.0)) == 8


class TestDecodeResponse:
    def test_round_trip(self):
        original = SensorState(25.0, 180.0, 5.0, -3.0, 720.0)
        raw = struct.pack("!fffff", *[original.distance_cm, original.heading,
                                       original.tilt_pitch, original.tilt_roll,
                                       original.left_angle])
        decoded = decode_response(raw)
        assert decoded.distance_cm == pytest.approx(25.0)
        assert decoded.heading == pytest.approx(180.0)
        assert decoded.tilt_pitch == pytest.approx(5.0)
        assert decoded.tilt_roll == pytest.approx(-3.0)
        assert decoded.left_angle == pytest.approx(720.0)

    def test_wrong_size_raises(self):
        with pytest.raises(ValueError, match="Expected 20 bytes"):
            decode_response(b"\x00" * 10)

    def test_zero_state(self):
        raw = struct.pack("!fffff", 0.0, 0.0, 0.0, 0.0, 0.0)
        state = decode_response(raw)
        assert state.distance_cm == 0.0


class TestBleWrappers:
    def test_ble_command_has_prefix(self):
        data = encode_ble_command(Command.STRAIGHT, 100.0)
        assert data[0:1] == PYBRICKS_WRITE_STDIN
        assert len(data) == COMMAND_SIZE + 1

    def test_ble_response_strips_prefix(self):
        payload = struct.pack("!fffff", 10.0, 90.0, 1.0, 2.0, 360.0)
        ble_data = bytes([PYBRICKS_WRITE_STDOUT]) + payload
        state = decode_ble_response(ble_data)
        assert state.distance_cm == pytest.approx(10.0)
        assert state.heading == pytest.approx(90.0)

    def test_ble_response_wrong_prefix_raises(self):
        payload = struct.pack("!fffff", 0.0, 0.0, 0.0, 0.0, 0.0)
        bad_data = bytes([0xFF]) + payload
        with pytest.raises(ValueError, match="Expected WRITE_STDOUT"):
            decode_ble_response(bad_data)

    def test_ble_response_empty_raises(self):
        with pytest.raises(ValueError):
            decode_ble_response(b"")


class TestCommandEnum:
    def test_all_unique_ids(self):
        ids = [c.value for c in Command]
        assert len(ids) == len(set(ids))

    def test_ids_are_positive(self):
        for cmd in Command:
            assert cmd.value > 0
