"""`bleak` shadow package backed by bumble_bleak.

Place this directory ahead of site-packages on the import path (e.g.
``PYTHONPATH=.../bumble-bleak/compat``) so that ``import bleak`` — including
third-party libraries such as aiobmsble — resolves to bumble_bleak instead of
the real BlueZ/D-Bus bleak.
"""

from bumble_bleak import (
    BLEDevice,
    BleakClient,
    BleakScanner,
    exc,
    uuids,
)
from bumble_bleak.exc import (
    BleakCharacteristicNotFoundError,
    BleakDeviceNotFoundError,
    BleakError,
)

# Report a bleak-3.x-compatible version for any feature/version checks.
__version__ = "3.0.2"

__all__ = [
    "BleakClient",
    "BleakScanner",
    "BLEDevice",
    "BleakError",
    "BleakDeviceNotFoundError",
    "BleakCharacteristicNotFoundError",
    "exc",
    "uuids",
]
