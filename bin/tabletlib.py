"""Shared tablet discovery for tablet-ctl and tablet-buttons.

Finds the connected Wacom tablet from /proc/bus/input/devices (the kernel
`wacom` driver splits one tablet into "<name> Pen", "<name> Finger" and
"<name> Pad" input devices), then looks the model up in libwacom, which every
Omarchy install has because libinput depends on it. libwacom supplies the
model name, whether the pad keys exist, which evdev code each key sends and
where each key sits on the tablet (its layout SVG).
"""
import configparser
import fcntl
import glob
import os
import re
import struct
import xml.etree.ElementTree as ET

LIBWACOM = "/usr/share/libwacom"
WACOM_VENDORS = {"056a", "2d1f", "0531"}
REMAP_SUFFIX = " Remap"  # tablet-buttons' virtual pen clone
ROTATED_SUFFIX = " Rotated"  # tablet-buttons' turned touch clone at 90/270

ABS_X, ABS_Y = 0x00, 0x01
PEN_BUTTONS = {0x14B: "pen1", 0x14C: "pen2", 0x149: "pen3"}  # BTN_STYLUS, BTN_STYLUS2, BTN_STYLUS3

# evdev key codes a tablet pad can send, by name (libwacom uses these names).
KEY_NAMES = {0x100 + i: f"BTN_{i}" for i in range(10)}
KEY_NAMES.update({
    0x110: "BTN_LEFT", 0x111: "BTN_RIGHT", 0x112: "BTN_MIDDLE", 0x113: "BTN_SIDE",
    0x114: "BTN_EXTRA", 0x115: "BTN_FORWARD", 0x116: "BTN_BACK", 0x117: "BTN_TASK",
    0x126: "BTN_BASE", 0x127: "BTN_BASE2", 0x128: "BTN_BASE3", 0x129: "BTN_BASE4",
    0x12A: "BTN_BASE5", 0x12B: "BTN_BASE6",
    0x130: "BTN_A", 0x131: "BTN_B", 0x132: "BTN_C", 0x133: "BTN_X", 0x134: "BTN_Y",
    0x135: "BTN_Z", 0x136: "BTN_TL", 0x137: "BTN_TR", 0x138: "BTN_TL2", 0x139: "BTN_TR2",
    0x13A: "BTN_SELECT", 0x13B: "BTN_START", 0x13C: "BTN_MODE", 0x13D: "BTN_THUMBL",
    0x13E: "BTN_THUMBR",
})
KEY_NAMES.update({0x2C0 + i: f"BTN_TRIGGER_HAPPY{i + 1}" for i in range(40)})
KEY_CODES = {v: k for k, v in KEY_NAMES.items()}
KEY_CODES["BTN_MOUSE"] = 0x110


def hypr_name(kernel_name):
    """Hyprland's deviceNameToInternalString()."""
    return "".join("-" if c in " \n," else c.lower() for c in kernel_name)


def _key_bits(bitmap):
    """Set bits of a sysfs capabilities bitmap ("800 630000 0 ..." high word first)."""
    bits = set()
    words = bitmap.split()
    for i, word in enumerate(reversed(words)):
        value = int(word, 16)
        b = 0
        while value:
            if value & 1:
                bits.add(i * 64 + b)
            value >>= 1
            b += 1
    return bits


def _input_devices():
    try:
        with open("/proc/bus/input/devices") as f:
            blocks = f.read().split("\n\n")
    except OSError:
        return []
    out = []
    for block in blocks:
        dev = {"keys": set()}
        for line in block.splitlines():
            kind, _, rest = line.partition(": ")
            if kind == "I":
                fields = dict(p.split("=", 1) for p in rest.split() if "=" in p)
                dev["vendor"] = fields.get("Vendor", "").lower()
                dev["product"] = fields.get("Product", "").lower()
            elif kind == "N":
                dev["name"] = rest.split("=", 1)[1].strip('"')
            elif kind == "P":
                dev["phys"] = rest.split("=", 1)[1]
            elif kind == "S":
                dev["sysfs"] = rest.split("=", 1)[1]
            elif kind == "H":
                for h in rest.split("=", 1)[1].split():
                    if h.startswith("event"):
                        dev["path"] = "/dev/input/" + h
            elif kind == "B" and rest.startswith("KEY="):
                dev["keys"] = _key_bits(rest[4:])
        if dev.get("name") and dev.get("path"):
            out.append(dev)
    return out


def _abs_mm(path):
    """Width/height in mm from the pen's ABS_X/ABS_Y range and resolution."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    except OSError:
        return None
    try:
        size = []
        for axis in (ABS_X, ABS_Y):
            buf = bytearray(24)
            fcntl.ioctl(fd, 0x80184540 + axis, buf)  # EVIOCGABS(axis)
            _, minimum, maximum, _, _, res = struct.unpack("6i", buf)
            if res <= 0:
                return None
            size.append(round((maximum - minimum) / res, 1))
        return size
    except OSError:
        return None
    finally:
        os.close(fd)


_libwacom_cache = {}


def libwacom(vendor, product):
    """Model data for a USB vendor:product from libwacom, or {}."""
    key = (vendor, product)
    if key in _libwacom_cache:
        return _libwacom_cache[key]
    want = f"usb|{vendor}|{product}"
    found = {}
    for path in glob.glob(os.path.join(LIBWACOM, "*.tablet")):
        try:
            with open(path, errors="replace") as f:
                text = f.read()
        except OSError:
            continue
        if want not in text.lower():
            continue
        cfg = configparser.ConfigParser(strict=False, interpolation=None)
        try:
            cfg.read_string(text)
        except configparser.Error:
            continue
        device = cfg["Device"] if cfg.has_section("Device") else {}
        if want not in device.get("DeviceMatch", "").lower():
            continue
        feats = cfg["Features"] if cfg.has_section("Features") else {}
        btns = cfg["Buttons"] if cfg.has_section("Buttons") else {}
        letters = {}
        for side in ("Left", "Right", "Top", "Bottom"):
            for letter in filter(None, btns.get(side, "").split(";")):
                letters[letter.strip()] = side.lower()
        codes = [c.strip() for c in btns.get("EvdevCodes", "").split(";") if c.strip()]
        found = {
            "name": device.get("Name", ""),
            "model": device.get("ModelName", ""),
            "layout": device.get("Layout", ""),
            "touch": feats.get("Touch", "false").lower() == "true",
            "reversible": feats.get("Reversible", "false").lower() == "true",
            "letters": letters,      # letter -> side of the tablet
            "codes": codes,          # evdev code name per letter, in letter order A, B, ...
        }
        break
    _libwacom_cache[key] = found
    return found


def _path_bbox(d):
    """Bounding box (x, y, w, h) of an SVG path's vertices; arcs and curves use
    their end and control points, which is plenty for button outlines."""
    tokens = re.findall(r"[MmLlHhVvCcSsQqTtAaZz]|-?(?:\d+\.?\d*|\.\d+)(?:e-?\d+)?", d or "")
    arity = {"M": 2, "L": 2, "T": 2, "H": 1, "V": 1, "C": 6, "S": 4, "Q": 4, "A": 7, "Z": 0}
    x = y = sx = sy = 0.0
    pts, cmd, i = [], None, 0
    while i < len(tokens):
        if re.fullmatch(r"[A-Za-z]", tokens[i]):
            cmd = tokens[i]
            i += 1
            if cmd in "Zz":
                x, y = sx, sy
                continue
        if cmd is None:
            break
        n = arity[cmd.upper()]
        args = [float(t) for t in tokens[i:i + n]]
        if len(args) < n:
            break
        i += n
        rel = cmd.islower()
        up = cmd.upper()
        if up == "H":
            x = x + args[0] if rel else args[0]
        elif up == "V":
            y = y + args[0] if rel else args[0]
        elif up == "A":
            x, y = (x + args[5], y + args[6]) if rel else (args[5], args[6])
        else:
            for j in range(0, n - 2, 2):  # control points
                pts.append((x + args[j], y + args[j + 1]) if rel else (args[j], args[j + 1]))
            x, y = (x + args[-2], y + args[-1]) if rel else (args[-2], args[-1])
        if up == "M":
            sx, sy = x, y
            cmd = "l" if rel else "L"  # extra pairs after a moveto are linetos
        pts.append((x, y))
    if not pts:
        return None
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return (min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))


def _layout_buttons(layout):
    """{letter: (x, y, w, h)} and the canvas size from a libwacom layout SVG."""
    path = os.path.join(LIBWACOM, "layouts", layout)
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError):
        return {}, None
    def num(v):
        m = re.match(r"-?[\d.]+", str(v or "0"))
        return float(m.group()) if m else 0.0
    canvas = (num(root.get("width")), num(root.get("height")))
    rects = {}
    for el in root.iter():
        ident = el.get("id", "")
        m = re.fullmatch(r"Button([A-Z])", ident)
        if not m:
            continue
        tag = el.tag.split("}")[-1]
        if tag == "rect":
            rects[m.group(1)] = (num(el.get("x")), num(el.get("y")), num(el.get("width")), num(el.get("height")))
        elif tag == "circle":
            r = num(el.get("r"))
            rects[m.group(1)] = (num(el.get("cx")) - r, num(el.get("cy")) - r, 2 * r, 2 * r)
        elif tag == "path":
            box = _path_bbox(el.get("d"))
            if box:
                rects[m.group(1)] = box
    return rects, canvas


def _synthetic_layout(count):
    """Keys stacked down the left edge, for tablets libwacom has no layout for."""
    canvas = (240.0, 160.0)
    gap, h = 6.0, min(28.0, (canvas[1] - 40) / max(1, count) - 6)
    top = (canvas[1] - (count * h + (count - 1) * gap)) / 2
    rects = {chr(65 + i): (14.0, top + i * (h + gap), 22.0, h) for i in range(count)}
    return rects, canvas


def _rotate(cx, cy, w, h, degrees):
    if degrees == 90:
        return h - cy, cx
    if degrees == 180:
        return w - cx, h - cy
    if degrees == 270:
        return cy, w - cx
    return cx, cy


def _ordinal_labels(count, vertical):
    first, last = ("Top", "Bottom") if vertical else ("Leftmost", "Rightmost")
    names = ["Second", "Third", "Fourth", "Fifth", "Sixth", "Seventh", "Eighth", "Ninth"]
    labels = []
    for i in range(count):
        if i == 0:
            labels.append(f"{first} key")
        elif i == count - 1:
            labels.append(f"{last} key")
        else:
            labels.append(f"{names[i - 1] if i - 1 < len(names) else str(i + 1) + 'th'} key")
    return labels


def detect():
    """The connected tablet, or None.

    Returns {vendor, product, kernelName, pen, finger, pad} where each of pen,
    finger and pad is {name, hypr, path, keys} or None. For testing,
    TABLET_ALLOW_VIRTUAL=1 accepts uinput devices and TABLET_ONLY=vid:pid
    picks one tablet.
    """
    allow_virtual = os.environ.get("TABLET_ALLOW_VIRTUAL") == "1"
    only = os.environ.get("TABLET_ONLY", "").lower()  # "vendor:product", testing only
    groups = {}
    for dev in _input_devices():
        if dev.get("vendor") not in WACOM_VENDORS or dev["name"].endswith(REMAP_SUFFIX):
            continue
        if "/virtual/" in dev.get("sysfs", "") and not allow_virtual:
            continue
        for role, suffix in (("pen", " Pen"), ("finger", " Finger"), ("pad", " Pad")):
            if dev["name"].endswith(suffix):
                base = dev["name"][: -len(suffix)]
                phys = dev.get("phys", "").rsplit("/input", 1)[0] or base
                group = groups.setdefault((dev["vendor"], dev["product"], phys), {"kernelName": base})
                group[role] = {"name": dev["name"], "hypr": hypr_name(dev["name"]),
                               "path": dev["path"], "keys": dev["keys"]}
    for (vendor, product, _), g in sorted(groups.items()):
        if only and only != f"{vendor}:{product}":
            continue
        if g.get("pen") or g.get("finger"):
            return {"vendor": vendor, "product": product, "kernelName": g["kernelName"],
                    "pen": g.get("pen"), "finger": g.get("finger"), "pad": g.get("pad")}
    return None


def describe(tablet, rotation=0, slot_codes=None):
    """Everything the panel and the remapper need to know about `tablet`.

    slot_codes overrides libwacom's key -> code assignment ({letter: code
    name}), as learnt by "Identify keys".
    """
    lw = libwacom(tablet["vendor"], tablet["product"])
    pen, pad = tablet.get("pen"), tablet.get("pad")

    size = _abs_mm(pen["path"]) if pen else None

    pen_buttons = [name for code, name in sorted(PEN_BUTTONS.items(), key=lambda kv: kv[1])
                   if pen and code in pen["keys"]]

    pad_codes = []
    if pad:
        pad_codes = [KEY_NAMES.get(c, f"KEY_{c}") for c in sorted(pad["keys"])
                     if not 0x140 <= c <= 0x14F]  # BTN_TOOL_*, BTN_TOUCH, BTN_STYLUS*
    # libwacom: letter -> code, in physical layout.
    rects, canvas = _layout_buttons(lw["layout"]) if lw.get("layout") else ({}, None)
    letters = sorted(rects) if rects else []
    by_letter = {}
    for i, letter in enumerate(sorted(lw.get("letters", {}))):
        if i < len(lw.get("codes", [])) and lw["codes"][i] in pad_codes:
            by_letter[letter] = lw["codes"][i]
    order_known = True
    if rects and len(letters) == len(pad_codes) and set(letters) != set(by_letter):
        # libwacom draws the keys but doesn't say which code each sends:
        # keep the real layout, guess the order, suggest Identify keys.
        by_letter = dict(zip(letters, sorted(pad_codes, key=lambda n: KEY_CODES.get(n, 0))))
        order_known = False
    elif not rects or len(by_letter) != len(pad_codes) or set(letters) != set(by_letter):
        # Unknown model or layout mismatch: draw a generic column, order by code.
        rects, canvas = _synthetic_layout(len(pad_codes))
        letters = sorted(rects)
        by_letter = dict(zip(letters, sorted(pad_codes, key=lambda n: KEY_CODES.get(n, 0))))
        order_known = False
    if slot_codes and sorted(slot_codes) == letters and sorted(slot_codes.values()) == sorted(by_letter.values()):
        by_letter = dict(slot_codes)
        order_known = True

    slots = []
    if pad_codes and canvas:
        w, h = canvas
        xs = [rects[l][0] + rects[l][2] / 2 for l in letters]
        ys = [rects[l][1] + rects[l][3] / 2 for l in letters]
        spread_x, spread_y = max(xs) - min(xs), max(ys) - min(ys)
        vertical_native = spread_y >= spread_x
        vertical = vertical_native != (rotation in (90, 270))
        def order_key(letter):
            x, y, rw, rh = rects[letter]
            rx, ry = _rotate(x + rw / 2, y + rh / 2, w, h, rotation)
            return (ry, rx) if vertical else (rx, ry)
        ordered = sorted(letters, key=order_key)
        labels = _ordinal_labels(len(ordered), vertical)
        for index, letter in enumerate(ordered):
            x, y, rw, rh = rects[letter]
            slots.append({"letter": letter, "code": by_letter[letter], "index": index,
                          "label": labels[index], "x": x, "y": y, "w": rw, "h": rh})

    return {
        "vendor": tablet["vendor"], "product": tablet["product"],
        "kernelName": tablet["kernelName"],
        "model": lw.get("model", ""),
        "modelName": lw.get("name", "") or tablet["kernelName"],
        "known": bool(lw),
        "reversible": lw.get("reversible", True),
        "hasPen": bool(pen), "hasTouch": bool(tablet.get("finger")), "hasPad": bool(slots),
        "penName": pen["hypr"] if pen else "",
        "fingerName": tablet["finger"]["hypr"] if tablet.get("finger") else "",
        "penButtons": pen_buttons,
        "sizeMm": size,
        "canvas": list(canvas) if slots else None,
        "slots": slots,
        "orderKnown": order_known or not slots,
    }


# "arealock" releases the pen from the app window it is locked to, or locks it again (tabletarea.py).
ACTIONS = {"default", "left", "right", "middle", "key", "command", "arealock", "none"}
# Fresh pad keys get these by position (first = top/leftmost with the tablet
# held normally); further keys start disabled.
PAD_DEFAULTS = [
    {"action": "key", "value": "ctrl+z"},
    {"action": "key", "value": "ctrl+shift+z"},
    {"action": "right", "value": ""},
    {"action": "middle", "value": ""},
]


def resolve_buttons(saved, desc):
    """Saved mappings completed with defaults for every button `desc` has.
    `desc` must be described at rotation 0 so defaults follow the hardware."""
    out = {k: dict(v) for k, v in (saved or {}).items()}
    for name in (desc or {}).get("penButtons", []):
        out.setdefault(name, {"action": "default", "value": ""})
    for slot in (desc or {}).get("slots", []):
        fallback = PAD_DEFAULTS[slot["index"]] if slot["index"] < len(PAD_DEFAULTS) else {"action": "none", "value": ""}
        out.setdefault(slot["code"], dict(fallback))
    return out


def pen_in_proximity(path):
    """True while the pen (or its eraser end) hovers over the tablet."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    except OSError:
        return False
    try:
        buf = bytearray(96)  # KEY_MAX + 1 bits
        fcntl.ioctl(fd, 0x80000000 | (len(buf) << 16) | (ord("E") << 8) | 0x18, buf)  # EVIOCGKEY
        return any(buf[code // 8] >> (code % 8) & 1 for code in (0x140, 0x141))  # BTN_TOOL_PEN/RUBBER
    except OSError:
        return False
    finally:
        os.close(fd)
