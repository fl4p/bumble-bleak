"""Hardware spike: drive a real BLE device through bumble-bleak on Linux.

This is the phase-1 validation from the plan. It uses the facade exactly like
batmon-ha would, against a real controller via an HCI socket.

Prerequisites on the Linux host:
  * bluetoothd must NOT own the adapter:  sudo systemctl stop bluetooth
    (or:  sudo hciconfig hci0 down)
  * run with privileges:  sudo .../python spike.py AA:BB:CC:DD:EE:FF [hci0]
    (or grant the interpreter CAP_NET_RAW + CAP_NET_ADMIN)

Usage:
  python spike.py <ADDRESS> [adapter]
  python spike.py            # scan only, list nearby devices
"""

import asyncio
import sys

import bumble_bleak.shadow  # noqa: F401 — activate the bleak shadow (the only hook needed)
from bleak import BleakClient, BleakScanner

ANT_CHAR = "0000ffe1-0000-1000-8000-00805f9b34fb"


def _crc16_modbus(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def _ant_command(func: int, addr: int, value: int) -> bytes:
    frame = bytes([0x7E, 0xA1, func, addr & 0xFF, (addr >> 8) & 0xFF, value])
    crc = _crc16_modbus(frame[1:])
    return frame + bytes([crc & 0xFF, (crc >> 8) & 0xFF, 0xAA, 0x55])


def _parse_status(buf: bytes):
    u16 = lambda i: int.from_bytes(buf[i : i + 2], "little", signed=False)
    i16 = lambda i: int.from_bytes(buf[i : i + 2], "little", signed=True)
    num_temp, num_cell = buf[8], buf[9]
    off = 34
    cells = [u16(off + i * 2) for i in range(num_cell)]
    off += num_cell * 2 + num_temp * 2 + 2 + 2  # temps + mos_temp + balancer_temp
    voltage = u16(off) * 0.01
    current = i16(off + 2) * 0.1
    soc = u16(off + 4)
    return num_cell, voltage, current, soc, cells


async def ant_subscribe_fetch(client, seconds=10):
    print(f"--- subscribing to ANT notify char {ANT_CHAR} ---")
    buffer = bytearray()
    frames = []

    def on_notify(_sender, data: bytearray):
        if bytes(data).startswith(b"\x7e\xa1"):
            buffer.clear()
        buffer.extend(data)
        if bytes(buffer).endswith(b"\x55"):
            frames.append(bytes(buffer))
            buffer.clear()

    await client.start_notify(ANT_CHAR, on_notify)
    # Request a Status frame (func=0x01, addr=0x0000, val=0xbe).
    cmd = _ant_command(0x01, 0x0000, 0xBE)
    print(f"  writing Status command: {cmd.hex()}")
    await client.write_gatt_char(ANT_CHAR, cmd, response=False)

    for _ in range(int(seconds / 0.2)):
        await asyncio.sleep(0.2)
        if frames:
            break

    await client.stop_notify(ANT_CHAR)

    if not frames:
        print("  no notification frames received")
        return
    frame = frames[-1]
    print(f"  received frame ({len(frame)} bytes): {frame.hex()}")
    try:
        n, v, i, soc, cells = _parse_status(frame)
        print(f"  decoded: {n} cells, pack={v:.2f} V, current={i:.1f} A, SoC={soc}%")
        print(f"  cell mV: {cells}")
    except Exception as e:
        print(f"  decode failed: {e}")


async def scan(adapter, seconds=6):
    print(f"scanning for {seconds}s on adapter {adapter or 'hci0'} ...")
    scanner = BleakScanner(adapter=adapter)
    await scanner.start()
    await asyncio.sleep(seconds)
    await scanner.stop()
    devices = scanner.discovered_devices_and_advertisement_data
    for dev, adv in devices.values():
        print(f"  {dev.address}  rssi={adv.rssi}  name={dev.name!r}")
    return scanner


async def resolve(address, adapter, seconds=8):
    """Scan to find the device so we connect with its real address type."""
    print(f"resolving {address} via scan on adapter {adapter or 'hci0'} ...")
    scanner = BleakScanner(adapter=adapter)
    await scanner.start()
    target = None
    for _ in range(int(seconds / 0.2)):
        await asyncio.sleep(0.2)
        for dev in scanner.discovered_devices:
            if dev.address.lower() == address.lower():
                target = dev
                break
        if target:
            break
    await scanner.stop()
    if target:
        print(f"  found {target.address} name={target.name!r} rssi={target.rssi}")
    else:
        print("  not found in scan; will try raw address")
    return target or address


async def explore(address, adapter):
    target = await resolve(address, adapter)
    print(f"connecting to {address} on adapter {adapter or 'hci0'} ...")
    client = BleakClient(target, adapter=adapter)
    await client.connect(timeout=15)
    print("connected:", client.is_connected)
    for service in client.services:
        print(f"[service] {service.uuid}")
        for char in service.characteristics:
            print(f"   [char] {char.uuid} handle={char.handle} props={char.properties}")
            if "read" in char.properties:
                try:
                    value = await client.read_gatt_char(char.uuid)
                    print(f"          value={bytes(value).hex()}")
                except Exception as e:
                    print(f"          read failed: {e}")
    # If this is an ANT BMS (has the ffe1 notify char), exercise a live subscribe+fetch.
    if client.services.get_characteristic(ANT_CHAR) is not None:
        await ant_subscribe_fetch(client)

    await client.disconnect()
    print("disconnected")


async def main():
    address = sys.argv[1] if len(sys.argv) > 1 else None
    adapter = sys.argv[2] if len(sys.argv) > 2 else None
    if address is None:
        await scan(adapter)
    else:
        await explore(address, adapter)


if __name__ == "__main__":
    asyncio.run(main())
