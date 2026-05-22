# bumble-bleak: adapters & the USB re-enumeration issue

## What bumble-bleak is

`bumble-bleak` is a **bleak-compatible BLE central API for Linux** backed by
[Bumble](https://github.com/google/bumble), a pure-Python Bluetooth stack. It
exposes the `bleak` API (`BleakClient`, `BleakScanner`, GATT, notifications, SMP
pairing) but talks to the controller over an **HCI socket** (`HCI_CHANNEL_USER`)
instead of BlueZ/D-Bus. So there is **no BlueZ, no D-Bus, and no in-kernel
Bluetooth stack** in the path — the whole HCI → L2CAP → ATT → GATT stack runs in
Python. It can also transparently *shadow* the `bleak` module so third-party
libraries (e.g. `aiobmsble`) use it unchanged. See the [README](../README.md).

## How an adapter is selected

The `adapter` argument (`BleakClient(..., adapter=...)`, `BleakScanner(adapter=...)`)
accepts:

| Form | Meaning |
|------|---------|
| `None` | `$BUMBLE_BLEAK_TRANSPORT` or `hci-socket:0` |
| `"hci0"`, `"hci1"`, … | that controller index → `hci-socket:N` |
| a MAC, e.g. `"2C:CF:67:5F:4A:6D"` | resolved to the controller's **current** `hciN` via `/sys/class/bluetooth/*/address` at connect time |
| anything else with `:` | a literal Bumble transport spec (`android-netsim`, a virtual link, …) |

**Prefer the MAC form on systems where the index can change** (see below).

## The exclusive-ownership rule

A Bluetooth controller has exactly **one** host stack at a time. To use
`HCI_CHANNEL_USER`, bumble-bleak brings the adapter **down** and takes it
exclusively (it does this automatically via `HCIDEVDOWN`; needs `CAP_NET_ADMIN`,
disable with `BUMBLE_BLEAK_NO_ADAPTER_DOWN=1`). While bumble-bleak owns an
adapter, **BlueZ / bleak / Home Assistant cannot see it**. To run both stacks at
once, **dedicate a second adapter** to bumble-bleak.

## The USB re-enumeration issue

On some **USB** Bluetooth dongles (e.g. Realtek RTL8761), bringing the controller
down and closing the User Channel triggers a **USB-level reset/re-enumeration**
(firmware reload). The kernel then re-registers the controller, and its index can
**climb**: `hci0` → `hci2` → `hci3` …

It gets much worse if another stack is **fighting** for the same adapter. Home
Assistant's `bluetooth_auto_recovery`, on losing "its" adapter, repeatedly
power-cycles it (`Resource busy`), forcing more resets and bumping the index
further. Symptoms:

- `hciconfig` shows the dongle at an ever-higher `hciN`;
- HA logs `adapter 'hci0' not found … has moved to hci2 … Resource busy`, and its
  Bluetooth scanner gets stuck;
- an index-pinned config (`adapter: hci0`) silently points at the wrong device.

**UART adapters do not do this** — there is no USB reset, so no re-enumeration.

### What to do (best first)

1. **Use a UART / built-in adapter for bumble-bleak** (e.g. `hci1`). No USB reset
   → no index churn at all, and it leaves the USB dongle to BlueZ/HA. This is the
   clean fix.
2. **Select by MAC, not index.** Pass the controller's MAC as `adapter`;
   bumble-bleak re-resolves the current `hciN` on every connect, so a moved index
   no longer breaks your config. (Doesn't stop the reset — just makes you immune
   to it.)
3. **If the USB dongle must be dedicated to bumble-bleak:** stop the other stack
   from fighting it — in Home Assistant, disable that adapter in the Bluetooth
   integration (Settings → Devices & Services → Bluetooth → adapter → disable),
   and move HA's own Bluetooth to the other controller. Without the auto-recovery
   tug-of-war, the index stops climbing (the kernel reuses the freed `hci0`).

### Recovering a churned adapter

If the index has already climbed and BlueZ/HA are confused:

```sh
# stop everything using the radio, then power-cycle:
sudo rfkill block bluetooth; sleep 2; sudo rfkill unblock bluetooth
sudo systemctl restart bluetooth
# in Home Assistant, reload the Bluetooth integration (or restart HA core)
```

The dongle should settle back to the lowest free index (`hci0`).
