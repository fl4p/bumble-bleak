"""Unit tests for the bleak->bumble pairing-callback adaptation."""

import pytest

from bumble.pairing import PairingDelegate

from bumble_bleak.pairing import BleakPairingDelegate


def test_no_callback_is_just_works():
    d = BleakPairingDelegate(None, "AA:BB:CC:DD:EE:FF")
    assert d.io_capability == PairingDelegate.NO_OUTPUT_NO_INPUT


def test_callback_selects_keyboard_input():
    d = BleakPairingDelegate(lambda *_: True, "AA:BB:CC:DD:EE:FF")
    assert d.io_capability == PairingDelegate.KEYBOARD_INPUT_ONLY


async def test_get_number_returns_entered_passkey():
    calls = []

    def cb(device, pin, passkey):
        calls.append((device, pin, passkey))
        return "123456"

    d = BleakPairingDelegate(cb, "AA:BB:CC:DD:EE:FF")
    assert await d.get_number() == 123456
    assert calls[0][0] == "AA:BB:CC:DD:EE:FF"


async def test_compare_numbers_uses_callback_decision():
    d_yes = BleakPairingDelegate(lambda device, pin, passkey: True, "x")
    d_no = BleakPairingDelegate(lambda device, pin, passkey: False, "x")
    assert await d_yes.compare_numbers(424242, 6) is True
    assert await d_no.compare_numbers(424242, 6) is False


async def test_get_string_pin_entry():
    d = BleakPairingDelegate(lambda *_: "0000", "x")
    assert await d.get_string(16) == "0000"


async def test_accept_defaults_true_without_callback():
    d = BleakPairingDelegate(None, "x")
    assert await d.accept() is True
