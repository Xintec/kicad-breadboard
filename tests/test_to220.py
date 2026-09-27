"""
Transistors in a vertical TO-220 (footprint TO-220-3_Vertical) keep their
electrical type and gain the package: 'NMOS_GDS:TO220'. KiCad's outline puts
the body 3.4 mm behind the pin line (the tab: covers the next row), 1.5 mm in
front (covers nothing) and 2.71 mm past each end pin (covers the next column).
Needs wx for the drawing test; run with KiCad's Python:
    "C:\\Program Files\\KiCad\\10.0\\bin\\python.exe" -m unittest discover -s tests
"""
from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'plugins'))
sys.modules['pcbnew'] = None    # see make_golden.py: avoids KiCad's modal assert

import wx  # noqa: E402

from breadboard.canvas import BreadboardCanvas  # noqa: E402
from breadboard.model import rules  # noqa: E402
from breadboard.model.breadboard import Breadboard, PlacedComponent, TieHole  # noqa: E402
from breadboard.model.components import ALL_DEFS, TO92_PINOUT_VARIANTS, guess_type_id  # noqa: E402
from breadboard.model.simulation import _element_line  # noqa: E402

TO220 = 'Package_TO_SOT_THT:TO-220-3_Vertical'


def _guess(props):
    return guess_type_id('Q1', 'IRLZ44N', 'Q_NMOS_GDS', 'BikainGarden', 'N-MOSFET', 3, props)


class To220Test(unittest.TestCase):
    def test_type_carries_the_package(self):
        tid = _guess({'Footprint': TO220, 'Pin.Functions': '1=G 2=D 3=S'})
        self.assertEqual(tid, 'NMOS_GDS:TO220')
        d = ALL_DEFS[tid]
        self.assertEqual((d.body, d.package), ((1, 1, 1, 0), 'TO220'))
        self.assertEqual(d.pin_names, ALL_DEFS['NMOS_GDS'].pin_names)
        self.assertEqual(_guess({'Footprint': TO220, 'Sim.Pins': '1=G 2=S 3=D'}), 'NMOS:TO220')

    def test_other_packages_unchanged(self):
        self.assertEqual(_guess({'Footprint': 'Package_TO_SOT_THT:TO-92_Inline',
                                 'Pin.Functions': '1=G 2=D 3=S'}), 'NMOS_GDS')

    def test_pinout_variants_follow_the_electrical_type(self):
        self.assertEqual(TO92_PINOUT_VARIANTS['NMOS_GDS:TO220'], TO92_PINOUT_VARIANTS['NMOS_GDS'])
        self.assertEqual(TO92_PINOUT_VARIANTS['NMOS:TO220'], TO92_PINOUT_VARIANTS['NMOS'])

    def test_tab_covers_the_row_behind_and_a_column_past_each_end(self):
        d = ALL_DEFS['NMOS_GDS:TO220']
        holes = d.place(TieHole(10, 'c'))
        self.assertEqual(rules.covered_holes('full', d, holes, 0),
                         {TieHole(c, 'b') for c in range(9, 14)} | {TieHole(9, 'c'), TieHole(13, 'c')})
        # turned 180°: the tab faces row j
        self.assertEqual({h.row for h in rules.covered_holes('full', d, d.place(TieHole(12, 'c'), 2), 2)},
                         {'c', 'd'})

    def test_simulates_as_its_electrical_type(self):
        for tid in ('NMOS:TO220', 'NMOS_GDS:TO220'):
            with self.subTest(tid=tid):
                line = _element_line('Q1', tid, {1: 'g', 2: 'd', 3: 's'}, 'IRLZ44N')
                self.assertTrue(line and line.startswith('M'), line)
        # the pin order still comes from the electrical type
        fields = _element_line('Q1', 'NMOS_GDS:TO220', {1: 'g', 2: 'd', 3: 's'}, '').split()
        self.assertEqual(fields[1:4], ['d', 'g', 's'])      # drain, gate, source


class To220DrawingTest(unittest.TestCase):
    def test_body_is_drawn_over_what_it_covers(self):
        app = wx.App(False)  # noqa: F841
        frame = wx.Frame(None)
        self.addCleanup(frame.Destroy)
        board = Breadboard(layout='full')
        d = ALL_DEFS['NMOS_GDS:TO220']
        board.place(PlacedComponent('Q1', d.type_id, d.place(TieHole(10, 'c'))))
        canvas = BreadboardCanvas(frame, board)
        img = canvas.render_to_bitmap(include_net_labels=False).ConvertToImage()

        def px(hole):
            x, y = canvas.layout.hole_xy(hole)
            return '#%02x%02x%02x' % (img.GetRed(x, y), img.GetGreen(x, y), img.GetBlue(x, y))
        hole_colour = px(TieHole(40, 'b'))                  # an empty hole, for reference
        for h in (TieHole(11, 'b'), TieHole(9, 'c'), TieHole(13, 'b')):
            self.assertNotEqual(px(h), hole_colour, h)
        self.assertEqual(px(TieHole(11, 'd')), hole_colour)  # the front covers nothing


if __name__ == '__main__':
    unittest.main()
