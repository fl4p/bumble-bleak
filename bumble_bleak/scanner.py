"""bleak-compatible ``BleakScanner``."""

from __future__ import annotations

import logging
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from bumble.core import AdvertisingData

from ._backend import get_backend
from .device import AdvertisementData, BLEDevice
from .uuids import normalize_uuid_str

logger = logging.getLogger(__name__)


def _device_name(ad: AdvertisingData) -> Optional[str]:
    name = ad.get(AdvertisingData.COMPLETE_LOCAL_NAME)
    if name is None:
        name = ad.get(AdvertisingData.SHORTENED_LOCAL_NAME)
    if isinstance(name, (bytes, bytearray)):
        return name.decode("utf-8", "replace")
    return name


def _normalized_uuid_set(uuids: Iterable) -> Optional[frozenset]:
    """Normalize an iterable of UUID strings, or ``None`` if it cannot be read.

    Returns ``None`` — meaning *undeterminable*, never *empty* — when the input
    is not an iterable of strings or an entry does not parse. Callers must treat
    ``None`` as "no evidence of a match", not as "no UUIDs advertised".
    """
    if uuids is None or isinstance(uuids, (str, bytes, bytearray)):
        return None
    try:
        normalized = set()
        for u in uuids:
            if not isinstance(u, str):
                return None
            normalized.add(normalize_uuid_str(u))
        return frozenset(normalized)
    except Exception:  # malformed UUID string, non-iterable, ...
        return None


class BleakScanner:
    def __init__(self, detection_callback=None, service_uuids=None, adapter=None, **kwargs):
        self._adapter = adapter
        self._detection_callback = detection_callback
        # Normalize the caller's filter once. An empty list / None means
        # "no filter" (bleak semantics: BaseBleakScanner.is_allowed_uuid).
        self._service_uuids: Optional[frozenset] = None
        if service_uuids:
            normalized = _normalized_uuid_set(service_uuids)
            if normalized is None:
                raise ValueError(f"invalid service_uuids filter: {service_uuids!r}")
            self._service_uuids = normalized
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

    def is_allowed_uuid(self, service_uuids: Optional[Sequence[str]]) -> bool:
        """Mirror of ``bleak.backends.scanner.BaseBleakScanner.is_allowed_uuid``.

        ``service_uuids`` are the UUIDs carried by the advertisement. With no
        filter configured everything is allowed. With a filter configured, an
        advertisement is allowed only if its UUIDs can be read AND intersect the
        filter — an unreadable/absent UUID list is *not* a match.
        """
        if not self._service_uuids:
            return True
        advertised = _normalized_uuid_set(service_uuids)
        if not advertised:  # None (unreadable) or empty -> no evidence of a match
            return False
        return not advertised.isdisjoint(self._service_uuids)

    def _on_advertisement(self, advertisement) -> None:
        # A malformed advertisement from any device in radio range must never
        # take down the scan loop.
        try:
            address = advertisement.address.to_string(False)
            device = BLEDevice(
                address=address,
                name=_device_name(advertisement.data),
                rssi=advertisement.rssi,
                _bumble_address=advertisement.address,
            )
            adv_data = AdvertisementData(advertisement.data, rssi=advertisement.rssi)
        except Exception:
            logger.exception("dropping unparseable advertisement")
            return

        if not self.is_allowed_uuid(getattr(adv_data, "service_uuids", None)):
            return

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
