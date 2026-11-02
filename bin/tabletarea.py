"""Lock the pen to the window of a chosen app.

Runs inside tablet-buttons. When "areaLock" is on in
~/.config/omarchy/tablet.json and the focused window's class is listed in
"apps", the pen is mapped onto that window (Hyprland's region_position /
region_size) using the largest centred part of the tablet with the window's
aspect ratio, so circles stay round. When focus moves to any other window the
pen goes back to the normal mapping from tablet.json (not in button mode, see below).

"areaActivate" decides what engages the lock. "button" (default): only the
"arealock" key or pen button, which locks the pen to the focused window, listed
or not, and releases it again. That lock stays on that window, whatever has focus
(focus follows the mouse, so it can wander), until the key is pressed again, the
window closes or it leaves the screen. "hover": focus alone on a listed app, which the
pen gives by moving the cursor. "tap": the first tap of the pen tip on a listed
app engages the lock once the tip lifts. The tap itself reaches the
app (a dot, a click): the pen is not grabbed for it, so there is no virtual pen
and no lag. The mapping never changes while the tip is down, so the pointer
cannot jump into the middle of a stroke and draw a line.

Hyprland tells us about focus, moves, fullscreen and closed windows over its
event socket, so nothing is polled while the pen is free. It sends no event
for a resize, so while locked the window is re-read twice a second.
"""
import asyncio
import glob
import json
import os
import sys
from collections import namedtuple

HOME = os.path.expanduser("~")
STATE = os.path.join(HOME, ".config/omarchy/tablet.json")
CTL = os.path.join(os.path.dirname(os.path.realpath(__file__)), "tablet-ctl")

POLL = 0.5   # seconds between window checks while locked
IDLE = 2.0   # seconds between settings-file checks while free
EVENTS = {"activewindowv2", "closewindow", "fullscreen", "movewindowv2", "workspacev2",
          "focusedmonv2", "changefloatingmode", "configreloaded"}


def log(*args):
    print("area:", *args, file=sys.stderr, flush=True)


def parse_desktop(text, file_id):
    """The launcher fields the app list needs from a .desktop file, or None when it is no drawing app.

    A drawing app is a visible application in the Graphics menu category. Its window class is the
    launcher's StartupWMClass when it has one; otherwise the file name stands in as a guess, which
    `guessed` flags so the panel can say that the class may need correcting from an open window."""
    fields = {}
    in_entry = False
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("["):
            in_entry = line == "[Desktop Entry]"
        elif in_entry and "=" in line and not line.startswith("#"):
            key, _, value = line.partition("=")
            fields.setdefault(key.strip(), value.strip())  # first one wins: later ones are translations
    if fields.get("Type") != "Application" or "Graphics" not in fields.get("Categories", "").split(";"):
        return None
    if fields.get("NoDisplay", "").lower() == "true" or fields.get("Hidden", "").lower() == "true":
        return None
    wm_class = fields.get("StartupWMClass", "")
    return {"name": fields.get("Name") or file_id, "class": wm_class or file_id,
            "icon": fields.get("Icon", ""), "guessed": not wm_class}


def installed_apps(dirs=None):
    """Drawing apps with a launcher, sorted by name. Earlier directories win, so a user's own
    launcher beats the system one with the same class."""
    if dirs is None:
        data_home = os.environ.get("XDG_DATA_HOME") or os.path.join(HOME, ".local/share")
        data_dirs = (os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share").split(":")
        dirs = [os.path.join(d, "applications") for d in [data_home, *data_dirs] if d]
    found = {}
    for directory in dirs:
        for path in sorted(glob.glob(os.path.join(directory, "*.desktop"))):
            try:
                with open(path, encoding="utf-8", errors="replace") as f:
                    app = parse_desktop(f.read(), os.path.basename(path)[:-len(".desktop")])
            except OSError:
                continue
            if app:
                found.setdefault(app["class"].lower(), app)
    return sorted(found.values(), key=lambda a: a["name"].lower())


def socket_path():
    runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    signature = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
    if signature:
        return f"{runtime}/hypr/{signature}/.socket2.sock"
    found = sorted(glob.glob(f"{runtime}/hypr/*/.socket2.sock"), key=os.path.getmtime)
    return found[-1] if found else None


async def run_cmd(*args, timeout=5):
    """Stdout of a command, or "" when it fails or times out."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
        return out.decode(errors="replace")
    except (OSError, asyncio.TimeoutError):
        return ""


Settings = namedtuple("Settings", "lock apps pen size mode")


class AreaLock:
    def __init__(self):
        self.rect = None        # what the pen is locked to now, None = free
        self.tip_down = False   # the pen tip touches the tablet: the mapping must not change now
        self.tapped = False     # the tip lifted since the last look: that was a tap on the focused window
        self.focus = None       # class of the focused listed app, if any
        self.focus_addr = None  # address of that window
        self.pinned = None      # button mode: address of the window the key locked the pen to
        self.active = None      # tap mode: class the pen was activated on
        self.hold = None        # class the pen was released from by hand; hover mode keeps it free until focus leaves
        self.dirty = asyncio.Event()
        self.busy = asyncio.Lock()  # one evaluation at a time: the event loop and a tap can both ask
        self.mtime = None

    def settings(self):
        """The settings from tablet.json: whether the area lock is on, the app classes (lower
        case), the pen device, the tablet size in mm and the activation mode ("button", "hover"
        or "tap")."""
        try:
            with open(STATE) as f:
                data = json.load(f)
        except (OSError, ValueError):
            return Settings(False, set(), None, None, "button")
        last = data.get("lastDevice") or {}
        apps = {k.lower() for k, v in (data.get("apps") or {}).items()
                if isinstance(v, dict) and v.get("mode", "window") == "window"}
        size = last.get("sizeMm")
        mode = data.get("areaActivate")
        mode = mode if mode in ("hover", "tap") else "button"
        # The button locks any window, so it needs no app list.
        lock = data.get("areaLock") is True and (bool(apps) or mode == "button") \
            and bool(last.get("pen")) and bool(size)
        # A quarter-turn tablet is not handled yet.
        if data.get("rotation", 0) in (90, 270):
            lock = False
        return Settings(lock, apps, last.get("pen"), size, mode)

    def pen_touch(self, down):
        """The pen tip touched the tablet or left it. A lift is a tap on whatever has focus; the
        lock engages then, never while the tip is down."""
        if down:
            self.tip_down = True
        elif self.tip_down:
            self.tip_down = False
            self.tapped = True
            self.dirty.set()

    def toggle(self):
        """The "arealock" key: let the pen go, or lock it to the focused listed app again.

        Letting go is what lets a pen-only user leave the window without the mouse. The pen is
        still over the app when the key is pressed, so in hover mode, where focus alone locks,
        `hold` stops the lock from coming straight back; it lasts until focus moves elsewhere.
        In tap and button mode locking is an explicit choice, so nothing needs holding. In button
        mode the lock is to the window, not to its class, so it survives a change of focus."""
        if self.rect is not None or self.pinned:
            self.active = None
            self.pinned = None
            self.hold = self.focus
        elif self.focus:
            self.hold = None
            self.active = self.focus
            self.pinned = self.focus_addr
        self.dirty.set()

    def reapply(self):
        """Something sent the pen's settings to Hyprland again, which drops the lock: apply it anew."""
        self.rect = None
        self.dirty.set()

    @staticmethod
    def target(window, size):
        """Window rectangle plus the matching centred part of the tablet, in mm."""
        x, y = window["at"]
        w, h = window["size"]
        tw, th = size
        if w <= 0 or h <= 0:
            return None
        if w / h > tw / th:
            aw, ah = tw, tw * h / w
        else:
            aw, ah = th * w / h, th
        return (x, y, w, h, round(aw, 2), round(ah, 2), round((tw - aw) / 2, 2), round((th - ah) / 2, 2))

    async def evaluate(self):
        async with self.busy:
            await self._evaluate()

    async def _evaluate(self):
        lock, apps, pen, size, mode = self.settings()
        want = None
        window = {}
        self.focus = None
        if not lock or mode != "button":
            self.pinned = None
        if lock and self.pinned:
            # The key locked the pen to this window: focus does not matter, only that it is still shown.
            pinned = await self.pinned_window()
            if pinned:
                want = self.target(pinned, size)
                window = pinned
            else:
                self.pinned = None
        if lock and not self.pinned:
            try:
                window = json.loads(await run_cmd("hyprctl", "activewindow", "-j") or "{}")
            except ValueError:
                window = {}
            cls = window.get("class", "").lower()
            if cls and (mode == "button" or cls in apps) and window.get("mapped", True):
                self.focus = cls
                self.focus_addr = window.get("address")
                if self.tapped and mode == "tap":
                    self.active = self.focus  # a tap on a listed app is the choice to lock
                    self.hold = None
                # Button mode locks through `pinned`, above, not through focus.
                if (mode == "hover" and self.hold != self.focus) or (mode == "tap" and self.active == self.focus):
                    want = self.target(window, size)
        self.tapped = False
        if self.focus != self.active:
            self.active = None  # focus left the app the tap activated
        if self.focus != self.hold:
            self.hold = None    # focus left the app the pen was released from
        if want == self.rect:
            return
        if self.tip_down:
            return  # a jump mid-stroke would draw a line; the lift asks again
        if want:
            await self.apply(pen, {
                "active_area_size": f"{want[4]} {want[5]}",
                "active_area_position": f"{want[6]} {want[7]}",
                "region_position": f"{want[0]} {want[1]}",
                "region_size": f"{want[2]} {want[3]}",
                "absolute_region_position": "true",
            })
            if self.rect is None:
                log("locked to", window.get("class"))
        else:
            await self.release(pen)
        self.rect = want

    async def pinned_window(self):
        """The window the button locked the pen to, or None once it has closed or left the screen."""
        try:
            clients = json.loads(await run_cmd("hyprctl", "clients", "-j") or "[]")
            monitors = json.loads(await run_cmd("hyprctl", "monitors", "-j") or "[]")
        except ValueError:
            return None
        window = next((c for c in clients if c.get("address") == self.pinned), None)
        if not window or not window.get("mapped", True) or window.get("hidden"):
            return None
        shown = {m.get("activeWorkspace", {}).get("id") for m in monitors}
        shown |= {m.get("specialWorkspace", {}).get("id") for m in monitors}
        return window if window.get("workspace", {}).get("id") in shown else None

    async def apply(self, pen, options):
        body = ", ".join(f'{k} = {v}' if v in ("true", "false") else f'{k} = "{v}"'
                         for k, v in options.items())
        for name in (pen, pen + "-remap"):  # tablet-buttons' clone while a pen button is remapped
            await run_cmd("hyprctl", "eval", f'hl.device({{ name = "{name}", {body} }})')

    async def release(self, pen=None):
        """Back to the mapping the settings describe, as `tablet-ctl status` reports it."""
        if self.rect is None:
            return
        pen = pen or self.settings().pen
        if not pen:
            return
        area = {}
        try:
            area = json.loads(await run_cmd(sys.executable, CTL, "status", timeout=10)).get("area") or {}
        except ValueError:
            pass
        options = {
            "active_area_size": f'{area.get("w", 0)} {area.get("h", 0)}',
            "active_area_position": f'{area.get("x", 0)} {area.get("y", 0)}',
            "region_position": "0 0",
            "region_size": "0 0",
            "absolute_region_position": "false",
        }
        await self.apply(pen, options)
        self.rect = None
        log("released")

    async def listen(self):
        while True:
            path = socket_path()
            try:
                if path:
                    reader, _ = await asyncio.open_unix_connection(path)
                    self.dirty.set()
                    while line := await reader.readline():
                        if line.split(b">>", 1)[0].decode(errors="ignore") in EVENTS:
                            if line.startswith(b"configreloaded"):
                                self.rect = None  # the reload dropped our override; apply it again
                            self.dirty.set()
            except (OSError, ValueError):
                pass
            await asyncio.sleep(2)

    async def run(self):
        listener = asyncio.create_task(self.listen())
        self.dirty.set()
        try:
            while True:
                timed_out = False
                try:
                    await asyncio.wait_for(self.dirty.wait(), POLL if self.rect else IDLE)
                except asyncio.TimeoutError:
                    timed_out = True
                self.dirty.clear()
                try:
                    mtime = os.stat(STATE).st_mtime
                except OSError:
                    mtime = 0
                if mtime != self.mtime:
                    if self.mtime is not None and self.rect:
                        # tablet-ctl writes the file, then sends every setting to Hyprland,
                        # which resets the pen's area: wait for that, then lock again.
                        await asyncio.sleep(0.5)
                        self.rect = None
                elif timed_out and not self.rect:
                    continue
                self.mtime = mtime
                await self.evaluate()
        finally:
            listener.cancel()
            await self.release()
