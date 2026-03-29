"""CLI entry point — wires transport, robot, and agent loop."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # Python < 3.11 fallback

import os

from spike_mind.transport import BleTransport, MockTransport
from spike_mind.robot import Robot
from spike_mind.agent import run_agent


def load_config() -> dict:
    """Load config.toml from project root, with env var overrides."""
    config_path = Path(__file__).resolve().parent.parent.parent / "config.toml"
    if config_path.exists():
        with open(config_path, "rb") as f:
            config = tomllib.load(f)
    else:
        config = {"transport": {"type": "ble"}, "ble": {}, "agent": {}}

    # Env var overrides
    if env_transport := os.environ.get("SPIKE_TRANSPORT"):
        config.setdefault("transport", {})["type"] = env_transport
    if env_address := os.environ.get("SPIKE_DEVICE_ADDRESS"):
        config.setdefault("ble", {})["device_address"] = env_address
    if env_model := os.environ.get("SPIKE_MODEL"):
        config.setdefault("agent", {})["model"] = env_model

    return config


def make_transport(config: dict):
    """Create transport based on config."""
    transport_type = config.get("transport", {}).get("type", "ble")
    if transport_type == "mock":
        return MockTransport()
    elif transport_type == "ble":
        ble_config = config.get("ble", {})
        retry_config = ble_config.get("retry", {})
        return BleTransport(
            device_address=ble_config.get("device_address", ""),
            timeout=float(ble_config.get("timeout", 10.0)),
            max_retries=int(retry_config.get("max_retries", 3)),
            backoff_base=float(retry_config.get("backoff_base", 1.0)),
            connect_timeout=float(retry_config.get("connect_timeout", 15.0)),
        )
    else:
        print(f"Unknown transport type: {transport_type}", file=sys.stderr)
        sys.exit(1)


async def async_main(config: dict) -> None:
    """Async entry point."""
    transport = make_transport(config)
    robot = Robot(transport)

    model = config.get("agent", {}).get("model", "claude-sonnet-4-20250514")

    print("spike-mind starting...")
    print(f"  transport: {config.get('transport', {}).get('type', 'ble')}")
    print(f"  model: {model}")

    await robot.connect()
    print("  connected!\n")

    try:
        while True:
            try:
                user_input = input("You: ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nBye!")
                break

            if not user_input:
                continue
            if user_input.lower() in ("quit", "exit", "q"):
                break

            response = await run_agent(robot, user_input, model=model)
            print(f"\nAgent: {response}\n")
    finally:
        await robot.disconnect()
        print("Disconnected.")


def main() -> None:
    """Sync entry point for console_scripts."""
    config = load_config()
    asyncio.run(async_main(config))


if __name__ == "__main__":
    main()
