# bumble-bleak

A **bleak-compatible BLE central API for Linux**, backed by
[Bumble](https://github.com/google/bumble) — a pure-Python Bluetooth stack.

It talks to the controller over an **HCI socket** (`HCI_CHANNEL_USER`), which
detaches the in-kernel BlueZ stack entirely. So unlike `bleak` on Linux, there is
**no BlueZ/D-Bus dependency and no in-kernel Bluetooth stack** in the path — the
whole HCI → L2CAP → ATT → GATT stack runs in Python.

This mirrors the approach of [micropython-bleak](https://github.com/fl4p/micropython-bleak)
(a thin bleak facade over `aioble`): here the lower stack is Bumble.

## Why

`bleak` on Linux drives BlueZ over D-Bus and can hit obscure bugs in the daemon
or kernel stack. `bumble-bleak` exposes the same client-side API but routes
around both.

## Status

Implements the GATT **central/client** subset that
[batmon-ha](https://github.com/fl4p/batmon-ha) uses:

- `BleakScanner`: `start`/`stop`, `discovered_devices`,
  `discovered_devices_and_advertisement_data`, `adapter=`
- `BleakClient`: `connect`/`disconnect`/`is_connected`, `services`/`get_services`,
  `read_gatt_char`/`write_gatt_char`/`read_gatt_descriptor`,
  `start_notify`/`stop_notify`, `address`, `disconnected_callback`
- `BLEDevice`, `AdvertisementData`, `BleakGATTCharacteristic`/`Service`/`Descriptor`
- `BleakError`, `BleakDeviceNotFoundError`, `BleakCharacteristicNotFoundError`
- `uuids.normalize_uuid_str`

**Not yet implemented:** SMP pairing/bonding (`BleakClient.pair`) — raises
`BleakError`. GATT server role.

## Usage

```python
import bumble_bleak as bleak
from bumble_bleak import BleakClient, BleakScanner
```

Adapter naming: `None` → `hci-socket:0` (override with `$BUMBLE_BLEAK_TRANSPORT`),
`"hci0"`/`"hciN"` → `hci-socket:N`, any string with `:` is used as a literal
Bumble transport spec.

### Run requirements (Linux)

The process must own the adapter exclusively:

```sh
sudo systemctl stop bluetooth      # or: sudo hciconfig hci0 down
sudo python examples/spike.py AA:BB:CC:DD:EE:FF hci0
```

(or grant the interpreter `CAP_NET_RAW` + `CAP_NET_ADMIN`).

## Tests

```sh
pip install -e . pytest pytest-asyncio
pytest
```

The integration tests run the facade against an in-process Bumble GATT server
over a virtual link — no hardware needed.
