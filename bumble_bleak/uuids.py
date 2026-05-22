"""UUID helpers, bleak-compatible.

bleak represents every characteristic/service UUID as the canonical lowercase
128-bit string (e.g. ``0000180a-0000-1000-8000-00805f9b34fb``). Bumble's
``core.UUID`` renders short forms (``UUID-16:180A``) and an undashed hex string,
so we convert here.
"""

from __future__ import annotations

import uuid as _uuid

_BASE_SUFFIX = "-0000-1000-8000-00805f9b34fb"


def normalize_uuid_str(uuid: str) -> str:
    """Return the canonical lowercase 128-bit string for a 16/32/128-bit UUID.

    Mirrors ``bleak.uuids.normalize_uuid_str``.
    """
    s = uuid.strip().lower()
    if len(s) == 4:  # 16-bit
        s = f"0000{s}{_BASE_SUFFIX}"
    elif len(s) == 8:  # 32-bit
        s = f"{s}{_BASE_SUFFIX}"
    elif len(s) == 32:  # 128-bit without dashes
        return str(_uuid.UUID(s))
    return str(_uuid.UUID(s))


def normalize_uuid_16(value: int) -> str:
    return normalize_uuid_str(f"{value:04x}")


def bumble_uuid_to_str(bumble_uuid) -> str:
    """Convert a Bumble ``core.UUID`` to a bleak-style canonical UUID string."""
    return normalize_uuid_str(bumble_uuid.to_hex_str())
