"""Hardware-free unit tests for the conversion logic."""

import pytest

from bumble.core import AdvertisingData
from bumble.gatt import Characteristic

import bumble_bleak as bleak
from bumble_bleak.characteristic import _properties_to_strings
from bumble_bleak.device import AdvertisementData
from bumble_bleak.uuids import bumble_uuid_to_str, normalize_uuid_str


def test_normalize_uuid_str_short_and_long():
    assert normalize_uuid_str("180A") == "0000180a-0000-1000-8000-00805f9b34fb"
    assert normalize_uuid_str("ffe0") == "0000ffe0-0000-1000-8000-00805f9b34fb"
    assert (
        normalize_uuid_str("0000180A-0000-1000-8000-00805F9B34FB")
        == "0000180a-0000-1000-8000-00805f9b34fb"
    )
    # 32-bit form
    assert normalize_uuid_str("12345678") == "12345678-0000-1000-8000-00805f9b34fb"


def test_bumble_uuid_to_str_matches_bleak_form():
    from bumble.core import UUID

    assert bumble_uuid_to_str(UUID("180A")) == "0000180a-0000-1000-8000-00805f9b34fb"
    custom = "12345678-1234-5678-1234-56789abcdef0"
    assert bumble_uuid_to_str(UUID(custom)) == custom


def test_properties_mapping():
    P = Characteristic.Properties
    props = _properties_to_strings(P.READ | P.NOTIFY | P.WRITE_WITHOUT_RESPONSE)
    assert set(props) == {"read", "notify", "write-without-response"}


def test_advertisement_data_parsing():
    ad = AdvertisingData(
        [
            (AdvertisingData.COMPLETE_LOCAL_NAME, b"MyBMS"),
            (AdvertisingData.COMPLETE_LIST_OF_16_BIT_SERVICE_CLASS_UUIDS, bytes.fromhex("e0ff")),
        ]
    )
    parsed = AdvertisementData(ad, rssi=-55)
    assert parsed.local_name == "MyBMS"
    assert parsed.rssi == -55
    assert "0000ffe0-0000-1000-8000-00805f9b34fb" in parsed.service_uuids


def test_transport_spec_mac_resolution(monkeypatch):
    import pytest

    from bumble_bleak import _backend

    # Fake the per-index MAC lookup (real one uses the HCIGETDEVINFO ioctl).
    addrs = {0: "0c:ef:15:47:4a:46", 1: "2c:cf:67:5f:4a:6d"}
    monkeypatch.setattr(_backend, "_hci_addr_for_index", lambda i: addrs.get(i))
    monkeypatch.setattr(_backend, "_candidate_hci_indices", lambda: [0, 1])

    assert _backend._transport_spec("2C:CF:67:5F:4A:6D") == "hci-socket:1"
    assert _backend._transport_spec("0c:ef:15:47:4a:46") == "hci-socket:0"  # case-insensitive
    assert _backend._transport_spec("hci1") == "hci-socket:1"
    assert _backend._transport_spec(None) == "hci-socket:0"
    with pytest.raises(_backend.BleakError):
        _backend._transport_spec("AA:BB:CC:DD:EE:FF")


def test_exceptions_are_bleak_errors():
    assert issubclass(bleak.BleakDeviceNotFoundError, bleak.BleakError)
    assert issubclass(bleak.BleakCharacteristicNotFoundError, bleak.BleakError)
    err = bleak.BleakCharacteristicNotFoundError("ffe1")
    assert err.char_specifier == "ffe1"
