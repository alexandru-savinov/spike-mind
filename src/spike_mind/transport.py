"""Transport layer for SPIKE Prime communication.

Two implementations:
  BleTransport  — real BLE via bleak
  MockTransport — in-memory simulation for development without hardware
"""

from __future__ import annotations

import asyncio
import math
import typing

from spike_mind.protocol import (
    Command,
    SensorState,
    COMMAND_SIZE,
    RESPONSE_SIZE,
    SERVICE_UUID,
    CHAR_UUID,
    PYBRICKS_WRITE_STDIN,
    PYBRICKS_WRITE_STDOUT,
    encode_command,
    decode_response,
)


class Transport(typing.Protocol):
    """Interface for sending commands and receiving responses."""

    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...
    async def send(self, data: bytes) -> None: ...
    async def receive(self) -> bytes: ...


class BleTransport:
    """Real BLE transport using bleak.

    Discovers SPIKE hub by service UUID, connects, and communicates
    via the Pybricks GATT characteristic.
    """

    def __init__(self, device_address: str = "", timeout: float = 10.0) -> None:
        self._device_address = device_address
        self._timeout = timeout
        self._client: typing.Any = None  # BleakClient, lazily imported
        self._response_queue: asyncio.Queue[bytes] = asyncio.Queue()

    async def connect(self) -> None:
        from bleak import BleakClient, BleakScanner

        if self._device_address:
            address = self._device_address
        else:
            device = await BleakScanner.find_device_by_filter(
                lambda d, adv: SERVICE_UUID.lower() in [
                    s.lower() for s in (adv.service_uuids or [])
                ],
                timeout=self._timeout,
            )
            if device is None:
                raise ConnectionError(
                    f"No SPIKE hub found (scanned {self._timeout}s for service {SERVICE_UUID})"
                )
            address = device.address

        self._client = BleakClient(address)
        await self._client.connect()
        await self._client.start_notify(CHAR_UUID, self._on_notification)

    def _on_notification(self, _sender: int, data: bytearray) -> None:
        """Handle BLE notifications from the hub."""
        if data and data[0] == PYBRICKS_WRITE_STDOUT:
            self._response_queue.put_nowait(bytes(data[1:]))

    async def disconnect(self) -> None:
        if self._client and self._client.is_connected:
            await self._client.disconnect()
        self._client = None

    async def send(self, data: bytes) -> None:
        if not self._client or not self._client.is_connected:
            raise ConnectionError("Not connected")
        await self._client.write_gatt_char(
            CHAR_UUID, PYBRICKS_WRITE_STDIN + data, response=False
        )

    async def receive(self) -> bytes:
        return await asyncio.wait_for(
            self._response_queue.get(), timeout=self._timeout
        )


class MockTransport:
    """In-memory transport that simulates robot state.

    Processes commands from protocol.py and returns realistic sensor data.
    Useful for developing and testing the agent loop without hardware.
    """

    def __init__(self) -> None:
        self._connected = False
        # Simulated robot state
        self._x = 0.0          # position in mm
        self._y = 0.0
        self._heading = 0.0    # degrees
        self._left_angle = 0.0 # encoder degrees
        self._turret_angle = 0.0
        self._response_buf: bytes = b""

    async def connect(self) -> None:
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False

    async def send(self, data: bytes) -> None:
        if not self._connected:
            raise ConnectionError("Not connected")
        if len(data) != COMMAND_SIZE:
            raise ValueError(f"Expected {COMMAND_SIZE} bytes, got {len(data)}")

        import struct
        cmd_id, value = struct.unpack("!if", data)
        cmd = Command(cmd_id)

        if cmd == Command.STRAIGHT:
            # Move forward/backward by value mm
            rad = math.radians(self._heading)
            self._x += value * math.cos(rad)
            self._y += value * math.sin(rad)
            # Approximate encoder: 1mm ≈ 6.4 degrees for 56mm wheel
            self._left_angle += value * 360 / (math.pi * 56)

        elif cmd == Command.TURN:
            self._heading = (self._heading + value) % 360

        elif cmd == Command.STOP:
            pass  # Nothing to simulate

        elif cmd == Command.READ_DISTANCE:
            pass  # Just return state

        elif cmd == Command.READ_COLOR:
            pass  # Return state with mock color (0.0 = none)

        elif cmd == Command.TURRET:
            self._turret_angle += value

        # Build response
        state = SensorState(
            distance_cm=self._mock_distance(),
            heading=self._heading,
            tilt_pitch=0.0,
            tilt_roll=0.0,
            left_angle=self._left_angle,
        )
        import struct as s
        self._response_buf = s.pack(
            "!fffff",
            state.distance_cm,
            state.heading,
            state.tilt_pitch,
            state.tilt_roll,
            state.left_angle,
        )

    async def receive(self) -> bytes:
        if not self._response_buf:
            raise RuntimeError("No pending response (send a command first)")
        data = self._response_buf
        self._response_buf = b""
        return data

    def _mock_distance(self) -> float:
        """Simulate ultrasonic sensor: return 100cm unless near origin."""
        dist_from_origin = math.sqrt(self._x ** 2 + self._y ** 2)
        # Simulate: closer to origin = closer to a wall
        return min(200.0, max(4.0, 100.0 - dist_from_origin / 10))

    @property
    def position(self) -> tuple[float, float]:
        """Current simulated position (mm). For test assertions."""
        return (self._x, self._y)

    @property
    def heading(self) -> float:
        """Current simulated heading (degrees). For test assertions."""
        return self._heading
