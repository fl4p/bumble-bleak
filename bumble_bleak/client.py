"""bleak-compatible ``BleakClient`` backed by a Bumble central connection."""

from __future__ import annotations

import asyncio
import logging
from typing import Callable, Optional, Union

from bumble.att import ATT_DEFAULT_MTU
from bumble.device import Peer
from bumble.hci import Address, AddressType

from . import _backend
from .characteristic import BleakGATTCharacteristic, BleakGATTService, BleakGATTServiceCollection
from .device import BLEDevice
from .exc import BleakCharacteristicNotFoundError, BleakError
from .uuids import normalize_uuid_str

logger = logging.getLogger(__name__)

CharSpec = Union[str, int, BleakGATTCharacteristic]

# BlueZ negotiates an ATT MTU on its own, so bleak callers never see the 23-byte
# default. Bumble does not: without an explicit exchange every payload is capped
# at ATT_DEFAULT_MTU-3 = 20 bytes, which silently truncates peripherals that size
# their records to the negotiated MTU. 517 is the largest LE ATT MTU; the peer
# caps it to whatever it supports.
REQUESTED_MTU = 517

# The GATT request timeout is 30s and independent of connect(timeout=...). The MTU
# exchange is now the first PDU on the link, so an unresponsive peer would stall
# every connect for that long; cap it well below.
MTU_EXCHANGE_TIMEOUT = 5.0


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
            self._name = address_or_device.name
            self._peer_address = address_or_device._bumble_address
        else:
            self.address = address_or_device
            self._name = None
            self._peer_address = None

        self._adapter = adapter
        self._disconnected_callback = disconnected_callback
        self._handle_pairing = handle_pairing

        self._backend = None
        self._connection = None
        self._peer: Optional[Peer] = None
        self._services = BleakGATTServiceCollection([])
        self._connected = False
        self._disconnect_reason = None
        self._mtu: Optional[int] = None
        self._subscriptions = {}  # char handle -> bumble subscriber callable

    # -- connection lifecycle ---------------------------------------------
    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def name(self) -> Optional[str]:
        return self._name

    @property
    def mtu_size(self) -> int:
        """Negotiated ATT MTU (bleak API parity).

        Like bleak's BlueZ backend, this reports the default MTU rather than
        raising when the value is unknown.
        """
        if self._mtu is not None:
            return self._mtu
        return ATT_DEFAULT_MTU

    def _candidate_addresses(self):
        if self._peer_address is not None:
            return [self._peer_address]
        # Unknown peer address type (no scan result): try public, then random.
        return [
            Address(self.address, AddressType.PUBLIC_DEVICE),
            Address(self.address, AddressType.RANDOM_DEVICE),
        ]

    async def connect(self, timeout: float = 10.0, **kwargs) -> bool:
        self._disconnect_reason = None
        self._backend = await _backend.get_backend(self._adapter)
        device = await self._backend.acquire()
        deadline = asyncio.get_running_loop().time() + timeout
        try:
            last_exc = None
            candidates = self._candidate_addresses()
            for index, peer_address in enumerate(candidates):
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    last_exc = asyncio.TimeoutError()
                    break
                attempt_timeout = remaining / (len(candidates) - index)
                try:
                    self._connection = await device.connect(
                        peer_address,
                        own_address_type=_backend.OWN_ADDRESS_TYPE,
                        timeout=attempt_timeout,
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
            await self._cleanup_failed_connect()
            raise

    async def _cleanup_failed_connect(self) -> None:
        if self._connection is not None and self._connected:
            try:
                await self._connection.disconnect()
            except (Exception, asyncio.CancelledError):
                pass
        await self._teardown()

    async def disconnect(self) -> bool:
        try:
            if self._connection is not None and self._connected:
                try:
                    await self._connection.disconnect()
                except Exception:
                    pass
        finally:
            await self._teardown()
        return True

    def _on_disconnection(self, reason) -> None:
        was_connected = self._connected
        self._connected = False
        self._disconnect_reason = reason
        # _teardown() is not on this path, so anything a caller can still read
        # has to be invalidated here or it reports the dead link's values.
        self._mtu = None
        if was_connected and self._disconnected_callback is not None:
            self._disconnected_callback(self)

    async def _teardown(self) -> None:
        self._connected = False
        self._connection = None
        self._peer = None
        self._mtu = None
        self._subscriptions.clear()
        self._services = BleakGATTServiceCollection([])
        if self._backend is not None:
            await self._backend.release()
            self._backend = None

    async def _translate_gatt_cancellation(self, awaitable, operation: str):
        try:
            return await awaitable
        except asyncio.CancelledError as exc:
            if self._connected:
                raise
            detail = (
                f" (reason={self._disconnect_reason})"
                if self._disconnect_reason is not None
                else ""
            )
            raise BleakError(
                f"{operation} failed: disconnected from {self.address}{detail}"
            ) from exc

    async def _await_gatt(self, awaitable, operation: str):
        operation_task = asyncio.ensure_future(
            self._translate_gatt_cancellation(awaitable, operation)
        )
        try:
            return await asyncio.shield(operation_task)
        except asyncio.CancelledError:
            # Only parent cancellation reaches this layer: disconnect-caused ATT
            # cancellation is translated inside operation_task. This remains
            # unambiguous when both happen in one loop turn and on Python 3.9,
            # which has no Task.cancelling().
            if not operation_task.done():
                operation_task.cancel()
            try:
                await operation_task
            except (Exception, asyncio.CancelledError):
                pass
            raise

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, *args):
        await self.disconnect()

    # -- GATT services -----------------------------------------------------
    async def _discover_services(self) -> None:
        await self._await_gatt(self._discover_services_impl(), "service discovery")

    async def _discover_services_impl(self) -> None:
        peer = Peer(self._connection)
        self._peer = peer
        # Before discovery, so the larger MTU applies to discovery reads too.
        try:
            self._mtu = await asyncio.wait_for(
                peer.request_mtu(REQUESTED_MTU), MTU_EXCHANGE_TIMEOUT
            )
        except Exception as exc:
            # A peer may reject the exchange, or have initiated one itself; the
            # link still works, so this is not fatal. Read the bearer rather than
            # assuming the default: the exchange may have completed on the wire
            # even though the call did not return it, and under-reporting here
            # would make callers size their writes down.
            self._mtu = getattr(peer.gatt_client, "mtu", ATT_DEFAULT_MTU)
            logger.debug(
                "ATT MTU exchange failed (%s); continuing at MTU %d", exc, self._mtu
            )
        await peer.discover_services()
        for service in peer.services:
            await service.discover_characteristics()
            for characteristic in service.characteristics:
                await characteristic.discover_descriptors()
        self._services = BleakGATTServiceCollection(
            [BleakGATTService(s) for s in peer.services]
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
        return bytearray(
            await self._await_gatt(char.obj.read_value(), "characteristic read")
        )

    async def write_gatt_char(self, char_specifier: CharSpec, data, response: bool = False) -> None:
        char = self._resolve_char(char_specifier)
        await self._await_gatt(
            char.obj.write_value(bytes(data), with_response=response),
            "characteristic write",
        )

    async def read_gatt_descriptor(self, handle: int) -> bytearray:
        if self._peer is None:
            raise BleakError("Service discovery has not been performed yet")
        return bytearray(
            await self._await_gatt(self._peer.read_value(handle), "descriptor read")
        )

    async def start_notify(
        self, char_specifier: CharSpec, callback: Callable, **kwargs
    ) -> None:
        char = self._resolve_char(char_specifier)

        def subscriber(data, _char=char):
            callback(_char, bytearray(data))

        await self._await_gatt(
            char.obj.subscribe(subscriber), "notification subscription"
        )
        self._subscriptions[char.handle] = subscriber

    async def stop_notify(self, char_specifier: CharSpec) -> None:
        char = self._resolve_char(char_specifier)
        subscriber = self._subscriptions.pop(char.handle, None)
        await self._await_gatt(
            char.obj.unsubscribe(subscriber), "notification unsubscription"
        )

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
