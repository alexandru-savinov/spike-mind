# Research Findings

## BLE Protocol

Pybricks GATT characteristic `c5f50002-8280-46da-89f4-6d8051e4aeef`:
- PC writes: `0x06` + payload = WRITE_STDIN
- Hub notifies: `0x01` + payload = WRITE_STDOUT
- Also: `0x00`/`0x01` start/stop program, `0x03`/`0x04` upload code

Message format (from [BricksRL](https://github.com/BricksRL/bricksrl)): struct-packed IEEE 754 floats, big-endian.

```python
# Send motor commands
action_bytes = struct.pack("!ff", left_deg, right_deg)
await client.write_gatt_char(CHAR_UUID, b"\x06" + action_bytes, response=False)

# Receive sensor state (notification handler)
if data[0] == 0x01:
    state = struct.unpack("!fffff", data[1:])
```

BLE MTU is ~20 bytes. Messages >20 bytes fragment — concatenate until expected size reached.

### Latency

| Metric | Value |
|--------|-------|
| BLE round-trip | ~90ms |
| Practical with 250ms hub wait | **3-4 Hz** |
| LLM API call | 500ms-2s (the real bottleneck) |

### RPi5 caveats

- **WiFi/BLE interference** on combo chip → use USB BT 5.0 adapter or `rfkill block wlan`
- **BlueZ cache** stale after firmware switch → `bluetoothctl -- remove XX:XX:XX:XX:XX:XX`
- Only one `BleakScanner` at a time (known bleak issue)

---

## Hub-Side Bridge Program

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

kbd_intr(-1)  # CRITICAL: allow raw binary on stdin

hub = PrimeHub()
left = Motor(Port.A)
right = Motor(Port.B)
turret = Motor(Port.C)
dist = UltrasonicSensor(Port.D)
color = ColorSensor(Port.E)
drive = DriveBase(left, right, wheel_diameter=56, axle_track=112)
drive.use_gyro(True)

keyboard = poll()
keyboard.register(stdin)

while True:
    while not keyboard.poll(0):
        wait(1)

    data = stdin.buffer.read(8)
    cmd, value = ustruct.unpack("!if", data)

    if cmd == 1: drive.straight(value)
    elif cmd == 2: drive.turn(value)
    elif cmd == 3: drive.stop()

    out = ustruct.pack("!fffff",
        dist.distance() / 10, float(hub.imu.heading()),
        *hub.imu.tilt(), left.angle()
    )
    stdout.buffer.write(out)
```

Key details:
- `kbd_intr(-1)` — without it, `0x03` in stdin raises KeyboardInterrupt
- `uselect.poll()` — non-blocking stdin check
- `wait=False` on motor commands for concurrent multi-motor
- `DriveBase.use_gyro(True)` — gyro-corrected turns (99 consecutive 90° turns stay on track)

---

## Safety

### BLE disconnect behavior

| Command | On disconnect |
|---------|--------------|
| `motor.run_time()` / `run_angle()` / `drive.straight()` | **Completes** full duration |
| `motor.run()` (indefinite) | **Runs forever** |

**Rule: Never use `motor.run()` over BLE.** Hub-side watchdog must stop motors if no stdin data in N seconds.

### Software limits

| Constraint | Limit | Hardware max |
|------------|-------|-------------|
| Speed | 400-600 deg/s | ~1050 |
| Single command | 5-10s | Unlimited |
| Min obstacle distance | 5-10cm | 4cm sensor min |

Never expose raw motor % to LLM — use named speed levels. Verify sensor state after every action.

---

## Pybricks API Quick Reference

| Class | Key Methods |
|-------|-------------|
| `UltrasonicSensor` | `distance()` (mm), `presence()` |
| `ColorSensor` | `color()`, `hsv()`, `reflection()`, `ambient()` |
| `ForceSensor` | `force()` (N), `pressed()` |
| `hub.imu` | `heading()`, `tilt()`, `acceleration()`, `angular_velocity()` |
| `Motor` | `angle()`, `speed()`, `run_angle()`, `run_time()`, `run_until_stalled()` |
| `DriveBase` | `straight()`, `turn()`, `curve()`, `use_gyro()`, `distance()` |

Limitations: No UART/I2C (no third-party sensors). No camera without LMS-ESP32 breakout. IMU drifts (ok for room-scale).

---

## MCP Server Pattern (Option B)

Use FastMCP. Tools for live sensors, resources for metadata. Lazy BLE connection. Thread-safe (`RLock`). Pre-built primitives (`follow_line`) run control loops server-side.

```json
{ "mcpServers": { "spike-prime": { "command": "python", "args": ["src/spike_mind/mcp_server.py"] } } }
```

Reference implementations: [ros-mcp-server](https://github.com/robotmcp/ros-mcp-server) (1,100 stars, production-grade), [robot-mcp](https://github.com/monteslu/robot-mcp) (77 lines, minimal).

---

## Key References

**Build on these:**
- [pybricks-projects `pc-communication`](https://github.com/pybricks/pybricks-projects/tree/master/tutorials/wireless/hub-to-device/pc-communication) — exact hub+PC template
- [BricksRL](https://github.com/BricksRL/bricksrl) — proven BLE transport, binary protocol, 5 robot envs ([paper](https://arxiv.org/abs/2406.17490))
- [pybricksdev](https://github.com/pybricks/pybricksdev) — BLE connection, firmware flashing

**Learn from these:**
- [nasa-jpl/rosa](https://github.com/nasa-jpl/rosa) — 9 system prompts for robot agents (steal the prompt engineering, skip the framework)
- [gpdaniels/spike-prime](https://github.com/gpdaniels/spike-prime) — hub simulator for testing without hardware

**Protocol docs:**
- [Pybricks BLE Profile](https://github.com/pybricks/technical-info/blob/master/pybricks-ble-profile.md)
- [LEGO SPIKE Prime Protocol](https://lego.github.io/spike-prime-docs/) (stock firmware, for reference only)

---

## Decisions Log

| Decision | Choice | Rejected | Why |
|----------|--------|----------|-----|
| LLM framework | Anthropic SDK direct | ROSA, LangChain | ~100 lines vs massive dep tree. ROSA is just a LangChain wrapper. |
| Hub firmware | Pybricks | Stock LEGO | Only option with Python BLE tooling. Reversible in 2 min. |
| BLE library | bleak (in nixpkgs) | pylgbst | pylgbst doesn't support SPIKE Prime at all. |
| MCP framework | FastMCP | Raw mcp SDK | 5x less boilerplate, auto schema from type hints. |

---

## NixOS Prerequisites

```nix
hardware.bluetooth.enable = true;  # in hosts/rpi5-full/configuration.nix
```

RPi5 kernel sees BT hardware (`hci0` active), but BlueZ userspace not enabled. `bleak` needs `bluetoothd`.
