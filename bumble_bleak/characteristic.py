"""bleak-compatible GATT service/characteristic/descriptor wrappers.

Each wraps a Bumble proxy and exposes the attributes batmon-ha reads:
service ``.uuid``/``.characteristics``; characteristic ``.uuid``/``.handle``/
``.properties``/``.descriptors``; descriptor ``.handle``/``.uuid``.
"""

from __future__ import annotations

from typing import List, Optional

from bumble.gatt import Characteristic

from .uuids import bumble_uuid_to_str, normalize_uuid_str

# Bumble property flag -> bleak property string
_PROPERTY_MAP = [
    (Characteristic.Properties.BROADCAST, "broadcast"),
    (Characteristic.Properties.READ, "read"),
    (Characteristic.Properties.WRITE_WITHOUT_RESPONSE, "write-without-response"),
    (Characteristic.Properties.WRITE, "write"),
    (Characteristic.Properties.NOTIFY, "notify"),
    (Characteristic.Properties.INDICATE, "indicate"),
    (Characteristic.Properties.AUTHENTICATED_SIGNED_WRITES, "authenticated-signed-writes"),
    (Characteristic.Properties.EXTENDED_PROPERTIES, "extended-properties"),
]


def _properties_to_strings(properties) -> List[str]:
    return [name for flag, name in _PROPERTY_MAP if properties & flag]


class BleakGATTDescriptor:
    def __init__(self, proxy, characteristic_uuid: str):
        self.obj = proxy
        self.handle: int = proxy.handle
        self.uuid: str = bumble_uuid_to_str(proxy.type)
        self.characteristic_uuid = characteristic_uuid

    def __str__(self):
        return f"{self.handle}: {self.uuid}"


class BleakGATTCharacteristic:
    def __init__(self, proxy, service_uuid: str):
        self.obj = proxy  # bumble CharacteristicProxy
        self.handle: int = proxy.handle
        self.uuid: str = bumble_uuid_to_str(proxy.uuid)
        self.properties: List[str] = _properties_to_strings(proxy.properties)
        self.service_uuid = service_uuid
        self.descriptors: List[BleakGATTDescriptor] = [
            BleakGATTDescriptor(d, self.uuid) for d in getattr(proxy, "descriptors", [])
        ]

    @property
    def description(self) -> str:
        return self.uuid

    def __hash__(self):
        return hash(self.handle)

    def __eq__(self, other):
        return isinstance(other, BleakGATTCharacteristic) and other.handle == self.handle

    def __str__(self):
        return f"{self.handle}: {self.uuid} ({','.join(self.properties)})"


class BleakGATTService:
    def __init__(self, proxy):
        self.obj = proxy  # bumble ServiceProxy
        self.handle: int = proxy.handle
        self.uuid: str = bumble_uuid_to_str(proxy.uuid)
        self.characteristics: List[BleakGATTCharacteristic] = [
            BleakGATTCharacteristic(c, self.uuid) for c in proxy.characteristics
        ]

    def __str__(self):
        return f"{self.handle}: {self.uuid}"


class BleakGATTServiceCollection:
    """Iterable collection mirroring ``BleakClient.services``.

    Truthy only once services have been discovered (batmon-ha relies on
    ``if client.services:`` to know whether discovery has happened).
    """

    def __init__(self, services: List[BleakGATTService]):
        self._services = services
        self._by_char_handle = {}
        self._by_char_uuid = {}
        self._by_desc_handle = {}
        for service in services:
            for char in service.characteristics:
                self._by_char_handle[char.handle] = char
                self._by_char_uuid.setdefault(char.uuid, char)
                for desc in char.descriptors:
                    self._by_desc_handle[desc.handle] = desc

    def __iter__(self):
        return iter(self._services)

    def __len__(self):
        return len(self._services)

    @property
    def services(self) -> List[BleakGATTService]:
        return self._services

    @property
    def characteristics(self):
        return dict(self._by_char_handle)

    def get_service(self, specifier) -> Optional[BleakGATTService]:
        """Get a service by handle (int) or UUID (str/UUID, short or long)."""
        if isinstance(specifier, int):
            for service in self._services:
                if service.handle == specifier:
                    return service
            return None
        uuid = normalize_uuid_str(str(specifier))
        for service in self._services:
            if service.uuid == uuid:
                return service
        return None

    def get_characteristic(self, specifier) -> Optional[BleakGATTCharacteristic]:
        """Get a characteristic by handle (int) or UUID (str/UUID, short or long)."""
        if isinstance(specifier, int):
            return self._by_char_handle.get(specifier)
        return self._by_char_uuid.get(normalize_uuid_str(str(specifier)))

    def get_descriptor(self, handle: int) -> Optional[BleakGATTDescriptor]:
        return self._by_desc_handle.get(handle)
