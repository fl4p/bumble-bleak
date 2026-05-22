"""Same as aiobmsble_ant.py but with NO bumble_bleak reference at all.

This mirrors how batmon-ha consumes the stack: code imports only ``bleak``, and
the shadow is activated transparently by the ``.pth`` installed alongside
bumble-bleak (see the Dockerfile). Run it in a venv where that .pth exists:

  sudo .venv/bin/python examples/aiobmsble_plain.py <ADDRESS> [adapter]
"""

import asyncio
import sys

import bleak  # transparently the bumble-bleak shadow (via the installed .pth)
from bleak import BleakScanner


async def resolve(address, adapter, seconds=8):
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
    return target


async def main():
    address = sys.argv[1]
    adapter = sys.argv[2] if len(sys.argv) > 2 else None
    print(f"bleak in use: {bleak.__file__} (v{bleak.__version__})")
    dev = await resolve(address, adapter)
    if dev is None:
        print("device not found")
        return
    print(f"found {dev.address} name={dev.name!r} rssi={dev.rssi}")

    from aiobmsble.bms.ant_bms import BMS

    bms = BMS(dev, keep_alive=False)
    try:
        sample = await bms.async_update()
        print(f"sample: {sample['cell_count']} cells, {sample['voltage']} V, "
              f"{sample['current']} A, SoC {sample['battery_level']}%")
    finally:
        await bms.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
