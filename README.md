# spike-mind

LLM-controlled LEGO SPIKE Prime robot via BLE from Raspberry Pi 5.

## Architecture

```
User → LLM (Claude via OpenRouter, tool-use) → Control Service (Python/bleak) → BLE → SPIKE Hub (Pybricks) → Motors+Sensors → back to LLM
```

**Option A — Standalone agent** (~150 lines, `anthropic` + `bleak`). Start here.
**Option B — MCP server** (FastMCP, any MCP client controls the robot). Future.
**Option C — OpenClaw integration** (spike-mind as MCP server → OpenClaw as the brain). End goal.

## Tools (LLM interface)

| Tool | Returns |
|------|---------|
| `move_forward(distance_cm)` | actual distance moved |
| `turn(angle_degrees)` | actual angle turned |
| `stop()` | confirmation |
| `read_distance()` | cm (ultrasonic, 4-200cm) |
| `read_color()` | color name |
| `scan_surroundings()` | distances at multiple angles |
| `follow_line(speed, duration_s)` | distance traveled (local PID loop) |

## Hardware

**Buy:** [SPIKE Prime 45678](https://education.lego.com/en-us/products/lego-education-spike-prime-set/45678/) ($430, **retires June 2026**) + USB BT 5.0 adapter (~$12).

| Port | Device | Purpose |
|------|--------|---------|
| A | Medium Motor | Left wheel |
| B | Medium Motor | Right wheel |
| C | Large Motor | Sensor turret |
| D | Ultrasonic Sensor | Distance (on turret) |
| E | Color Sensor | Line detection |
| F | Force Sensor | Bumper |

Build: [Pybricks StarterBot](https://pybricks.com/learn/building-a-robot/spike-prime/) + rotating ultrasonic turret.

## Safety

**Critical:** BLE disconnect does NOT stop motors. Never use `motor.run()` (indefinite). Always time-bounded commands + hub-side watchdog.

Four layers: Hardware (overcurrent/thermal) → Hub (watchdog/stall detection) → Host (parameter bounds/geofencing) → LLM (weakest).

## Development

```bash
nix develop
```

**NixOS prerequisite:** `hardware.bluetooth.enable = true;` in rpi5-full config.

## OpenClaw Integration

End goal: OpenClaw (Claude Code on sancta-claw VPS) controls the robot via MCP.

```
User → OpenClaw (sancta-claw) → Tailscale → spike-mind MCP (rpi5) → BLE → SPIKE Hub
```

This requires Option B (MCP server) + network transport. OpenClaw already supports
MCP servers — spike-mind just needs to be reachable over Tailscale. The MCP server
runs on rpi5 (close to BLE hardware), OpenClaw connects remotely.

Considerations:
- MCP stdio transport is local-only — need streamable-http or SSE transport for remote
- Tailscale provides the encrypted tunnel (no extra auth needed)
- Latency: ~20ms Tailscale + ~90ms BLE = ~110ms per command (fine for LLM control)
- OpenClaw's tool-use capabilities map directly to spike-mind's tool interface

## Status

- [x] Research → [docs/research.md](docs/research.md)
- [ ] Phase 1: BLE connectivity
- [ ] Phase 2: Robot control module
- [ ] Phase 3: LLM tool-use loop (standalone, Option A)
- [ ] Phase 4: MCP server (Option B)
- [ ] Phase 5: OpenClaw integration (Option C)
