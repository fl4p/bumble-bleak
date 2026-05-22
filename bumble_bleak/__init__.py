"""bumble-bleak: a bleak-compatible BLE central API backed by Bumble.

Drop-in for the subset of bleak used by GATT-client applications, with no
BlueZ/D-Bus dependency. Use as::

    import bumble_bleak as bleak
    from bumble_bleak import BleakClient, BleakScanner
"""

from __future__ import annotations

from . import exc, uuids
from .characteristic import (
    BleakGATTCharacteristic,
    BleakGATTDescriptor,
    BleakGATTService,
    BleakGATTServiceCollection,
)
from .client import BleakClient
from .device import AdvertisementData, BLEDevice
from .exc import (
    BleakCharacteristicNotFoundError,
    BleakDeviceNotFoundError,
    BleakError,
)
from .scanner import BleakScanner

__version__ = "0.1.0"

__all__ = [
    "BleakClient",
    "BleakScanner",
    "BLEDevice",
    "AdvertisementData",
    "BleakGATTCharacteristic",
    "BleakGATTDescriptor",
    "BleakGATTService",
    "BleakGATTServiceCollection",
    "BleakError",
    "BleakDeviceNotFoundError",
    "BleakCharacteristicNotFoundError",
    "exc",
    "uuids",
]
