# Phase 2: Robustness - BLE Resilience, Mock Realism, Integration Tests

## Overview

Make the robot agent resilient to BLE disconnects via auto-reconnect with configurable retry/timeout, improve MockTransport to simulate obstacles/colors/sensor noise, and add an integration test that runs the agent loop against MockTransport for N turns. Done when 10-minute mock sessions and 5-minute hardware sessions complete without errors.

## Context

- Files involved: `src/spike_mind/transport.py`, `src/spike_mind/robot.py`, `src/spike_mind/agent.py`, `config.toml`, `tests/test_robot.py`
- Related patterns: Transport Protocol interface (connect/disconnect/send/receive), MockTransport state simulation, execute_tool error-to-JSON conversion
- Dependencies: bleak (BLE), pytest-asyncio (tests)

## Development Approach

- **Testing approach**: Regular (code first, then tests)
- Complete each task fully before moving to the next
- **CRITICAL: every task MUST include new/updated tests**
- **CRITICAL: all tests must pass before starting next task**

## Implementation Steps

### Task 1: BLE reconnect with retry and timeout config

**Files:**
- Modify: `src/spike_mind/transport.py`
- Modify: `config.toml`

- [x] Add `[ble.retry]` section to config.toml with `max_retries` (default 3), `backoff_base` (default 1.0s), and `connect_timeout` (default 15.0s)
- [x] Add reconnect logic to BleTransport: on send/receive failure, attempt auto-reconnect in background using exponential backoff up to max_retries
- [x] On disconnect during a command, raise a descriptive error (e.g. "BLE disconnected, reconnecting...") so execute_tool surfaces it to the agent as a tool error result; the next command should work if reconnect succeeded
- [x] Add BleTransport constructor parameter for retry config (max_retries, backoff_base, connect_timeout) read from config
- [x] Write tests: mock bleak internals to simulate disconnect during send/receive, verify reconnect attempts, verify error surfaced, verify next call succeeds after reconnect
- [x] Run project test suite - must pass before task 2

### Task 2: Improve MockTransport realism - obstacles, colors, noise

**Files:**
- Modify: `src/spike_mind/transport.py`

- [ ] Add an `obstacles` parameter to MockTransport: list of (x, y, radius) tuples representing circular obstacles; ultrasonic distance should return distance to nearest obstacle surface along current heading (not just distance from origin)
- [ ] Add a `color_zones` parameter: list of (x, y, radius, color_id) tuples; READ_COLOR returns the color_id if robot position is within a zone, else 0 (no color)
- [ ] Add a `noise` parameter (float, default 0.0): when > 0, add Gaussian noise scaled by this factor to all sensor readings (distance, heading, pitch, roll, motor angle)
- [ ] Update existing tests to pass with new defaults (obstacles=[], color_zones=[], noise=0.0 preserves current behavior)
- [ ] Write tests: place obstacles and verify distance readings change with heading, place color zones and verify color detection, enable noise and verify readings vary within expected range
- [ ] Run project test suite - must pass before task 3

### Task 3: Integration test - agent loop against mock for N turns

**Files:**
- Create: `tests/test_integration.py`
- Modify: `src/spike_mind/agent.py` (only if needed for testability)

- [ ] Create integration test that sets up MockTransport with obstacles and color zones, creates Robot, and runs run_agent() with a simple exploration prompt for a configurable number of turns (default 20)
- [ ] Verify the agent completes without raising exceptions, uses multiple tool types, and produces a final text response
- [ ] Add a longer-running stress test (marked with pytest.mark.slow) that runs the agent loop for max_turns=100 to approximate sustained sessions; verify no errors accumulate
- [ ] Write a test for the error-surfacing path: use a mock transport that fails once mid-session, verify the agent receives the error and continues
- [ ] Run project test suite - must pass before task 4

### Task 4: Verify acceptance criteria

- [ ] Run full test suite: `python -m pytest tests/ -v`
- [ ] Run linter if configured
- [ ] Verify all new tests pass including slow integration tests: `python -m pytest tests/ -v -m slow`

### Task 5: Update documentation

- [ ] Update README.md with BLE retry config section and MockTransport obstacle/color/noise parameters
- [ ] Update CLAUDE.md if internal patterns changed
- [ ] Move this plan to `docs/plans/completed/`
