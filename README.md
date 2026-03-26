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

**Option A — Standalone agent (Phase 1-4 target)**

```
User text → Anthropic API (tool-use) → Python tool executor → bleak → SPIKE hub
                    ↑                                              │
                    └──────── sensor readings ←────────────────────┘
```

~150 lines of Python. `anthropic` + `bleak` dependencies only. Start here.

**Option B — MCP server (future)**

```
Claude Code ──→ spike-mind MCP server ──→ bleak ──→ SPIKE hub
Open-WebUI ─┘         │                               │
n8n ────────┘         └←────── sensor readings ←───────┘
```

Any MCP client can control the robot. Enables multi-client access and fits into the
existing rpi5 service infrastructure.

## Layers

| Layer | Responsibility | Tech |
|-------|---------------|------|
| **LLM** | Reasoning, planning, tool selection | Claude / GPT via Anthropic SDK (direct tool-use, no LangChain) |
| **Control Service** | Translates tool calls → BLE commands, manages sensor polling | Python, `bleak`, runs on RPi5 |
| **BLE Transport** | Bidirectional comms with hub | Pybricks GATT service (`c5f50002-...`) with WRITE_STDIN/WRITE_STDOUT |
| **Hub Firmware** | Executes motor commands, reads sensors, reports back | Pybricks MicroPython on SPIKE Prime |
| **Hardware** | Physical actuation and sensing | SPIKE Prime motors, distance/color/gyro sensors |

## Robot Tools (LLM function interface)

The LLM sees the robot as a set of callable tools:

### Movement
- `move_forward(distance_cm)` — drive straight
- `move_backward(distance_cm)` — reverse
- `turn(angle_degrees)` — pivot in place (positive = right, negative = left)
- `stop()` — immediate stop

### Sensors
- `read_distance()` → distance in cm (ultrasonic sensor)
- `read_color()` → color name string (color sensor)
- `get_gyro_angle()` → cumulative rotation in degrees
- `get_motor_position(port)` → encoder degrees

### Compound (future)
- `follow_line(speed)` — PID line follower
- `scan_surroundings()` → distance readings at multiple angles
- `navigate_to(x, y)` — odometry-based movement

## Feedback Loop

The system runs in a continuous observe-think-act cycle:

1. **Observe** — poll sensors, build a state snapshot
2. **Think** — send state + goal to LLM, receive tool calls
3. **Act** — execute tool calls on the robot
4. **Report** — capture results, feed back to step 1

The LLM is never the latency bottleneck (~1s per API call vs 90ms BLE round-trip),
so sensors can be polled multiple times between LLM decisions. This enables a fast
reactive layer (Python) alongside a slow planning layer (LLM).

## Hardware Requirements

- Raspberry Pi 5 (built-in Bluetooth 5.0)
- LEGO SPIKE Prime hub (set 45678 or 45681)
- SPIKE Prime motors and sensors
- Pybricks firmware flashed to hub (reversible — stock LEGO firmware can be restored)
- **Recommended:** USB Bluetooth 5.0 adapter (e.g. TP-Link UB500) to avoid WiFi/BLE coexistence issues on RPi5's combo chip

## Development

```bash
nix develop          # enter dev shell with bleak + bluez
python3 -c "import bleak; print('BLE ready')"
```

## Project Status

**Phase 0 — Scaffolding** ← you are here

- [x] Repository and flake setup
- [x] Research and architecture decisions
- [ ] Phase 1: BLE connectivity — discover, connect, send/receive with SPIKE hub
- [ ] Phase 2: Robot control module — Python functions for motors and sensors
- [ ] Phase 3: LLM integration — tool-use loop with Anthropic SDK
- [ ] Phase 4: Autonomy — continuous observe-think-act cycle

---

## Research Notes

### Architecture Decisions

#### Why direct Anthropic SDK over ROSA/LangChain

[NASA-JPL ROSA](https://github.com/nasa-jpl/rosa) (1,452 stars) was evaluated as the primary
LLM-to-robot framework. Findings:

- ROSA is ~200 lines of useful code wrapping LangChain's `create_tool_calling_agent()` + `AgentExecutor`
- ROS-specific tools are cleanly separated (easy to swap), but the `ros_version` parameter is mandatory
- Pulls in a massive dependency tree: `langchain` + `azure-identity` + `numpy` + `pillow` — all unnecessary for our use case
- Test coverage is weak (empty test files, mocked tool tests)
- For 5-10 robot tools, the direct Anthropic SDK tool-use protocol is simpler and sufficient

| Approach | Dependencies | Glue code | Verdict |
|----------|-------------|-----------|---------|
| **Direct Anthropic SDK** | `anthropic`, `bleak` | ~100-150 lines | **Chosen** |
| LangChain agent | `langchain` + 30 transitive | ~50 lines | Overkill |
| ROSA | `jpl-rosa` + LangChain + azure + numpy | ~20 lines | ROS baggage |
| MCP server | `mcp`, `bleak` | ~200 lines | Future Option B |

#### Why Pybricks firmware over stock LEGO firmware

| Aspect | Stock SPIKE App 3 | Pybricks |
|--------|-------------------|----------|
| BLE protocol | Binary + COBS framing | GATT commands + stdin/stdout |
| Python library | None (implement from scratch) | `pybricksdev` (mature) |
| Real-time control | Direct motor messages | Via bridge program on hub |
| Firmware change | No | Yes (reversible) |
| Documentation | Official LEGO docs | Pybricks technical-info repo |
| Community tooling | Minimal | Active ecosystem |

Pybricks wins on tooling maturity. Stock firmware would avoid flashing but requires
implementing the LEGO binary protocol from scratch with no existing Python library.

#### Why not pylgbst

[pylgbst](https://github.com/undera/pylgbst) (577 stars) does **not** support SPIKE Prime.
It only supports Boost-era hubs (MoveHub, SmartHub, RemoteHandset). Confirmed by source code inspection.

### BLE Transport Details

#### Pybricks BLE protocol

The hub exposes a Pybricks GATT service (UUID base `c5f5XXXX-8280-46da-89f4-6d8051e4aeef`):

- **Command/Event characteristic** (`c5f50002-...`): Bidirectional control channel
  - PC writes: `0x06` + payload = WRITE_STDIN (send data to hub program)
  - Hub notifies: `0x01` + payload = WRITE_STDOUT (hub program output)
  - Also: START/STOP program, upload code, reboot, status reports
- **Hub Capabilities** (`c5f50003-...`): Read-only, reports max write size and feature flags

There is no way to send raw motor commands over BLE. The architecture is always:
1. Upload a MicroPython bridge program to the hub
2. Bridge polls `usys.stdin` with `uselect.poll()`, decodes commands, actuates motors
3. Bridge writes sensor data to `stdout.buffer.write()`
4. PC receives via BLE GATT notifications

#### Latency

From [BricksRL paper](https://arxiv.org/abs/2406.17490) (academic RL on physical SPIKE robots):

| Metric | Value |
|--------|-------|
| Max control frequency | **~11 Hz** (90ms round-trip) |
| Practical frequency | 2-8 Hz |
| Hub internal loop | >1000 Hz |
| Bottleneck | stdin/stdout MicroPython overhead |

11 Hz is more than adequate for LLM-controlled robotics where API calls take 500ms-2s.

#### RPi5 Bluetooth caveats

- **WiFi/BLE interference**: RPi5's Broadcom combo chip shares antenna. Causes occasional
  `le-connection-abort-by-local` errors and missed BLE advertisements during WiFi activity.
  Workaround: USB BT 5.0 adapter or `rfkill block wlan`.
- **BlueZ cache**: When switching between stock/Pybricks firmware, remove device from BlueZ
  cache: `bluetoothctl -- remove XX:XX:XX:XX:XX:XX`
- **No aarch64-specific issues**: bleak uses D-Bus → BlueZ, which is architecture-independent.

### Key Reference Projects

#### Must-use (direct dependencies or templates)

| Repo | Stars | What to take |
|------|-------|-------------|
| [pybricks/pybricksdev](https://github.com/pybricks/pybricksdev) | 67 | BLE connection code, firmware flashing, protocol constants |
| [pybricks-projects `pc-communication`](https://github.com/pybricks/pybricks-projects/tree/master/tutorials/wireless/hub-to-device/pc-communication) | 109 | Exact template for hub-side bridge + PC-side bleak client |
| [antonvh/mpy-robot-tools](https://github.com/antonvh/mpy-robot-tools) | — | Hub-side BLE UART, motor sync, SerialTalk protocol |

#### Proven patterns (architecture reference)

| Repo | Stars | What to learn |
|------|-------|--------------|
| [BricksRL/bricksrl](https://github.com/BricksRL/bricksrl) | — | Full RL training loop via Pybricks BLE — proves 11Hz control works on physical SPIKE robots |
| [gpdaniels/spike-prime](https://github.com/gpdaniels/spike-prime) | 311 | Hub simulator (tkinter GUI) for testing without hardware, reverse-engineered protocol docs |
| [monteslu/robot-mcp](https://github.com/monteslu/robot-mcp) | 7 | Simplest MCP server for physical hardware (Johnny-Five + servo) |
| [AimanMadan/Arduino_MCP_Server](https://github.com/AimanMadan/Arduino_MCP_Server) | 5 | MCP-to-GPIO pattern with FastMCP + PyFirmata2 |

#### LLM-robot integration patterns

| Repo | Stars | What to learn |
|------|-------|--------------|
| [nasa-jpl/rosa](https://github.com/nasa-jpl/rosa) | 1,452 | `@tool` decorator pattern, prompt engineering for robot agents (9 system prompts) |
| [babycommando/machinascript-for-robots](https://github.com/babycommando/machinascript-for-robots) | 196 | LLM → JSON → robot pattern, vision mode with Llama 3.2 |
| [zhoupingjay/LlamaPi](https://github.com/zhoupingjay/LlamaPi) | 23 | Voice + LLM + physical actuator on RPi5 |
| [robotmcp/ros-mcp-server](https://github.com/robotmcp/ros-mcp-server) | 1,100 | MCP server for robotics (ROS-specific but pattern is reusable) |
| [yang-ian/spike-prime-vibe-kit](https://github.com/yang-ian/spike-prime-vibe-kit) | 1 | SPIKE Prime + AI dev workflow, hot-reload for rapid iteration |

#### Voice pipeline (future)

| Repo | Stars | What to learn |
|------|-------|--------------|
| [m15-ai/TrooperAI](https://github.com/m15-ai/TrooperAI) | 20 | Vosk STT + Ollama + Piper TTS on RPi5 |
| [m15-ai/Local-Voice](https://github.com/m15-ai/Local-Voice) | — | Fully offline voice assistant for RPi |

#### Protocol references

| Resource | What it documents |
|----------|------------------|
| [LEGO SPIKE Prime Protocol Docs](https://lego.github.io/spike-prime-docs/) | Official stock firmware BLE protocol (binary + COBS) |
| [LEGO BLE Wireless Protocol v3](https://lego.github.io/lego-ble-wireless-protocol-docs/) | PoweredUp protocol spec (Boost/Technic hubs) |
| [Pybricks BLE Profile](https://github.com/pybricks/technical-info/blob/master/pybricks-ble-profile.md) | Pybricks GATT service specification |

### Interesting alternatives not pursued

- **MCP for robotics**: Emerging fast. No SPIKE Prime MCP server exists yet — building one would be novel.
  Template projects: `robot-mcp` (JS, servo control), `Arduino_MCP_Server` (Python, GPIO).
- **Stock LEGO firmware + raw BLE**: Avoids reflashing but no Python library exists. LEGO published
  the binary protocol (COBS framing) for App 3 firmware. Only worth it if LEGO app compatibility
  is required simultaneously.
- **PicoLM**: Runs TinyLlama 1.1B in C on RPi5 at ~10 tok/s with JSON-constrained output.
  Enables fully offline LLM inference. Not practical for complex reasoning but interesting for
  fast reactive decisions. ([RightNow-AI/picolm](https://github.com/RightNow-AI/picolm), 1,400 stars)
- **Google ADK**: 17k+ stars, model-agnostic agent framework. No robotics examples but clean
  tool-calling primitives. Heavier than direct SDK calls.

### NixOS prerequisites (not yet applied)

```nix
# Required in hosts/rpi5-full/configuration.nix:
hardware.bluetooth.enable = true;   # enables BlueZ daemon + bluetoothctl
```

The RPi5 kernel already detects the BT hardware (`hci0` is active), but the BlueZ
userspace stack is not enabled. `bleak` requires the `bluetoothd` D-Bus service.
