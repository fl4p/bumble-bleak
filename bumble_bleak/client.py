"""bleak-compatible ``BleakClient`` backed by a Bumble central connection."""

from __future__ import annotations

import asyncio
from typing import Callable, Optional, Union

from bumble.device import Peer
from bumble.hci import Address, AddressType

from . import _backend
from .characteristic import BleakGATTCharacteristic, BleakGATTService, BleakGATTServiceCollection
from .device import BLEDevice
from .exc import BleakCharacteristicNotFoundError, BleakError
from .uuids import normalize_uuid_str

CharSpec = Union[str, int, BleakGATTCharacteristic]


class BleakClient:
    def __init__(
        self,
        address_or_device: Union[str, BLEDevice],
        disconnected_callback: Optional[Callable[["BleakClient"], None]] = None,
        adapter: Optional[str] = None,
        handle_pairing: bool = False,
        **kwargs,
    ):
        if isinstance(address_or_device, BLEDevice):
            self.address = address_or_device.address
            self._peer_address = address_or_device._bumble_address
        else:
            self.address = address_or_device
            self._peer_address = None

        self._adapter = adapter
        self._disconnected_callback = disconnected_callback
        self._handle_pairing = handle_pairing

        self._backend = None
        self._connection = None
        self._peer: Optional[Peer] = None
        self._services = BleakGATTServiceCollection([])
        self._connected = False
        self._subscriptions = {}  # char handle -> bumble subscriber callable

    # -- connection lifecycle ---------------------------------------------
    @property
    def is_connected(self) -> bool:
        return self._connected

    def _candidate_addresses(self):
        if self._peer_address is not None:
            return [self._peer_address]
        # Unknown peer address type (no scan result): try public, then random.
        return [
            Address(self.address, AddressType.PUBLIC_DEVICE),
            Address(self.address, AddressType.RANDOM_DEVICE),
        ]

    async def connect(self, timeout: float = 10.0, **kwargs) -> bool:
        self._backend = await _backend.get_backend(self._adapter)
        device = await self._backend.acquire()
        try:
            last_exc = None
            for peer_address in self._candidate_addresses():
                try:
                    self._connection = await device.connect(
                        peer_address,
                        own_address_type=_backend.OWN_ADDRESS_TYPE,
                        timeout=timeout,
                    )
                    break
                except Exception as e:  # noqa: BLE001 - try next address type
                    last_exc = e
            if self._connection is None:
                raise BleakError(f"Could not connect to {self.address}: {last_exc}") from last_exc

            self._connection.on("disconnection", self._on_disconnection)
            self._connected = True
            await self._discover_services()
            return True
        except BaseException:
            await self._backend.release()
            self._backend = None
            self._connection = None
            self._connected = False
            raise

    async def disconnect(self) -> bool:
        if self._connection is not None:
            try:
                await self._connection.disconnect()
            except Exception:
                pass
        await self._teardown()
        return True

    def _on_disconnection(self, _reason) -> None:
        was_connected = self._connected
        self._connected = False
        if was_connected and self._disconnected_callback is not None:
            self._disconnected_callback(self)

    async def _teardown(self) -> None:
        self._connected = False
        self._connection = None
        self._peer = None
        self._subscriptions.clear()
        self._services = BleakGATTServiceCollection([])
        if self._backend is not None:
            await self._backend.release()
            self._backend = None

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, *args):
        await self.disconnect()

    # -- GATT services -----------------------------------------------------
    async def _discover_services(self) -> None:
        self._peer = Peer(self._connection)
        await self._peer.discover_services()
        for service in self._peer.services:
            await service.discover_characteristics()
            for characteristic in service.characteristics:
                await characteristic.discover_descriptors()
        self._services = BleakGATTServiceCollection(
            [BleakGATTService(s) for s in self._peer.services]
        )

    @property
    def services(self) -> BleakGATTServiceCollection:
        return self._services

    async def get_services(self) -> BleakGATTServiceCollection:
        if not self._services and self._connection is not None:
            await self._discover_services()
        return self._services

    # -- characteristic access --------------------------------------------
    def _resolve_char(self, spec: CharSpec) -> BleakGATTCharacteristic:
        if isinstance(spec, BleakGATTCharacteristic):
            return spec
        if isinstance(spec, int):
            char = self._services.get_characteristic(spec)
        else:
            char = self._services.get_characteristic(normalize_uuid_str(spec))
        if char is None:
            raise BleakCharacteristicNotFoundError(spec)
        return char

    async def read_gatt_char(self, char_specifier: CharSpec) -> bytearray:
        char = self._resolve_char(char_specifier)
        return bytearray(await char.obj.read_value())

    async def write_gatt_char(self, char_specifier: CharSpec, data, response: bool = False) -> None:
        char = self._resolve_char(char_specifier)
        await char.obj.write_value(bytes(data), with_response=response)

    async def read_gatt_descriptor(self, handle: int) -> bytearray:
        if self._peer is None:
            raise BleakError("Service discovery has not been performed yet")
        return bytearray(await self._peer.read_value(handle))

    async def start_notify(
        self, char_specifier: CharSpec, callback: Callable, **kwargs
    ) -> None:
        char = self._resolve_char(char_specifier)

        def subscriber(data, _char=char):
            callback(_char, bytearray(data))

        await char.obj.subscribe(subscriber)
        self._subscriptions[char.handle] = subscriber

    async def stop_notify(self, char_specifier: CharSpec) -> None:
        char = self._resolve_char(char_specifier)
        subscriber = self._subscriptions.pop(char.handle, None)
        await char.obj.unsubscribe(subscriber)

    # -- pairing (SMP) -----------------------------------------------------
    async def pair(self, callback: Optional[Callable] = None, **kwargs) -> bool:
        """Pair/bond with the connected peer via SMP.

        ``callback`` follows bleak's shape ``callback(device, pin, passkey)``:
        return a ``str`` to enter a passkey/PIN, or a truthy value to accept a
        displayed one. With no callback, "Just Works" pairing is attempted.
        """
        if self._connection is None:
            raise BleakError("not connected")

        from bumble.pairing import PairingConfig

        from .pairing import BleakPairingDelegate

        delegate = BleakPairingDelegate(callback, self.address)
        device = self._connection.device
        previous_factory = device.pairing_config_factory
        device.pairing_config_factory = lambda _conn: PairingConfig(
            sc=True,
            mitm=callback is not None,
            bonding=True,
            delegate=delegate,
        )
        try:
            await self._connection.pair()
            return True
        except Exception as exc:  # noqa: BLE001
            raise BleakError(f"pairing failed: {exc}") from exc
        finally:
            device.pairing_config_factory = previous_factory

    async def unpair(self) -> bool:
        raise BleakError("unpair is not implemented in bumble-bleak")
