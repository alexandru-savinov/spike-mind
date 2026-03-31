"""Transport layer for SPIKE Prime communication.

Three implementations:
  PybricksTransport — full lifecycle via pybricksdev (connect + upload + run + I/O)
  BleTransport      — raw BLE via bleak (requires pre-loaded hub program)
  MockTransport     — in-memory simulation for development without hardware
"""

from __future__ import annotations

import asyncio
import math
import random
import typing
from pathlib import Path

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


class PybricksTransport:
    """BLE transport using pybricksdev for full hub lifecycle.

    Handles: BLE discovery → connect → compile & upload hub program →
    start program → binary stdin/stdout I/O.  This is the recommended
    transport for real hardware because it manages the entire Pybricks
    protocol (program download, stdin/stdout framing) correctly.

    Smart connect logic:
      1. If hub program is already running → just subscribe to stdout
      2. If program is stored in flash → start it without re-uploading
      3. Otherwise → full compile + upload + start
    """

    def __init__(
        self,
        hub_name: str = "",
        hub_program: str = "",
        timeout: float = 10.0,
        connect_timeout: float = 15.0,
        max_retries: int = 3,
        backoff_base: float = 1.0,
    ) -> None:
        self._hub_name = hub_name
        self._hub_program = hub_program or str(
            Path(__file__).resolve().parent.parent.parent / "hub" / "main.py"
        )
        self._timeout = timeout
        self._connect_timeout = connect_timeout
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._hub: typing.Any = None  # PybricksHubBLE, lazily imported
        self._response_queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._subscription: typing.Any = None  # RxPy Disposable
        self._reconnecting = False

    def _subscribe_stdout(self) -> None:
        """Subscribe to hub stdout with thread-safe queue delivery."""
        loop = asyncio.get_running_loop()
        self._subscription = self._hub.stdout_observable.subscribe(
            on_next=lambda data: loop.call_soon_threadsafe(
                self._response_queue.put_nowait, data
            )
        )

    def _is_program_running(self) -> bool:
        """Check if a user program is currently running on the hub."""
        from pybricksdev.ble.pybricks import StatusFlag
        status = self._hub.status_observable.value
        return bool(status & StatusFlag.USER_PROGRAM_RUNNING)

    async def connect(self) -> None:
        from pybricksdev.ble import find_device
        from pybricksdev.connections.pybricks import PybricksHubBLE

        label = f" ({self._hub_name})" if self._hub_name else ""
        print(f"  scanning for hub{label}...")
        device = await find_device(self._hub_name or None)

        self._hub = PybricksHubBLE(device)
        await self._hub.connect()
        self._hub.print_output = False
        self._hub._enable_line_handler = False
        self._subscribe_stdout()

        # Smart connect: skip upload if program is already running or stored
        if self._is_program_running():
            print("  hub program already running, skipping upload.")
            return

        # Try starting stored program (no upload needed)
        try:
            print("  starting stored program...")
            await self._hub.start_user_program()
            # Give the program a moment to start, then verify
            await asyncio.sleep(0.5)
            if self._is_program_running():
                print("  hub program started from flash.")
                return
        except Exception:
            pass  # No stored program or old firmware — fall through to upload

        # Full upload
        print(f"  uploading {Path(self._hub_program).name}...")
        await self._hub.run(
            self._hub_program,
            wait=False,
            print_output=False,
            line_handler=False,
        )

    async def disconnect(self) -> None:
        if self._subscription:
            self._subscription.dispose()
            self._subscription = None
        if self._hub:
            try:
                await self._hub.disconnect()
            except Exception:
                pass
            self._hub = None

    async def _reconnect(self) -> None:
        """Reconnect with exponential backoff."""
        if self._reconnecting:
            return
        self._reconnecting = True
        try:
            await self.disconnect()
            # Drain stale data
            while not self._response_queue.empty():
                self._response_queue.get_nowait()

            for attempt in range(self._max_retries):
                if attempt > 0:
                    delay = self._backoff_base * (2 ** (attempt - 1))
                    print(f"  reconnect attempt {attempt + 1}/{self._max_retries} "
                          f"(waiting {delay:.1f}s)...")
                    await asyncio.sleep(delay)
                try:
                    await self.connect()
                    print("  reconnected!")
                    return
                except Exception:
                    if attempt == self._max_retries - 1:
                        raise ConnectionError(
                            f"Reconnect failed after {self._max_retries} attempts"
                        )
        finally:
            self._reconnecting = False

    async def send(self, data: bytes) -> None:
        if not self._hub:
            await self._reconnect()
        try:
            await self._hub.write(data)
        except Exception:
            await self._reconnect()
            raise ConnectionError(
                "BLE disconnected during send, reconnected... retry the command"
            )

    async def receive(self) -> bytes:
        """Receive a complete RESPONSE_SIZE response, reassembling BLE fragments."""
        buf = b""
        deadline = asyncio.get_event_loop().time() + self._timeout
        while len(buf) < RESPONSE_SIZE:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                raise TimeoutError(
                    f"No complete response within {self._timeout}s "
                    f"(got {len(buf)}/{RESPONSE_SIZE} bytes)"
                )
            try:
                chunk = await asyncio.wait_for(
                    self._response_queue.get(), timeout=remaining
                )
                buf += chunk
            except asyncio.TimeoutError:
                raise TimeoutError(
                    f"No complete response within {self._timeout}s "
                    f"(got {len(buf)}/{RESPONSE_SIZE} bytes)"
                )
        return buf[:RESPONSE_SIZE]


class BleTransport:
    """Raw BLE transport using bleak (requires hub program already running).

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
        self._bleak_scanner_cls: type | None = None

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
        self._bleak_scanner_cls = bleak_scanner_cls
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
            # Drain stale notifications from the old connection
            while not self._response_queue.empty():
                self._response_queue.get_nowait()

            for attempt in range(self._max_retries):
                if attempt > 0:
                    delay = self._backoff_base * (2 ** (attempt - 1))
                    await asyncio.sleep(delay)
                try:
                    if self._bleak_client_cls is not None and self._bleak_scanner_cls is not None:
                        await self._connect_impl(self._bleak_client_cls, self._bleak_scanner_cls)
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
            await self._reconnect()
            raise ConnectionError("BLE disconnected, reconnected... retry the command")
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
        except asyncio.TimeoutError:
            if self._client and self._client.is_connected:
                raise TimeoutError(
                    f"No response within {self._timeout}s (connection still active)"
                )
            await self._reconnect()
            raise ConnectionError(
                "BLE disconnected during receive, reconnected... retry the command"
            )


class MockTransport:
    """In-memory transport that simulates robot state.

    Processes commands from protocol.py and returns realistic sensor data.
    Useful for developing and testing the agent loop without hardware.
    """

    def __init__(
        self,
        obstacles: list[tuple[float, float, float]] | None = None,
        color_zones: list[tuple[float, float, float, int]] | None = None,
        noise: float = 0.0,
    ) -> None:
        self._connected = False
        # Simulated robot state
        self._x = 0.0          # position in mm
        self._y = 0.0
        self._heading = 0.0    # degrees
        self._left_angle = 0.0 # encoder degrees
        self._turret_angle = 0.0
        self._response_buf: bytes = b""
        # Environment simulation
        self._obstacles = obstacles or []  # (x, y, radius) in mm
        self._color_zones = color_zones or []  # (x, y, radius, color_id) in mm
        self._noise = noise
        self._rng = random.Random(42)  # deterministic by default

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
            pass  # Handled in response building below

        elif cmd == Command.TURRET:
            self._turret_angle = value

        elif cmd == Command.HEAD_TILT:
            pass  # Simulate tilt (no position change)

        # Build response
        if cmd == Command.READ_COLOR:
            distance_field = float(self._mock_color_id())
        else:
            distance_field = self._mock_distance()

        heading = self._heading
        tilt_pitch = 0.0
        tilt_roll = 0.0
        left_angle = self._left_angle

        # Apply noise if configured (skip distance_field for color reads — it's a categorical ID)
        if self._noise > 0:
            if cmd != Command.READ_COLOR:
                distance_field += self._rng.gauss(0, self._noise)
            heading += self._rng.gauss(0, self._noise)
            tilt_pitch += self._rng.gauss(0, self._noise)
            tilt_roll += self._rng.gauss(0, self._noise)
            left_angle += self._rng.gauss(0, self._noise)

        state = SensorState(
            distance_cm=distance_field,
            heading=heading,
            tilt_pitch=tilt_pitch,
            tilt_roll=tilt_roll,
            left_angle=left_angle,
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
        """Simulate ultrasonic sensor: distance to nearest obstacle along heading.

        If obstacles are configured, casts a ray from the robot position along
        the current heading (plus turret angle) and returns the distance to the
        nearest obstacle surface in cm. Otherwise falls back to the simple
        origin-based distance model.
        """
        if not self._obstacles:
            dist_from_origin = math.sqrt(self._x ** 2 + self._y ** 2)
            return min(200.0, max(4.0, 100.0 - dist_from_origin / 10))

        ray_angle = math.radians(self._heading + self._turret_angle)
        dx = math.cos(ray_angle)
        dy = math.sin(ray_angle)
        min_dist_cm = 200.0  # max sensor range

        for ox, oy, r in self._obstacles:
            # Vector from robot to obstacle center
            fx = self._x - ox
            fy = self._y - oy
            a = dx * dx + dy * dy  # always 1 for unit direction
            b = 2 * (fx * dx + fy * dy)
            c = fx * fx + fy * fy - r * r
            discriminant = b * b - 4 * a * c
            if discriminant < 0:
                continue  # ray misses obstacle
            sqrt_disc = math.sqrt(discriminant)
            t1 = (-b - sqrt_disc) / (2 * a)
            t2 = (-b + sqrt_disc) / (2 * a)
            # Take the nearest positive intersection (in front of robot)
            t = t1 if t1 > 0 else t2
            if t > 0:
                dist_cm = t / 10.0  # mm to cm
                min_dist_cm = min(min_dist_cm, dist_cm)

        return max(4.0, min_dist_cm)

    def _mock_color_id(self) -> int:
        """Return color_id based on robot position within color zones."""
        for zx, zy, zr, color_id in self._color_zones:
            dx = self._x - zx
            dy = self._y - zy
            if dx * dx + dy * dy <= zr * zr:
                return color_id
        return 0  # no color

    @property
    def position(self) -> tuple[float, float]:
        """Current simulated position (mm). For test assertions."""
        return (self._x, self._y)

    @property
    def heading(self) -> float:
        """Current simulated heading (degrees). For test assertions."""
        return self._heading
