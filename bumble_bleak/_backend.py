"""Per-adapter Bumble backend, shared (ref-counted) by scanners and clients.

bleak presents every ``BleakClient``/``BleakScanner`` as independent, but a
single Bumble ``Device`` owns the HCI transport exclusively and performs *both*
scanning and all connections. So we keep one backend per adapter, reference
counted across the facade objects that use it.
"""

from __future__ import annotations

import asyncio
import fcntl
import os
import re
import socket
import struct
import sys
from typing import Callable, Dict, List, Optional

from .exc import BleakError

_MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")
_BT_SYSFS = "/sys/class/bluetooth"
_HCIGETDEVINFO = 0x800448D3  # _IOR('H', 211, int)


def _hci_addr_for_index(dev_id: int) -> Optional[str]:
    """Return the lowercase MAC of controller ``dev_id`` via the HCIGETDEVINFO
    ioctl, or None if it doesn't exist / can't be queried.

    Uses the ioctl rather than sysfs because ``/sys/class/bluetooth/hciN/address``
    isn't present on all kernels. Needs CAP_NET_RAW to open the HCI socket.
    """
    _ensure_bluetooth_socket_constants()
    try:
        sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_RAW, socket.BTPROTO_HCI)
    except OSError:
        return None
    try:
        buf = bytearray(96)  # struct hci_dev_info (~90 bytes); we read bdaddr@10
        struct.pack_into("H", buf, 0, dev_id)
        try:
            fcntl.ioctl(sock.fileno(), _HCIGETDEVINFO, buf)
        except OSError:
            return None
        return ":".join("%02X" % b for b in reversed(buf[10:16])).lower()
    finally:
        sock.close()


def _candidate_hci_indices() -> List[int]:
    """Controller indices to probe: those listed in sysfs (handles re-enumerated
    high indices) plus a small fallback range."""
    indices = set(range(8))
    try:
        for name in os.listdir(_BT_SYSFS):
            if name.startswith("hci") and ":" not in name:  # skip connection nodes
                try:
                    indices.add(int(name[3:]))
                except ValueError:
                    pass
    except OSError:
        pass
    return sorted(indices)


def _hci_index_for_mac(mac: str) -> Optional[int]:
    """Resolve a controller MAC (e.g. ``2C:CF:67:5F:4A:6D``) to its current hci
    index.

    The kernel hci index can change across USB re-enumeration; selecting by MAC
    re-resolves it on every acquire so config survives the index moving.
    """
    target = mac.lower()
    for dev_id in _candidate_hci_indices():
        if _hci_addr_for_index(dev_id) == target:
            return dev_id
    return None


def _ensure_bluetooth_socket_constants() -> None:
    """Inject Linux Bluetooth socket constants if the runtime lacks them.

    python-build-standalone CPython (used by uv/pyenv) is built without
    ``socket.AF_BLUETOOTH``/``BTPROTO_HCI`` even though the kernel supports
    them. These are fixed Linux ABI values, so we add them when missing so
    Bumble's HCI-socket transport works on such interpreters.
    """
    if not sys.platform.startswith("linux"):
        return
    if not hasattr(socket, "AF_BLUETOOTH"):
        socket.AF_BLUETOOTH = 31  # type: ignore[attr-defined]
    if not hasattr(socket, "BTPROTO_HCI"):
        socket.BTPROTO_HCI = 1  # type: ignore[attr-defined]
    if not hasattr(socket, "SOCK_NONBLOCK"):
        socket.SOCK_NONBLOCK = 0o4000  # type: ignore[attr-defined]

from bumble.device import Device
from bumble.hci import Address, AddressType, OwnAddressType
from bumble.transport import open_transport

# We act as an LE central with a static random identity address.
OWN_ADDRESS_TYPE = OwnAddressType.RANDOM


def _transport_spec(adapter: Optional[str]) -> str:
    """Map a bleak-style adapter name to a Bumble transport spec.

    * ``None``        -> ``$BUMBLE_BLEAK_TRANSPORT`` or ``hci-socket:0``
    * a controller MAC (``2C:CF:67:5F:4A:6D``) -> resolved to its current
      ``hci-socket:N`` via sysfs (robust to the index changing)
    * ``hci0``/``hciN`` -> ``hci-socket:N``
    * anything else containing ``:`` is treated as a literal Bumble transport
      spec (handy for tests, e.g. ``android-netsim`` or a virtual link).
    """
    if adapter is None:
        return os.environ.get("BUMBLE_BLEAK_TRANSPORT", "hci-socket:0")
    if _MAC_RE.match(adapter):
        index = _hci_index_for_mac(adapter)
        if index is None:
            raise BleakError(
                f"No Bluetooth adapter with address {adapter} found in {_BT_SYSFS}"
            )
        return f"hci-socket:{index}"
    if ":" in adapter:
        return adapter
    if adapter.startswith("hci"):
        return f"hci-socket:{adapter[3:]}"
    return adapter


def _hci_index_from_spec(spec: str) -> Optional[int]:
    """Return the controller index for an ``hci-socket:N`` spec, else None."""
    if spec.startswith("hci-socket:"):
        suffix = spec.split(":", 1)[1]
        if suffix.isdigit():
            return int(suffix)
        return 0  # default controller
    return None


def _bring_adapter_down(index: int) -> None:
    """Detach the controller from the kernel/BlueZ so we can take a User Channel.

    Binding ``HCI_CHANNEL_USER`` requires the device to be DOWN. We issue the
    ``HCIDEVDOWN`` ioctl directly so callers don't need ``hciconfig``/bluetoothd.
    Best-effort: needs CAP_NET_ADMIN; a no-op if already down or not permitted
    (open_transport will then surface a clear error). Set
    ``BUMBLE_BLEAK_NO_ADAPTER_DOWN=1`` to skip (e.g. bluetoothd already stopped).
    """
    if not sys.platform.startswith("linux"):
        return
    if os.environ.get("BUMBLE_BLEAK_NO_ADAPTER_DOWN"):
        return
    HCIDEVDOWN = 0x400448CA  # _IOW('H', 202, int)
    try:
        sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_RAW, socket.BTPROTO_HCI)
    except OSError:
        return
    try:
        fcntl.ioctl(sock.fileno(), HCIDEVDOWN, index)
    except OSError:
        pass  # already down, or lacking CAP_NET_ADMIN
    finally:
        sock.close()


# Test hook: map an adapter name to a pre-built, already-powered Bumble Device
# (wired to an in-process virtual controller). When present, the backend uses it
# instead of opening an HCI transport, and never powers it off.
_TEST_DEVICES: Dict[Optional[str], Device] = {}


def _random_static_address() -> Address:
    raw = bytearray(os.urandom(6))
    raw[0] |= 0xC0  # top two bits set => static random address
    mac = ":".join(f"{b:02X}" for b in raw)
    return Address(mac, AddressType.RANDOM_DEVICE)


class _Backend:
    def __init__(self, adapter: Optional[str]):
        self.adapter = adapter
        self.device: Optional[Device] = None
        self._transport = None
        self._users = 0
        self._scanners = 0
        self._lock = asyncio.Lock()
        self._adv_handlers: set[Callable] = set()
        self._owns_device = True

    # -- lifecycle ---------------------------------------------------------
    async def acquire(self) -> Device:
        async with self._lock:
            if self.device is None:
                if self.adapter in _TEST_DEVICES:
                    self.device = _TEST_DEVICES[self.adapter]
                    self._owns_device = False
                else:
                    _ensure_bluetooth_socket_constants()
                    spec = _transport_spec(self.adapter)
                    index = _hci_index_from_spec(spec)
                    if index is not None:
                        _bring_adapter_down(index)
                    self._transport = await open_transport(spec)
                    self.device = Device.with_hci(
                        "bumble-bleak",
                        _random_static_address(),
                        self._transport.source,
                        self._transport.sink,
                    )
                    self._owns_device = True
                self.device.on("advertisement", self._dispatch_advertisement)
                if self._owns_device:
                    await self.device.power_on()
            self._users += 1
            return self.device

    async def release(self) -> None:
        async with self._lock:
            self._users -= 1
            if self._users > 0:
                return
            self._users = 0
            self._scanners = 0
            self._adv_handlers.clear()
            device, transport = self.device, self._transport
            owns = self._owns_device
            if device is not None:
                try:
                    device.remove_listener("advertisement", self._dispatch_advertisement)
                except Exception:
                    pass
            self.device, self._transport = None, None
        if device is not None and owns:
            try:
                await device.power_off()
            except Exception:
                pass
        if transport is not None:
            try:
                await transport.close()
            except Exception:
                pass

    # -- scanning ----------------------------------------------------------
    def add_advertisement_handler(self, handler: Callable) -> None:
        self._adv_handlers.add(handler)

    def remove_advertisement_handler(self, handler: Callable) -> None:
        self._adv_handlers.discard(handler)

    def _dispatch_advertisement(self, advertisement) -> None:
        for handler in list(self._adv_handlers):
            handler(advertisement)

    async def start_scanning(self) -> None:
        async with self._lock:
            self._scanners += 1
            start = self._scanners == 1
        if start:
            await self.device.start_scanning(own_address_type=OWN_ADDRESS_TYPE)

    async def stop_scanning(self) -> None:
        async with self._lock:
            self._scanners = max(0, self._scanners - 1)
            stop = self._scanners == 0
        if stop and self.device is not None:
            await self.device.stop_scanning()


_backends: Dict[Optional[str], _Backend] = {}
_registry_lock = asyncio.Lock()


async def get_backend(adapter: Optional[str]) -> _Backend:
    async with _registry_lock:
        backend = _backends.get(adapter)
        if backend is None:
            backend = _Backend(adapter)
            _backends[adapter] = backend
        return backend
