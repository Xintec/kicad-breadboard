"""
Two-pin single-row parts (SIP2 headers, 2-way XY308 terminal blocks) have a
fixed geometry: adjacent holes, placed from an anchor in one click, turned in
90° steps and drawn as a strip — not placed lead by lead and drawn like a
resistor, as every other two-pin part is. Needs wx; run with KiCad's Python:
    "C:\\Program Files\\KiCad\\10.0\\bin\\python.exe" -m unittest discover -s tests
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'plugins'))
sys.modules['pcbnew'] = None    # see make_golden.py: avoids KiCad's modal assert

import wx  # noqa: E402

from breadboard import headless as hl  # noqa: E402
from breadboard.canvas import BreadboardCanvas  # noqa: E402
from breadboard.model import rules  # noqa: E402
from breadboard.model.breadboard import Breadboard, PlacedComponent, TieHole  # noqa: E402
from breadboard.model.components import ALL_DEFS  # noqa: E402
from breadboard.prefs import Preferences  # noqa: E402

NET = '''(export (version "E")
  (components
    (comp (ref "J13") (value "Flotador") (footprint "TerminalBlock:TerminalBlock_Xinya_XY308-2.54-2P_1x02_P2.54mm_Horizontal")
      (libsource (lib "Connector") (part "Screw_Terminal_01x02") (description "")))
    (comp (ref "R1") (value "10k") (libsource (lib "Device") (part "R") (description ""))))
  (nets
    (net (code "1") (name "/A") (node (ref "J13") (pin "1") (pintype "passive")) (node (ref "R1") (pin "1") (pintype "passive")))
    (net (code "2") (name "/B") (node (ref "J13") (pin "2") (pintype "passive")) (node (ref "R1") (pin "2") (pintype "passive")))))
'''


class ModelTest(unittest.TestCase):
    def test_kinds(self):
        for t in ('SIP2', 'XY308_2'):
            self.assertFalse(ALL_DEFS[t].two_lead, t)
            self.assertTrue(ALL_DEFS[t].quad_rotates, t)
        self.assertTrue(ALL_DEFS['R'].two_lead)
        self.assertFalse(ALL_DEFS['R'].quad_rotates)
        self.assertTrue(ALL_DEFS['NPN'].quad_rotates)
        self.assertFalse(ALL_DEFS['DIP8'].quad_rotates)

    def test_anchor_and_turn(self):
        d = ALL_DEFS['XY308_2']
        self.assertEqual(d.place(TieHole(5, 'c')), {1: TieHole(5, 'c'), 2: TieHole(6, 'c')})
        self.assertEqual(d.place(TieHole(5, 'b'), flipped=1), {1: TieHole(5, 'b'), 2: TieHole(5, 'c')})
        turned = rules.covered_holes('full', d, d.place(TieHole(5, 'b'), flipped=1), 1)
        self.assertEqual(turned, {TieHole(c, r) for c in (4, 6) for r in 'bc'})

    def test_cli_places_it_from_an_anchor(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        net = os.path.join(tmp, 'j.net')
        with open(net, 'w', encoding='utf-8') as f:
            f.write(NET)
        s = hl.Session.create(os.path.join(tmp, 'j.kicad_bbrd'), net, prefs=Preferences())
        self.assertEqual({c['ref']: c['placement'] for c in s.info()['components']},
                         {'J13': 'anchor', 'R1': 'pins'})
        s.place('J13', anchor='a10')
        self.assertEqual(s.board.get_placement('J13').pin_holes,
                         {1: TieHole(10, 'a'), 2: TieHole(11, 'a')})


class CanvasTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = wx.App(False)

    def _canvas(self, board):
        frame = wx.Frame(None)
        self.addCleanup(frame.Destroy)
        return BreadboardCanvas(frame, board)

    def test_placed_in_one_click(self):
        canvas = self._canvas(Breadboard(layout='full'))
        canvas.begin_place(ALL_DEFS['XY308_2'], 'J13')
        x, y = canvas.layout.hole_xy(TieHole(20, 'c'))
        self.assertTrue(canvas._commit_place(x, y))
        self.assertEqual(canvas.board.get_placement('J13').pin_holes,
                         {1: TieHole(20, 'c'), 2: TieHole(21, 'c')})

    def test_drawn_as_a_strip(self):
        board = Breadboard(layout='full')
        d = ALL_DEFS['XY308_2']
        board.place(PlacedComponent('J13', d.type_id, d.place(TieHole(30, 'c'))))
        canvas = self._canvas(board)
        img = canvas.render_to_bitmap(include_net_labels=False).ConvertToImage()
        x, y = canvas.layout.hole_xy(TieHole(31, 'b'))    # covered by the body
        self.assertEqual('#%02x%02x%02x' % (img.GetRed(x, y), img.GetGreen(x, y), img.GetBlue(x, y)),
                         d.color)


if __name__ == '__main__':
    unittest.main()
