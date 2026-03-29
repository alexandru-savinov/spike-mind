"""Tests for BLE reconnect logic in BleTransport."""

import asyncio
from unittest.mock import AsyncMock, PropertyMock

import pytest

from spike_mind.transport import BleTransport
from spike_mind.protocol import CHAR_UUID, PYBRICKS_WRITE_STDIN


@pytest.fixture
def transport():
    """BleTransport with fast retry settings for testing."""
    return BleTransport(
        device_address="AA:BB:CC:DD:EE:FF",
        timeout=1.0,
        max_retries=2,
        backoff_base=0.01,  # fast backoff for tests
        connect_timeout=1.0,
    )


def _make_mock_client(is_connected=True):
    """Create a mock BleakClient."""
    client = AsyncMock()
    type(client).is_connected = PropertyMock(return_value=is_connected)
    client.connect = AsyncMock()
    client.disconnect = AsyncMock()
    client.start_notify = AsyncMock()
    client.write_gatt_char = AsyncMock()
    return client


def _mock_client_cls(client):
    """Return a callable that produces the given mock client."""
    return lambda addr: client


class TestRetryConfig:
    def test_default_retry_config(self):
        t = BleTransport()
        assert t._max_retries == 3
        assert t._backoff_base == 1.0
        assert t._connect_timeout == 15.0

    def test_custom_retry_config(self):
        t = BleTransport(max_retries=5, backoff_base=2.0, connect_timeout=30.0)
        assert t._max_retries == 5
        assert t._backoff_base == 2.0
        assert t._connect_timeout == 30.0


class TestSendDisconnect:
    @pytest.mark.asyncio
    async def test_send_raises_when_not_connected(self, transport):
        """send() raises ConnectionError when client is None."""
        with pytest.raises(ConnectionError, match="disconnected"):
            await transport.send(b"\x00" * 8)

    @pytest.mark.asyncio
    async def test_send_failure_triggers_reconnect(self, transport):
        """When write_gatt_char fails, transport reconnects then raises."""
        bad_client = _make_mock_client()
        bad_client.write_gatt_char.side_effect = Exception("BLE write failed")
        transport._client = bad_client

        good_client = _make_mock_client()
        transport._bleak_client_cls = _mock_client_cls(good_client)
        transport._address = "AA:BB:CC:DD:EE:FF"

        with pytest.raises(ConnectionError, match="reconnecting"):
            await transport.send(b"\x00" * 8)

        # Verify old client was cleaned up
        bad_client.disconnect.assert_called()

    @pytest.mark.asyncio
    async def test_send_succeeds_after_reconnect(self, transport):
        """After a failed send triggers reconnect, next send works."""
        bad_client = _make_mock_client()
        bad_client.write_gatt_char.side_effect = Exception("BLE write failed")
        transport._client = bad_client

        good_client = _make_mock_client()
        transport._bleak_client_cls = _mock_client_cls(good_client)
        transport._address = "AA:BB:CC:DD:EE:FF"

        with pytest.raises(ConnectionError):
            await transport.send(b"\x00" * 8)

        # transport._client is now good_client after reconnect
        await transport.send(b"\x00" * 8)
        good_client.write_gatt_char.assert_called()


class TestReceiveDisconnect:
    @pytest.mark.asyncio
    async def test_receive_timeout_triggers_reconnect(self, transport):
        """When receive times out, transport reconnects then raises."""
        transport._client = _make_mock_client()

        good_client = _make_mock_client()
        transport._bleak_client_cls = _mock_client_cls(good_client)
        transport._address = "AA:BB:CC:DD:EE:FF"

        with pytest.raises(ConnectionError, match="reconnecting"):
            await transport.receive()


class TestReconnectRetries:
    @pytest.mark.asyncio
    async def test_reconnect_retries_up_to_max(self, transport):
        """Reconnect tries max_retries times before giving up."""
        transport._client = _make_mock_client()
        transport._address = "AA:BB:CC:DD:EE:FF"

        fail_client = _make_mock_client()
        fail_client.connect.side_effect = Exception("Connection failed")
        transport._bleak_client_cls = _mock_client_cls(fail_client)

        with pytest.raises(ConnectionError, match="failed after 2 attempts"):
            await transport._reconnect()

        assert fail_client.connect.call_count == 2

    @pytest.mark.asyncio
    async def test_reconnect_succeeds_on_second_attempt(self, transport):
        """Reconnect succeeds if a retry works."""
        transport._client = _make_mock_client()
        transport._address = "AA:BB:CC:DD:EE:FF"

        attempt = 0

        def make_client(addr):
            nonlocal attempt
            attempt += 1
            c = _make_mock_client()
            if attempt == 1:
                c.connect.side_effect = Exception("First try fails")
            return c

        transport._bleak_client_cls = make_client

        await transport._reconnect()

        # Should have reconnected successfully on second attempt
        assert transport._client is not None
        assert attempt == 2


class TestErrorMessages:
    @pytest.mark.asyncio
    async def test_send_error_is_descriptive(self, transport):
        """Error message mentions BLE disconnect and reconnecting."""
        client = _make_mock_client()
        client.write_gatt_char.side_effect = Exception("fail")
        transport._client = client

        good_client = _make_mock_client()
        transport._bleak_client_cls = _mock_client_cls(good_client)
        transport._address = "AA:BB:CC:DD:EE:FF"

        with pytest.raises(ConnectionError) as exc_info:
            await transport.send(b"\x00" * 8)
        assert "BLE disconnected" in str(exc_info.value)
        assert "reconnecting" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_receive_error_is_descriptive(self, transport):
        """Error message mentions BLE disconnect and reconnecting."""
        transport._client = _make_mock_client()

        good_client = _make_mock_client()
        transport._bleak_client_cls = _mock_client_cls(good_client)
        transport._address = "AA:BB:CC:DD:EE:FF"

        with pytest.raises(ConnectionError) as exc_info:
            await transport.receive()
        assert "BLE disconnected" in str(exc_info.value)
        assert "reconnecting" in str(exc_info.value)
