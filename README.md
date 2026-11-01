# Wacom Bamboo for Omarchy

A bar widget for the [Omarchy](https://omarchy.org) shell that sets up a Wacom
pen tablet from the bar: which display the pen draws on, orientation
(including flipped for left-handed use), active area, pressure, finger touch,
and what every pen and tablet button does.

<img src="preview.png" alt="Tablet panel: Pen and Buttons tabs" width="720">

## Features

- **Pen** – target display (any monitor or all of them), active-area size with
  a scale preview, crop to the display's aspect ratio so circles stay round,
  pressure sensitivity.
- **Orientation** – *Flipped* turns the pen, finger touch and the key order
  around for left-handed use. *90°* and *270°* hold the tablet portrait: pen,
  finger touch and key order all turn with it, and palm rejection keeps working.
- **Touch** – finger touch on/off (handy while drawing), pointer speed, tap to
  click, natural scrolling.
- **Buttons** – remap the pen side buttons and the ExpressKeys to a click, a
  keyboard shortcut (`ctrl+z`, `ctrl+shift+s`, `super+w`…), any shell command,
  or nothing, or leave them on **App default** so drawing apps (Krita, Rnote…)
  still get them. Presets cover the usual drawing shortcuts (undo, hold space
  to pan, brush size, eraser, zoom) and commands (toggle finger touch,
  screenshot, colour picker); **Test** runs a command on the spot. The tablet
  is drawn from its real layout; press a physical button and its picture and
  row light up. **Identify keys** relearns the key order if a model's data is
  incomplete.
- The panel adapts to the tablet that's plugged in: tabs and buttons it doesn't
  have are hidden, and the bar icon disappears while no tablet is connected.
  Changes apply instantly and survive reboots. Colours and fonts follow the
  current Omarchy theme.

## Supported hardware

Any Wacom tablet handled by the kernel `wacom` driver, which splits a tablet
into `… Pen`, `… Finger` and `… Pad` input devices. The model name, touch
support, key codes and key layout come from **libwacom**, already installed on
every Omarchy system as a libinput dependency; the active-area size comes from
the driver.

✅ = confirmed working on real hardware · ☐ = should work (checked against
libwacom's data and with virtual devices), not tried yet

| | Family | Models | Notes |
|---|---|---|---|
| ✅ | Bamboo Pen & Touch, Fun, SE | CTH-460/461/661 and (A)/SE variants | CTH-460(A), the author's tablet: pen, touch, 4 keys, every orientation |
| ✅ | Intuos5 | PTH-650 | [Reported by a user](https://github.com/mug31/omarchy-tablet/issues/1): every key recognised. 8 keys, touch; the touch ring isn't handled |
| ☐ | Bamboo Capture, Create, 16FG | CTH-470/670, CTT-470 | 4 keys, touch |
| ☐ | Bamboo Touch | CTT-460/460(A) | touch and keys, no pen |
| ☐ | Bamboo Pen, Connect, One | CTL-460/660/470, CTF-430, CTE-660 | pen only |
| ☐ | Bamboo Fun / Pen (older) | CTE-450/650, MTE-450 | 4 keys along the top |
| ☐ | Bamboo Pad | CTH-300/301 | 2 keys, touch |
| ☐ | Intuos S/M, One by Wacom | CTL/CTH-480/490/680/690, CTL-4100/6100, CTL-471/472/671/672 | Intuos Pen & Touch 1st/2nd gen: run **Identify keys** once, libwacom doesn't record their key order |

Got it working on a model not ticked here, or found one that doesn't?
[Open an issue](https://github.com/mug31/omarchy-tablet/issues) with the model
number and it'll be added.

Huion, XP-Pen and other brands use different drivers and are not supported.
Touch rings and strips on pads are not handled.

## Requirements

- Omarchy 4.x with the Lua Hyprland config (`~/.config/hypr/hyprland.lua`).
- The kernel `wacom` driver. Check with `lsmod | grep wacom`.
  **OpenTabletDriver blocks it** (`/usr/lib/modprobe.d/99-opentabletdriver.conf`).
  To use this widget instead of OpenTabletDriver:

  ```bash
  systemctl --user disable --now opentabletdriver.service
  sudo sh -c ': > /etc/modprobe.d/99-opentabletdriver.conf'   # override the block
  sudo modprobe wacom                                          # then replug the tablet
  ```

Nothing else to install: the widget only uses Python's standard library.

## Install

```bash
omarchy plugin add https://github.com/mug31/omarchy-tablet.git --enable
```

That's it: there is no setup step and the Hyprland config is never edited.

**Button remapping and portrait** need to grab the tablet and create virtual
input devices, which a stock Omarchy keeps to root. If so, the panel shows
**Enable**: it asks for your password once (pkexec) and installs
`/etc/udev/rules.d/70-omarchy-wacom-bamboo.rules`, which gives the logged-in
user access to `/dev/uinput` and to Wacom tablets only. It takes effect
straight away, without logging out.

**Updating from 0.3 or older** happens on its own the first time the bar
loads 0.4: the `require("hypr.tablet")` line is removed from
`hyprland.lua` (the old file is kept as `hyprland.lua.wacom-bamboo.bak`),
`tablet.lua` becomes `tablet.lua.bak`, and the `tablet-buttons` systemd
service is stopped and its unit kept as `tablet-buttons.service.bak`.
Settings and button mappings carry over.

## How it works

| Piece | Role |
|---|---|
| `Panel.qml` | The bar icon and panel. Every change is sent to `bin/tablet-ctl`. |
| `bin/tabletlib.py` | Finds the tablet in `/proc/bus/input/devices` and reads its libwacom entry and layout. |
| `bin/tablet-ctl` | Saves settings to `~/.config/omarchy/tablet.json` and sends them to Hyprland with `hyprctl eval`. |
| `bin/tablet-buttons` | Helper the panel keeps running (one per session; extra bars wait their turn). Re-applies the settings when the session starts, the tablet is plugged in, Hyprland reloads its config or a monitor changes. While any ExpressKey is remapped it grabs the pad and runs the actions through a virtual keyboard and mouse; keys left on App default are replayed through a virtual pad of the same name, so apps still see them. The pen is grabbed while a side button is remapped or the tablet is portrait, and replayed through a virtual clone (`… Pen Remap`) so pressure and tilt keep working; in portrait the finger touch is replayed turned (`… Finger Rotated`). Log: `~/.cache/omarchy-tablet/tablet-buttons.log`. |
| `bin/evio.py` | Minimal evdev/uinput access (reading, grabbing, virtual devices) from the standard library. |

`bin/tablet-ctl toggle fingerEnabled` flips finger touch on or off with a
notification, so it can go on a tablet button or a Hyprland keybinding.

## Uninstall

```bash
omarchy plugin remove io.github.mug31.wacom-bamboo
rm ~/.config/omarchy/tablet.json                            # saved settings
sudo rm /etc/udev/rules.d/70-omarchy-wacom-bamboo.rules     # only if you pressed Enable
```

The settings were only ever applied at runtime, so they are gone after the
next Hyprland reload or login.

## License

MIT
