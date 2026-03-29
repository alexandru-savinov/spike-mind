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
