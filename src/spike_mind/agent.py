"""Claude tool-use agent loop for robot control.

Defines tools matching the Robot API, runs a conversation loop where
Claude picks tools to execute, and feeds results back.
"""

from __future__ import annotations

import json
from typing import Any

from anthropic import AsyncAnthropic

from spike_mind.robot import Robot

SYSTEM_PROMPT = """\
You are controlling a LEGO SPIKE Prime robot via tool calls. The robot has:
- Differential drive (two wheel motors: left on A, right on E)
- Ultrasonic distance sensor (port D), mounted on rotating head (port B)
- Color sensor (port C, detects: black, blue, green, yellow, red, white, orange, violet)
- Head tilt + arm motor (port F) — tilts head up/down and moves arm
- Gyro/IMU for heading

Safety rules:
- Maximum single move: 50cm
- Maximum single turn: 360°
- Always check distance before moving forward
- Stop immediately if distance < 10cm
- Verify sensor state after each action

You receive sensor data after every command. Use it to plan your next action.
When the user's goal is achieved, say so and stop calling tools.
"""

TOOLS = [
    {
        "name": "move_forward",
        "description": "Move the robot forward (positive) or backward (negative) by the given distance in centimeters. Max 50cm per call.",
        "input_schema": {
            "type": "object",
            "properties": {
                "distance_cm": {
                    "type": "number",
                    "description": "Distance to move in cm (-50 to 50)",
                }
            },
            "required": ["distance_cm"],
        },
    },
    {
        "name": "turn",
        "description": "Turn the robot by the given angle in degrees. Positive = clockwise, negative = counter-clockwise.",
        "input_schema": {
            "type": "object",
            "properties": {
                "angle_degrees": {
                    "type": "number",
                    "description": "Angle to turn in degrees (-360 to 360)",
                }
            },
            "required": ["angle_degrees"],
        },
    },
    {
        "name": "stop",
        "description": "Emergency stop all motors immediately.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "read_distance",
        "description": "Read the ultrasonic distance sensor. Returns distance in cm (4-200 range).",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "read_color",
        "description": "Read the color sensor. Returns detected color name.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "scan_surroundings",
        "description": "Sweep the ultrasonic turret across 5 angles (-90, -45, 0, 45, 90) and return distance readings at each angle.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "head_tilt",
        "description": "Tilt the head and arm by the given angle in degrees. Positive = tilt up, negative = tilt down. Max 90 degrees.",
        "input_schema": {
            "type": "object",
            "properties": {
                "angle_degrees": {
                    "type": "number",
                    "description": "Angle to tilt in degrees (-90 to 90)",
                }
            },
            "required": ["angle_degrees"],
        },
    },
    {
        "name": "follow_line",
        "description": "Follow a line on the ground for the given duration. Speed is in cm/s.",
        "input_schema": {
            "type": "object",
            "properties": {
                "speed": {
                    "type": "number",
                    "description": "Speed in cm/s",
                },
                "duration_s": {
                    "type": "number",
                    "description": "Duration in seconds (max 10)",
                },
            },
            "required": ["speed", "duration_s"],
        },
    },
]


async def execute_tool(robot: Robot, name: str, args: dict[str, Any]) -> str:
    """Execute a tool call and return the JSON result string."""
    try:
        if name == "move_forward":
            result = await robot.move_forward(args["distance_cm"])
        elif name == "turn":
            result = await robot.turn(args["angle_degrees"])
        elif name == "stop":
            result = await robot.stop()
        elif name == "read_distance":
            result = await robot.read_distance()
        elif name == "read_color":
            result = await robot.read_color()
        elif name == "scan_surroundings":
            result = await robot.scan_surroundings()
        elif name == "head_tilt":
            result = await robot.head_tilt(args["angle_degrees"])
        elif name == "follow_line":
            result = await robot.follow_line(args["speed"], args["duration_s"])
        else:
            result = {"error": f"Unknown tool: {name}"}
    except Exception as e:
        result = {"error": str(e)}
    return json.dumps(result)


async def run_agent(
    robot: Robot,
    user_message: str,
    model: str = "claude-sonnet-4-20250514",
    max_turns: int = 20,
) -> str:
    """Run the agent loop until Claude stops calling tools or max_turns is reached.

    Returns Claude's final text response.
    """
    client = AsyncAnthropic()

    messages: list[dict] = [{"role": "user", "content": user_message}]

    for _ in range(max_turns):
        response = await client.messages.create(
            model=model,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
        )

        # Check if Claude wants to use tools
        tool_uses = [b for b in response.content if b.type == "tool_use"]

        if not tool_uses:
            # No tool calls — extract final text
            text_parts = [b.text for b in response.content if b.type == "text"]
            return "\n".join(text_parts) if text_parts else "(no response)"

        # Add assistant response to history
        messages.append({"role": "assistant", "content": response.content})

        # Execute each tool and build tool_result messages
        tool_results = []
        for tool_use in tool_uses:
            result_str = await execute_tool(robot, tool_use.name, tool_use.input)
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tool_use.id,
                "content": result_str,
            })

        messages.append({"role": "user", "content": tool_results})

    return "(max turns reached)"
