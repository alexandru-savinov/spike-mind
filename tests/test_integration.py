"""Integration tests: agent loop against MockTransport.

Tests the full pipeline: MockTransport -> Robot -> execute_tool -> run_agent,
with the Anthropic API mocked to simulate Claude's tool-use responses.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from spike_mind.agent import execute_tool, run_agent
from spike_mind.robot import Robot
from spike_mind.transport import MockTransport


# ---------------------------------------------------------------------------
# Helpers to build fake Anthropic API responses
# ---------------------------------------------------------------------------

@dataclass
class _TextBlock:
    type: str = "text"
    text: str = ""


@dataclass
class _ToolUseBlock:
    type: str = "tool_use"
    id: str = ""
    name: str = ""
    input: dict = None

    def __post_init__(self):
        if self.input is None:
            self.input = {}


@dataclass
class _FakeResponse:
    content: list = None

    def __post_init__(self):
        if self.content is None:
            self.content = []


def _tool_response(tool_id: str, name: str, args: dict | None = None) -> _FakeResponse:
    """Build a fake API response containing a single tool_use block."""
    return _FakeResponse(content=[
        _ToolUseBlock(type="tool_use", id=tool_id, name=name, input=args or {}),
    ])


def _text_response(text: str) -> _FakeResponse:
    """Build a fake API response containing only text (no tool calls)."""
    return _FakeResponse(content=[_TextBlock(type="text", text=text)])


def _multi_tool_response(tools: list[tuple[str, str, dict]]) -> _FakeResponse:
    """Build a fake API response with multiple tool_use blocks."""
    blocks = [
        _ToolUseBlock(type="tool_use", id=tid, name=name, input=args)
        for tid, name, args in tools
    ]
    return _FakeResponse(content=blocks)


def _make_mock_client(responses: list[_FakeResponse]) -> AsyncMock:
    """Return a mock AsyncAnthropic whose messages.create returns responses in order."""
    client = AsyncMock()
    client.messages.create = AsyncMock(side_effect=responses)
    return client


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_transport_with_env():
    """MockTransport with obstacles and color zones for integration testing."""
    return MockTransport(
        obstacles=[
            (500.0, 0.0, 50.0),    # obstacle ahead at 500mm
            (0.0, 400.0, 60.0),    # obstacle to the north
        ],
        color_zones=[
            (0.0, 0.0, 100.0, 5),    # red zone at origin
            (200.0, 0.0, 50.0, 2),   # blue zone ahead
        ],
    )


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------

class TestAgentIntegration:
    """Integration tests running the agent loop against MockTransport."""

    @pytest.mark.asyncio
    async def test_agent_completes_exploration(self, mock_transport_with_env):
        """Agent completes a multi-turn exploration using multiple tool types."""
        transport = mock_transport_with_env
        robot = Robot(transport)
        await robot.connect()

        # Simulate an agent that: reads distance, reads color, moves, scans, then finishes
        responses = [
            _tool_response("t1", "read_distance"),
            _tool_response("t2", "read_color"),
            _tool_response("t3", "move_forward", {"distance_cm": 10.0}),
            _tool_response("t4", "scan_surroundings"),
            _tool_response("t5", "turn", {"angle_degrees": 45.0}),
            _tool_response("t6", "read_distance"),
            _text_response("Exploration complete. I found a red zone at the start and obstacles ahead."),
        ]

        mock_client = _make_mock_client(responses)
        with patch("spike_mind.agent.AsyncAnthropic", return_value=mock_client):
            result = await run_agent(robot, "Explore the area", max_turns=20)

        assert "Exploration complete" in result
        # Verify the API was called the expected number of times
        assert mock_client.messages.create.call_count == len(responses)
        await robot.disconnect()

    @pytest.mark.asyncio
    async def test_agent_uses_multiple_tool_types(self, mock_transport_with_env):
        """Verify the agent actually exercises multiple distinct tool types."""
        transport = mock_transport_with_env
        robot = Robot(transport)
        await robot.connect()

        responses = [
            _tool_response("t1", "read_distance"),
            _tool_response("t2", "move_forward", {"distance_cm": 5.0}),
            _tool_response("t3", "turn", {"angle_degrees": 90.0}),
            _tool_response("t4", "read_color"),
            _tool_response("t5", "head_tilt", {"angle_degrees": 15.0}),
            _text_response("Done exploring."),
        ]

        tool_names_used = []
        original_execute = execute_tool.__wrapped__ if hasattr(execute_tool, '__wrapped__') else None

        async def tracking_execute(r, name, args):
            tool_names_used.append(name)
            return await execute_tool(r, name, args)

        mock_client = _make_mock_client(responses)
        with patch("spike_mind.agent.AsyncAnthropic", return_value=mock_client), \
             patch("spike_mind.agent.execute_tool", side_effect=tracking_execute):
            result = await run_agent(robot, "Look around", max_turns=20)

        assert result == "Done exploring."
        assert len(set(tool_names_used)) >= 3, f"Expected 3+ tool types, got {set(tool_names_used)}"
        await robot.disconnect()

    @pytest.mark.asyncio
    async def test_agent_max_turns_reached(self, mock_transport_with_env):
        """Agent that never stops calling tools hits max_turns gracefully."""
        transport = mock_transport_with_env
        robot = Robot(transport)
        await robot.connect()

        # Generate more tool calls than max_turns
        responses = [
            _tool_response(f"t{i}", "read_distance") for i in range(10)
        ]

        mock_client = _make_mock_client(responses)
        with patch("spike_mind.agent.AsyncAnthropic", return_value=mock_client):
            result = await run_agent(robot, "Keep checking distance", max_turns=5)

        assert result == "(max turns reached)"
        assert mock_client.messages.create.call_count == 5
        await robot.disconnect()

    @pytest.mark.asyncio
    async def test_agent_parallel_tool_calls(self, mock_transport_with_env):
        """Agent can issue multiple tool calls in a single turn."""
        transport = mock_transport_with_env
        robot = Robot(transport)
        await robot.connect()

        responses = [
            _multi_tool_response([
                ("t1", "read_distance", {}),
                ("t2", "read_color", {}),
            ]),
            _tool_response("t3", "move_forward", {"distance_cm": 5.0}),
            _text_response("Checked sensors and moved."),
        ]

        mock_client = _make_mock_client(responses)
        with patch("spike_mind.agent.AsyncAnthropic", return_value=mock_client):
            result = await run_agent(robot, "Check sensors then move", max_turns=20)

        assert "Checked sensors" in result
        await robot.disconnect()


class TestAgentStress:
    """Longer-running stress tests for the agent loop."""

    @pytest.mark.slow
    @pytest.mark.asyncio
    async def test_sustained_100_turn_session(self):
        """Run the agent for 100 turns to verify no errors accumulate."""
        transport = MockTransport(
            obstacles=[(500.0, 0.0, 50.0), (0.0, 500.0, 50.0), (-300.0, -300.0, 80.0)],
            color_zones=[(0.0, 0.0, 100.0, 5), (200.0, 0.0, 50.0, 2), (-100.0, 200.0, 75.0, 3)],
            noise=1.0,
        )
        robot = Robot(transport)
        await robot.connect()

        # Build 99 tool-call turns + 1 text response
        responses = []
        tool_cycle = [
            ("read_distance", {}),
            ("move_forward", {"distance_cm": 5.0}),
            ("turn", {"angle_degrees": 30.0}),
            ("read_color", {}),
            ("scan_surroundings", {}),
            ("read_distance", {}),
            ("move_forward", {"distance_cm": -3.0}),
            ("turn", {"angle_degrees": -45.0}),
            ("head_tilt", {"angle_degrees": 10.0}),
            ("stop", {}),
        ]
        for i in range(100):
            name, args = tool_cycle[i % len(tool_cycle)]
            responses.append(_tool_response(f"t{i}", name, args))
        # After 100 tool turns, agent finishes
        responses.append(_text_response("100-turn session complete, no errors."))

        mock_client = _make_mock_client(responses)
        with patch("spike_mind.agent.AsyncAnthropic", return_value=mock_client):
            result = await run_agent(robot, "Explore continuously", max_turns=200)

        assert "100-turn session complete" in result
        # All 101 responses consumed (100 tool + 1 text)
        assert mock_client.messages.create.call_count == 101
        await robot.disconnect()


class TestErrorSurfacing:
    """Test that transport errors are surfaced to the agent and it continues."""

    @pytest.mark.asyncio
    async def test_transport_failure_mid_session(self):
        """A transport that fails once mid-session; agent receives error and continues."""
        transport = MockTransport(
            obstacles=[(500.0, 0.0, 50.0)],
            color_zones=[(0.0, 0.0, 100.0, 5)],
        )
        robot = Robot(transport)
        await robot.connect()

        # After the first successful command, make the transport fail on the next send
        original_send = transport.send
        call_count = 0

        async def failing_send(data: bytes) -> None:
            nonlocal call_count
            call_count += 1
            if call_count == 2:  # fail on second command
                raise ConnectionError("BLE disconnected, reconnecting...")
            return await original_send(data)

        transport.send = failing_send

        # Agent sequence: read_distance (ok), move_forward (fails), move_forward (retry ok), done
        responses = [
            _tool_response("t1", "read_distance"),
            _tool_response("t2", "move_forward", {"distance_cm": 5.0}),
            # After error, agent tries again
            _tool_response("t3", "move_forward", {"distance_cm": 5.0}),
            _text_response("Recovered from error and completed task."),
        ]

        mock_client = _make_mock_client(responses)
        with patch("spike_mind.agent.AsyncAnthropic", return_value=mock_client):
            result = await run_agent(robot, "Move forward carefully", max_turns=20)

        assert "Recovered from error" in result
        await robot.disconnect()

    @pytest.mark.asyncio
    async def test_error_appears_in_tool_result(self):
        """Verify that execute_tool surfaces transport errors as JSON error results."""
        transport = MockTransport()
        robot = Robot(transport)
        await robot.connect()

        # Make send fail
        transport.send = AsyncMock(side_effect=ConnectionError("BLE disconnected"))

        result_str = await execute_tool(robot, "read_distance", {})
        result = json.loads(result_str)
        assert "error" in result
        assert "BLE disconnected" in result["error"]
        await robot.disconnect()

    @pytest.mark.asyncio
    async def test_agent_continues_after_error(self):
        """Full loop: error on one tool call doesn't crash the agent loop."""
        transport = MockTransport()
        robot = Robot(transport)
        await robot.connect()

        original_send = transport.send
        fail_next = False

        async def controlled_send(data: bytes) -> None:
            nonlocal fail_next
            if fail_next:
                fail_next = False
                raise ConnectionError("Simulated disconnect")
            return await original_send(data)

        transport.send = controlled_send

        # Agent: read_distance (ok), then we inject failure on next, agent retries, done
        responses = [
            _tool_response("t1", "read_distance"),
            _tool_response("t2", "read_distance"),  # this one will fail
            _tool_response("t3", "read_distance"),   # retry succeeds
            _text_response("All done."),
        ]

        call_idx = [0]
        original_create = None

        async def create_with_failure_injection(**kwargs):
            call_idx[0] += 1
            if call_idx[0] == 2:
                nonlocal fail_next
                fail_next = True
            return responses[call_idx[0] - 1]

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(side_effect=create_with_failure_injection)

        with patch("spike_mind.agent.AsyncAnthropic", return_value=mock_client):
            result = await run_agent(robot, "Check distance repeatedly", max_turns=20)

        assert result == "All done."
        await robot.disconnect()
