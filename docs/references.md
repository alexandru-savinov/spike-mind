# References and Reusable Projects

## Architecture Validation

- [Claude Code as Embodied Agent](https://medium.com/@brianytsui/claude-code-as-embodied-agent-to-control-robots-85-96-success-in-sim-with-zero-demonstration-455a9044d353) — ReAct loop + high-level API, 85-96% success in sim. Same pattern as spike-mind.
- [ROSA (NASA JPL)](https://github.com/nasa-jpl/rosa) — 9 system prompts for robot agents. Steal the prompt engineering.
- [Code as Policies (Google DeepMind)](https://code-as-policies.github.io/) — LLM outputs code calling robot primitives. Intellectual foundation for tool-use approach.

## MCP Server References (for Option B)

- [ros-mcp-server](https://github.com/robotmcp/ros-mcp-server) — 1.1k stars. MCP wrapping robot tools. Architecture reference.
- [UnitMCP](https://github.com/UnitApi/mcp) — Hardware MCP server with security layer and command pipelines.
- [Serial Hardware Bridge MCP](https://www.pulsemcp.com/servers/serial-hardware-bridge) — Generic serial-to-MCP bridge pattern.

## Pybricks Ecosystem

- [pybricksdev](https://github.com/pybricks/pybricksdev) — CLI for uploading/running programs. `pybricksdev run ble hub/main.py`.
- [pybricksdev-demo](https://github.com/pybricks/pybricksdev-demo) — Jupyter notebooks using `BLEPUPConnection`. Could replace raw bleak.
- [Hub-to-PC Tutorial](https://pybricks.com/projects/tutorials/wireless/hub-to-device/pc-communication/) — Canonical NUS communication reference.
- [mpy-robot-tools](https://github.com/antonvh/mpy-robot-tools) — Hub-side BLE library with symmetrical protocol.
- [pybricks-ble](https://github.com/fkleon/pybricks-ble) — Connectionless BLE broadcast/observe for Pybricks.

## LEGO + AI Projects

- [AI-LEGO-HEAD](https://github.com/CreativeMindstorms/AI-LEGO-HEAD) — Gemini + LEGO Mindstorms robotic head. Parallel pipelines (audio, vision, motors).
- [BricksRL](https://github.com/BricksRL/bricksrl) — Reinforcement learning for LEGO robots. Proven BLE transport. [Paper](https://arxiv.org/abs/2406.17490).

## Hub Program Persistence

Programs uploaded via `pybricksdev run ble` persist in flash after clean shutdown (hold button to power off). On next boot, press button to start — no computer needed. Supports 5 program slots, 256KB total.
