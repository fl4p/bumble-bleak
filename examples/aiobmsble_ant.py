"""Drive aiobmsble's ANT plugin over bumble-bleak (the Phase-2 shadow path).

This proves a *third-party* library whose own ``import bleak`` is redirected to
bumble_bleak (via the self-installing shadow) connects and reads a real BMS with
no BlueZ/D-Bus.

Run:
  sudo .venv/bin/python examples/aiobmsble_ant.py <ADDRESS> [adapter]
"""

import asyncio
import sys

import bumble_bleak.shadow  # noqa: F401  activate the bleak shadow (the only hook needed)

import bleak  # now resolves to the shadow -> bumble_bleak
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

    print(f"bleak module in use: {bleak.__file__} (v{bleak.__version__})")
    print(f"resolving {address} via scan on adapter {adapter or 'hci0'} ...")
    dev = await resolve(address, adapter)
    if dev is None:
        print("device not found in scan")
        return
    print(f"  found {dev.address} name={dev.name!r} rssi={dev.rssi}")

    # aiobmsble's ANT plugin — its internal `import bleak` hits the shim.
    from aiobmsble.bms.ant_bms import BMS

    bms = BMS(dev, keep_alive=False)
    print(f"connecting + updating via aiobmsble {type(bms).__module__} ...")
    try:
        sample = await bms.async_update()
        print("=== aiobmsble BMSSample (over bumble-bleak) ===")
        for k, v in sample.items():
            print(f"  {k}: {v}")
    finally:
        await bms.disconnect()
    print("done")


if __name__ == "__main__":
    asyncio.run(main())
