"""bleak-compatible ``BleakScanner``."""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from bumble.core import AdvertisingData

from ._backend import get_backend
from .device import AdvertisementData, BLEDevice


def _device_name(ad: AdvertisingData) -> Optional[str]:
    name = ad.get(AdvertisingData.COMPLETE_LOCAL_NAME)
    if name is None:
        name = ad.get(AdvertisingData.SHORTENED_LOCAL_NAME)
    if isinstance(name, (bytes, bytearray)):
        return name.decode("utf-8", "replace")
    return name


class BleakScanner:
    def __init__(self, detection_callback=None, service_uuids=None, adapter=None, **kwargs):
        self._adapter = adapter
        self._detection_callback = detection_callback
        self._backend = None
        self._running = False
        # address (clean MAC) -> (BLEDevice, AdvertisementData)
        self._found: Dict[str, Tuple[BLEDevice, AdvertisementData]] = {}

    async def start(self) -> None:
        if self._running:
            return
        self._backend = await get_backend(self._adapter)
        await self._backend.acquire()
        self._backend.add_advertisement_handler(self._on_advertisement)
        await self._backend.start_scanning()
        self._running = True

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        self._backend.remove_advertisement_handler(self._on_advertisement)
        try:
            await self._backend.stop_scanning()
        finally:
            await self._backend.release()

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, *args):
        await self.stop()

    def _on_advertisement(self, advertisement) -> None:
        address = advertisement.address.to_string(False)
        device = BLEDevice(
            address=address,
            name=_device_name(advertisement.data),
            rssi=advertisement.rssi,
            _bumble_address=advertisement.address,
        )
        adv_data = AdvertisementData(advertisement.data, rssi=advertisement.rssi)
        self._found[address] = (device, adv_data)
        if self._detection_callback is not None:
            self._detection_callback(device, adv_data)

    @property
    def discovered_devices(self) -> List[BLEDevice]:
        return [device for device, _ in self._found.values()]

    @property
    def discovered_devices_and_advertisement_data(self) -> Dict[str, Tuple[BLEDevice, AdvertisementData]]:
        # Matches bleak: keyed by address, values are (device, advertisement) tuples.
        return dict(self._found)
