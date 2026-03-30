# spike-mind

LLM-controlled LEGO SPIKE Prime robot via BLE.

## Architecture

```
User -> Claude (tool-use) -> Python host (bleak) -> BLE -> SPIKE Hub (Pybricks) -> Motors+Sensors
```

**Option A -- Standalone agent** (`anthropic` + `bleak`). Implemented.
**Option B -- MCP server** (FastMCP, any MCP client controls the robot). Future.
**Option C -- OpenClaw integration** (spike-mind as MCP server, OpenClaw as the brain). End goal.

## Project Structure

```
src/spike_mind/
  protocol.py     Binary command encoding (struct.pack/unpack, 8-byte cmd, 20-byte response)
  transport.py    Transport protocol + BleTransport + MockTransport
  robot.py        High-level async API (move_forward, turn, scan, etc.)
  agent.py        Claude tool-use loop (Anthropic SDK)
  cli.py          Entry point, config loading, wiring

hub/
  main.py         MicroPython for Pybricks (runs on SPIKE Prime hub)

tests/
  test_protocol.py        Protocol encode/decode tests
  test_robot.py           Robot API tests against MockTransport
  test_ble_reconnect.py   BLE auto-reconnect tests
  test_integration.py     Agent loop integration tests
```

## Tools (LLM interface)

| Tool | Args | Returns |
|------|------|---------|
| `move_forward` | `distance_cm` (max 50) | distance, heading |
| `turn` | `angle_degrees` (max 360) | heading |
| `stop` | -- | confirmation |
| `read_distance` | -- | cm (ultrasonic, 4-200cm) |
| `read_color` | -- | color name |
| `scan_surroundings` | -- | distances at 5 angles |
| `follow_line` | `speed`, `duration_s` (max 10) | distance, heading |

## Quickstart

```bash
nix develop

# Run tests
python -m pytest tests/ -v

# Run with mock transport (no hardware needed)
SPIKE_TRANSPORT=mock ANTHROPIC_API_KEY=sk-... python -m spike_mind

# Run with real BLE hardware
ANTHROPIC_API_KEY=sk-... python -m spike_mind
```

## Configuration

`config.toml` at project root:

```toml
[transport]
type = "ble"  # "ble" | "mock"

[ble]
device_address = ""  # blank = auto-scan by service UUID
service_uuid = "c5f50001-8280-46da-89f4-6d8051e4aeef"
char_uuid = "c5f50002-8280-46da-89f4-6d8051e4aeef"

[ble.retry]
max_retries = 3        # reconnect attempts on BLE failure
backoff_base = 1.0     # seconds; exponential: backoff_base * 2^attempt
connect_timeout = 15.0 # seconds per connection attempt

[agent]
model = "claude-sonnet-4-20250514"
```

Env overrides: `SPIKE_TRANSPORT`, `SPIKE_DEVICE_ADDRESS`, `SPIKE_MODEL`.

### BLE Auto-Reconnect

BleTransport automatically reconnects on send/receive failures using exponential backoff. Configure via `[ble.retry]` in `config.toml`. On disconnect during a command, the error is surfaced to the agent as a tool error result; the next command succeeds if reconnect completed.

### MockTransport Simulation

MockTransport accepts optional parameters for realistic simulation:

- `obstacles`: list of `(x, y, radius)` tuples -- circular obstacles; ultrasonic readings reflect distance to nearest obstacle surface along current heading
- `color_zones`: list of `(x, y, radius, color_id)` tuples -- READ_COLOR returns the color_id when the robot is inside a zone, else 0
- `noise`: float (default 0.0) -- when > 0, adds Gaussian noise scaled by this factor to sensor readings (distance, heading, pitch, roll, motor angle)

## Hardware

**Buy:** [SPIKE Prime 45678](https://education.lego.com/en-us/products/lego-education-spike-prime-set/45678/) ($430, retires June 2026) + USB BT 5.0 adapter (~$12).

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

BLE disconnect does NOT stop motors. The hub runs a 2-second watchdog that stops all motors if no command is received. Host-side bounds: max 50cm per move, max 360 per turn, max 10s duration.

Four layers: Hardware (overcurrent/thermal) -> Hub (watchdog/stall detection) -> Host (parameter bounds) -> LLM (system prompt rules).

## Cross-Platform

Python code has zero platform branching. Only `flake.nix` knows about platform differences:
- **macOS**: bleak uses CoreBluetooth natively
- **Linux**: adds bluez + dbus-next

## OpenClaw Integration (Future)

```
User -> OpenClaw (VPS) -> Tailscale -> spike-mind MCP (rpi5) -> BLE -> SPIKE Hub
```

Requires Option B (MCP server) + streamable-http transport over Tailscale.

## Status

- [x] Research -> [docs/research.md](docs/research.md)
- [x] Phase 1: Binary protocol + BLE transport
- [x] Phase 2: Robot control module + mock transport
- [x] Phase 3: LLM tool-use loop (standalone, Option A)
- [ ] Phase 4: MCP server (Option B)
- [ ] Phase 5: OpenClaw integration (Option C)
