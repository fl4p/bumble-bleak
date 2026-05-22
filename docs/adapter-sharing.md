# Can a Bluetooth adapter be shared (without D-Bus)?

Short answer: **D-Bus is not what enables sharing — the *host stack* is.** Dropping
D-Bus is easy; sharing one physical radio across independent processes is the hard
part, and it is unrelated to D-Bus.

## Why

A Bluetooth controller has exactly **one host stack** at a time. "Sharing" means
that single host multiplexes several consumers. On Linux there are exactly two
candidates for that arbiter:

1. **The in-kernel BlueZ stack** — multiple apps share it through kernel sockets.
2. **`bluetoothd`** — a userspace daemon *on top of* the kernel stack.

**D-Bus is only the API to option 2.** So yes, you can share *without D-Bus* — by
talking to the kernel BlueZ stack directly, with no `bluetoothd` involved:

- **mgmt socket** (`HCI_CHANNEL_CONTROL`) — adapter control, scanning
- **L2CAP sockets** (`BTPROTO_L2CAP`, ATT CID `0x0004`) — GATT data per connection
- (`HCI_CHANNEL_RAW` for raw HCI alongside the kernel)

The kernel arbitrates, so several processes coexist on one adapter — and you never
touch D-Bus or `bluetoothd`. (This is "path A" from the original bumble-bleak plan.)

## The catch — it is incompatible with Bumble

That sharing only works because **the kernel stack owns the radio**. Bumble's
`hci-socket` transport takes the adapter via **`HCI_CHANNEL_USER`**, which
*detaches* the kernel stack and holds the controller exclusively. So while Bumble
owns it, `bluetoothd`/bleak cannot even see the adapter.

| Goal | Mechanism | D-Bus? | Kernel stack? | Multi-process sharing? |
|---|---|---|---|---|
| Bumble (bumble-bleak) | `HCI_CHANNEL_USER` | no | **no** | **no** — exclusive |
| Kernel sockets | mgmt + L2CAP | **no** | yes | **yes** |
| bluetoothd | D-Bus | yes | yes | yes |

There is **no userspace sharing daemon that Bumble plugs into.** Sharing requires a
single arbiter; without D-Bus that arbiter is the kernel — and the kernel stack and
Bumble are mutually exclusive on the same radio.

So you cannot have *both* "no kernel stack (Bumble)" *and* "shared across
processes." Pick the axis that matters:

- **Escape the kernel stack** (the original motivation — suspected kernel bugs) →
  Bumble, exclusive adapter. Use a **second adapter** for the others
  (the Pi has `hci0` USB + `hci1` UART).
- **Share one adapter, just without D-Bus** → kernel L2CAP/mgmt sockets. But you
  are back on the kernel BlueZ stack you wanted to avoid, and you would
  implement/borrow an ATT/GATT layer (Bumble does not help here).

## The only way to share *on top of* Bumble

Run **one** process that owns the adapter via Bumble and re-exposes it over a small
IPC/socket API that other processes call — i.e. write a minimal sharing daemon
("bumbled-lite"). That gives D-Bus-free, kernel-stack-free, multi-process sharing,
but it is real work: you are reinventing the multiplexing role `bluetoothd` plays.

Within a *single* process, Bumble already shares freely — bumble-bleak's backend
runs all connections + scanning over one `Device`. Only *other processes / the
kernel stack* are excluded.

## Practical recommendation

If another consumer (e.g. Home Assistant's own Bluetooth integration, which is
built on bleak → BlueZ → **D-Bus** and cannot run without them) needs Bluetooth
at the same time: **dedicate one adapter to bumble-bleak and leave the other to
BlueZ/HA.** They run fully in parallel because they are different controllers.
