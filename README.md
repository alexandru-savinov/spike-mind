# spike-mind

LLM-controlled LEGO SPIKE Prime robot, driven from a Raspberry Pi 5 over Bluetooth Low Energy.

## Concept

An LLM (via OpenRouter) acts as the robot's "brain" — it receives sensor observations,
reasons about the environment, and issues motor commands through tool-use / function-calling.
The RPi5 bridges between the LLM API and the SPIKE Prime hub over BLE.

## Architecture

```
User (voice / text / goal)
        │
        ▼
   LLM (Claude via OpenRouter)
        │  tool calls: move(), turn(), read_distance(), ...
        ▼
   Control Service (Python, on RPi5)
        │  BLE (bleak → Pybricks GATT service)
        ▼
   SPIKE Prime Hub (Pybricks firmware)
        │  wired LPF2
        ▼
   Motors + Sensors
        │  sensor readings
        ▼
   Control Service → LLM context (observation loop)
```

### Two target architectures

**Option A — Standalone agent (start here)**

```
User text → Anthropic API (tool-use) → Python tool executor → bleak → SPIKE hub
                    ↑                                              │
                    └──────── sensor readings ←────────────────────┘
```

~150 lines of Python. `anthropic` + `bleak` only. No frameworks.

**Option B — MCP server (future)**

```
Claude Code ──→ spike-mind MCP server ──→ bleak ──→ SPIKE hub
Open-WebUI ─┘         │                               │
n8n ────────┘         └←────── sensor readings ←───────┘
```

Any MCP client controls the robot. FastMCP for tool definitions.

## Key decisions

| Decision | Choice | Why |
|----------|--------|-----|
| LLM framework | Direct Anthropic SDK | ROSA/LangChain overkill for 5-10 tools ([details](docs/research.md#why-direct-anthropic-sdk-over-rosalangchain)) |
| Hub firmware | Pybricks | Only option with mature Python BLE tooling ([details](docs/research.md#why-pybricks-firmware-over-stock-lego-firmware)) |
| BLE protocol | struct-packed binary floats | Proven at 3-11 Hz by BricksRL ([details](docs/research.md#ble-message-format)) |
| Safety | 4-layer defense-in-depth | Hardware → Hub → Host → LLM ([details](docs/research.md#safety)) |
| MCP framework | FastMCP | 5x less boilerplate than raw SDK ([details](docs/research.md#mcp-server-design)) |

## Robot tools (LLM function interface)

### Movement
- `move_forward(distance_cm)` — drive straight (max 500cm)
- `move_backward(distance_cm)` — reverse
- `turn(angle_degrees)` — pivot (positive = right)
- `stop()` — immediate stop
- `set_speed(level)` — "slow" / "medium" / "fast"

### Sensors
- `read_distance()` → cm (ultrasonic, 4-200cm)
- `read_color()` → color name (color sensor)
- `get_gyro_angle()` → degrees (IMU)
- `scan_surroundings()` → distance at multiple angles (turret sweep)

### Compound (local control loops)
- `follow_line(speed, duration_s)` — PID line follower
- `drive_until_obstacle(speed, min_distance_cm)` — drive + stop on obstacle

## Hardware

**Minimum:** [SPIKE Prime 45678](https://education.lego.com/en-us/products/lego-education-spike-prime-set/45678/) ($430) — retires June 30, 2026.

| Port | Device | Purpose |
|------|--------|---------|
| A | Medium Motor | Left wheel |
| B | Medium Motor | Right wheel |
| C | Large Motor | Sensor turret |
| D | Ultrasonic Sensor | Distance (on turret) |
| E | Color Sensor | Line detection (ground-facing) |
| F | Force Sensor | Bumper |

**Recommended:** USB Bluetooth 5.0 adapter (~$12) — RPi5's combo chip has WiFi/BLE interference.

Build: Start with [Pybricks StarterBot](https://pybricks.com/learn/building-a-robot/spike-prime/),
add rotating ultrasonic turret on Large Motor.

See [docs/research.md → Hardware](docs/research.md#hardware) for full specs and upgrade path.

## Safety

Four independent layers — each operates if layers above fail:

| Layer | What it does | Strongest? |
|-------|-------------|------------|
| Hardware | Overcurrent, thermal shutdown, physical e-stop button | Yes |
| Hub runtime | Watchdog timer, stall detection, speed caps | Strong |
| Host validation | Parameter bounds, geofencing, rate limiting, logging | Medium |
| LLM prompt | Constraint descriptions, state feedback | Weakest |

**Critical rule:** Never use indefinite `motor.run()` over BLE — if BLE disconnects,
the motor runs forever. Always time-bounded commands + hub-side watchdog.

See [docs/research.md → Safety](docs/research.md#safety) for full details.

## Development

```bash
nix develop          # enter dev shell with bleak + bluez
python3 -c "import bleak; print('BLE ready')"
```

**NixOS prerequisite** (not yet applied):
```nix
hardware.bluetooth.enable = true;  # in hosts/rpi5-full/configuration.nix
```

## Project status

**Phase 0 — Scaffolding** ← you are here

- [x] Repository and flake setup
- [x] Research and architecture decisions → [docs/research.md](docs/research.md)
- [ ] Phase 1: BLE connectivity — discover, connect, send/receive with SPIKE hub
- [ ] Phase 2: Robot control module — Python functions for motors and sensors
- [ ] Phase 3: LLM integration — tool-use loop with Anthropic SDK
- [ ] Phase 4: Autonomy — continuous observe-think-act cycle

## Documentation

- [docs/research.md](docs/research.md) — Full research findings: architecture decisions, BLE protocol details, safety analysis, hardware guide, MCP patterns, 30+ reference projects, academic references
