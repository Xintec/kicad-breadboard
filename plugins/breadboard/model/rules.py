"""
Placement rules that do not depend on drawing: which holes exist on a layout,
which binding posts are active, and where a DIP's pins land on sunny-11.

These used to live only in canvas.py (CanvasLayout / BreadboardCanvas), which
made them unreachable without a wx window. The canvas now delegates here, so
it and the headless CLI apply the same rules.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Dict, FrozenSet, Iterator, Set, Tuple

from .breadboard import (
    Breadboard, Hole, TieHole, RailHole, Terminal, ModulePin,
    ALL_ROWS, TOP_ROWS, BOT_ROWS, RAIL_NAMES, RAIL_LEN, RAILLESS_LAYOUTS,
    VERT_RAIL_NAMES, VERT_RAIL_NAMES_RIGHT, VERT_RAIL_LEN_PER_SECTION,
    TERMINAL_NAMES, _LAYOUT_PARAMS, COLUMNS,
    SUNNY11_UPPER_COLS, SUNNY11_UPPER_RAIL_LEN, SUNNY11_LOWER_COLS,
    SUNNY11_LOWER_ROWS, SUNNY11_LOWER_RAIL_LEN,
)
from .components import ComponentDef, PHYS_ROW, ROW_AT_PHYS


# ---------------------------------------------------------------------------
# Which holes exist
# ---------------------------------------------------------------------------

def rail_len(layout: str) -> int:
    """Holes drawn per horizontal rail on the standard layouts. 'half' has 24
    (cols 2–29, one column of padding at each end); sunny-11 has its own rails."""
    if layout in RAILLESS_LAYOUTS or layout == 'sunny-11':
        return 0
    if layout == 'half':
        return 24
    columns, _ = _LAYOUT_PARAMS.get(layout, (COLUMNS, 1))
    return min(RAIL_LEN, columns)


def _standard_holes(layout: str) -> Iterator[Hole]:
    columns, sections = _LAYOUT_PARAMS.get(layout, (COLUMNS, 1))
    for section in range(sections):
        for col in range(1, columns + 1):
            for row in ALL_ROWS:
                yield TieHole(col, row, section)
    for section in range(sections):
        for rail in RAIL_NAMES:
            for idx in range(1, rail_len(layout) + 1):
                yield RailHole(rail, idx, section)
    vert = ()
    if layout in ('triple', 'double_rails'):
        vert = VERT_RAIL_NAMES
    if layout == 'double_rails':
        vert += VERT_RAIL_NAMES_RIGHT
    for rail in vert:
        for idx in range(1, sections * VERT_RAIL_LEN_PER_SECTION + 1):
            yield RailHole(rail, idx)


def _sunny11_holes() -> Iterator[Hole]:
    for section in (0, 1):
        for col in range(1, SUNNY11_UPPER_COLS + 1):
            for row in ALL_ROWS:
                yield TieHole(col, row, section)
        for idx in range(1, SUNNY11_UPPER_RAIL_LEN + 1):
            yield RailHole('top_plus', idx, section)
    for col in range(1, SUNNY11_LOWER_COLS + 1):
        for row in SUNNY11_LOWER_ROWS:
            yield TieHole(col, row, 2)
    for name in ('lower_plus_left', 'lower_plus_right'):
        for idx in range(1, SUNNY11_LOWER_RAIL_LEN + 1):
            yield RailHole(name, idx, 2)
    for idx in range(1, 2 * SUNNY11_UPPER_RAIL_LEN + 1):
        yield RailHole('sunny_top_minus', idx)
    for idx in range(1, 2 * SUNNY11_LOWER_RAIL_LEN + 1):
        yield RailHole('sunny_bot_minus', idx)


@lru_cache(maxsize=None)
def board_holes(layout: str) -> FrozenSet[Hole]:
    """Every tie-strip and rail hole physically present on a layout
    (binding posts and module pins are not board holes)."""
    if layout == 'sunny-11':
        return frozenset(_sunny11_holes())
    return frozenset(_standard_holes(layout))


def active_terminals(layout: str, binding_post_side: str = 'left',
                     num_terminals: int = 3) -> Tuple[str, ...]:
    """Binding posts shown for these preferences. sunny-11 always has all
    five; elsewhere posts on the left/right side fit at most three, except
    on the tall stacked layouts, which fit four."""
    if layout == 'sunny-11':
        return TERMINAL_NAMES
    horizontal = binding_post_side in ('left', 'right')
    large = layout in ('double', 'triple', 'double_rails')
    max_terms = 4 if (not horizontal or large) else 3
    return TERMINAL_NAMES[:max(2, min(num_terminals, max_terms))]


def hole_exists(board: Breadboard, hole: Hole,
                terminals: Tuple[str, ...] = TERMINAL_NAMES) -> bool:
    """True if `hole` is a real, reachable point on this board. `terminals` are
    the active binding posts (see active_terminals); a module pin exists only
    while that module is placed."""
    if isinstance(hole, (TieHole, RailHole)):
        return hole in board_holes(board.layout)
    if isinstance(hole, Terminal):
        return hole.name in terminals
    if isinstance(hole, ModulePin):
        placed = board.get_placement(hole.ref)
        return placed is not None and isinstance(placed.pin_holes.get(hole.pin), ModulePin)
    return False


# ---------------------------------------------------------------------------
# DIP placement on sunny-11
# ---------------------------------------------------------------------------

# Sunny-11's upper blocks (sections 0/1) have three straddleable gutters
# instead of every other layout's one: each block's own internal gap,
# plus the gap between the two blocks. (row, section) pairs here are
# what a cross_gap=False / cross_gap=True pin lands on for that gutter.
SUNNY11_GUTTERS = (
    (('e', 0), ('f', 0)),   # left block's own internal gap
    (('j', 0), ('a', 1)),   # between the two upper blocks
    (('e', 1), ('f', 1)),   # right block's own internal gap
)


def sunny11_chain_pos(row: str, section: int) -> float:
    """Position of (row, section) along the 20-row chain formed by
    treating the two upper blocks as one strip: 0-4/5-9 = block 0's
    top/bottom banks, 10-14/15-19 = block 1's."""
    if row in TOP_ROWS:
        return section * 10 + TOP_ROWS.index(row)
    return section * 10 + 5 + BOT_ROWS.index(row)


def sunny11_gutter_index(row: str, section: int) -> int:
    """Which of the three gutters (see SUNNY11_GUTTERS) is nearest
    (row, section) along the chain."""
    pos = sunny11_chain_pos(row, section)
    boundaries = (4.5, 9.5, 14.5)
    return min(range(3), key=lambda i: abs(pos - boundaries[i]))


def sunny11_place_dip(comp_def: ComponentDef, anchor: TieHole, flipped,
                      lenient: bool = False) -> Dict[int, Hole]:
    """Cross-gap pin placement for a DIP anchored on sunny-11's upper
    blocks: straddle whichever of the three gutters is nearest the
    anchor hole, instead of always forcing row 'e' (which can only
    ever reach one of the two blocks' own internal gaps).

    The row->x / col->y axis swap of the portrait blocks is a transpose of
    the normal board's col->x / row->y mapping, and a plain transpose is a
    mirror image, not a 90° rotation — reusing col_delta's sign unchanged
    here would reflect the pin order instead of rotating it, so col_delta
    is negated relative to comp_def.place()'s formula to restore proper
    rotation and correct pin-1 placement."""
    false_side, true_side = SUNNY11_GUTTERS[sunny11_gutter_index(anchor.row, anchor.section)]
    result: Dict[int, Hole] = {}
    for pin, offset in comp_def.pin_offsets.items():
        cross = (not offset.cross_gap) if flipped else offset.cross_gap
        row, section = true_side if cross else false_side
        col = anchor.col + (offset.col_delta if flipped else -offset.col_delta)
        try:
            result[pin] = TieHole(col, row, section)
        except AssertionError:
            if lenient:
                continue
            raise
    return result


def resolve_pin_holes(layout: str, comp_def: ComponentDef, anchor: TieHole, flipped,
                      lenient: bool = False) -> Dict[int, Hole]:
    """comp_def.place()/place_lenient(), routed through sunny-11's
    multi-gutter DIP logic when the DIP is anchored on its upper blocks.
    Raises AssertionError/IndexError when a pin cannot be resolved (strict)."""
    if comp_def.row_span and layout == 'sunny-11':
        # Its portrait blocks and 6-row lower block have no row pair this far apart.
        if lenient:
            return {}
        raise IndexError(f'{comp_def.display_name} does not fit sunny-11')
    if comp_def.is_dip and layout == 'sunny-11' and anchor.section in (0, 1):
        return sunny11_place_dip(comp_def, anchor, flipped, lenient=lenient)
    return comp_def.place_lenient(anchor, flipped=flipped) if lenient \
        else comp_def.place(anchor, flipped=flipped)


# ---------------------------------------------------------------------------
# Holes under a component's body
# ---------------------------------------------------------------------------

def covered_holes(layout: str, comp_def: ComponentDef, pin_holes: Dict[int, Hole],
                  flipped: int = 0) -> Set[TieHole]:
    """Tie holes a placed part's body covers, pins excluded: nothing can go in
    them. The rows between a wide module's pin rows, plus comp_def.body's
    margins turned with the part. Only for the standard layouts (sunny-11's
    blocks are not on one row grid); empty for parts without a body."""
    if layout == 'sunny-11' or not (comp_def.row_span or any(comp_def.body)):
        return set()
    ties = [h for h in pin_holes.values() if isinstance(h, TieHole)]
    if not ties:
        return set()
    cols = [h.col for h in ties]
    rows = [PHYS_ROW[h.row] for h in ties]
    before, after, low, high = comp_def.body
    if comp_def.is_dip or comp_def.pin_count < 3:
        turns = 2 if flipped else 0       # DIP-style parts: flipped = 180°
    else:
        turns = flipped % 4               # single-row parts: quad rotation
    # Turn the rotation-0 margins (along = +columns, across = +rows) with the
    # part: 90° clockwise takes +columns to +rows and +rows to -columns.
    if turns == 0:
        c_lo, c_hi, r_lo, r_hi = before, after, low, high
    elif turns == 1:
        c_lo, c_hi, r_lo, r_hi = high, low, before, after
    elif turns == 2:
        c_lo, c_hi, r_lo, r_hi = after, before, high, low
    else:
        c_lo, c_hi, r_lo, r_hi = low, high, after, before
    section = ties[0].section
    pins = set(ties)
    real = board_holes(layout)
    out: Set[TieHole] = set()
    for col in range(min(cols) - c_lo, max(cols) + c_hi + 1):
        for prow in range(min(rows) - r_lo, max(rows) + r_hi + 1):
            if col < 1 or prow not in ROW_AT_PHYS:
                continue
            h = TieHole(col, ROW_AT_PHYS[prow], section)
            if h in real and h not in pins:
                out.add(h)
    return out
