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
        │  BLE (bleak → Nordic UART Service)
        ▼
   SPIKE Prime Hub (Pybricks firmware)
        │  wired LPF2
        ▼
   Motors + Sensors
        │  sensor readings
        ▼
   Control Service → LLM context (observation loop)
```

## Layers

| Layer | Responsibility | Tech |
|-------|---------------|------|
| **LLM** | Reasoning, planning, tool selection | Claude / GPT via OpenRouter API |
| **Control Service** | Translates tool calls → BLE commands, manages sensor polling | Python, `bleak`, runs on RPi5 |
| **BLE Transport** | Bidirectional comms with hub | Nordic UART Service (NUS) over BLE |
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

## Hardware Requirements

- Raspberry Pi 5 (built-in Bluetooth 5.0)
- LEGO SPIKE Prime hub (set 45678 or 45681)
- SPIKE Prime motors and sensors
- Pybricks firmware flashed to hub (reversible — stock LEGO firmware can be restored)

## Development

```bash
nix develop          # enter dev shell with bleak + bluez
python3 -c "import bleak; print('BLE ready')"
```

## Project Status

**Phase 0 — Scaffolding** ← you are here

- [x] Repository and flake setup
- [ ] Phase 1: BLE connectivity — discover, connect, send/receive with SPIKE hub
- [ ] Phase 2: Robot control module — Python functions for motors and sensors
- [ ] Phase 3: LLM integration — tool-use loop with OpenRouter API
- [ ] Phase 4: Autonomy — continuous observe-think-act cycle
