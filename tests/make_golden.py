"""
Capture reference ("golden") data from the ORIGINAL canvas implementation,
before the placement rules are moved into the model. tests/test_rules.py then
checks the model against these files, so the move is proven behaviour-neutral.

Run once, against the unmodified upstream canvas.py (captured at v1.2.21-2-gb1c49dc;
it calls canvas methods that the move removed, so it no longer runs on this
branch — check out that commit to regenerate), with KiCad's Python:
    "C:\\Program Files\\KiCad\\10.0\\bin\\python.exe" tests/make_golden.py

One deliberate deviation is baked in: on the standard layouts CanvasLayout's
hole_xy() accepts rail indices up to RAIL_LEN (50) even where only rail_len
holes are drawn (24 on 'half'), so a click can snap to an invisible hole. The
golden keeps only the drawn rail holes. Likewise hole_xy() does not bound
tie columns on the standard layouts (a DIP near the right edge renders off the
board), so the golden starts from all_holes(), the canvas's own enumeration.

The sunny-11 DIP golden keeps one DIP per distinct pin geometry, in a compact
"<row><col>@<section>" form, and records the lenient result only where the
strict one raised (elsewhere they are identical by construction).
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'plugins'))
# Block pcbnew so breadboard/__init__.py skips registering the action plugin:
# outside a running KiCad it fails the PgmOrNull() assert, and on Windows that
# assert is a modal dialog that blocks the script until someone clicks it.
sys.modules['pcbnew'] = None

from breadboard.canvas import CanvasLayout, BreadboardCanvas  # noqa: E402
from breadboard.model.breadboard import (  # noqa: E402
    TieHole, RailHole, Terminal, ALL_ROWS, RAIL_NAMES, TERMINAL_NAMES,
)
from breadboard.model.components import ALL_DEFS  # noqa: E402
from breadboard.model.session import _hole_to_json  # noqa: E402

LAYOUTS = ('mini', 'half', 'full', 'double', 'triple', 'double_rails', 'sunny-11')
POST_SIDES = ('left', 'right', 'top_left', 'top_center', 'top_right',
              'bottom_left', 'bottom_center', 'bottom_right')
GOLDEN = os.path.join(HERE, 'golden')


def existing_holes():
    out = {}
    for layout in LAYOUTS:
        lay = CanvasLayout(layout)
        holes = []
        for h in lay.all_holes():
            if not isinstance(h, (TieHole, RailHole)):
                continue    # terminals: active_terminals(); module pins: not board holes
            if (layout != 'sunny-11' and isinstance(h, RailHole)
                    and h.rail in RAIL_NAMES and h.index > lay.rail_len):
                continue    # the deliberate deviation — see module docstring
            holes.append(_hole_to_json(h))
        out[layout] = sorted(holes, key=json.dumps)
    return out


def active_terminals():
    out = {}
    for layout in LAYOUTS:
        for side in POST_SIDES:
            for num in range(2, 6):
                lay = CanvasLayout(layout, binding_post_side=side, num_terminals=num)
                out[f'{layout}|{side}|{num}'] = [
                    t for t in TERMINAL_NAMES if lay.hole_xy(Terminal(t)) is not None]
    return out


class _Sunny11DipOnly:
    """Just the three canvas methods the sunny-11 DIP logic uses, lifted off
    BreadboardCanvas so they run without a wx window."""
    _SUNNY11_GUTTERS = BreadboardCanvas._SUNNY11_GUTTERS
    _sunny11_chain_pos = staticmethod(BreadboardCanvas._sunny11_chain_pos)
    _sunny11_gutter_index = BreadboardCanvas._sunny11_gutter_index
    _sunny11_place_dip = BreadboardCanvas._sunny11_place_dip


def _compact(holes):
    return ' '.join(f'{p}:{h.row}{h.col}@{h.section}' for p, h in sorted(holes.items()))


def sunny11_dips():
    fake = _Sunny11DipOnly()
    seen = set()
    dips = []
    for type_id in sorted(t for t, d in ALL_DEFS.items() if d.is_dip):
        geom = tuple(sorted(ALL_DEFS[type_id].pin_offsets.items()))
        if geom not in seen:
            seen.add(geom)
            dips.append(type_id)
    out = {}
    for type_id in dips:
        comp_def = ALL_DEFS[type_id]
        for section in (0, 1):
            for col in range(1, 29):
                for row in ALL_ROWS:
                    for flipped in (0, 1):
                        key = f'{type_id}|{col}|{row}|{section}|{flipped}'
                        anchor = TieHole(col, row, section)
                        try:
                            out[key] = _compact(fake._sunny11_place_dip(
                                anchor, flipped, comp_def, lenient=False))
                        except AssertionError:
                            out[key] = 'error'
                            out[key + '|lenient'] = _compact(fake._sunny11_place_dip(
                                anchor, flipped, comp_def, lenient=True))
    return out


def main():
    os.makedirs(GOLDEN, exist_ok=True)
    for name, fn in (('existing_holes', existing_holes),
                     ('active_terminals', active_terminals),
                     ('sunny11_dips', sunny11_dips)):
        data = fn()
        with open(os.path.join(GOLDEN, name + '.json'), 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=0, sort_keys=True)
        print(f'{name}: {len(data)} entries')


if __name__ == '__main__':
    main()
