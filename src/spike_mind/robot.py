"""High-level robot API.

Composes Transport + protocol to provide human-readable async methods.
All motor commands enforce safety bounds.
"""

from __future__ import annotations

from spike_mind.protocol import Command, SensorState, encode_command, decode_response
from spike_mind.transport import Transport

# Safety bounds
MAX_DISTANCE_MM = 500.0
MAX_TURN_DEGREES = 360.0
MAX_TURRET_DEGREES = 180.0
MAX_HEAD_TILT_DEGREES = 90.0

# Color ID -> name mapping (matches hub/main.py COLOR_MAP)
COLOR_NAMES = {
    0: "none",
    1: "black",
    2: "blue",
    3: "green",
    4: "yellow",
    5: "red",
    6: "white",
    7: "orange",
    8: "violet",
}


class Robot:
    """Async robot controller."""

    def __init__(self, transport: Transport) -> None:
        self._transport = transport

    async def connect(self) -> None:
        await self._transport.connect()

    async def disconnect(self) -> None:
        await self._transport.disconnect()

    async def _command(self, cmd: Command, value: float = 0.0) -> SensorState:
        """Send a command and return the sensor state response."""
        await self._transport.send(encode_command(cmd, value))
        data = await self._transport.receive()
        return decode_response(data)

    async def move_forward(self, distance_cm: float) -> dict:
        """Move forward by distance_cm. Returns actual sensor state."""
        distance_mm = distance_cm * 10
        if abs(distance_mm) > MAX_DISTANCE_MM:
            raise ValueError(f"Distance {distance_cm}cm exceeds max {MAX_DISTANCE_MM/10}cm")
        state = await self._command(Command.STRAIGHT, distance_mm)
        return {"distance_cm": state.distance_cm, "heading": state.heading}

    async def turn(self, angle_degrees: float) -> dict:
        """Turn by angle_degrees (positive = clockwise). Returns heading."""
        if abs(angle_degrees) > MAX_TURN_DEGREES:
            raise ValueError(f"Angle {angle_degrees}\u00b0 exceeds max {MAX_TURN_DEGREES}\u00b0")
        state = await self._command(Command.TURN, angle_degrees)
        return {"heading": state.heading}

    async def stop(self) -> dict:
        """Emergency stop all motors."""
        state = await self._command(Command.STOP)
        return {"stopped": True, "heading": state.heading}

    async def read_distance(self) -> dict:
        """Read ultrasonic distance sensor."""
        state = await self._command(Command.READ_DISTANCE)
        return {"distance_cm": state.distance_cm}

    async def read_color(self) -> dict:
        """Read color sensor."""
        state = await self._command(Command.READ_COLOR)
        color_id = int(state.distance_cm)  # color ID packed in distance field
        color_name = COLOR_NAMES.get(color_id, f"unknown({color_id})")
        return {"color": color_name, "color_id": color_id}

    async def scan_surroundings(self) -> dict:
        """Sweep turret and take distance readings at multiple angles."""
        readings = []
        for angle in [-90, -45, 0, 45, 90]:
            await self._command(Command.TURRET, angle)
            state = await self._command(Command.READ_DISTANCE)
            readings.append({"angle": angle, "distance_cm": state.distance_cm})
        # Return turret to center
        await self._command(Command.TURRET, 0)
        return {"readings": readings}

    async def head_tilt(self, angle_degrees: float) -> dict:
        """Tilt head and arm by angle_degrees. Positive = up, negative = down."""
        if abs(angle_degrees) > MAX_HEAD_TILT_DEGREES:
            raise ValueError(f"Angle {angle_degrees}° exceeds max {MAX_HEAD_TILT_DEGREES}°")
        state = await self._command(Command.HEAD_TILT, angle_degrees)
        return {"heading": state.heading, "tilt_angle": angle_degrees}

    async def follow_line(self, speed: float, duration_s: float) -> dict:
        """Follow line for duration_s seconds. Simplified: just move forward."""
        if duration_s > 10.0:
            raise ValueError(f"Duration {duration_s}s exceeds max 10s")
        distance_mm = speed * duration_s * 10  # rough approximation
        if abs(distance_mm) > MAX_DISTANCE_MM:
            distance_mm = MAX_DISTANCE_MM if distance_mm > 0 else -MAX_DISTANCE_MM
        state = await self._command(Command.STRAIGHT, distance_mm)
        return {"distance_cm": state.distance_cm, "heading": state.heading}
