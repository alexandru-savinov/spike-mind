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
    via the Pybricks GATT characteristic. Auto-reconnects on failure
    using exponential backoff.
    """

    def __init__(
        self,
        device_address: str = "",
        timeout: float = 10.0,
        max_retries: int = 3,
        backoff_base: float = 1.0,
        connect_timeout: float = 15.0,
    ) -> None:
        self._device_address = device_address
        self._timeout = timeout
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._connect_timeout = connect_timeout
        self._client: typing.Any = None  # BleakClient, lazily imported
        self._address: str | None = None  # resolved address for reconnect
        self._response_queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._reconnecting = False
        self._bleak_client_cls: type | None = None

    async def connect(self) -> None:
        from bleak import BleakClient, BleakScanner
        await self._connect_impl(BleakClient, BleakScanner)

    async def _connect_impl(
        self, bleak_client_cls: type, bleak_scanner_cls: type
    ) -> None:
        if self._device_address:
            self._address = self._device_address
        elif self._address is None:
            device = await bleak_scanner_cls.find_device_by_filter(
                lambda d, adv: SERVICE_UUID.lower() in [
                    s.lower() for s in (adv.service_uuids or [])
                ],
                timeout=self._connect_timeout,
            )
            if device is None:
                raise ConnectionError(
                    f"No SPIKE hub found (scanned {self._connect_timeout}s for service {SERVICE_UUID})"
                )
            self._address = device.address

        self._bleak_client_cls = bleak_client_cls
        self._client = bleak_client_cls(self._address)
        await asyncio.wait_for(
            self._client.connect(), timeout=self._connect_timeout
        )
        await self._client.start_notify(CHAR_UUID, self._on_notification)

    async def _reconnect(self) -> None:
        """Attempt to reconnect with exponential backoff."""
        if self._reconnecting:
            return
        self._reconnecting = True
        try:
            # Clean up old client
            if self._client:
                try:
                    await self._client.disconnect()
                except Exception:
                    pass
                self._client = None

            for attempt in range(self._max_retries):
                delay = self._backoff_base * (2 ** attempt)
                await asyncio.sleep(delay)
                try:
                    if self._bleak_client_cls is not None:
                        await self._connect_impl(self._bleak_client_cls, type(None))
                    else:
                        await self.connect()
                    return  # success
                except Exception:
                    if attempt == self._max_retries - 1:
                        raise ConnectionError(
                            f"BLE reconnect failed after {self._max_retries} attempts"
                        )
        finally:
            self._reconnecting = False

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
            raise ConnectionError("BLE disconnected, reconnecting...")
        try:
            await self._client.write_gatt_char(
                CHAR_UUID, PYBRICKS_WRITE_STDIN + data, response=False
            )
        except Exception:
            await self._reconnect()
            raise ConnectionError(
                "BLE disconnected during send, reconnecting... retry the command"
            )

    async def receive(self) -> bytes:
        try:
            return await asyncio.wait_for(
                self._response_queue.get(), timeout=self._timeout
            )
        except Exception:
            await self._reconnect()
            raise ConnectionError(
                "BLE disconnected during receive, reconnecting... retry the command"
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

        elif cmd == Command.HEAD_TILT:
            pass  # Simulate tilt (no position change)

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
