"""
The canvas applies the model's placement rules (model/rules.py). Needs wx, so
run with KiCad's Python; the window is created but never shown.
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
from breadboard.model.breadboard import Breadboard, TieHole  # noqa: E402
from breadboard.model.components import ALL_DEFS  # noqa: E402


class CanvasRulesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = wx.App(False)

    def _canvas(self, layout):
        frame = wx.Frame(None)
        self.addCleanup(frame.Destroy)
        return BreadboardCanvas(frame, Breadboard(layout=layout))

    def test_dip_past_the_right_edge_is_rejected(self):
        canvas = self._canvas('full')
        self.assertEqual(canvas.layout.board_layout, 'full')
        dip8 = ALL_DEFS['DIP8']
        inside = canvas._resolve_pin_holes(dip8, TieHole(10, 'e'), 0)
        past = canvas._resolve_pin_holes(dip8, TieHole(62, 'e'), 0)   # pins reach col 65
        self.assertTrue(canvas._pin_holes_valid(inside))
        self.assertFalse(canvas._pin_holes_valid(past))


class RenderTest(unittest.TestCase):
    """cli.py render must leave out the screen-space net legend: it is placed
    by window size and drawn semi-transparent, which a plain bitmap renders
    as an opaque white box."""

    @classmethod
    def setUpClass(cls):
        cls.app = wx.App(False)

    def test_render_omits_the_screen_space_legend(self):
        import tempfile
        from breadboard import cli
        from breadboard.headless import Session
        from breadboard.prefs import Preferences

        tmp = tempfile.mkdtemp()
        s = Session.create(os.path.join(tmp, 'd.kicad_bbrd'),
                           os.path.join(HERE, 'fixtures', 'demo.net'), prefs=Preferences())
        s.place('U1', anchor='e20')      # its NC pins are single-endpoint nets → legend rows

        def boom(self, dc):
            raise AssertionError('net legend drawn')
        orig = BreadboardCanvas._draw_net_labels
        BreadboardCanvas._draw_net_labels = boom
        try:
            cli._render(s, os.path.join(tmp, 'b.png'), with_check=True)
        finally:
            BreadboardCanvas._draw_net_labels = orig


if __name__ == '__main__':
    unittest.main()
