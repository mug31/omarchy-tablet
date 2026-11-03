"""Checks for the pure parts of bin/tabletarea.py. Run: python3 -m unittest discover tests"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin"))
import tabletarea  # noqa: E402

PTH650 = (223.52, 139.7)  # Intuos5 touch M, mm


def window(w, h, x=0, y=0):
    return {"at": [x, y], "size": [w, h]}


class Target(unittest.TestCase):
    def check(self, win, size=PTH650):
        x, y, w, h, aw, ah, ax, ay = tabletarea.AreaLock.target(win, size)
        # The pen has to move the same distance on screen per mm in both directions,
        # which is what keeps a drawn circle round.
        self.assertAlmostEqual(w / aw, h / ah, delta=0.02 * w / aw)
        # The area stays on the tablet.
        self.assertGreaterEqual(ax, 0)
        self.assertGreaterEqual(ay, 0)
        self.assertLessEqual(ax + aw, size[0] + 0.01)
        self.assertLessEqual(ay + ah, size[1] + 0.01)
        return aw, ah, ax, ay

    def test_wider_window_uses_full_width(self):
        # PhotoCraft's tiled window on the test machine: 2536x1416, aspect 1.79 against 1.60.
        aw, ah, ax, ay = self.check(window(2536, 1416, 5132, 12))
        self.assertEqual(aw, PTH650[0])
        self.assertAlmostEqual(ah, 124.8, delta=0.1)
        self.assertAlmostEqual(ay, 7.45, delta=0.05)

    def test_taller_window_uses_full_height(self):
        aw, ah, ax, ay = self.check(window(1000, 1400))
        self.assertEqual(ah, PTH650[1])
        self.assertLess(aw, PTH650[0])
        self.assertGreater(ax, 0)

    def test_same_aspect_uses_whole_tablet(self):
        aw, ah, ax, ay = self.check(window(1600, 1000))
        self.assertAlmostEqual(aw, PTH650[0], delta=0.1)
        self.assertAlmostEqual(ah, PTH650[1], delta=0.1)

    def test_empty_window_is_refused(self):
        self.assertIsNone(tabletarea.AreaLock.target(window(0, 100), PTH650))


PHOTOCRAFT = """[Desktop Entry]
Type=Application
Name=PhotoCraft
Name[de]=FotoCraft
Categories=Graphics;RasterGraphics;
StartupWMClass=ai.storyteller.photocraft
Icon=photocraft
"""


class Scan(unittest.TestCase):
    def test_graphics_app_with_wm_class(self):
        app = tabletarea.parse_desktop(PHOTOCRAFT, "ai.storyteller.photocraft")
        self.assertEqual(app["name"], "PhotoCraft")  # not the German translation
        self.assertEqual(app["class"], "ai.storyteller.photocraft")
        self.assertFalse(app["guessed"])

    def test_missing_wm_class_is_guessed_from_the_file_name(self):
        text = "[Desktop Entry]\nType=Application\nName=Blender\nCategories=Graphics;3DGraphics;\n"
        app = tabletarea.parse_desktop(text, "blender")
        self.assertEqual(app["class"], "blender")
        self.assertTrue(app["guessed"])

    def test_other_categories_and_hidden_launchers_are_skipped(self):
        self.assertIsNone(tabletarea.parse_desktop("[Desktop Entry]\nType=Application\nName=Kitty\nCategories=System;\n", "kitty"))
        hidden = PHOTOCRAFT + "NoDisplay=true\n"
        self.assertIsNone(tabletarea.parse_desktop(hidden, "x"))
        # Fields outside [Desktop Entry] (actions) must not count.
        action = "[Desktop Entry]\nType=Application\nName=A\n[Desktop Action x]\nCategories=Graphics;\n"
        self.assertIsNone(tabletarea.parse_desktop(action, "a"))

    def test_user_launcher_beats_system_one(self):
        import tempfile
        with tempfile.TemporaryDirectory() as user, tempfile.TemporaryDirectory() as system:
            for directory, name in ((user, "PhotoCraft (mine)"), (system, "PhotoCraft")):
                with open(os.path.join(directory, "photocraft.desktop"), "w") as f:
                    f.write(PHOTOCRAFT.replace("Name=PhotoCraft", f"Name={name}"))
            apps = tabletarea.installed_apps([user, system])
        self.assertEqual([a["name"] for a in apps], ["PhotoCraft (mine)"])


class SettingsFile(unittest.TestCase):
    """AreaLock.settings() reads tablet.json; point it at a temporary one."""

    def settings(self, data):
        import json
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "tablet.json")
            with open(path, "w") as f:
                json.dump(data, f)
            saved, tabletarea.STATE = tabletarea.STATE, path
            try:
                return tabletarea.AreaLock().settings()
            finally:
                tabletarea.STATE = saved

    BASE = {"areaLock": True, "apps": {"Blender": {"mode": "window"}},
            "lastDevice": {"pen": "wacom-pen", "sizeMm": [223.5, 139.7]}}

    def test_defaults_are_button_without_ignoring(self):
        s = self.settings(self.BASE)
        self.assertTrue(s.lock)
        self.assertEqual(s.mode, "button")
        self.assertFalse(s.ignore)
        self.assertEqual(s.apps, {"blender"})  # compared in lower case

    def test_modes_are_read(self):
        for mode in ("hover", "tap", "button"):
            self.assertEqual(self.settings({**self.BASE, "areaActivate": mode}).mode, mode)
        self.assertEqual(self.settings({**self.BASE, "areaActivate": "bogus"}).mode, "button")

    def test_button_mode_needs_no_app_list(self):
        self.assertTrue(self.settings({**self.BASE, "apps": {}}).lock)
        self.assertFalse(self.settings({**self.BASE, "apps": {}, "areaActivate": "tap"}).lock)

    def test_lock_is_off_without_apps_or_when_switched_off(self):
        self.assertFalse(self.settings({**self.BASE, "apps": {}, "areaActivate": "hover"}).lock)
        self.assertFalse(self.settings({**self.BASE, "areaLock": False}).lock)

    def test_a_quarter_turn_locks_on_the_swapped_tablet(self):
        upright = self.settings(self.BASE)
        self.assertFalse(upright.quarter)
        self.assertEqual(list(upright.size), [223.5, 139.7])
        for rotation in (90, 270):
            s = self.settings({**self.BASE, "rotation": rotation})
            self.assertTrue(s.lock)
            self.assertTrue(s.quarter)
            self.assertEqual(list(s.size), [139.7, 223.5])
        self.assertFalse(self.settings({**self.BASE, "rotation": 180}).quarter)  # flipped needs no swap

    def test_ignoring_taps_is_off_by_default_and_independent_of_the_lock(self):
        self.assertFalse(self.settings(self.BASE).ignore)
        # No lock, no apps, a quarter-turn tablet: ignoring taps does not depend on any of them.
        s = self.settings({"ignoreTaps": True, "rotation": 90})
        self.assertFalse(s.lock)
        self.assertTrue(s.ignore)

    def test_ignored_apps_start_as_the_file_managers_and_are_the_users_to_change(self):
        s = self.settings({"ignoreTaps": True})
        self.assertIn("org.gnome.nautilus", s.ignored)  # lower case, like the window class it is compared with
        s = self.settings({"ignoreTaps": True, "tapIgnoredApps": ["MyApp"]})
        self.assertEqual(s.ignored, {"myapp"})  # the list replaces the starting one, nothing stays built in


class Pinned(unittest.IsolatedAsyncioTestCase):
    """Button mode: the key locks the pen to a window, and focus moving away does not release it."""

    def setUp(self):
        self.a = tabletarea.AreaLock()
        self.a.settings = lambda: tabletarea.Settings(True, set(), "pen", PTH650, "button", False, set())
        self.a.unsure = False  # these tests start from a pen known to be free
        self.log = []
        self.clients = [{"address": "0xa", "class": "krita", "at": [0, 0], "size": [800, 600],
                         "mapped": True, "workspace": {"id": 1}},
                        {"address": "0xb", "class": "foot", "at": [800, 0], "size": [800, 600],
                         "mapped": True, "workspace": {"id": 1}}]
        self.focused = self.clients[0]

        async def run_cmd(*args, timeout=5):
            return json.dumps({"activewindow": self.focused, "clients": self.clients,
                               "monitors": [{"activeWorkspace": {"id": 1}, "specialWorkspace": {"id": 0}}]}[args[1]])

        async def apply(pen, options, quarter=False):
            self.log.append("apply")

        async def release(pen=None, force=False):
            self.log.append("release")
            self.a.rect = None

        tabletarea.run_cmd, self.saved = run_cmd, tabletarea.run_cmd
        self.a.apply, self.a.release = apply, release

    def tearDown(self):
        tabletarea.run_cmd = self.saved

    async def test_lock_stays_on_its_window_when_focus_moves(self):
        await self.a.evaluate()
        self.a.toggle()
        await self.a.evaluate()
        self.assertEqual(self.log, ["apply"])
        locked = self.a.rect
        self.focused = self.clients[1]  # focus follows the mouse onto the neighbour
        await self.a.evaluate()
        self.assertEqual(self.log, ["apply"])
        self.assertEqual(self.a.rect, locked)

    async def test_key_releases_it_and_the_window_closing_does_too(self):
        await self.a.evaluate()
        self.a.toggle()
        await self.a.evaluate()
        self.a.toggle()
        await self.a.evaluate()
        self.assertEqual(self.log, ["apply", "release"])
        self.a.toggle()  # locks the focused window again
        await self.a.evaluate()
        self.clients.pop(0)
        self.focused = self.clients[0]
        await self.a.evaluate()
        self.assertEqual(self.log, ["apply", "release", "apply", "release"])
        self.assertIsNone(self.a.pinned)

    async def test_tap_guard_still_sees_the_focused_window_while_the_lock_is_pinned(self):
        self.a.settings = lambda: tabletarea.Settings(True, set(), "pen", PTH650, "button", True, {"foot"})
        await self.a.evaluate()
        self.a.toggle()
        self.focused = self.clients[1]  # the lock stays on krita while foot has focus
        await self.a.evaluate()
        self.assertEqual(self.log, ["apply"])
        self.assertTrue(self.a.in_ignored)

    async def test_window_on_another_workspace_releases_it(self):
        await self.a.evaluate()
        self.a.toggle()
        await self.a.evaluate()
        self.clients[0]["workspace"]["id"] = 2
        await self.a.evaluate()
        self.assertEqual(self.log, ["apply", "release"])


if __name__ == "__main__":
    unittest.main()


class QuarterTurn(unittest.IsolatedAsyncioTestCase):
    """At 90/270 the area goes to the turned clone only, and a release turns the area back."""

    def setUp(self):
        self.sent = []

        async def run_cmd(*args, timeout=5):
            if args[0] == "hyprctl":
                self.sent.append(args[-1])
                return ""
            return json.dumps({"area": {"w": 200.0, "h": 125.0, "x": 11.5, "y": 7.3}})

        tabletarea.run_cmd, self.saved = run_cmd, tabletarea.run_cmd

    def tearDown(self):
        tabletarea.run_cmd = self.saved

    async def test_lock_is_sent_to_the_clone_only(self):
        await tabletarea.AreaLock().apply("pen", {"region_size": "800 600"}, True)
        self.assertEqual(len(self.sent), 1)
        self.assertIn('name = "pen-remap"', self.sent[0])

    async def test_upright_lock_goes_to_both_devices(self):
        await tabletarea.AreaLock().apply("pen", {"region_size": "800 600"})
        self.assertEqual(len(self.sent), 2)

    async def test_release_swaps_the_area_for_the_clone(self):
        a = tabletarea.AreaLock()
        a.rect = (0, 0, 1, 1, 1, 1, 0, 0)
        a.settings = lambda: tabletarea.Settings(True, set(), "pen", [139.7, 223.5], "button", False, set(), True)
        await a.release()
        real = [c for c in self.sent if 'name = "pen"' in c]
        self.assertEqual(len(real), 1)
        self.assertIn('active_area_size = "200.0 125.0"', real[0])
        turned = [c for c in self.sent if 'name = "pen-remap"' in c]
        self.assertEqual(len(turned), 2)  # the unturned send, then the turned one that wins
        self.assertIn('active_area_size = "125.0 200.0"', turned[-1])
        self.assertIn('active_area_position = "7.3 11.5"', turned[-1])


class RenderFullArea(unittest.TestCase):
    """A full area is written out, turned for the clone, so no older area survives on a device."""

    def render(self, rotation):
        from importlib.machinery import SourceFileLoader
        ctl = SourceFileLoader("tablet_ctl", os.path.join(os.path.dirname(__file__), "..", "bin", "tablet-ctl")).load_module()
        s = {"rotation": rotation, "output": "", "pressure": 0, "fingerEnabled": True, "fingerSpeed": 0,
             "naturalScroll": False, "tapToClick": True}
        full = {"w": 223.5, "h": 139.7, "x": 0.0, "y": 0.0}
        return ctl.render(s, full, (223.5, 139.7), "pen", None)

    def test_upright_writes_the_full_area_to_both_devices(self):
        text = self.render(0)
        self.assertEqual(text.count('active_area_size = "223.5 139.7"'), 2)

    def test_quarter_turn_writes_it_turned_for_the_clone_only(self):
        for rotation in (90, 270):
            text = self.render(rotation)
            pen, clone = text.split('name = "pen-remap"')
            self.assertIn('active_area_size = "223.5 139.7"', pen)
            self.assertIn('active_area_size = "139.7 223.5"', clone)


class ClearsAStaleLock(unittest.IsolatedAsyncioTestCase):
    """A free pen is cleared once when the device may still hold an old lock, and not again after."""

    def setUp(self):
        self.a = tabletarea.AreaLock()
        self.a.settings = lambda: tabletarea.Settings(False, set(), "pen", PTH650, "button", False, set())
        self.calls = []

        async def run_cmd(*args, timeout=5):
            if args[0] == "hyprctl":
                self.calls.append(args[-1])
                return ""
            return json.dumps({"area": {"w": 223.5, "h": 139.7, "x": 0, "y": 0}})

        tabletarea.run_cmd, self.saved = run_cmd, tabletarea.run_cmd

    def tearDown(self):
        tabletarea.run_cmd = self.saved

    async def test_first_look_clears_it_once(self):
        await self.a._evaluate()
        cleared = [c for c in self.calls if 'region_size = "0 0"' in c]
        self.assertEqual(len(cleared), 2)  # the pen and its clone
        await self.a._evaluate()
        self.assertEqual(len([c for c in self.calls if 'region_size = "0 0"' in c]), 2)

    async def test_a_settings_change_asks_for_another_clear(self):
        await self.a._evaluate()
        self.a.reapply()
        await self.a._evaluate()
        self.assertEqual(len([c for c in self.calls if 'region_size = "0 0"' in c]), 4)
