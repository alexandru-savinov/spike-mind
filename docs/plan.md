# spike-mind Development Plan

## Current State (2026-03-29)

Option A (standalone agent) is implemented and working with mock transport.
BLE scanning finds the hub ("Kuzea Hub") but hardware testing not yet completed.

### What exists
- Binary protocol (8-byte cmd, 20-byte response) with 7 commands
- BleTransport + MockTransport
- Robot API with safety bounds
- Claude tool-use agent loop (8 tools)
- Hub program with watchdog, matched to actual hardware ports
- 23 passing tests

### What's missing
- No hardware-verified BLE connection end-to-end
- No pybricksdev in workflow yet
- System prompt is minimal (no ROSA-style reasoning prompts)
- No MCP server (Option B)
- No OpenClaw integration (Option C)

---

## Phase 1: Hardware Connection (next)

**Goal:** Run the full agent loop controlling the physical robot.

1. Install pybricksdev: `pipx install pybricksdev`
2. Upload hub program: `pybricksdev run ble hub/main.py`
3. Verify clean disconnect (hub stays running with green light)
4. Run `python -m spike_mind` against real hardware
5. Calibrate wheel diameter and axle track if movement is inaccurate
6. Test all 8 tools: move, turn, stop, distance, color, scan, head_tilt, follow_line

**Done when:** Claude can scan surroundings and navigate around an obstacle on a table.

---

## Phase 2: Robustness

**Goal:** Reliable enough for multi-minute sessions without crashes.

1. Handle BLE disconnects gracefully (retry connect, inform user)
2. Add connection timeout configuration
3. Improve MockTransport realism (obstacles, colors, noise)
4. Add integration test that runs agent loop against mock for N turns
5. Consider replacing raw bleak with pybricksdev's `BLEPUPConnection` for better connection management

**Done when:** 10-minute mock sessions and 5-minute hardware sessions without errors.

---

## Phase 3: Smarter Agent

**Goal:** Claude makes better decisions, reasons about space, remembers context.

1. Improve system prompt using ROSA patterns (capability framing, explicit limitations, reasoning instructions)
2. Add state memory to agent — track position history, build simple occupancy grid
3. Add `nod` and `shake` head gestures (combine head_rotation + head_tilt)
4. Add `wave` arm gesture
5. Add multi-step planning: Claude proposes a plan, executes steps, adapts on failure
6. Stream tool execution output so user sees progress in real-time

**Done when:** Claude can navigate a simple maze and report what it found.

---

## Phase 4: MCP Server (Option B)

**Goal:** Any MCP client can control the robot.

1. Add FastMCP to dependencies
2. Create `src/spike_mind/mcp_server.py` wrapping Robot API as MCP tools
3. Use streamable-http transport (not stdio) for remote access
4. Test with Claude Desktop as MCP client
5. Test with Claude Code as MCP client
6. Add MCP resources for robot state (position, heading, sensor history)

**Reference:** [ros-mcp-server](https://github.com/robotmcp/ros-mcp-server) architecture.

**Done when:** Claude Desktop can control the robot via MCP.

---

## Phase 5: OpenClaw Integration (Option C)

**Goal:** OpenClaw on sancta-claw VPS controls the robot remotely.

```
User -> OpenClaw (sancta-claw) -> Tailscale -> spike-mind MCP (rpi5) -> BLE -> SPIKE Hub
```

1. Deploy spike-mind MCP server on RPi5
2. Verify Tailscale connectivity from VPS to rpi5
3. Configure OpenClaw to use spike-mind as MCP server
4. Handle latency (~20ms Tailscale + ~90ms BLE = ~110ms per command)
5. Add camera feed if RPi5 has one (stream to OpenClaw for vision)

**Prerequisite:** RPi5 with NixOS, `hardware.bluetooth.enable = true`, USB BT 5.0 adapter.

**Done when:** OpenClaw on VPS sends "explore the room" and the robot moves.

---

## Hardware Notes

| Port | Device | Notes |
|------|--------|-------|
| A | Left wheel | Medium motor |
| B | Head rotation | Controls ultrasonic sweep direction |
| C | Color sensor | Faces down for line detection |
| D | Ultrasonic sensor | 4-200cm range, mounted on head |
| E | Right wheel | Medium motor |
| F | Head tilt + arm | Single motor, dual purpose |

Hub: "Kuzea Hub" (SPIKE Prime with Pybricks firmware)
Wheel diameter: 56mm, axle track: 112mm (needs calibration)

## Key Decisions

| Decision | Choice | Why |
|----------|--------|-----|
| LLM framework | Anthropic SDK direct | ~120 lines vs massive dep tree |
| Hub firmware | Pybricks | Only option with Python BLE stdio |
| BLE library | bleak | In nixpkgs, cross-platform |
| MCP framework (future) | FastMCP | 5x less boilerplate |
| Transport | streamable-http (future) | Needed for remote access over Tailscale |
