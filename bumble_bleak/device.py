"""``BLEDevice`` and ``AdvertisementData`` — bleak-compatible value objects."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from bumble.core import AdvertisingData


@dataclass
class BLEDevice:
    """Mirror of ``bleak.backends.device.BLEDevice``.

    ``_bumble_address`` retains the original Bumble ``Address`` so a later
    connect preserves the peer's address type (public vs random) instead of
    guessing it from the string form.
    """

    address: str
    name: Optional[str] = None
    details: Any = None
    rssi: int = 0  # deprecated in bleak but still read by batmon-ha
    _bumble_address: Any = field(default=None, repr=False, compare=False)

    def __hash__(self):
        return hash(self.address)

    def __str__(self):
        return f"{self.address}: {self.name}"


class AdvertisementData:
    """Subset of ``bleak``'s AdvertisementData built from a Bumble advertisement."""

    __slots__ = (
        "local_name",
        "rssi",
        "service_uuids",
        "manufacturer_data",
        "service_data",
        "tx_power",
        "platform_data",
    )

    def __init__(self, ad: AdvertisingData, rssi: int, tx_power=None):
        from .uuids import bumble_uuid_to_str

        self.rssi = rssi
        self.tx_power = tx_power
        self.platform_data = (ad,)

        name = ad.get(AdvertisingData.COMPLETE_LOCAL_NAME)
        if name is None:
            name = ad.get(AdvertisingData.SHORTENED_LOCAL_NAME)
        self.local_name = name.decode("utf-8") if isinstance(name, (bytes, bytearray)) else name

        uuids = []
        for ad_type in (
            AdvertisingData.COMPLETE_LIST_OF_16_BIT_SERVICE_CLASS_UUIDS,
            AdvertisingData.INCOMPLETE_LIST_OF_16_BIT_SERVICE_CLASS_UUIDS,
            AdvertisingData.COMPLETE_LIST_OF_128_BIT_SERVICE_CLASS_UUIDS,
            AdvertisingData.INCOMPLETE_LIST_OF_128_BIT_SERVICE_CLASS_UUIDS,
        ):
            value = ad.get(ad_type)
            if value:
                uuids.extend(bumble_uuid_to_str(u) for u in value)
        self.service_uuids = uuids

        self.manufacturer_data = {}
        md = ad.get(AdvertisingData.MANUFACTURER_SPECIFIC_DATA, raw=True)
        if md:
            # First two bytes are the company id (little-endian).
            self.manufacturer_data[int.from_bytes(md[:2], "little")] = bytes(md[2:])

        self.service_data = {}
