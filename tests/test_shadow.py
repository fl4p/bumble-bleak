"""The shadow makes `import bleak` (and bleak_retry_connector) resolve to us."""

import importlib


def test_shadow_redirects_bleak():
    import bumble_bleak.shadow  # activates on import

    assert bumble_bleak.shadow.is_active()

    import bleak
    import bleak.backends.characteristic as bc
    import bleak.backends.device as bd
    import bleak.backends.scanner as bs
    import bleak.backends.service as bsv
    import bleak.exc as be
    import bleak.uuids as bu
    import bleak_retry_connector as brc

    assert bleak.BleakClient.__module__ == "bumble_bleak.client"
    assert bleak.BleakScanner.__module__ == "bumble_bleak.scanner"
    assert bc.BleakGATTCharacteristic.__module__ == "bumble_bleak.characteristic"
    assert bd.BLEDevice.__module__ == "bumble_bleak.device"
    assert bs.AdvertisementData.__module__ == "bumble_bleak.device"
    assert bsv.BleakGATTServiceCollection.__module__ == "bumble_bleak.characteristic"
    assert issubclass(be.BleakDeviceNotFoundError, be.BleakError)
    assert bu.normalize_uuid_str("ffe0") == "0000ffe0-0000-1000-8000-00805f9b34fb"
    assert brc.BLEAK_TIMEOUT == 20.0
    assert callable(brc.establish_connection)


def test_shadow_covers_aiobmsble_retry_connector_surface():
    """aiobmsble's basebms imports all four of these at module level.

    A missing one is an ImportError at plugin import time, which batmon-ha
    reports as "Unknown device type" (#385, #407) -- so pin the surface here.
    """
    import bumble_bleak.shadow  # noqa: F401  (activates on import)

    from bleak_retry_connector import (  # noqa: F401
        BLEAK_TIMEOUT,
        MAX_CONNECT_ATTEMPTS,
        close_stale_connections,
        establish_connection,
    )

    assert MAX_CONNECT_ATTEMPTS >= 1
