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


class ModuleDrawingTest(unittest.TestCase):
    """Bodies are drawn over the holes they cover, so the picture shows
    which holes are unusable."""

    @classmethod
    def setUpClass(cls):
        cls.app = wx.App(False)

    def _pixel(self, canvas, img, hole):
        x, y = canvas.layout.hole_xy(hole)
        return '#%02x%02x%02x' % (img.GetRed(x, y), img.GetGreen(x, y), img.GetBlue(x, y))

    def _render(self, placements):
        from breadboard.model.breadboard import PlacedComponent
        frame = wx.Frame(None)
        self.addCleanup(frame.Destroy)
        board = Breadboard(layout='full')
        for ref, type_id, anchor, rot in placements:
            d = ALL_DEFS[type_id]
            board.place(PlacedComponent(ref, type_id, d.place(anchor, flipped=rot), flipped=rot))
        canvas = BreadboardCanvas(frame, board)
        return canvas, canvas.render_to_bitmap(include_net_labels=False).ConvertToImage()

    def test_nodemcu_body_covers_rows_between_and_its_overhang(self):
        canvas, img = self._render([('U2', 'NodeMCU_Amica_V2', TieHole(10, 'b'), 0)])
        body = ALL_DEFS['NodeMCU_Amica_V2'].color
        for h in (TieHole(12, 'c'), TieHole(12, 'g'), TieHole(8, 'd')):
            self.assertEqual(self._pixel(canvas, img, h), body, h)
        self.assertNotEqual(self._pixel(canvas, img, TieHole(12, 'a')), body)
        self.assertNotEqual(self._pixel(canvas, img, TieHole(6, 'd')), body)

    def test_terminal_block_body_covers_the_next_rows(self):
        from breadboard.model.components import _type_from_footprint
        _type_from_footprint('TerminalBlock_Xinya_XY308-2.54-3P_1x03', '')   # registers XY308_3
        canvas, img = self._render([('J3', 'XY308_3', TieHole(30, 'c'), 0)])
        self.assertEqual(self._pixel(canvas, img, TieHole(31, 'b')), ALL_DEFS['XY308_3'].color)
        self.assertEqual(self._pixel(canvas, img, TieHole(31, 'd')), ALL_DEFS['XY308_3'].color)

    def test_header_is_drawn(self):
        from breadboard.model.components import _type_from_footprint
        _type_from_footprint('PinHeader_1x03_P2.54mm', '')                  # registers SIP3
        def between_pins(img, canvas):
            x1, y = canvas.layout.hole_xy(TieHole(40, 'c'))
            x2, _ = canvas.layout.hole_xy(TieHole(41, 'c'))
            m = (x1 + x2) // 2
            return '#%02x%02x%02x' % (img.GetRed(m, y), img.GetGreen(m, y), img.GetBlue(m, y))
        empty_canvas, empty = self._render([])
        self.assertNotEqual(between_pins(empty, empty_canvas), ALL_DEFS['SIP3'].color)
        canvas, img = self._render([('JP1', 'SIP3', TieHole(40, 'c'), 0)])
        self.assertEqual(between_pins(img, canvas), ALL_DEFS['SIP3'].color)
        # …as a header, not as the trimpot every other 3-pin part falls back to
        x1, y = canvas.layout.hole_xy(TieHole(40, 'c'))
        x2, _ = canvas.layout.hole_xy(TieHole(42, 'c'))
        screw = [(x, yy) for x in range(x1 - 12, x2 + 13) for yy in range(y - 12, y + 13)
                 if (img.GetRed(x, yy), img.GetGreen(x, yy), img.GetBlue(x, yy)) == (0xd4, 0xa5, 0x20)]
        self.assertEqual(screw, [])


class WideModuleFlipTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = wx.App(False)

    def test_right_click_flip_keeps_the_footprint_in_place(self):
        from breadboard.model.breadboard import PlacedComponent
        frame = wx.Frame(None)
        self.addCleanup(frame.Destroy)
        board = Breadboard(layout='full')
        d = ALL_DEFS['NodeMCU_Amica_V2']
        board.place(PlacedComponent('U2', d.type_id, d.place(TieHole(10, 'b'))))
        canvas = BreadboardCanvas(frame, board)
        before = set(board.get_placement('U2').pin_holes.values())
        canvas._flip_component('U2')
        after = board.get_placement('U2').pin_holes
        self.assertEqual(set(after.values()), before)
        self.assertEqual(after[1], TieHole(24, 'i'))
        canvas._flip_component('U2')
        self.assertEqual(board.get_placement('U2').pin_holes[1], TieHole(10, 'b'))


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
