"""End-to-end test of the facade against an in-process Bumble GATT server.

Two Bumble devices are wired over a virtual LocalLink: a server hosting a GATT
service, and a client device that the bumble-bleak facade drives. This exercises
connect / service discovery / read / write / notify with no hardware.
"""

import asyncio

import pytest

from bumble.controller import Controller
from bumble.device import Device
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
