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
existing rpi5 service infrastructure. Use FastMCP (not raw mcp SDK) for tool definitions —
5x less boilerplate, auto-generates JSON Schema from Python type hints.

### Control timescales

The LLM acts as a **high-level planner**, not a low-level controller:

| Timescale | Layer | Example |
|-----------|-------|---------|
| 1-5ms | Hub firmware (Pybricks PID) | Motor speed regulation, stall detection |
| 50-250ms | Hub bridge program | Sensor polling, motor command execution |
| 90ms | BLE round-trip | One command + response cycle |
| 500ms-2s | LLM API call | Reasoning about next action |
| Seconds-minutes | LLM planning | Multi-step goal execution |

Pre-built primitives (e.g., `follow_line()`, `drive_until_obstacle()`) run tight
control loops locally. The LLM orchestrates these primitives, not individual motor pulses.

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
- `move_forward(distance_cm)` — drive straight (max 500cm per call)
- `move_backward(distance_cm)` — reverse
- `turn(angle_degrees)` — pivot in place (positive = right, negative = left)
- `stop()` — immediate stop
- `set_speed(level)` — "slow" / "medium" / "fast" (never expose raw motor %)

### Sensors
- `read_distance()` → distance in cm (ultrasonic sensor, 4-200cm range)
- `read_color()` → color name string (color sensor)
- `get_gyro_angle()` → cumulative rotation in degrees
- `get_motor_position(port)` → encoder degrees
- `scan_surroundings()` → distance readings at multiple angles (rotates sensor turret)

### Compound (local control loops, LLM just invokes)
- `follow_line(speed, duration_s)` — PID line follower
- `drive_until_obstacle(speed, min_distance_cm)` — drive forward, stop when obstacle detected
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

## Safety Architecture

Defense-in-depth — four independent layers, each operates even if layers above fail:

```
┌─────────────────────────────────────────────────┐
│  Layer 4: LLM (weakest, advisory only)          │
│  - System prompt with physical constraints       │
│  - Tool descriptions include safety notes        │
│  - State feedback after each action              │
├─────────────────────────────────────────────────┤
│  Layer 3: Host Validation (Python on RPi5)       │
│  - Parameter bounds checking on every tool call  │
│  - Geofencing (encoder-based position estimate)  │
│  - Rate limiting (max N commands/second)         │
│  - Command timeout enforcement                   │
│  - Sensor precondition checks (don't drive if    │
│    obstacle < 5cm)                               │
│  - Action logging for debugging                  │
├─────────────────────────────────────────────────┤
│  Layer 2: Hub Runtime (Pybricks on SPIKE)        │
│  - Watchdog timer (no command in N sec → stop)   │
│  - Speed/acceleration caps in bridge program     │
│  - Stall detection via motor.control              │
│  - All commands time-bounded (no run-forever)    │
├─────────────────────────────────────────────────┤
│  Layer 1: Hardware (strongest, always on)        │
│  - Overcurrent protection (hub firmware)         │
│  - Thermal shutdown                              │
│  - Physical e-stop (hub center button)           │
│  - Battery undervoltage cutoff                   │
└─────────────────────────────────────────────────┘
```

Key safety rules:
- Never expose raw motor power % to the LLM — use named speed levels
- Never use indefinite `motor.run()` over BLE — always time-bounded commands
- Verify state (sensor readings) after every action, feed discrepancies back to LLM
- If BLE disconnects mid-action, hub watchdog stops all motors within N seconds
- Cap max speed at 400-600 deg/s (hardware max is ~1050 deg/s)
- Cap max single command duration at 5-10 seconds

## Hardware

### What to buy

**Minimum:** SPIKE Prime Base Set (45678) — $430, includes everything needed.
**Warning:** Set 45678 retires June 30, 2026. Buy soon if interested.

| Component | Included in 45678 | Notes |
|-----------|-------------------|-------|
| Programmable Hub | Yes | 6 ports, 5x5 LED, 6-axis IMU, BLE, rechargeable battery |
| Large Angular Motor (45602) | 1x | ~1050 deg/s, ~25 Ncm stall torque, 1° encoder |
| Medium Angular Motor (45603) | 2x | ~1110 deg/s, ~18 Ncm stall torque, 1° encoder |
| Ultrasonic Distance Sensor (45604) | 1x | 4-200cm range, ~30° cone, 4 programmable LEDs |
| Color Sensor (45605) | 1x | 8 colors, HSV, reflection %, ambient light |
| Force Sensor (45606) | 1x | 0-10N, usable as bumper/collision detector |
| Technic elements | 528 pieces | Beams, gears, wheels, axles |

**Optional:** Expansion Set (45681) — $109. Adds larger wheels, extra color sensor,
Maker Plate (for mounting RPi), and 600+ parts. Not essential for first build.

**Recommended:** USB Bluetooth 5.0 adapter (TP-Link UB500, ~$12) to avoid WiFi/BLE
coexistence issues on RPi5's Broadcom combo chip.

### Sensor specifications

| Sensor | Range | Resolution | Port needed |
|--------|-------|------------|-------------|
| Ultrasonic distance | 4–200 cm | ~1 cm | Yes |
| Color | ~1–5 cm optimal | 8 discrete colors + HSV | Yes |
| Force | 0–10 N | 0.65 N steps | Yes |
| IMU (gyro + accel) | 3-axis each | Built-in | No (in hub) |
| Motor encoders | 360° absolute | 1° | Per motor port |

### Recommended robot build

**Start with the [Pybricks StarterBot](https://pybricks.com/learn/building-a-robot/spike-prime/)**
— builds entirely from base set, differential drive, modular sensor attachments.

**Target port allocation:**

| Port | Device | Purpose |
|------|--------|---------|
| A | Medium Motor | Left wheel (drive) |
| B | Medium Motor | Right wheel (drive) |
| C | Large Motor | Sensor turret (rotating ultrasonic mount) |
| D | Ultrasonic Sensor | Distance sensing (on turret) |
| E | Color Sensor | Line/surface detection (ground-facing) |
| F | Force Sensor | Bumper / collision detection |

This uses all 3 motors and all 3 sensors from the base set. The Pybricks `DriveBase`
class handles motor synchronization and gyro-assisted navigation for ports A+B.

**Future upgrades:**
- LMS-ESP32 v2.0 (~$40): Adds WiFi to hub, enabling direct LLM communication without RPi BLE bridge
- OpenMV camera + breakout board (~$100): Vision capabilities
- Second hub: For >6 port devices

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
| MCP server (FastMCP) | `fastmcp`, `bleak` | ~200 lines | Future Option B |

#### Why Pybricks firmware over stock LEGO firmware

| Aspect | Stock SPIKE App 3 | Pybricks |
|--------|-------------------|----------|
| BLE protocol | Binary + COBS framing | GATT commands + stdin/stdout |
| Python library | None (implement from scratch) | `pybricksdev` (mature) |
| Real-time control | Direct motor messages | Via bridge program on hub |
| Firmware change | No | Yes (reversible in ~2 min) |
| Documentation | Official LEGO docs | Pybricks technical-info repo |
| Community tooling | Minimal | Active ecosystem |
| DriveBase abstraction | N/A | Built-in with gyro-assisted PID |

Pybricks wins on tooling maturity. Stock firmware would avoid flashing but requires
implementing the LEGO binary protocol from scratch with no existing Python library.

#### Why not pylgbst

[pylgbst](https://github.com/undera/pylgbst) (577 stars) does **not** support SPIKE Prime.
It only supports Boost-era hubs (MoveHub, SmartHub, RemoteHandset). Confirmed by source code inspection.

#### MCP server design (Option B)

Based on analysis of [robotmcp/ros-mcp-server](https://github.com/robotmcp/ros-mcp-server),
[monteslu/robot-mcp](https://github.com/monteslu/robot-mcp), and
[AimanMadan/Arduino_MCP_Server](https://github.com/AimanMadan/Arduino_MCP_Server):

- **Use FastMCP** (not raw mcp SDK) — `@mcp.tool` decorator auto-generates JSON Schema from type hints
- **Lazy BLE connection** — don't connect at import time; connect on first tool call or via explicit `connect_hub()` tool
- **Thread-safe connection state** — BLE callbacks fire on separate threads; use `threading.RLock`
- **Sensor data as tools, not resources** — MCP resources are static snapshots; tools actively read sensors on demand
- **MCP resources for metadata** — hub battery level, firmware version, port configuration (rarely changes)
- **Long-running ops use polling loops** with `ctx.report_progress()` — no need for experimental Tasks API
- **Pre-built primitives run server-side** — `follow_line()` runs a local PID loop, returns summary when done
- **Claude Code integration** — stdio transport, spawned as subprocess via `.mcp.json` config

### BLE Transport Details

#### Pybricks BLE protocol

The hub exposes a Pybricks GATT service (UUID base `c5f5XXXX-8280-46da-89f4-6d8051e4aeef`):

- **Command/Event characteristic** (`c5f50002-...`): Bidirectional control channel
  - PC writes: `0x06` + payload = WRITE_STDIN (send data to hub program)
  - Hub notifies: `0x01` + payload = WRITE_STDOUT (hub program output)
  - Also: START/STOP program (`0x00`/`0x01`), upload code (`0x03`/`0x04`), reboot (`0x05`), status reports (`0x00` event)
- **Hub Capabilities** (`c5f50003-...`): Read-only, reports max write size (~20 bytes default) and feature flags

There is no way to send raw motor commands over BLE. The architecture is always:
1. Upload a MicroPython bridge program to the hub
2. Bridge polls `usys.stdin` with `uselect.poll()`, decodes commands, actuates motors
3. Bridge writes sensor data to `stdout.buffer.write()`
4. PC receives via BLE GATT notifications

#### BLE message format (from BricksRL)

Messages are **struct-packed IEEE 754 floats**, big-endian (`!` prefix):

```python
# PC side — sending action
action_bytes = struct.pack("!fff", motor_a, motor_b, turret)
await client.write_gatt_char(CHAR_UUID, b"\x06" + action_bytes, response=False)

# PC side — receiving state
# notification handler receives: 0x01 + state_bytes
state = struct.unpack("!fffff", state_bytes)  # e.g. (left_angle, right_angle, pitch, roll, distance)
```

BLE has ~20-byte MTU. Messages >20 bytes are fragmented and must be reassembled
(concatenate notification payloads until expected byte count reached).

#### Hub-side bridge program pattern (from BricksRL)

```python
import ustruct
from micropython import kbd_intr
from pybricks.hubs import PrimeHub
from pybricks.parameters import Port
from pybricks.pupdevices import Motor, UltrasonicSensor
from pybricks.tools import wait
from uselect import poll
from usys import stdin, stdout

kbd_intr(-1)  # Disable Ctrl+C — essential for raw binary stdin

hub = PrimeHub()
left = Motor(Port.A)
right = Motor(Port.B)
dist = UltrasonicSensor(Port.D)

keyboard = poll()
keyboard.register(stdin)

while True:
    while not keyboard.poll(0):  # Non-blocking poll for incoming data
        wait(1)                   # 1ms sleep to avoid busy-wait

    data = stdin.buffer.read(8)   # 2 floats = 8 bytes
    left_cmd, right_cmd = ustruct.unpack("!ff", data)

    left.run_angle(speed=500, rotation_angle=left_cmd, wait=False)
    right.run_angle(speed=500, rotation_angle=right_cmd, wait=False)

    wait(250)  # Let motors settle

    out = ustruct.pack("!fffff",
        left.angle(), right.angle(),
        hub.imu.tilt()[0], hub.imu.tilt()[1],
        dist.distance()
    )
    stdout.buffer.write(out)
```

#### Latency

From [BricksRL paper](https://arxiv.org/abs/2406.17490) (academic RL on physical SPIKE robots):

| Metric | Value |
|--------|-------|
| Max control frequency | **~11 Hz** (90ms round-trip) |
| Practical frequency (with 250ms hub wait) | **3-4 Hz** |
| Hub internal PID loop | >1000 Hz |
| Bottleneck | stdin/stdout MicroPython overhead + hub `wait()` |

Effective rate depends on the hub-side `wait()` value. BricksRL uses 100-250ms hub-side
wait plus BLE round-trip overhead. For LLM control (500ms-2s per decision), this is
more than adequate.

#### RPi5 Bluetooth caveats

- **WiFi/BLE interference**: RPi5's Broadcom combo chip shares antenna. Causes occasional
  `le-connection-abort-by-local` errors and missed BLE advertisements during WiFi activity.
  Workaround: USB BT 5.0 adapter or `rfkill block wlan`.
- **BlueZ cache**: When switching between stock/Pybricks firmware, remove device from BlueZ
  cache: `bluetoothctl -- remove XX:XX:XX:XX:XX:XX`
- **No aarch64-specific issues**: bleak uses D-Bus → BlueZ, which is architecture-independent.
- **BlueZ 5.84** available in nixpkgs — recent and well-supported by bleak.

### Pybricks Feature Support

All SPIKE Prime native sensors are fully supported:

| Sensor/Feature | Pybricks Class | Notes |
|----------------|---------------|-------|
| Ultrasonic | `UltrasonicSensor` | mm precision, `distance()`, `presence()` |
| Color | `ColorSensor` | `color()`, `hsv()`, `reflection()`, `ambient()`, configurable color list |
| Force | `ForceSensor` | `force()`, `pressed()`, `touched()` |
| Hub IMU | `hub.imu` | `heading()`, `tilt()`, `acceleration()`, `angular_velocity()` |
| Motors | `Motor` | `angle()`, `speed()`, `run_angle()`, `run_time()`, `run_until_stalled()` |
| DriveBase | `DriveBase` | `straight()`, `turn()`, `curve()`, `use_gyro(True)` for precision |

**Limitations:**
- No UART/I2C access — limits third-party sensor integration
- No native camera support — requires LMS-ESP32 breakout board + OpenMV/HuskyLens
- IMU drift over time — manageable for room-scale, not precision navigation

### Safety Details

#### SPIKE Prime hardware protections

- **Motor stall detection**: Hub firmware monitors current draw, cuts power on stall
- **Overcurrent protection**: Per-port, always active in hardware
- **Pybricks `motor.control.stall_tolerances(speed, time)`**: Configurable software stall detection
- **Battery**: Rechargeable Li-ion, undervoltage cutoff prevents deep discharge

#### BLE disconnect behavior (critical)

When BLE disconnects while a hub program is running:
- `motor.run_time(speed, time)`: **Completes** the full duration — motor runs even without BLE
- `motor.run()` (indefinite): **Runs forever** until battery dies or stall protection
- **Mitigation**: Never use indefinite `run()`. Always time-bounded commands. Hub-side watchdog
  stops all motors if no stdin data received within N seconds.

#### Software-enforced limits

| Constraint | Recommended Limit | Hardware Max |
|------------|-------------------|-------------|
| Motor speed | 400-600 deg/s | ~1050 deg/s |
| Acceleration | 200-400 deg/s² | Unlimited |
| Single command duration | 5-10 seconds | Unlimited |
| Min obstacle distance | 5-10 cm before stop | 4 cm sensor minimum |
| Continuous operation | 30-60s then state check | Battery life |

### Key Reference Projects

#### Must-use (direct dependencies or templates)

| Repo | Stars | What to take |
|------|-------|-------------|
| [pybricks/pybricksdev](https://github.com/pybricks/pybricksdev) | 67 | BLE connection code, firmware flashing, protocol constants |
| [pybricks-projects `pc-communication`](https://github.com/pybricks/pybricks-projects/tree/master/tutorials/wireless/hub-to-device/pc-communication) | 109 | Exact template for hub-side bridge + PC-side bleak client |
| [BricksRL/bricksrl](https://github.com/BricksRL/bricksrl) | — | Proven BLE transport at 3-11 Hz, struct-packed binary protocol, hub-side bridge programs |
| [antonvh/mpy-robot-tools](https://github.com/antonvh/mpy-robot-tools) | — | Hub-side BLE UART, motor sync, SerialTalk protocol |

#### Proven patterns (architecture reference)

| Repo | Stars | What to learn |
|------|-------|--------------|
| [gpdaniels/spike-prime](https://github.com/gpdaniels/spike-prime) | 311 | Hub simulator (tkinter GUI) for testing without hardware, reverse-engineered protocol docs |
| [monteslu/robot-mcp](https://github.com/monteslu/robot-mcp) | 7 | Simplest MCP server for physical hardware — 77 lines total |
| [AimanMadan/Arduino_MCP_Server](https://github.com/AimanMadan/Arduino_MCP_Server) | 5 | FastMCP + hardware pattern — 38 lines total |
| [robotmcp/ros-mcp-server](https://github.com/robotmcp/ros-mcp-server) | 1,100 | Production-grade MCP for robotics: lazy connection, progress reporting, tool annotations |

#### LLM-robot integration patterns

| Repo | Stars | What to learn |
|------|-------|--------------|
| [nasa-jpl/rosa](https://github.com/nasa-jpl/rosa) | 1,452 | Prompt engineering for robot agents (9 system prompts), tool safety patterns |
| [babycommando/machinascript-for-robots](https://github.com/babycommando/machinascript-for-robots) | 196 | LLM → JSON → robot pattern, vision mode with Llama 3.2 |
| [zhoupingjay/LlamaPi](https://github.com/zhoupingjay/LlamaPi) | 23 | Voice + LLM + physical actuator on RPi5 |
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
| [BricksRL paper](https://arxiv.org/abs/2406.17490) | Academic validation of Pybricks BLE for real-time robot control |

#### Academic references (LLM + robotics safety)

| Paper | Key contribution |
|-------|-----------------|
| SayCan (Google, 2022) | Ground LLM outputs in physical affordances — score each action by feasibility |
| Inner Monologue (Google, 2022) | Sensor feedback after each action catches hallucinations early |
| Code as Policies (Google, 2023) | LLM generates code using safe primitives, not raw motor commands |
| ProgPrompt (Microsoft, 2023) | Programmatic plans with assertions as implicit safety gates |

### Interesting alternatives not pursued

- **Stock LEGO firmware + raw BLE**: Avoids reflashing but no Python library exists. LEGO published
  the binary protocol (COBS framing) for App 3 firmware. Only worth it if LEGO app compatibility
  is required simultaneously.
- **PicoLM**: Runs TinyLlama 1.1B in C on RPi5 at ~10 tok/s with JSON-constrained output.
  Enables fully offline LLM inference. Not practical for complex reasoning but interesting for
  fast reactive decisions. ([RightNow-AI/picolm](https://github.com/RightNow-AI/picolm), 1,400 stars)
- **Google ADK**: 17k+ stars, model-agnostic agent framework. No robotics examples but clean
  tool-calling primitives. Heavier than direct SDK calls.
- **LMS-ESP32 v2.0**: Anton Vamborg's WiFi bridge board (~$40) plugs into a SPIKE port. Adds
  WiFi to the hub, enabling direct HTTP/WebSocket communication with the LLM host — could
  eliminate the BLE bridge entirely. Experimental Pybricks support.
- **OpenMV camera**: Vision capabilities via breakout board (~$100 total). Interesting for future
  "describe what you see" tool, but adds significant complexity.

### NixOS prerequisites (not yet applied)

```nix
# Required in hosts/rpi5-full/configuration.nix:
hardware.bluetooth.enable = true;   # enables BlueZ daemon + bluetoothctl
```

The RPi5 kernel already detects the BT hardware (`hci0` is active), but the BlueZ
userspace stack is not enabled. `bleak` requires the `bluetoothd` D-Bus service.
