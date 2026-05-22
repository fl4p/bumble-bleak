"""SMP pairing support, mapping bleak's ``pair(callback=...)`` to Bumble.

bleak's callback has the shape ``callback(device, pin, passkey) -> bool | str``
(as used by batmon-ha): return ``str`` to *enter* a passkey/PIN the peripheral
expects, or a truthy value to *accept/confirm* a displayed value.
"""

from __future__ import annotations

from typing import Callable, Optional

from bumble.pairing import PairingDelegate


class BleakPairingDelegate(PairingDelegate):
    """Adapts a bleak-style passkey callback to a Bumble PairingDelegate."""

    def __init__(self, callback: Optional[Callable], address: str):
        # KEYBOARD_INPUT_ONLY => we can enter a passkey the peripheral displays;
        # NO_OUTPUT_NO_INPUT  => "Just Works" when no callback is supplied.
        super().__init__(
            io_capability=PairingDelegate.KEYBOARD_INPUT_ONLY
            if callback is not None
            else PairingDelegate.NO_OUTPUT_NO_INPUT
        )
        self._callback = callback
        self._address = address

    def _invoke(self, pin=None, passkey=None):
        if self._callback is None:
            return True
        # The batmon-ha callback is callback(device, pin, passkey); be lenient
        # toward simpler arities too.
        for args in ((self._address, pin, passkey), (self._address,), ()):
            try:
                return self._callback(*args)
            except TypeError:
                continue
        return True

    async def accept(self) -> bool:
        result = self._invoke()
        return True if result is None else bool(result)

    async def get_number(self) -> Optional[int]:
        """Peripheral expects us to enter a passkey (the configured PIN/PSK)."""
        result = self._invoke()
        if result in (True, False, None):
            return None
        try:
            return int(result)
        except (TypeError, ValueError):
            return None

    async def get_string(self, max_length: int) -> Optional[str]:
        result = self._invoke()
        if isinstance(result, str):
            return result[:max_length]
        return None

    async def compare_numbers(self, number: int, digits: int) -> bool:
        result = self._invoke(pin=number)
        return True if result is None else bool(result)

    async def display_number(self, number: int, digits: int) -> None:
        self._invoke(passkey=number)
