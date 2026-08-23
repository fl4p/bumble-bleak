"""End-to-end test of the facade against an in-process Bumble GATT server.

Two Bumble devices are wired over a virtual LocalLink: a server hosting a GATT
service, and a client device that the bumble-bleak facade drives. This exercises
connect / service discovery / read / write / notify with no hardware.
"""

import asyncio

import pytest

from bumble.controller import Controller
from bumble.device import Device, Peer
from bumble.gatt import (
    Characteristic,
    CharacteristicValue,
    Service,
)
from bumble.hci import Address, AddressType
from bumble.host import Host
from bumble.link import LocalLink
from bumble.transport.common import AsyncPipeSink

import bumble_bleak as bleak
from bumble_bleak import _backend
from bumble_bleak import BleakClient

SERVICE_UUID = "FFE0"
READ_UUID = "FFE1"
WRITE_UUID = "FFE2"
NOTIFY_UUID = "FFE3"

SERVER_ADDR = "F0:F0:F0:F0:F0:F0"
CLIENT_ADDR = "F1:F1:F1:F1:F1:F1"


def _make_device(name, addr, link):
    controller = Controller(name, link=link, public_address=addr)
    host = Host(controller, AsyncPipeSink(controller))
    return Device(name=name, address=Address(addr), host=host)


@pytest.fixture
async def server_and_client():
    link = LocalLink()

    # ---- server with a GATT service ----
    server = _make_device("server", SERVER_ADDR, link)
    written = {}

    def on_write(connection, value):
        written["value"] = bytes(value)

    read_char = Characteristic(
        READ_UUID,
        Characteristic.Properties.READ,
        Characteristic.READABLE,
        value=bytes([0xAB, 0xCD]),
    )
    write_char = Characteristic(
        WRITE_UUID,
        Characteristic.Properties.WRITE | Characteristic.Properties.WRITE_WITHOUT_RESPONSE,
        Characteristic.WRITEABLE,
        value=CharacteristicValue(write=on_write),
    )
    notify_char = Characteristic(
        NOTIFY_UUID,
        Characteristic.Properties.NOTIFY,
        Characteristic.READABLE,
        value=b"",
    )
    server.add_service(Service(SERVICE_UUID, [read_char, write_char, notify_char]))
    await server.power_on()
    await server.start_advertising(advertising_interval_min=1.0)

    # ---- client device the facade will drive ----
    client_device = _make_device("client", CLIENT_ADDR, link)
    await client_device.power_on()
    _backend._TEST_DEVICES["test"] = client_device

    try:
        yield server, written, notify_char
    finally:
        _backend._TEST_DEVICES.pop("test", None)
        _backend._backends.clear()


async def test_connect_discover_read_write_notify(server_and_client):
    server, written, notify_char = server_and_client

    # Connect to the peripheral's advertised (random) address.
    server_address = server.random_address
    dev = bleak.BLEDevice(
        address=server_address.to_string(False),
        name="server",
        _bumble_address=server_address,
    )
    client = BleakClient(dev, adapter="test")

    await client.connect(timeout=5)
    assert client.is_connected

    # service discovery
    service_uuids = [s.uuid for s in client.services]
    assert bleak.uuids.normalize_uuid_str(SERVICE_UUID) in service_uuids

    char = next(
        c
        for s in client.services
        for c in s.characteristics
        if c.uuid == bleak.uuids.normalize_uuid_str(READ_UUID)
    )
    assert "read" in char.properties

    # read
    value = await client.read_gatt_char(bleak.uuids.normalize_uuid_str(READ_UUID))
    assert bytes(value) == bytes([0xAB, 0xCD])

    # write (by 16-bit short uuid, exercising normalization)
    await client.write_gatt_char(WRITE_UUID, bytes([0x01, 0x02, 0x03]), response=True)
    await asyncio.sleep(0.05)
    assert written.get("value") == bytes([0x01, 0x02, 0x03])

    # notify
    received = []
    await client.start_notify(NOTIFY_UUID, lambda sender, data: received.append(bytes(data)))
    await asyncio.sleep(0.05)
    await server.notify_subscribers(notify_char, bytes([0x99]))
    await asyncio.sleep(0.1)
    assert received and received[-1] == bytes([0x99])

    await client.stop_notify(NOTIFY_UUID)
    await client.disconnect()
    assert not client.is_connected


def _client_for(server):
    server_address = server.random_address
    dev = bleak.BLEDevice(
        address=server_address.to_string(False),
        name="server",
        _bumble_address=server_address,
    )
    return bleak.BleakClient(dev, adapter="test")


async def test_connect_negotiates_mtu(server_and_client):
    """Without an ATT MTU exchange every payload is capped at 20 bytes, which
    silently truncates peripherals that size their records to the negotiated MTU.
    """
    server, _written, notify_char = server_and_client

    client = _client_for(server)
    assert client.mtu_size == 23  # nothing negotiated yet

    await client.connect(timeout=5)
    try:
        assert client.mtu_size == 517

        # The point of the exchange: a record larger than the 20-byte default
        # payload has to survive intact.
        received = []
        await client.start_notify(NOTIFY_UUID, lambda s, d: received.append(bytes(d)))
        await asyncio.sleep(0.05)
        payload = bytes(range(64))
        await server.notify_subscribers(notify_char, payload)
        await asyncio.sleep(0.1)
        assert received and received[-1] == payload
    finally:
        await client.disconnect()


async def test_connect_survives_failed_mtu_exchange(server_and_client, monkeypatch):
    """A peer that refuses the exchange must still yield a usable link."""
    server, _written, _notify = server_and_client

    async def boom(self, mtu):
        raise RuntimeError("peer refused")

    monkeypatch.setattr(Peer, "request_mtu", boom)

    client = _client_for(server)
    await client.connect(timeout=5)
    try:
        assert client.is_connected
        assert client.mtu_size >= 23
        value = await client.read_gatt_char(bleak.uuids.normalize_uuid_str(READ_UUID))
        assert bytes(value) == bytes([0xAB, 0xCD])
    finally:
        await client.disconnect()


async def test_direct_address_connect_timeout_is_total(server_and_client, monkeypatch):
    _server, _written, _notify = server_and_client
    device = _backend._TEST_DEVICES["test"]
    timeouts = []

    async def fail_connect(*_args, timeout, **_kwargs):
        timeouts.append(timeout)
        if len(timeouts) == 1:
            await asyncio.sleep(0.08)
        raise RuntimeError("unreachable")

    monkeypatch.setattr(device, "connect", fail_connect)

    client = BleakClient(SERVER_ADDR, adapter="test")
    with pytest.raises(bleak.BleakError):
        await client.connect(timeout=0.12)

    assert len(timeouts) == 2
    assert timeouts[0] == pytest.approx(0.06, abs=0.015)
    assert 0 < timeouts[1] < 0.06
    assert _backend._backends["test"]._users == 0


async def test_disconnect_during_service_discovery_is_bleak_error(
    server_and_client, monkeypatch
):
    server, _written, _notify = server_and_client
    client = _client_for(server)

    async def disconnect_during_discovery(peer):
        await peer.connection.disconnect()
        cancelled = asyncio.get_running_loop().create_future()
        cancelled.cancel()
        await cancelled

    monkeypatch.setattr(Peer, "discover_services", disconnect_during_discovery)

    with pytest.raises(bleak.BleakError, match="service discovery.*disconnected"):
        await client.connect(timeout=5)
    await asyncio.sleep(0.05)
    assert not client.is_connected
    assert not server.connections
    assert _backend._backends["test"]._users == 0


async def test_cancelling_connect_disconnects_physical_link(
    server_and_client, monkeypatch
):
    server, _written, _notify = server_and_client
    client = _client_for(server)
    discovery_started = asyncio.Event()

    async def blocked_discovery(_peer):
        discovery_started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(Peer, "discover_services", blocked_discovery)

    task = asyncio.create_task(client.connect(timeout=5))
    await asyncio.wait_for(discovery_started.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0)

    assert not client.is_connected
    assert not server.connections
    assert _backend._backends["test"]._users == 0


async def test_caller_cancellation_wins_after_disconnect(
    server_and_client, monkeypatch
):
    server, _written, _notify = server_and_client
    client = _client_for(server)
    discovery_started = asyncio.Event()
    pending_att = asyncio.get_running_loop().create_future()

    async def blocked_discovery(_peer):
        discovery_started.set()
        await pending_att

    monkeypatch.setattr(Peer, "discover_services", blocked_discovery)

    task = asyncio.create_task(client.connect(timeout=5))
    await asyncio.wait_for(discovery_started.wait(), timeout=5)
    connection = next(iter(server.connections.values()))
    await connection.disconnect()
    await asyncio.sleep(0.05)
    assert not client.is_connected

    # Bumble cancels the pending ATT future as the caller cancels its operation.
    # Caller cancellation must win even when both happen in one event-loop turn.
    pending_att.cancel()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert not server.connections
    assert _backend._backends["test"]._users == 0


async def test_disconnect_during_notify_is_bleak_error(server_and_client, monkeypatch):
    server, _written, _notify = server_and_client
    client = _client_for(server)
    await client.connect(timeout=5)

    char = client.services.get_characteristic(
        bleak.uuids.normalize_uuid_str(NOTIFY_UUID)
    )

    async def disconnect_during_subscribe(_subscriber):
        connection = next(iter(server.connections.values()))
        await connection.disconnect()
        await asyncio.sleep(0.05)
        cancelled = asyncio.get_running_loop().create_future()
        cancelled.cancel()
        await cancelled

    monkeypatch.setattr(char.obj, "subscribe", disconnect_during_subscribe)

    with pytest.raises(bleak.BleakError, match="notification subscription.*disconnected"):
        await client.start_notify(char, lambda _sender, _data: None)
    assert not client.is_connected
    await client.disconnect()
    assert _backend._backends["test"]._users == 0


async def test_scanner_discovers_advertiser(server_and_client):
    server, _written, _notify = server_and_client

    scanner = bleak.BleakScanner(adapter="test")
    await scanner.start()
    for _ in range(50):
        await asyncio.sleep(0.05)
        if scanner.discovered_devices:
            break
    await scanner.stop()

    addresses = [d.address for d in scanner.discovered_devices]
    assert server.random_address.to_string(False) in addresses

    # bleak-compatible shape: {address: (BLEDevice, AdvertisementData)}
    daad = scanner.discovered_devices_and_advertisement_data
    addr = server.random_address.to_string(False)
    assert addr in daad
    dev, adv = daad[addr]  # values are (device, advertisement) tuples
    assert dev.address == addr
    assert hasattr(adv, "rssi")


async def test_pairing_just_works(server_and_client):
    from bumble.pairing import PairingConfig, PairingDelegate

    server, _written, _notify = server_and_client
    # Peripheral accepts "Just Works" pairing.
    server.pairing_config_factory = lambda _conn: PairingConfig(
        sc=True,
        mitm=False,
        bonding=True,
        delegate=PairingDelegate(PairingDelegate.NO_OUTPUT_NO_INPUT),
    )

    dev = bleak.BLEDevice(
        address=server.random_address.to_string(False),
        name="server",
        _bumble_address=server.random_address,
    )
    client = BleakClient(dev, adapter="test")
    await client.connect(timeout=5)

    ok = await client.pair()
    assert ok is True
    assert client._connection.is_encrypted

    await client.disconnect()
