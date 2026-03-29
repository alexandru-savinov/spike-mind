"""Tests for the Robot API using MockTransport."""

import pytest
import pytest_asyncio
from spike_mind.robot import Robot, MAX_DISTANCE_MM, MAX_TURN_DEGREES
from spike_mind.transport import MockTransport


@pytest_asyncio.fixture
async def robot():
    transport = MockTransport()
    r = Robot(transport)
    await r.connect()
    yield r
    await r.disconnect()


@pytest_asyncio.fixture
async def obstacle_robot():
    """Robot with obstacles placed in the environment."""
    # Obstacle at (500, 0) with radius 50mm - directly ahead from origin
    transport = MockTransport(obstacles=[(500.0, 0.0, 50.0)])
    r = Robot(transport)
    await r.connect()
    yield r
    await r.disconnect()


@pytest_asyncio.fixture
async def color_robot():
    """Robot with color zones in the environment."""
    # Red zone at origin, blue zone at (200, 0)
    transport = MockTransport(color_zones=[
        (0.0, 0.0, 100.0, 5),   # red zone, radius 100mm at origin
        (200.0, 0.0, 50.0, 2),  # blue zone, radius 50mm at (200, 0)
    ])
    r = Robot(transport)
    await r.connect()
    yield r
    await r.disconnect()


@pytest_asyncio.fixture
async def noisy_robot():
    """Robot with sensor noise enabled."""
    transport = MockTransport(noise=2.0)
    r = Robot(transport)
    await r.connect()
    yield r, transport
    await r.disconnect()


class TestMovement:
    @pytest.mark.asyncio
    async def test_move_forward(self, robot):
        result = await robot.move_forward(10.0)
        assert "distance_cm" in result
        assert "heading" in result

    @pytest.mark.asyncio
    async def test_move_forward_exceeds_max(self, robot):
        with pytest.raises(ValueError, match="exceeds max"):
            await robot.move_forward(100.0)  # 1000mm > 500mm

    @pytest.mark.asyncio
    async def test_turn(self, robot):
        result = await robot.turn(90.0)
        assert "heading" in result

    @pytest.mark.asyncio
    async def test_turn_exceeds_max(self, robot):
        with pytest.raises(ValueError, match="exceeds max"):
            await robot.turn(400.0)

    @pytest.mark.asyncio
    async def test_stop(self, robot):
        result = await robot.stop()
        assert result["stopped"] is True


class TestSensors:
    @pytest.mark.asyncio
    async def test_read_distance(self, robot):
        result = await robot.read_distance()
        assert "distance_cm" in result
        assert 4.0 <= result["distance_cm"] <= 200.0

    @pytest.mark.asyncio
    async def test_read_color(self, robot):
        result = await robot.read_color()
        assert "color" in result
        assert "color_id" in result

    @pytest.mark.asyncio
    async def test_scan_surroundings(self, robot):
        result = await robot.scan_surroundings()
        assert "readings" in result
        assert len(result["readings"]) == 5
        angles = [r["angle"] for r in result["readings"]]
        assert angles == [-90, -45, 0, 45, 90]


class TestFollowLine:
    @pytest.mark.asyncio
    async def test_follow_line(self, robot):
        result = await robot.follow_line(5.0, 1.0)
        assert "distance_cm" in result

    @pytest.mark.asyncio
    async def test_follow_line_exceeds_duration(self, robot):
        with pytest.raises(ValueError, match="exceeds max"):
            await robot.follow_line(5.0, 15.0)


class TestObstacles:
    @pytest.mark.asyncio
    async def test_distance_changes_with_heading(self, obstacle_robot):
        """Obstacle at (500, 0) - should be close when facing east, far when facing north."""
        # Facing east (heading=0), obstacle is ahead
        result_east = await obstacle_robot.read_distance()
        # Turn to face north (heading=90)
        await obstacle_robot.turn(90.0)
        result_north = await obstacle_robot.read_distance()
        # Distance should be much less when facing the obstacle
        assert result_east["distance_cm"] < result_north["distance_cm"]

    @pytest.mark.asyncio
    async def test_obstacle_distance_decreases_as_approaching(self, obstacle_robot):
        """Moving toward obstacle should decrease distance reading."""
        dist_before = (await obstacle_robot.read_distance())["distance_cm"]
        await obstacle_robot.move_forward(10.0)  # 100mm toward obstacle
        dist_after = (await obstacle_robot.read_distance())["distance_cm"]
        assert dist_after < dist_before

    @pytest.mark.asyncio
    async def test_no_obstacles_preserves_default(self):
        """With empty obstacles list, behavior matches original."""
        transport = MockTransport(obstacles=[])
        r = Robot(transport)
        await r.connect()
        result = await r.read_distance()
        assert 4.0 <= result["distance_cm"] <= 200.0
        await r.disconnect()


class TestColorZones:
    @pytest.mark.asyncio
    async def test_color_at_origin(self, color_robot):
        """Robot starts at origin, which is in the red zone."""
        result = await color_robot.read_color()
        assert result["color_id"] == 5
        assert result["color"] == "red"

    @pytest.mark.asyncio
    async def test_color_outside_zones(self, color_robot):
        """Move far from any zone, should read no color."""
        # Move to (0, 500) - outside both zones
        await color_robot.turn(90.0)  # face north
        await color_robot.move_forward(50.0)  # 500mm north
        result = await color_robot.read_color()
        assert result["color_id"] == 0
        assert result["color"] == "none"

    @pytest.mark.asyncio
    async def test_color_in_second_zone(self, color_robot):
        """Move to the blue zone at (200, 0)."""
        await color_robot.move_forward(20.0)  # 200mm east
        result = await color_robot.read_color()
        assert result["color_id"] == 2
        assert result["color"] == "blue"

    @pytest.mark.asyncio
    async def test_no_color_zones_returns_zero(self):
        """With no color zones, always returns 0."""
        transport = MockTransport(color_zones=[])
        r = Robot(transport)
        await r.connect()
        result = await r.read_color()
        assert result["color_id"] == 0
        await r.disconnect()


class TestNoise:
    @pytest.mark.asyncio
    async def test_noise_varies_readings(self, noisy_robot):
        """With noise enabled, repeated readings should vary."""
        robot, transport = noisy_robot
        readings = []
        for _ in range(10):
            result = await robot.read_distance()
            readings.append(result["distance_cm"])
        # Not all readings should be identical
        assert len(set(readings)) > 1, "Noise should cause readings to vary"

    @pytest.mark.asyncio
    async def test_noise_within_range(self, noisy_robot):
        """Noisy readings should stay within a reasonable range of the base value."""
        robot, transport = noisy_robot
        # Base value at origin with no obstacles is ~100cm
        readings = []
        for _ in range(20):
            result = await robot.read_distance()
            readings.append(result["distance_cm"])
        # With noise=2.0, readings should be roughly 100 +/- a few
        for r in readings:
            assert 80.0 <= r <= 120.0, f"Reading {r} too far from expected ~100"

    @pytest.mark.asyncio
    async def test_zero_noise_preserves_determinism(self):
        """With noise=0, readings should be perfectly deterministic."""
        transport = MockTransport(noise=0.0)
        r = Robot(transport)
        await r.connect()
        readings = []
        for _ in range(5):
            result = await r.read_distance()
            readings.append(result["distance_cm"])
        assert len(set(readings)) == 1, "Zero noise should give identical readings"
        await r.disconnect()
