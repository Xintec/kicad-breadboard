"""
'rail:~col' names the free hole of a rail nearest to a column, resolved when
the command runs: recipes no longer carry hand-computed rail indices.
Run with KiCad's Python:
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
from breadboard.model import rules  # noqa: E402
from breadboard.model.breadboard import RailHole  # noqa: E402
from breadboard.prefs import Preferences  # noqa: E402

DEMO_NET = os.path.join(HERE, 'fixtures', 'demo.net')


class RailColumnTest(unittest.TestCase):
    def test_rail_hole_columns(self):
        # 5 holes, a gap column, 5 holes… from column 2; split rails skip 2 more after 25
        self.assertEqual([rules.rail_col(i, False) for i in (1, 5, 6, 25, 26, 50)],
                         [2, 6, 8, 30, 32, 60])
        self.assertEqual([rules.rail_col(i, True) for i in (25, 26, 50)], [30, 34, 62])


class NearestRailTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)
        self.s = hl.Session.create(os.path.join(self.dir, 'n.kicad_bbrd'), DEMO_NET,
                                   layout='double', prefs=Preferences(), rail_split=False)

    def test_nearest_free_hole(self):
        self.s.wire('a10', 'top_minus:~10')
        self.assertIsNotNone(self.s.board.wire_at(hl.parse_hole('a10'), RailHole('top_minus', 8)))
        self.s.wire('a11', 'top_minus:~10')             # col 10 taken: col 9 or 11, lower index wins
        self.assertIsNotNone(self.s.board.wire_at(hl.parse_hole('a11'), RailHole('top_minus', 7)))

    def test_on_a_part_and_in_another_section(self):
        placed = self.s.place('R1', pins={1: 'top_plus:~20', 2: 'a20'})
        self.assertEqual(placed.pin_holes[1], RailHole('top_plus', 16))
        placed = self.s.place('R2', pins={1: 'bot_minus:~5@1', 2: 'j5@1'})
        self.assertEqual(placed.pin_holes[1], RailHole('bot_minus', 4, 1))

    def test_both_ends_on_one_rail_get_different_holes(self):
        self.s.wire('top_plus:~20', 'top_plus:~20')
        w = self.s.board.wires[0]
        self.assertNotEqual(w.h1, w.h2)

    def test_split_rails_skip_the_gap(self):
        s = hl.Session.create(os.path.join(self.dir, 's.kicad_bbrd'), DEMO_NET,
                              prefs=Preferences(), rail_split=True)
        s.wire('a33', 'top_plus:~33')
        self.assertEqual(s.board.wires[0].h2, RailHole('top_plus', 26))    # col 34

    def test_only_horizontal_rails(self):
        with self.assertRaises(hl.CliError):
            self.s.wire('a10', 'vert_plus:~10')
        with self.assertRaises(hl.CliError):
            hl.parse_hole('top_plus:~10')              # needs a session: resolved when run

    def test_cli_reports_what_it_chose(self):
        self.s.save()
        cli = os.path.join(HERE, '..', 'plugins', 'breadboard', 'cli.py')
        env = dict(os.environ, APPDATA=self.dir, XDG_CONFIG_HOME=self.dir)
        r = subprocess.run([sys.executable, cli, '--session', self.s.path, 'wire', 'a10',
                            'top_minus:~10'], capture_output=True, text=True, timeout=60, env=env)
        self.assertEqual(json.loads(r.stdout), {'ok': True, 'wire': ['a10', 'top_minus:8']})


if __name__ == '__main__':
    unittest.main()
