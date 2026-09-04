"""connect(timeout=...) is a budget for the CALL, not for each candidate address.

With no scan result the peer's address type is unknown, so connect() tries PUBLIC then RANDOM.
Giving each candidate the full timeout made a connect to an absent peer cost 2x what the caller
asked for. The controller can initiate exactly one link at a time, so that overrun is paid by
every other peer the caller is trying to reach: measured 2026-09-04, timeout=20 spent 40 s in
the initiator and blocked a live board's reconnect for the whole of it.
"""

import asyncio
import time

import pytest

import bumble_bleak as bleak
from bumble_bleak import _backend


class _FakeDevice:
    """Records the timeout each candidate is given and burns `spend` of it before failing.

    `spend=None` means "burn the whole slice", as an absent peer does. A small `spend` models
    the peer that rejects the wrong address type immediately -- the case that distinguishes a
    recomputed deadline from a share precomputed once.
    """

    def __init__(self, spend=None):
        self.timeouts = []
        self._spend = spend

    async def connect(self, peer_address, own_address_type=None, timeout=None):
        self.timeouts.append(timeout)
        await asyncio.sleep(timeout if self._spend is None else min(self._spend, timeout))
        raise asyncio.TimeoutError("no such peer")


class _FakeBackend:
    def __init__(self, device):
        self._device = device

    async def acquire(self):
        return self._device

    async def release(self):
        pass


def _install(monkeypatch, device):
    backend = _FakeBackend(device)

    async def get_backend(_adapter):
        return backend

    monkeypatch.setattr(_backend, "get_backend", get_backend)
    return device


@pytest.fixture
def fake_backend(monkeypatch):
    return _install(monkeypatch, _FakeDevice())


async def test_budget_is_total_not_per_candidate(fake_backend):
    """Two candidates, one budget. The sum is what the caller asked for, not a multiple."""
    client = bleak.BleakClient("34:85:18:98:15:89")
    t0 = time.monotonic()
    with pytest.raises(bleak.BleakError):
        await client.connect(timeout=0.4)
    elapsed = time.monotonic() - t0

    assert len(fake_backend.timeouts) == 2, "both address types should still be tried"
    assert sum(fake_backend.timeouts) == pytest.approx(0.4, abs=0.01)
    # The whole point: the call does not overrun the caller's budget.
    assert elapsed < 0.4 * 1.6, f"connect overran its budget: {elapsed:.2f}s for 0.4s"


async def test_every_candidate_gets_a_share(fake_backend):
    """Splitting must not starve the later candidate -- a peer whose address type is RANDOM
    is reached only by the second attempt, and a zero-length attempt would never find it."""
    client = bleak.BleakClient("34:85:18:98:15:89")
    with pytest.raises(bleak.BleakError):
        await client.connect(timeout=0.4)
    assert all(t > 0 for t in fake_backend.timeouts)


async def test_known_address_type_spends_the_whole_budget(fake_backend):
    """A peer seen by the scanner has one candidate, so it must get the full timeout -- the
    split must not quietly halve the budget for the common case."""
    from bumble.hci import Address, AddressType

    device = bleak.BLEDevice(
        "34:85:18:98:15:89", "ftr", None, -40,
        Address("34:85:18:98:15:89", AddressType.PUBLIC_DEVICE))
    client = bleak.BleakClient(device)
    assert client._peer_address is not None, "a scanned device must carry its address type"
    with pytest.raises(bleak.BleakError):
        await client.connect(timeout=0.2)
    assert fake_backend.timeouts == [pytest.approx(0.2, abs=0.01)]


async def test_early_failure_hands_its_remainder_to_the_next_candidate(monkeypatch):
    """The share must be recomputed from the live deadline, not precomputed once.

    A peer of the RANDOM type rejects the PUBLIC candidate almost immediately. Whatever that
    candidate did not spend belongs to the next one -- otherwise the fix for the 2x overrun
    quietly becomes a 2x UNDERRUN for every real peer, halving the time available to reach it.
    A `timeout / len(candidates)` share computed once looks identical in every other test here
    and fails only this one.
    """
    device = _install(monkeypatch, _FakeDevice(spend=0.01))
    client = bleak.BleakClient("34:85:18:98:15:89")
    t0 = time.monotonic()
    with pytest.raises(bleak.BleakError):
        await client.connect(timeout=0.4)
    elapsed = time.monotonic() - t0

    assert len(device.timeouts) == 2
    assert device.timeouts[0] == pytest.approx(0.2, abs=0.01), "first candidate gets half"
    # Not 0.2: the first candidate spent 0.01 of its 0.2, so ~0.39 is still on the clock and
    # the last candidate is entitled to all of it.
    assert device.timeouts[1] > 0.3, (
        f"unspent budget was dropped, not carried: {device.timeouts}")
    # Carrying the remainder forward must still respect the deadline -- the grant is larger,
    # the wall clock is not.
    assert elapsed < 0.4 * 1.6, f"connect overran its budget: {elapsed:.2f}s for 0.4s"
