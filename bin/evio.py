"""Just enough evdev and uinput for tablet-buttons, from the standard library.

Replaces python-evdev, which isn't installed on a stock Omarchy system. Event
and key codes are read from the kernel's input-event-codes.h, which
linux-api-headers (a glibc dependency) puts on every Arch install.

  Device(path)          an /dev/input/event* device: name, ids, caps, absinfo, props
  UInput(...)           a virtual device the events are written through
  ecodes                EV_*, KEY_*, BTN_*, ABS_*, ... as attributes
"""
import ctypes
import fcntl
import os
import re
import struct
from collections import namedtuple
from types import SimpleNamespace

HEADER = "/usr/include/linux/input-event-codes.h"
PREFIXES = ("EV_", "SYN_", "KEY_", "BTN_", "REL_", "ABS_", "MSC_", "SW_", "LED_", "SND_", "REP_",
            "INPUT_PROP_")


def _load_codes():
    values = {}
    with open(HEADER) as f:
        for name, value in re.findall(r"^#define\s+(\w+)\s+(\w+)\s*(?:/\*.*)?$", f.read(), re.M):
            if not name.startswith(PREFIXES):
                continue
            if re.fullmatch(r"0x[0-9a-fA-F]+|\d+", value):
                values[name] = int(value, 0)
            elif value in values:  # alias such as KEY_HANGUEL -> KEY_HANGEUL
                values[name] = values[value]
    return values


ecodes = SimpleNamespace(**_load_codes())
e = ecodes

AbsInfo = namedtuple("AbsInfo", "value min max fuzz flat resolution")
InputEvent = namedtuple("InputEvent", "type code value")

EVENT = struct.Struct("llHHi")      # struct input_event: timeval, type, code, value
ABSINFO = struct.Struct("6i")       # struct input_absinfo
INPUT_ID = struct.Struct("4H")      # struct input_id: bustype, vendor, product, version
BUS_VIRTUAL = 0x06                  # from linux/input.h


_libc = ctypes.CDLL(None, use_errno=True)


def _ioc(direction, kind, nr, size):
    return direction << 30 | size << 16 | ord(kind) << 8 | nr


def _ior(kind, nr, size):
    return _ioc(2, kind, nr, size)


def _iow(kind, nr, size):
    return _ioc(1, kind, nr, size)


EVIOCGID = _ior("E", 0x02, INPUT_ID.size)
EVIOCGRAB = _iow("E", 0x90, 4)
UI_DEV_CREATE = _ioc(0, "U", 1, 0)
UI_DEV_DESTROY = _ioc(0, "U", 2, 0)
UI_DEV_SETUP = _iow("U", 3, INPUT_ID.size + 80 + 4)
UI_ABS_SETUP = _iow("U", 4, 4 + ABSINFO.size)
UI_SET_EVBIT = _iow("U", 100, 4)
UI_SET_PHYS = _iow("U", 108, ctypes.sizeof(ctypes.c_void_p))
UI_SET_PROPBIT = _iow("U", 110, 4)
# Event type -> UI_SET_*BIT for its codes.
UI_SET_BITS = {e.EV_KEY: _iow("U", 101, 4), e.EV_REL: _iow("U", 102, 4), e.EV_ABS: _iow("U", 103, 4),
               e.EV_MSC: _iow("U", 104, 4), e.EV_LED: _iow("U", 105, 4), e.EV_SND: _iow("U", 106, 4),
               e.EV_SW: _iow("U", 109, 4)}
# Highest code of each event type, for sizing EVIOCGBIT buffers.
MAX_CODE = {0: e.EV_MAX, e.EV_KEY: e.KEY_MAX, e.EV_REL: e.REL_MAX, e.EV_ABS: e.ABS_MAX,
            e.EV_MSC: e.MSC_MAX, e.EV_LED: e.LED_MAX, e.EV_SND: e.SND_MAX, e.EV_SW: e.SW_MAX}


def _bits(fd, request_nr, max_code):
    buf = bytearray(max_code // 8 + 1)
    fcntl.ioctl(fd, _ior("E", request_nr, len(buf)), buf)
    return [i for i in range(max_code + 1) if buf[i // 8] >> (i % 8) & 1]


def _string(fd, nr):
    buf = bytearray(256)
    try:
        fcntl.ioctl(fd, _ior("E", nr, len(buf)), buf)
    except OSError:
        return ""
    return buf.split(b"\0", 1)[0].decode(errors="replace")


class Device:
    """An evdev device opened non-blocking for reading."""

    def __init__(self, path):
        self.path = path
        self.fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        try:
            self.name = _string(self.fd, 0x06)
            self.phys = _string(self.fd, 0x07)
            buf = bytearray(INPUT_ID.size)
            fcntl.ioctl(self.fd, EVIOCGID, buf)
            self.bustype, self.vendor, self.product, self.version = INPUT_ID.unpack(buf)
            self.props = _bits(self.fd, 0x09, e.INPUT_PROP_MAX)
            self.caps = {}
            for kind in _bits(self.fd, 0x20, e.EV_MAX):
                if kind == e.EV_SYN:
                    continue
                self.caps[kind] = _bits(self.fd, 0x20 + kind, MAX_CODE[kind]) if kind in MAX_CODE else []
            self.absinfo = {}
            for code in self.caps.get(e.EV_ABS, []):
                buf = bytearray(ABSINFO.size)
                fcntl.ioctl(self.fd, _ior("E", 0x40 + code, ABSINFO.size), buf)
                self.absinfo[code] = AbsInfo(*ABSINFO.unpack(buf))
        except OSError:
            os.close(self.fd)
            raise

    def read(self):
        """Pending events. Raises BlockingIOError when there are none."""
        data = os.read(self.fd, EVENT.size * 64)
        return [InputEvent(*EVENT.unpack_from(data, i)[2:]) for i in range(0, len(data), EVENT.size)]

    def grab(self):
        fcntl.ioctl(self.fd, EVIOCGRAB, 1)

    def ungrab(self):
        fcntl.ioctl(self.fd, EVIOCGRAB, 0)

    def close(self):
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1


class UInput:
    """A virtual input device.

    caps: {event type: [codes]}; absinfo: {abs code: AbsInfo} for every EV_ABS code.
    """

    def __init__(self, caps, name, absinfo=None, vendor=0, product=0, version=0,
                 bustype=BUS_VIRTUAL, props=(), phys=""):
        self.fd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)
        try:
            for kind, codes in caps.items():
                if kind == e.EV_SYN:
                    continue
                fcntl.ioctl(self.fd, UI_SET_EVBIT, kind)
                for code in codes if kind in UI_SET_BITS else ():
                    fcntl.ioctl(self.fd, UI_SET_BITS[kind], code)
            for prop in props:
                fcntl.ioctl(self.fd, UI_SET_PROPBIT, prop)
            if phys:
                # UI_SET_PHYS takes a pointer to the string, which fcntl.ioctl can't pass.
                if _libc.ioctl(self.fd, ctypes.c_ulong(UI_SET_PHYS), ctypes.c_char_p(phys.encode())) < 0:
                    raise OSError(ctypes.get_errno(), "UI_SET_PHYS failed")
            for code, info in (absinfo or {}).items():
                fcntl.ioctl(self.fd, UI_ABS_SETUP, struct.pack("Hxx", code) + ABSINFO.pack(*info))
            setup = INPUT_ID.pack(bustype, vendor, product, version) + name.encode()[:79].ljust(80, b"\0") + struct.pack("I", 0)
            fcntl.ioctl(self.fd, UI_DEV_SETUP, setup)
            fcntl.ioctl(self.fd, UI_DEV_CREATE)
        except OSError:
            os.close(self.fd)
            raise

    def write(self, kind, code, value):
        os.write(self.fd, EVENT.pack(0, 0, kind, code, value))

    def write_event(self, ev):
        self.write(ev.type, ev.code, ev.value)

    def syn(self):
        self.write(e.EV_SYN, e.SYN_REPORT, 0)

    def close(self):
        if self.fd >= 0:
            try:
                fcntl.ioctl(self.fd, UI_DEV_DESTROY)
            except OSError:
                pass
            os.close(self.fd)
            self.fd = -1
