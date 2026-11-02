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

    def test_defaults_to_button_mode(self):
        s = self.settings(self.BASE)
        self.assertTrue(s.lock)
        self.assertEqual(s.mode, "button")
        self.assertEqual(s.apps, {"blender"})  # compared in lower case

    def test_modes_are_read(self):
        for mode in ("hover", "tap", "button"):
            self.assertEqual(self.settings({**self.BASE, "areaActivate": mode}).mode, mode)
        self.assertEqual(self.settings({**self.BASE, "areaActivate": "bogus"}).mode, "button")

    def test_button_mode_needs_no_app_list(self):
        self.assertTrue(self.settings({**self.BASE, "apps": {}}).lock)
        self.assertFalse(self.settings({**self.BASE, "apps": {}, "areaActivate": "tap"}).lock)

    def test_lock_is_off_without_apps_or_while_rotated_a_quarter_turn(self):
        self.assertFalse(self.settings({**self.BASE, "apps": {}, "areaActivate": "hover"}).lock)
        self.assertFalse(self.settings({**self.BASE, "rotation": 90}).lock)
        self.assertFalse(self.settings({**self.BASE, "areaLock": False}).lock)


class Pinned(unittest.IsolatedAsyncioTestCase):
    """Button mode: the key locks the pen to a window, and focus moving away does not release it."""

    def setUp(self):
        self.a = tabletarea.AreaLock()
        self.a.settings = lambda: tabletarea.Settings(True, set(), "pen", PTH650, "button")
        self.log = []
        self.clients = [{"address": "0xa", "class": "krita", "at": [0, 0], "size": [800, 600],
                         "mapped": True, "workspace": {"id": 1}},
                        {"address": "0xb", "class": "foot", "at": [800, 0], "size": [800, 600],
                         "mapped": True, "workspace": {"id": 1}}]
        self.focused = self.clients[0]

        async def run_cmd(*args, timeout=5):
            return json.dumps({"activewindow": self.focused, "clients": self.clients,
                               "monitors": [{"activeWorkspace": {"id": 1}, "specialWorkspace": {"id": 0}}]}[args[1]])

        async def apply(pen, options):
            self.log.append("apply")

        async def release(pen=None):
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

    async def test_window_on_another_workspace_releases_it(self):
        await self.a.evaluate()
        self.a.toggle()
        await self.a.evaluate()
        self.clients[0]["workspace"]["id"] = 2
        await self.a.evaluate()
        self.assertEqual(self.log, ["apply", "release"])


if __name__ == "__main__":
    unittest.main()
