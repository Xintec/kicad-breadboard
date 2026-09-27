"""
The placement rules that used to live only in canvas.py, now in model/rules.py
so the canvas and the headless CLI share one implementation.

Checked against golden files captured from the original canvas code by
tests/make_golden.py (see its docstring for the two deliberate deviations).

Run with KiCad's Python (no pytest there):
    "C:\\Program Files\\KiCad\\10.0\\bin\\python.exe" -m unittest discover -s tests
"""
from __future__ import annotations

import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'plugins'))
sys.modules['pcbnew'] = None    # see make_golden.py: avoids KiCad's modal assert

from breadboard.model import rules  # noqa: E402
from breadboard.model.breadboard import (  # noqa: E402
    Breadboard, PlacedComponent, TieHole, RailHole, Terminal, ModulePin,
    ALL_ROWS, ALL_RAIL_NAMES,
)
from breadboard.model.components import ALL_DEFS  # noqa: E402
from breadboard.model.session import _hole_to_json  # noqa: E402

LAYOUTS = ('mini', 'half', 'full', 'double', 'triple', 'double_rails', 'sunny-11')


def _golden(name):
    with open(os.path.join(HERE, 'golden', name + '.json'), encoding='utf-8') as f:
        return json.load(f)


def _key(hole):
    return json.dumps(_hole_to_json(hole))


def _candidates():
    """Every tie/rail address any layout could use, and plenty that none do."""
    for section in range(4):
        for col in range(1, 71):
            for row in ALL_ROWS:
                yield TieHole(col, row, section)
        for rail in ALL_RAIL_NAMES:
            for idx in range(1, 71):
                yield RailHole(rail, idx, section)


def _compact(holes):
    return ' '.join(f'{p}:{h.row}{h.col}@{h.section}' for p, h in sorted(holes.items()))


class BoardHolesTest(unittest.TestCase):
    def test_board_holes_match_the_canvas(self):
        golden = _golden('existing_holes')
        for layout in LAYOUTS:
            with self.subTest(layout=layout):
                got = sorted((_hole_to_json(h) for h in rules.board_holes(layout)),
                             key=json.dumps)
                self.assertEqual(got, golden[layout])

    def test_hole_exists_accepts_exactly_the_board_holes(self):
        golden = _golden('existing_holes')
        for layout in LAYOUTS:
            board = Breadboard(layout=layout)
            real = {json.dumps(h) for h in golden[layout]}
            with self.subTest(layout=layout):
                wrong = [repr(h) for h in _candidates()
                         if rules.hole_exists(board, h) != (_key(h) in real)]
                self.assertEqual(wrong[:10], [])

    def test_off_board_column_is_rejected(self):
        # The canvas bug this fixes: on standard layouts a DIP near the right
        # edge used to be accepted with pins past the last column.
        board = Breadboard(layout='full')
        self.assertTrue(rules.hole_exists(board, TieHole(63, 'e')))
        self.assertFalse(rules.hole_exists(board, TieHole(64, 'e')))

    def test_module_pin_exists_only_for_a_placed_module_pin(self):
        board = Breadboard(layout='full')
        nano = next(t for t, d in ALL_DEFS.items() if d.is_module)
        pins = ALL_DEFS[nano].pin_offsets
        board.place(PlacedComponent(ref='MCU1', type_id=nano,
                                    pin_holes={p: ModulePin('MCU1', p) for p in pins}))
        first = min(pins)
        self.assertTrue(rules.hole_exists(board, ModulePin('MCU1', first)))
        self.assertFalse(rules.hole_exists(board, ModulePin('MCU1', 999)))
        self.assertFalse(rules.hole_exists(board, ModulePin('MCU2', first)))


class TerminalsTest(unittest.TestCase):
    def test_active_terminals_match_the_canvas(self):
        for key, expected in _golden('active_terminals').items():
            layout, side, num = key.split('|')
            with self.subTest(key=key):
                self.assertEqual(list(rules.active_terminals(layout, side, int(num))),
                                 expected)

    def test_hole_exists_honours_the_active_terminals(self):
        board = Breadboard(layout='full')
        self.assertTrue(rules.hole_exists(board, Terminal('V2'), terminals=('GND', 'V1', 'V2')))
        self.assertFalse(rules.hole_exists(board, Terminal('V3'), terminals=('GND', 'V1', 'V2')))


class Sunny11DipTest(unittest.TestCase):
    def test_sunny11_dip_placement_matches_the_canvas(self):
        wrong = []
        for key, expected in _golden('sunny11_dips').items():
            parts = key.split('|')
            type_id, col, row, section, flipped = parts[:5]
            lenient = len(parts) == 6
            anchor = TieHole(int(col), row, int(section))
            try:
                got = _compact(rules.resolve_pin_holes(
                    'sunny-11', ALL_DEFS[type_id], anchor, int(flipped), lenient=lenient))
            except AssertionError:
                got = 'error'
            if got != expected:
                wrong.append((key, expected, got))
        self.assertEqual(wrong[:5], [])

    def test_other_layouts_use_the_component_def(self):
        dip8 = ALL_DEFS['DIP8']
        anchor = TieHole(10, 'c')
        self.assertEqual(rules.resolve_pin_holes('full', dip8, anchor, 0),
                         dip8.place(anchor, flipped=0))
        # sunny-11's lower block (section 2) is a plain landscape block too
        self.assertEqual(rules.resolve_pin_holes('sunny-11', dip8, TieHole(10, 'c', 2), 0),
                         dip8.place(TieHole(10, 'c', 2), flipped=0))


if __name__ == '__main__':
    unittest.main()
