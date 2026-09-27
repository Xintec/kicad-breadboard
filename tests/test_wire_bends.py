"""
Bent wires from the CLI. The window stores a wire's bend as a canvas point
(Wire.mid_point); the CLI computes it with the same geometry: --bend h (along
first, then across), --bend v (across first), or --via HOLE (corner on that
hole). `bend A B ...` changes an existing wire. Needs wx (the canvas
geometry); run with KiCad's Python:
    "C:\\Program Files\\KiCad\\10.0\\bin\\python.exe" -m unittest discover -s tests
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'plugins'))
sys.modules['pcbnew'] = None    # see make_golden.py: avoids KiCad's modal assert

from breadboard import headless as hl  # noqa: E402
from breadboard.canvas import CanvasLayout  # noqa: E402
from breadboard.model.session import load_session  # noqa: E402
from breadboard.prefs import Preferences  # noqa: E402

DEMO_NET = os.path.join(HERE, 'fixtures', 'demo.net')


class WireBendTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)
        self.prefs = Preferences()
        self.s = hl.Session.create(os.path.join(self.dir, 'w.kicad_bbrd'), DEMO_NET,
                                   prefs=self.prefs, rail_split=False)
        p = self.prefs
        self.lay = CanvasLayout('full', p.binding_post_side, p.show_branding, False,
                                p.num_terminals)

    def xy(self, hole):
        return self.lay.hole_xy(hl.parse_hole(hole))

    def test_along_first(self):
        w = self.s.wire('a10', 'c20', bend='h')
        self.assertEqual(w.mid_point, (self.xy('c20')[0], self.xy('a10')[1]))

    def test_across_first(self):
        w = self.s.wire('a10', 'c20', bend='v')
        self.assertEqual(w.mid_point, (self.xy('a10')[0], self.xy('c20')[1]))

    def test_via_a_hole(self):
        w = self.s.wire('a10', 'c20', via='e15')
        self.assertEqual(w.mid_point, self.xy('e15'))

    def test_a_straight_wire_gets_no_bend(self):
        self.assertIsNone(self.s.wire('a10', 'a20', bend='h').mid_point)

    def test_bend_an_existing_wire_and_clear_it(self):
        self.s.wire('a10', 'c20')
        self.s.bend('c20', 'a10', 'v')                 # either end order
        self.assertEqual(self.s.board.wires[0].mid_point, (self.xy('a10')[0], self.xy('c20')[1]))
        self.s.bend('a10', 'c20', 'none')
        self.assertIsNone(self.s.board.wires[0].mid_point)
        with self.assertRaises(hl.CliError):
            self.s.bend('a10', 'd30', 'h')              # no such wire

    def test_saved(self):
        self.s.wire('a10', 'c20', bend='h')
        self.s.save()
        self.assertEqual(load_session(self.s.path)['board'].wires[0].mid_point,
                         (self.xy('c20')[0], self.xy('a10')[1]))

    def test_cli(self):
        self.s.save()
        cli = os.path.join(HERE, '..', 'plugins', 'breadboard', 'cli.py')
        env = dict(os.environ, APPDATA=self.dir, XDG_CONFIG_HOME=self.dir)
        run = lambda *a: subprocess.run([sys.executable, cli, '--session', self.s.path, *a],
                                        capture_output=True, text=True, timeout=60, env=env)
        r = run('wire', 'a10', 'c20', '--bend', 'h')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        r = run('bend', 'a10', 'c20', 'e15')
        self.assertEqual(json.loads(r.stdout)['ok'], True, r.stdout)
        self.assertEqual(load_session(self.s.path)['board'].wires[0].mid_point, self.xy('e15'))


if __name__ == '__main__':
    unittest.main()
