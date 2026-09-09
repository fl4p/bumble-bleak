"""Minimal `bleak_retry_connector` shim backed by bumble_bleak.

Covers the surface aiobmsble imports: ``BLEAK_TIMEOUT``,
``MAX_CONNECT_ATTEMPTS``, ``establish_connection`` and
``close_stale_connections`` (aiobmsble>=0.25 imports all four; a missing symbol
here surfaces as a cryptic ``ImportError: cannot import name
'MAX_CONNECT_ATTEMPTS'`` that makes every aiobmsble BMS look like an "Unknown
device type" -- see batmon-ha #385, #407).
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Optional

from bumble_bleak import BLEDevice, BleakClient

BLEAK_TIMEOUT: float = 20.0

# Default connection attempts. aiobmsble uses this to size its hard connect
# timeout (``MAX_CONNECT_ATTEMPTS * BLEAK_TIMEOUT + 1``); keep it in step with
# ``establish_connection``'s ``max_attempts`` default below.
MAX_CONNECT_ATTEMPTS: int = 4


class BleakNotFoundError(Exception):
    """Compatibility alias used by some callers (e.g. batmon-ha)."""


async def close_stale_connections(
    device: BLEDevice, only_other_adapters: bool = False, **kwargs: Any
) -> None:
    """No-op: there is no shared connection state to clean up.

    Real ``bleak_retry_connector`` drops stale BlueZ *D-Bus* connections that a
    crashed client left behind, because bluetoothd keeps them alive beyond the
    process that opened them. bumble-bleak owns its controller exclusively via
    an HCI User Channel, so every link it holds dies with this process and no
    other host stack can be holding one on this adapter. Nothing to close.
    """
    return None


async def establish_connection(
    client_class: type,
    device: BLEDevice,
    name: str,
    disconnected_callback: Optional[Callable[[Any], None]] = None,
    max_attempts: int = MAX_CONNECT_ATTEMPTS,
    **kwargs: Any,
) -> BleakClient:
    """Create a client of ``client_class`` for ``device`` and connect, with retries.

    Extra kwargs (e.g. ``services=``, ``cached_services=``) are accepted and
    ignored — bumble-bleak discovers services on connect.
    """
    last_exc: Optional[BaseException] = None
    for attempt in range(max_attempts):
        client = client_class(device, disconnected_callback=disconnected_callback)
        try:
            await client.connect()
            return client
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            try:
                await client.disconnect()
            except Exception:
                pass
            await asyncio.sleep(0.25 * (attempt + 1))
    raise last_exc if last_exc else RuntimeError(f"could not connect to {name}")
