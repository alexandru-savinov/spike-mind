# Research Findings

Comprehensive research into LLM-controlled LEGO SPIKE Prime robotics via BLE.
Last updated: 2026-03-26.

## Table of Contents

- [Architecture Decisions](#architecture-decisions)
- [BLE Transport](#ble-transport)
- [Hub-Side Bridge Program](#hub-side-bridge-program)
- [Safety](#safety)
- [Hardware](#hardware)
- [MCP Server Design](#mcp-server-design)
- [Reference Projects](#reference-projects)
- [Academic References](#academic-references)
- [Alternatives Not Pursued](#alternatives-not-pursued)
- [NixOS Prerequisites](#nixos-prerequisites)

---

## Architecture Decisions

### Why direct Anthropic SDK over ROSA/LangChain

[NASA-JPL ROSA](https://github.com/nasa-jpl/rosa) (1,452 stars) was evaluated as the primary
LLM-to-robot framework.

**ROSA internals (from source code analysis):**
- Core class `ROSA` in `src/rosa/rosa.py` is ~200 lines wrapping LangChain's
  `create_tool_calling_agent()` + `AgentExecutor`
- Tools use LangChain's `@tool` decorator — plain Python functions with docstrings
- Tool registration via `ROSATools.__init__()` scans module attributes for `.name`/`.func`
- Supports OpenAI, Azure OpenAI, Anthropic, Ollama (any LangChain `BaseChatModel`)
- ROS-specific code isolated in `tools/ros1.py` and `tools/ros2.py` — cleanly separated
- `ros_version` parameter is mandatory even for non-ROS usage
- Clever blacklist injection wraps tool functions to auto-filter items
- 9 system prompts in `prompts.py` for tool usage discipline, sequential execution, verification
- Dependencies: `langchain` + `azure-identity` + `numpy` + `pillow` — heavy for our use case
- Tests: `test_rosa.py` is empty (just license header). Weak coverage overall.

**Verdict:** For 5-10 robot tools, ROSA adds framework weight without meaningful value.
The prompt engineering patterns (9 system prompts) are worth studying, but the code
itself is replaceable with ~100 lines of direct API calls.

| Approach | Dependencies | Glue code | Verdict |
|----------|-------------|-----------|---------|
| **Direct Anthropic SDK** | `anthropic`, `bleak` | ~100-150 lines | **Chosen** |
| LangChain agent | `langchain` + 30 transitive | ~50 lines | Overkill |
| ROSA | `jpl-rosa` + LangChain + azure + numpy | ~20 lines | ROS baggage |
| MCP server (FastMCP) | `fastmcp`, `bleak` | ~200 lines | Future Option B |

### Why Pybricks firmware over stock LEGO firmware

| Aspect | Stock SPIKE App 3 | Pybricks |
|--------|-------------------|----------|
| BLE protocol | Binary + COBS framing | GATT commands + stdin/stdout |
| Python library | None (implement from scratch) | `pybricksdev` (mature) |
| Real-time control | Direct motor messages | Via bridge program on hub |
| Firmware change | No | Yes (reversible in ~2 min) |
| Documentation | [Official LEGO docs](https://lego.github.io/spike-prime-docs/) | [Pybricks technical-info](https://github.com/pybricks/technical-info/blob/master/pybricks-ble-profile.md) |
| Community tooling | Minimal | Active ecosystem |
| DriveBase abstraction | N/A | Built-in with gyro-assisted PID |
| Sensor support | Full (native firmware) | Full (all SPIKE sensors) |

Pybricks wins on tooling maturity. Stock firmware would avoid flashing but requires
implementing the LEGO binary protocol from scratch with no existing Python library.

**Reverting to stock firmware:** Connect hub via USB, open LEGO SPIKE app, it detects
non-stock firmware and prompts to restore. Takes ~2 minutes. The bootloader is never
overwritten — always recoverable. Hub-side programs stored in the LEGO app are unaffected.

### Why not pylgbst

[pylgbst](https://github.com/undera/pylgbst) (577 stars) does **not** support SPIKE Prime.
Confirmed by source code inspection — only supports Boost-era hubs: `MoveHub`, `SmartHub`,
`RemoteHandset`. The LEGO Wireless Protocol v3 it implements covers Boost/PoweredUp but
not SPIKE Prime's specific BLE service.

---

## BLE Transport

### Pybricks BLE protocol

The hub exposes three BLE services:

**Pybricks Service** (UUID base `c5f5XXXX-8280-46da-89f4-6d8051e4aeef`):

| Characteristic | UUID suffix | Direction | Purpose |
|---------------|-------------|-----------|---------|
| Command/Event | `c5f50002-...` | Bidirectional | Primary control channel |
| Hub Capabilities | `c5f50003-...` | Read-only | Max write size, feature flags, program slots |

**Command/Event protocol (first byte = command/event ID):**

| Byte | Direction | Name | Purpose |
|------|-----------|------|---------|
| `0x00` | PC → Hub | STOP_USER_PROGRAM | Stop running program |
| `0x01` | PC → Hub | START_USER_PROGRAM | Start program in slot |
| `0x02` | PC → Hub | START_REPL | Enter MicroPython REPL |
| `0x03` | PC → Hub | WRITE_USER_PROGRAM_META | Set program size before upload |
| `0x04` | PC → Hub | WRITE_USER_RAM | Upload program data chunks |
| `0x05` | PC → Hub | REBOOT_TO_UPDATE_MODE | Enter DFU mode |
| `0x06` | PC → Hub | WRITE_STDIN | Send data to running program's stdin |
| `0x07` | PC → Hub | WRITE_APP_DATA | Bidirectional app buffer (v1.4.0+) |
| `0x00` | Hub → PC | STATUS_REPORT | 32-bit flags: battery, BLE state, button, program running |
| `0x01` | Hub → PC | WRITE_STDOUT | Program's stdout data |

**Nordic UART Service (NUS)** (UUID base `6e40XXXX-b5a3-f393-e0a9-e50e24dcca9e`):
Available since protocol v1.3.0 for user-defined purposes. stdin/stdout moved to
the Pybricks service via WRITE_STDIN/WRITE_STDOUT commands.

**Device Information Service** (standard Bluetooth 0x180A):
Firmware revision, software revision (protocol version), PnP ID.

### BLE message format

From [BricksRL](https://github.com/BricksRL/bricksrl) source code analysis — the proven
binary protocol for real-time control:

**Format:** struct-packed IEEE 754 floats, big-endian (network byte order, `!` prefix).

```python
# PC side — sending motor commands
import struct
action_bytes = struct.pack("!ff", left_motor_deg, right_motor_deg)
await client.write_gatt_char(
    "c5f50002-8280-46da-89f4-6d8051e4aeef",
    b"\x06" + action_bytes,  # 0x06 = WRITE_STDIN
    response=False,          # fire-and-forget BLE write
)

# PC side — receiving sensor state
# Notification handler receives: first byte 0x01 = WRITE_STDOUT, rest = payload
def on_notify(sender, data):
    if data[0] == 0x01:
        payload = data[1:]
        state = struct.unpack("!fffff", payload)
        # e.g. (left_angle, right_angle, pitch, roll, distance)
```

**BLE fragmentation:** BLE has ~20-byte MTU. Messages >20 bytes arrive as multiple
notifications. Must reassemble by concatenating payloads until expected byte count:

```python
# BricksRL pattern
if len(self.payload_buffer) < expected_size:
    self.payload_buffer += payload
if len(self.payload_buffer) == expected_size:
    await self.rx_queue.put(self.payload_buffer)
    self.payload_buffer = None
```

### Latency

From [BricksRL paper](https://arxiv.org/abs/2406.17490):

| Metric | Value | Notes |
|--------|-------|-------|
| BLE round-trip | ~90ms | One command + response |
| Max control frequency | ~11 Hz | With minimal hub-side wait |
| Practical frequency | **3-4 Hz** | With 250ms hub-side `wait()` for motor settling |
| Hub internal PID | >1000 Hz | Pybricks handles this internally |
| LLM API call | 500ms-2s | The actual bottleneck |

Effective rate depends on hub-side `wait()` value. For LLM control, even 3 Hz provides
multiple sensor readings per LLM decision cycle.

### RPi5 Bluetooth caveats

**WiFi/BLE interference:** RPi5's Broadcom combo chip shares antenna. Symptoms:
- `le-connection-abort-by-local` errors
- Missed BLE advertisements during WiFi activity
- HCI error 0x3e

Workarounds (pick one):
1. USB Bluetooth 5.0 adapter (TP-Link UB500 with Realtek RTL8761B, ~$12) — **recommended**
2. `rfkill block wlan` if using Ethernet/USB Tailscale
3. Reduce WiFi traffic during BLE operations

**BlueZ cache stale data:** When switching between stock/Pybricks firmware:
```bash
bluetoothctl -- remove XX:XX:XX:XX:XX:XX
# For BlueZ < 5.62, also: rm -rf /var/lib/bluetooth/YY:YY/cache/XX:XX
```

**Scanner concurrency:** Only one `BleakScanner` instance at a time on RPi (known bleak issue).

**No aarch64-specific issues:** bleak uses D-Bus → BlueZ, architecture-independent.
BlueZ 5.84 available in nixpkgs — recent, well-supported.

---

## Hub-Side Bridge Program

### Canonical pattern (from BricksRL)

Every hub program follows this structure:

```python
import ustruct
from micropython import kbd_intr
from pybricks.hubs import PrimeHub
from pybricks.parameters import Port
from pybricks.pupdevices import Motor, UltrasonicSensor, ColorSensor
from pybricks.robotics import DriveBase
from pybricks.tools import wait
from uselect import poll
from usys import stdin, stdout

# CRITICAL: Disable Ctrl+C so stdin receives raw binary data
kbd_intr(-1)

# Initialize hardware
hub = PrimeHub()
left_motor = Motor(Port.A)
right_motor = Motor(Port.B)
turret_motor = Motor(Port.C)
distance = UltrasonicSensor(Port.D)
color = ColorSensor(Port.E)

# DriveBase for synchronized differential drive
drive = DriveBase(left_motor, right_motor, wheel_diameter=56, axle_track=112)
drive.use_gyro(True)  # Gyro-corrected navigation

# Setup non-blocking stdin polling
keyboard = poll()
keyboard.register(stdin)

# Main loop: wait for command → execute → report state
while True:
    while not keyboard.poll(0):  # Non-blocking: returns immediately if no data
        wait(1)                   # 1ms sleep to avoid busy-wait

    # Read command bytes (format defined by PC-side protocol)
    data = stdin.buffer.read(8)   # e.g. 2 floats = 8 bytes
    cmd_type, value = ustruct.unpack("!if", data)  # int command + float parameter

    # Execute command
    if cmd_type == 1:    # move forward
        drive.straight(value)
    elif cmd_type == 2:  # turn
        drive.turn(value)
    elif cmd_type == 3:  # stop
        drive.stop()
    # ... etc

    # Read all sensors
    dist_cm = distance.distance() / 10  # mm to cm
    col = color.color()  # returns Color enum
    heading = hub.imu.heading()
    pitch, roll = hub.imu.tilt()
    left_pos = left_motor.angle()
    right_pos = right_motor.angle()

    # Pack and send state back
    out = ustruct.pack("!fffff",
        dist_cm, float(heading), pitch, roll, left_pos
    )
    stdout.buffer.write(out)
```

### Key implementation details

- **`kbd_intr(-1)`** is essential — without it, byte `0x03` in stdin raises KeyboardInterrupt
- **`uselect.poll()`** provides non-blocking I/O detection without blocking the main loop
- **`wait=False`** on motor commands for concurrent multi-motor operation
- **Hub-side `wait()`** after motor commands lets PID controllers settle before reading sensors
- **All sensor reads happen on-hub** — only packed results cross BLE
- **`DriveBase.use_gyro(True)`** enables gyro-corrected straight-line driving and turns
  (documented: 99 consecutive 90° turns still on track)

### BricksRL environment patterns

BricksRL implements 5 different robot environments, each with specific sensor/motor configurations:

| Environment | Motors | Sensors | Hub wait | Control rate |
|-------------|--------|---------|----------|-------------|
| RunAway | 2 (differential drive) | encoders, IMU pitch/roll, ultrasonic | blocking `straight()` | Variable |
| Spinning | 2 (differential drive) | encoders, IMU pitch/roll, z angular velocity | 100ms | ~5-6 Hz |
| Walker | 4 (legged) | encoders, IMU pitch/roll, x acceleration | 250ms | ~3-4 Hz |
| RoboArm | 4 (articulated) | 4x joint angles | 250ms | ~3-4 Hz |
| RoboArmMixed | 3 + camera | 3x joint angles | 250ms | ~3-4 Hz |

**Error handling in BricksRL (lessons learned):**
- If received byte length doesn't match expected, returns **previous state** (stale data)
- BLE disconnection is fatal — no reconnection logic
- Queue is FIFO (despite "LifoQueue" comment in source) with maxsize=8
- `asyncio.create_task()` called from sync context in disconnect handler (bug)

These are shortcomings to improve on in spike-mind.

---

## Safety

### Defense-in-depth architecture

Four independent layers, each operates even if layers above fail:

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

### Critical BLE disconnect behavior

When BLE disconnects while a hub program is running:

| Motor command | Behavior on disconnect | Risk |
|--------------|----------------------|------|
| `motor.run_time(speed, time)` | **Completes** full duration | Medium — bounded |
| `motor.run_angle(speed, angle)` | **Completes** full rotation | Medium — bounded |
| `motor.run(speed)` | **Runs forever** | **HIGH** — until battery dies or stall |
| `drive.straight(distance)` | **Completes** full distance | Medium — bounded |

**Rule: Never use indefinite `motor.run()` over BLE.** Always time-bounded commands.
Hub-side watchdog must stop all motors if no stdin data received within N seconds.

### Pybricks safety features

- **Stall detection**: `motor.control.stall_tolerances(speed, time)` — configurable
- **`motor.run_until_stalled()`**: Runs until stall, then stops (useful for calibration)
- **Overcurrent protection**: Hardware-level, per-port, always active
- **DriveBase odometry**: `drive.distance()` and `drive.angle()` for position verification
- **IMU for orientation**: Detect if robot has tipped over

### Software-enforced limits

| Constraint | Recommended Limit | Hardware Max | Rationale |
|------------|-------------------|-------------|-----------|
| Motor speed | 400-600 deg/s | ~1050 deg/s | Prevents tipping, mechanical stress |
| Acceleration | 200-400 deg/s² | Unlimited | Prevents wheel slip, jerky motion |
| Single command duration | 5-10 seconds | Unlimited | Bounds runaway if state estimate is wrong |
| Min obstacle distance | 5-10 cm before stop | 4 cm sensor minimum | Reaction buffer |
| Continuous operation | 30-60s then state check | Battery life | Prevent cascading errors |
| Command rate | Max 10/second | BLE throughput | Prevent command queue flooding |

### Tool design for safety

- **Never expose raw motor power %** — use named speed levels: `"slow"`, `"medium"`, `"fast"`
- **Never use indefinite commands** — always specify distance, angle, or time
- **Verify state after every action** — read sensors, compare expected vs actual
- **Feed discrepancies back to LLM** — "expected to move 50cm but only moved 12cm (possible stall)"
- **Rate limit tool calls** — prevent LLM from flooding the hub
- **Log everything** — every tool call, sensor reading, LLM decision

### Key safety patterns from existing projects

**ROSA (NASA-JPL):** Tool abstraction is the safety boundary. LLM only calls predefined
functions, never gets raw hardware access. Operators configure blacklisted topics/services.

**BricksRL:** Action space clamped to [-1, 1], mapped to safe motor ranges on hub side.
Episode time limits. Reset procedures to return to known safe state.

**SayCan (Google):** Each LLM-proposed action scored by a learned "affordance" model
for physical feasibility. LLM is good at planning but terrible at physics.

**Inner Monologue (Google):** Sensor feedback after each action. If LLM says "pick up cup"
but gripper reports no object, it replans instead of continuing phantom sequence.

**Code as Policies (Google):** LLM generates code using safe primitives in a sandbox.
Cannot bypass built-in safety limits.

---

## Hardware

### What to buy

**Minimum:** SPIKE Prime Base Set (45678) — $430, includes everything needed.

**WARNING:** Set 45678 retires June 30, 2026. Buy soon.

| Component | Part # | Included in 45678 | Specs |
|-----------|--------|-------------------|-------|
| Programmable Hub | — | Yes | 6 ports, 5x5 LED matrix, 6-axis IMU, BLE 4.2, Li-ion battery |
| Large Angular Motor | 45602 | 1x | ~1050 deg/s, ~25 Ncm stall torque, 1° absolute encoder |
| Medium Angular Motor | 45603 | 2x | ~1110 deg/s, ~18 Ncm stall torque, 1° absolute encoder |
| Ultrasonic Distance | 45604 | 1x | 4-200cm, ~1cm resolution, ~30° cone, 4 programmable LEDs |
| Color Sensor | 45605 | 1x | 8 colors, HSV, reflection %, ambient light, 1-5cm optimal |
| Force Sensor | 45606 | 1x | 0-10N, 0.65N steps, pressed/released binary |
| Technic elements | — | 528 pieces | Beams, gears, wheels, axles |

**Optional:** Expansion Set (45681) — $109. Adds: larger wheels (better on carpet),
extra color sensor (dual-sensor line following), extra large motor, Maker Plate
(mounting bracket for RPi/SBC), 600+ additional parts.

**Recommended:** USB Bluetooth 5.0 adapter (TP-Link UB500, ~$12) for reliable BLE.

### Pybricks sensor support

All SPIKE Prime sensors fully supported:

| Sensor/Feature | Pybricks Class | Key Methods |
|----------------|---------------|-------------|
| Ultrasonic | `UltrasonicSensor` | `distance()` (mm), `presence()` |
| Color | `ColorSensor` | `color()`, `hsv()`, `reflection()`, `ambient()`, `detectable_colors()` |
| Force | `ForceSensor` | `force()` (N), `pressed()`, `touched()` |
| Hub IMU | `hub.imu` | `heading()`, `tilt()`, `acceleration()`, `angular_velocity()` |
| Motors | `Motor` | `angle()`, `speed()`, `run_angle()`, `run_time()`, `run_until_stalled()` |
| DriveBase | `DriveBase` | `straight()`, `turn()`, `curve()`, `use_gyro(True)`, `distance()`, `angle()` |

**Limitations:**
- No UART/I2C access — limits third-party sensors
- No native camera support — requires LMS-ESP32 breakout + OpenMV/HuskyLens
- IMU drifts over time — adequate for room-scale, not precision mapping

### Recommended robot build

**Start with [Pybricks StarterBot](https://pybricks.com/learn/building-a-robot/spike-prime/)**
— builds from base set, differential drive, modular sensor attachments, free instructions.

**Target port allocation for navigation robot:**

| Port | Device | Purpose |
|------|--------|---------|
| A | Medium Motor (45603) | Left wheel (drive) |
| B | Medium Motor (45603) | Right wheel (drive) |
| C | Large Motor (45602) | Sensor turret (rotating ultrasonic mount) |
| D | Ultrasonic Sensor (45604) | Distance sensing (mounted on turret) |
| E | Color Sensor (45605) | Line/surface detection (ground-facing) |
| F | Force Sensor (45606) | Bumper / collision detection |

Uses all 3 motors and all 3 sensors from the base set. `DriveBase(A, B)` handles
motor synchronization and gyro-assisted navigation.

**Build progression:**
1. StarterBot with 2 drive motors → test basic movement
2. Add ultrasonic sensor on a Large Motor turret → test obstacle detection + scanning
3. Add ground-facing color sensor → test line following
4. Add force sensor bumper → test collision detection
5. Flash Pybricks firmware → test BLE communication from RPi5

### Future hardware upgrades

| Upgrade | Cost | What it adds |
|---------|------|-------------|
| LMS-ESP32 v2.0 (Anton Vamborg) | ~$40 | WiFi on hub — eliminates BLE bridge, I2C/UART/SPI, hobby servos |
| OpenMV H7+ camera + breakout | ~$100 | Vision capabilities, on-device ML |
| HuskyLens + breakout | ~$60 | Built-in face/object/line recognition |
| Expansion Set (45681) | $109 | Bigger wheels, extra sensors, Maker Plate |
| Second SPIKE hub | ~$200 | >6 port devices, hub-to-hub BLE |

---

## MCP Server Design

### Option B architecture (future)

Based on analysis of three robotics MCP servers:

- [robotmcp/ros-mcp-server](https://github.com/robotmcp/ros-mcp-server) (1,100 stars) — production-grade, modular FastMCP
- [monteslu/robot-mcp](https://github.com/monteslu/robot-mcp) (7 stars) — 77-line proof of concept
- [AimanMadan/Arduino_MCP_Server](https://github.com/AimanMadan/Arduino_MCP_Server) (5 stars) — 38-line FastMCP + GPIO

### Framework choice: FastMCP

| Aspect | FastMCP | Raw mcp SDK |
|--------|---------|------------|
| Tool definition | `@mcp.tool` decorator | Manual `Tool()` + JSON Schema + dispatch |
| Schema generation | Auto from Python type hints | Manual JSON Schema |
| Lines per tool | ~5 | ~20 |
| Resources | `@mcp.resource("uri")` | Manual handlers |
| Progress reporting | `ctx.report_progress()` | Same (FastMCP wraps SDK) |
| Dependencies | Pulls in `mcp` SDK | Just `mcp` |

**FastMCP is the clear winner** — all Python robotics MCP servers examined use it.

### Tool definition pattern

```python
from fastmcp import FastMCP, Context, ToolAnnotations

mcp = FastMCP("spike-prime")

# Lazy BLE connection (not at import time)
hub_connection = None

@mcp.tool(
    description="Move robot forward by specified distance",
    annotations=ToolAnnotations(title="Move Forward", destructiveHint=True),
)
async def move_forward(distance_cm: float, speed: str = "medium", ctx: Context = None) -> dict:
    """Move the robot forward. Speed: 'slow', 'medium', 'fast'."""
    conn = await ensure_connected()
    # Validate parameters (Layer 3 safety)
    distance_cm = min(max(distance_cm, 1), 500)
    # Send to hub, wait for completion
    result = await conn.send_command(CMD_FORWARD, distance_cm)
    if ctx:
        await ctx.report_progress(progress=1, total=1, message=f"Moved {distance_cm}cm")
    return {"success": True, "distance_cm": distance_cm, "actual_distance": result.distance}

@mcp.tool
def read_distance() -> dict:
    """Read ultrasonic distance sensor. Returns distance in cm (4-200 range)."""
    conn = get_connection()
    value = conn.read_sensor("distance")
    return {"distance_cm": value, "unit": "cm"}
```

### Connection lifecycle

From ros-mcp-server's `WebSocketManager` — adapted for BLE:

1. **Lazy connection**: Don't connect at import. Connect on first tool call.
2. **Explicit connect tool**: `connect_hub(name="Pybricks Hub")` lets LLM initiate connection
3. **Thread-safe state**: `threading.RLock` around all BLE operations (callbacks on separate threads)
4. **Auto-reconnect**: `send_command()` calls `connect()` internally if disconnected
5. **Graceful errors**: Return error dicts, don't raise exceptions

### Sensor data: tools not resources

MCP resources are pull-based static snapshots — wrong for live sensor data.

- **Resources** for metadata: `spike://hub/status` → battery, firmware, connected ports
- **Tools** for live readings: `read_distance()`, `read_color()` — actively samples on demand
- The LLM explicitly decides when to sample sensors (by calling a tool)

### Long-running operations

Motor movements take 0.5-5 seconds. Use polling with progress reporting:

```python
@mcp.tool
async def move_forward(distance_cm: float, ctx: Context = None) -> dict:
    await hub.send(CMD_FORWARD, distance_cm)
    start = time.time()
    while time.time() - start < timeout:
        state = await hub.read_state()
        if state.movement_complete:
            if ctx:
                await ctx.report_progress(1, 1, "Movement complete")
            return {"success": True, "actual_distance": state.distance}
        await asyncio.sleep(0.05)
    return {"success": False, "error": "Movement timed out"}
```

### Pre-built primitives run server-side

The LLM orchestrates at second-to-minute timescale. Tight control loops run locally:

```python
@mcp.tool
async def follow_line(speed: str = "medium", duration_s: float = 10.0) -> dict:
    """Follow a line using the color sensor. Runs PID locally until duration expires."""
    # This runs a local control loop at hub-native speed (>100 Hz)
    # The LLM just invokes it and gets a summary when done
    result = await hub.run_primitive("follow_line", speed, duration_s)
    return {"success": True, "distance_traveled": result.distance, "line_lost": result.line_lost}
```

### Claude Code integration

```json
// .mcp.json in project root
{
  "mcpServers": {
    "spike-prime": {
      "command": "python",
      "args": ["src/spike_mind/mcp_server.py"],
      "env": {}
    }
  }
}
```

Claude Code spawns the server via stdio transport. Tools auto-discovered. Saying "move
the robot forward 30cm" in Claude Code triggers the `move_forward` tool.

---

## Reference Projects

### Must-use (direct dependencies or templates)

| Repo | Stars | What to take |
|------|-------|-------------|
| [pybricks/pybricksdev](https://github.com/pybricks/pybricksdev) | 67 | BLE connection code, firmware flashing, protocol constants. Python ≥3.10, uses bleak ≥1.1.0 |
| [pybricks-projects `pc-communication`](https://github.com/pybricks/pybricks-projects/tree/master/tutorials/wireless/hub-to-device/pc-communication) | 109 | Exact template: hub-side bridge (`usys.stdin` + `uselect.poll`) + PC-side bleak client |
| [BricksRL/bricksrl](https://github.com/BricksRL/bricksrl) | — | Proven BLE transport at 3-11 Hz. Binary struct protocol. 5 robot environments. Hub-side bridge programs. |
| [antonvh/mpy-robot-tools](https://github.com/antonvh/mpy-robot-tools) | — | Hub-side BLE UART, motor sync, SerialTalk protocol (stock firmware) |

### Architecture reference

| Repo | Stars | What to learn |
|------|-------|--------------|
| [gpdaniels/spike-prime](https://github.com/gpdaniels/spike-prime) | 311 | Hub simulator (tkinter GUI) for testing without hardware. USB/BT Classic serial communication. |
| [robotmcp/ros-mcp-server](https://github.com/robotmcp/ros-mcp-server) | 1,100 | Production MCP for robotics: lazy connection, progress reporting, tool annotations, modular design |
| [monteslu/robot-mcp](https://github.com/monteslu/robot-mcp) | 7 | Simplest MCP server for physical hardware — 77 lines, one servo tool |
| [AimanMadan/Arduino_MCP_Server](https://github.com/AimanMadan/Arduino_MCP_Server) | 5 | FastMCP + hardware GPIO — 38 lines |
| [Novakasa/brickrail](https://github.com/Novakasa/brickrail) | 107 | LEGO train automation via Pybricks BLE. Long-running PC-to-hub control. |

### LLM-robot integration

| Repo | Stars | What to learn |
|------|-------|--------------|
| [nasa-jpl/rosa](https://github.com/nasa-jpl/rosa) | 1,452 | Prompt engineering for robot agents (9 system prompts). Tool safety patterns. Blacklist injection. |
| [babycommando/machinascript-for-robots](https://github.com/babycommando/machinascript-for-robots) | 196 | LLM → JSON → robot pattern. Vision mode with Llama 3.2 via Groq. Arduino + RPi support. |
| [zhoupingjay/LlamaPi](https://github.com/zhoupingjay/LlamaPi) | 23 | Voice + LLM + physical actuator on RPi5. Llama 3.2 3B local inference. |
| [yang-ian/spike-prime-vibe-kit](https://github.com/yang-ian/spike-prime-vibe-kit) | 1 | SPIKE Prime + AI dev workflow, BT upload, hot-reload for rapid iteration |

### Voice pipeline (future)

| Repo | Stars | What to learn |
|------|-------|--------------|
| [m15-ai/TrooperAI](https://github.com/m15-ai/TrooperAI) | 20 | Vosk STT + Ollama + Piper TTS on RPi5. Clean pipeline. |
| [m15-ai/Local-Voice](https://github.com/m15-ai/Local-Voice) | — | Fully offline voice assistant for RPi |

### Protocol documentation

| Resource | What it documents |
|----------|------------------|
| [LEGO SPIKE Prime Protocol Docs](https://lego.github.io/spike-prime-docs/) | Official stock firmware BLE protocol (binary + COBS). App 3 firmware. |
| [LEGO BLE Wireless Protocol v3](https://lego.github.io/lego-ble-wireless-protocol-docs/) | PoweredUp protocol spec (Boost/Technic hubs, not SPIKE-specific) |
| [Pybricks BLE Profile](https://github.com/pybricks/technical-info/blob/master/pybricks-ble-profile.md) | Pybricks GATT service, command/event protocol, capability flags |
| [BricksRL paper](https://arxiv.org/abs/2406.17490) | Academic validation of Pybricks BLE for real-time robot control |

### Curated lists

| List | Stars | Scope |
|------|-------|-------|
| [GT-RIPL/Awesome-LLM-Robotics](https://github.com/GT-RIPL/Awesome-LLM-Robotics) | 4,308 | Comprehensive paper+code list for LLM+robotics |
| [jrin771/Everything-LLMs-And-Robotics](https://github.com/jrin771/Everything-LLMs-And-Robotics) | 852 | Another curated list |

---

## Academic References

### LLM + robotics safety

| Paper | Year | Key contribution |
|-------|------|-----------------|
| **SayCan** (Google) | 2022 | Ground LLM outputs in physical affordances — score each action by feasibility before executing |
| **Inner Monologue** (Google) | 2022 | Sensor feedback after each action catches hallucinations early. Replan on discrepancy. |
| **Code as Policies** (Google) | 2023 | LLM generates code using safe primitives in sandbox, cannot bypass built-in limits |
| **ProgPrompt** (Microsoft) | 2023 | Programmatic plans with assertions as implicit safety gates |
| **ROSA** (NASA-JPL) | 2024 | LLM tool-calling for ROS robots. [arXiv:2410.06472](https://arxiv.org/abs/2410.06472) |
| **BricksRL** | 2024 | RL on physical LEGO robots via Pybricks BLE. [arXiv:2406.17490](https://arxiv.org/abs/2406.17490) |

### Minimal agent loop pattern

The "LLM agent in N lines" pattern is well-documented:
- [O'Reilly: 131-line agent](https://www.oreilly.com/radar/how-to-build-a-general-purpose-ai-agent-in-131-lines-of-python/)
- [DEV.to: 50-line agent loop](https://dev.to/klement_gunndu/build-an-ai-agent-loop-in-50-lines-of-python-59jk)
- [sketch.dev: Agent loop effectiveness](https://sketch.dev/blog/agent-loop)

Core pattern: call LLM with tools → check for tool_use → execute → feed result back → repeat.

---

## Alternatives Not Pursued

### Stock LEGO firmware + raw BLE
Avoids reflashing but no Python library exists. LEGO published the binary protocol
(COBS framing) for App 3 firmware at [lego.github.io/spike-prime-docs](https://lego.github.io/spike-prime-docs/).
Would require implementing from scratch. Only worth it if LEGO app compatibility needed simultaneously.

### PicoLM (offline LLM on RPi5)
[RightNow-AI/picolm](https://github.com/RightNow-AI/picolm) (1,400 stars). Runs TinyLlama
1.1B in ~2,500 lines of C. ~10 tok/s on RPi5. `--json` flag for grammar-constrained output
(tool calling). Not practical for complex reasoning but interesting for fast reactive decisions
without cloud API.

### Google ADK
[google/adk-python](https://github.com/google/adk-python) (17k+ stars). Model-agnostic
agent framework. No robotics examples. Clean tool-calling primitives but heavier than
direct SDK calls.

### Robot Context Protocol (RCP)
[Alibaba DAMO Academy](https://arxiv.org/abs/2506.11650). Middleware-agnostic protocol
(HTTP + WebSocket) with read/write/execute/subscribe operations. Implementation:
[RynnRCP](https://github.com/alibaba-damo-academy/RynnRCP) (126 stars). More complex
than MCP, designed for multi-tenant robot fleets.

### LMS-ESP32 WiFi bridge
Anton Vamborg's [LMS-ESP32 v2.0](https://www.antonsmindstorms.com/2024/08/26/lms-esp32-v2-0-the-new-bridge-for-lego-and-electronics/)
(~$40). Plugs into SPIKE port, adds WiFi. Could enable direct HTTP/WebSocket communication
with LLM host, eliminating the BLE bridge. Experimental Pybricks support. Worth revisiting
after basic BLE path works.

### CrewAI / AutoGen
No robotics implementations found. Enterprise/software automation focused.

---

## NixOS Prerequisites

Not yet applied to rpi5-full configuration:

```nix
# Required in hosts/rpi5-full/configuration.nix:
hardware.bluetooth.enable = true;   # enables BlueZ daemon (bluetoothd) + bluetoothctl
```

The RPi5 kernel already detects BT hardware (`hci0` active via `sys-subsystem-bluetooth-devices-hci0.device`),
but the BlueZ userspace stack is not enabled. `bleak` requires the `bluetoothd` D-Bus service.

Current state (verified 2026-03-26):
- `bluetooth.target` is active (kernel-level)
- `bluetoothctl` is not available (no BlueZ userspace)
- `python3` is not in system packages (only in `nix develop` shell)
- BlueZ 5.84 available in nixpkgs
