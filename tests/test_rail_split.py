"""
Whether the power rails are split in the middle is a property of the physical
board (some are, BikainGarden's X-PROTO-830 is verified not to be), so the
session records it. It used to come only from the user's preferences, and
load_session always rebuilt the board split. Needs wx for the window test;
run with KiCad's Python:
    "C:\\Program Files\\KiCad\\10.0\\bin\\python.exe" -m unittest discover -s tests
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'plugins'))
sys.modules['pcbnew'] = None    # see make_golden.py: avoids KiCad's modal assert

from breadboard import headless as hl  # noqa: E402
from breadboard.model.session import load_session  # noqa: E402
from breadboard.prefs import Preferences  # noqa: E402

DEMO_NET = os.path.join(HERE, 'fixtures', 'demo.net')


class RailSplitTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.path = os.path.join(self.dir, 'r.kicad_bbrd')

    def test_saved_and_restored(self):
        s = hl.Session.create(self.path, DEMO_NET, prefs=Preferences(), rail_split=False)
        s.save()
        self.assertFalse(load_session(self.path)['board'].rail_split)

    def test_the_session_beats_the_preferences(self):
        hl.Session.create(self.path, DEMO_NET, prefs=Preferences(), rail_split=False).save()
        s = hl.Session.open(self.path, prefs=Preferences(rail_split=True))
        self.assertFalse(s.board.rail_split)
        self.assertFalse(s.info()['rail_split'])

    def test_without_one_the_preferences_decide(self):
        hl.Session.create(self.path, DEMO_NET, prefs=Preferences()).save()
        with open(self.path, encoding='utf-8') as f:
            doc = json.load(f)
        doc['board'].pop('rail_split', None)             # an older session file
        with open(self.path, 'w', encoding='utf-8') as f:
            json.dump(doc, f)
        self.assertFalse(hl.Session.open(self.path, prefs=Preferences(rail_split=False))
                         .board.rail_split)
        self.assertTrue(hl.Session.open(self.path, prefs=Preferences(rail_split=True))
                        .board.rail_split)

    def test_continuous_rails_join_both_halves(self):
        s = hl.Session.create(self.path, DEMO_NET, prefs=Preferences(), rail_split=False)
        uf = s.board.build_connectivity()
        self.assertTrue(uf.connected(hl.parse_hole('top_plus:1'), hl.parse_hole('top_plus:50')))

    def test_cli_new_takes_it(self):
        import subprocess
        cli = os.path.join(HERE, '..', 'plugins', 'breadboard', 'cli.py')
        env = dict(os.environ, APPDATA=self.dir, XDG_CONFIG_HOME=self.dir)
        r = subprocess.run([sys.executable, cli, '--session', self.path, '--netlist', DEMO_NET,
                            'new', '--no-rail-split'], capture_output=True, text=True,
                           timeout=60, env=env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(json.loads(r.stdout)['rail_split'])


class WindowRailSplitTest(unittest.TestCase):
    def test_window_adopts_the_session_value(self):
        import wx
        from breadboard.window import BreadboardWindow
        app = wx.App(False)  # noqa: F841
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        path = os.path.join(d, 'r.kicad_bbrd')
        net = os.path.join(d, 'demo.net')
        shutil.copy(DEMO_NET, net)
        hl.Session.create(path, net, prefs=Preferences(), rail_split=False).save()
        win = BreadboardWindow(parent=None)
        win.Hide()
        self.addCleanup(win.Destroy)
        win._on_load(path=path)
        self.assertFalse(win.board.rail_split)
        self.assertFalse(win.canvas.layout.rail_split)


if __name__ == '__main__':
    unittest.main()
