"""Tests for ``BleakScanner(service_uuids=...)`` filtering.

No hardware: advertisements are real ``bumble.device.Advertisement`` objects
built from real ``AdvertisingData``, fed straight into the scanner's
``_on_advertisement`` handler (the same callable ``_backend`` dispatches to).
"""

import pytest

from bumble.core import AdvertisingData
from bumble.hci import Address

from bumble_bleak.scanner import BleakScanner

CUSTOM = "e8308d3d-c3b4-45ff-ba58-9c0fb99d0ecb"
OTHER = "12345678-1234-5678-1234-56789abcdef0"
FFE0_16 = "ffe0"
FFE0_128 = "0000ffe0-0000-1000-8000-00805f9b34fb"


def make_adv(address="AA:BB:CC:DD:EE:FF", name="Dev", ad_items=(), rssi=-55):
    ad = AdvertisingData(
        [(AdvertisingData.COMPLETE_LOCAL_NAME, name.encode())] + list(ad_items)
    )
    from bumble.device import Advertisement

    return Advertisement(
        address=Address(address, Address.RANDOM_DEVICE_ADDRESS),
        rssi=rssi,
        data_bytes=bytes(ad),
    )


def uuid128_le(uuid_str):
    from bumble.core import UUID

    return bytes(UUID(uuid_str))


def uuid16_le(hex4):
    return bytes.fromhex(hex4)[::-1]


def collect(scanner):
    seen = []
    scanner._detection_callback = lambda d, a: seen.append((d, a))
    return seen


# --- basic accept / reject -------------------------------------------------


def test_matching_advertisement_reaches_callback():
    s = BleakScanner(service_uuids=[CUSTOM])
    seen = collect(s)
    s._on_advertisement(
        make_adv(
            ad_items=[
                (
                    AdvertisingData.COMPLETE_LIST_OF_128_BIT_SERVICE_CLASS_UUIDS,
                    uuid128_le(CUSTOM),
                )
            ]
        )
    )
    assert len(seen) == 1
    assert CUSTOM in seen[0][1].service_uuids


def test_non_matching_advertisement_is_dropped():
    """The regression that is otherwise silent: wrong device reaches the callback."""
    s = BleakScanner(service_uuids=[CUSTOM])
    seen = collect(s)
    s._on_advertisement(
        make_adv(
            ad_items=[
                (
                    AdvertisingData.COMPLETE_LIST_OF_128_BIT_SERVICE_CLASS_UUIDS,
                    uuid128_le(OTHER),
                )
            ]
        )
    )
    assert seen == []
    assert s.discovered_devices == []


@pytest.mark.parametrize("filt", [None, []])
def test_no_filter_passes_everything(filt):
    s = BleakScanner(service_uuids=filt)
    seen = collect(s)
    s._on_advertisement(make_adv())  # no service UUIDs at all
    s._on_advertisement(
        make_adv(
            address="11:22:33:44:55:66",
            ad_items=[
                (
                    AdvertisingData.COMPLETE_LIST_OF_128_BIT_SERVICE_CLASS_UUIDS,
                    uuid128_le(OTHER),
                )
            ],
        )
    )
    assert len(seen) == 2
    assert len(s.discovered_devices) == 2


# --- normalization ---------------------------------------------------------


def test_16_bit_filter_matches_128_bit_advertisement():
    s = BleakScanner(service_uuids=[FFE0_16])
    seen = collect(s)
    s._on_advertisement(
        make_adv(
            ad_items=[
                (
                    AdvertisingData.COMPLETE_LIST_OF_128_BIT_SERVICE_CLASS_UUIDS,
                    uuid128_le(FFE0_128),
                )
            ]
        )
    )
    assert len(seen) == 1


def test_128_bit_filter_matches_16_bit_advertisement():
    s = BleakScanner(service_uuids=[FFE0_128])
    seen = collect(s)
    s._on_advertisement(
        make_adv(
            ad_items=[
                (
                    AdvertisingData.COMPLETE_LIST_OF_16_BIT_SERVICE_CLASS_UUIDS,
                    uuid16_le(FFE0_16),
                )
            ]
        )
    )
    assert len(seen) == 1


def test_case_insensitive_match():
    s = BleakScanner(service_uuids=[CUSTOM.upper()])
    seen = collect(s)
    s._on_advertisement(
        make_adv(
            ad_items=[
                (
                    AdvertisingData.COMPLETE_LIST_OF_128_BIT_SERVICE_CLASS_UUIDS,
                    uuid128_le(CUSTOM),
                )
            ]
        )
    )
    assert len(seen) == 1


@pytest.mark.parametrize(
    "ad_type",
    [
        AdvertisingData.COMPLETE_LIST_OF_16_BIT_SERVICE_CLASS_UUIDS,
        AdvertisingData.INCOMPLETE_LIST_OF_16_BIT_SERVICE_CLASS_UUIDS,
    ],
)
def test_incomplete_and_complete_16_bit_lists_both_match(ad_type):
    s = BleakScanner(service_uuids=[FFE0_16])
    seen = collect(s)
    s._on_advertisement(make_adv(ad_items=[(ad_type, uuid16_le(FFE0_16))]))
    assert len(seen) == 1


@pytest.mark.parametrize(
    "ad_type",
    [
        AdvertisingData.COMPLETE_LIST_OF_128_BIT_SERVICE_CLASS_UUIDS,
        AdvertisingData.INCOMPLETE_LIST_OF_128_BIT_SERVICE_CLASS_UUIDS,
    ],
)
def test_incomplete_and_complete_128_bit_lists_both_match(ad_type):
    s = BleakScanner(service_uuids=[CUSTOM])
    seen = collect(s)
    s._on_advertisement(make_adv(ad_items=[(ad_type, uuid128_le(CUSTOM))]))
    assert len(seen) == 1


@pytest.mark.parametrize(
    "ad_type",
    [
        AdvertisingData.COMPLETE_LIST_OF_32_BIT_SERVICE_CLASS_UUIDS,
        AdvertisingData.INCOMPLETE_LIST_OF_32_BIT_SERVICE_CLASS_UUIDS,
    ],
)
def test_32_bit_lists_match(ad_type):
    uuid32 = "12345678"
    s = BleakScanner(service_uuids=[uuid32])
    seen = collect(s)
    s._on_advertisement(
        make_adv(ad_items=[(ad_type, bytes.fromhex(uuid32)[::-1])])
    )
    assert len(seen) == 1


def test_one_of_several_advertised_uuids_matches():
    s = BleakScanner(service_uuids=[CUSTOM])
    seen = collect(s)
    s._on_advertisement(
        make_adv(
            ad_items=[
                (
                    AdvertisingData.COMPLETE_LIST_OF_16_BIT_SERVICE_CLASS_UUIDS,
                    uuid16_le("180a"),
                ),
                (
                    AdvertisingData.COMPLETE_LIST_OF_128_BIT_SERVICE_CLASS_UUIDS,
                    uuid128_le(CUSTOM),
                ),
            ]
        )
    )
    assert len(seen) == 1


# --- discovered_devices mirrors the callback -------------------------------


def test_discovered_devices_is_filtered_like_the_callback():
    s = BleakScanner(service_uuids=[CUSTOM])
    seen = collect(s)
    s._on_advertisement(
        make_adv(
            address="AA:AA:AA:AA:AA:AA",
            ad_items=[
                (
                    AdvertisingData.COMPLETE_LIST_OF_128_BIT_SERVICE_CLASS_UUIDS,
                    uuid128_le(CUSTOM),
                )
            ],
        )
    )
    s._on_advertisement(
        make_adv(
            address="BB:BB:BB:BB:BB:BB",
            ad_items=[
                (
                    AdvertisingData.COMPLETE_LIST_OF_128_BIT_SERVICE_CLASS_UUIDS,
                    uuid128_le(OTHER),
                )
            ],
        )
    )
    addresses = [d.address for d in s.discovered_devices]
    assert addresses == ["AA:AA:AA:AA:AA:AA"]
    assert set(s.discovered_devices_and_advertisement_data) == {"AA:AA:AA:AA:AA:AA"}
    assert [d.address for d, _ in seen] == ["AA:AA:AA:AA:AA:AA"]


# --- do not fail open, do not crash ----------------------------------------


def test_advertisement_without_uuids_is_dropped_when_filter_active():
    s = BleakScanner(service_uuids=[CUSTOM])
    seen = collect(s)
    s._on_advertisement(make_adv())  # name only, no service UUID AD types
    assert seen == []
    assert s.discovered_devices == []


def test_unreadable_uuid_list_does_not_match():
    """``None``/garbage service_uuids must not be treated as a match."""
    s = BleakScanner(service_uuids=[CUSTOM])
    assert s.is_allowed_uuid(None) is False
    assert s.is_allowed_uuid([]) is False
    assert s.is_allowed_uuid([object()]) is False
    assert s.is_allowed_uuid(["not-a-uuid"]) is False
    assert s.is_allowed_uuid(CUSTOM) is False  # bare str, not a list


def test_malformed_advertisement_does_not_raise():
    class Broken:
        @property
        def address(self):
            raise RuntimeError("malformed advertisement")

        data = None
        rssi = -50

    for filt in (None, [CUSTOM]):
        s = BleakScanner(service_uuids=filt)
        seen = collect(s)
        s._on_advertisement(Broken())  # must not propagate
        assert seen == []
        assert s.discovered_devices == []


def test_invalid_filter_uuid_raises_at_construction():
    with pytest.raises(ValueError):
        BleakScanner(service_uuids=["nonsense"])


def test_detection_callback_positional_still_works():
    seen = []
    s = BleakScanner(lambda d, a: seen.append(d), [CUSTOM])
    s._on_advertisement(
        make_adv(
            ad_items=[
                (
                    AdvertisingData.COMPLETE_LIST_OF_128_BIT_SERVICE_CLASS_UUIDS,
                    uuid128_le(CUSTOM),
                )
            ]
        )
    )
    assert len(seen) == 1
