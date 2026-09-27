"""
Headless editing of a breadboard session (.kicad_bbrd) — no wx.

The layer under cli.py: open a session with its netlist and the user's saved
preferences, place/remove parts, add/remove wires, assign binding posts, check
the build and simulate it, then save. Placement goes through model/rules.py,
the same rules the canvas applies, plus one the canvas does not enforce: a
physical hole takes one lead (component pin or wire end); binding posts take
any number of wires.

Holes are written as text:
    e10, e10@1        tie hole: row + column, @section on multi-board layouts
    top_plus:3, …@1   rail hole: rail name : index
    GND, V1 … V4      binding posts
    MCU1.5            pin 5 of a free-floating module
"""
from __future__ import annotations

import dataclasses
import os
import re
from typing import Dict, List, Optional, Tuple

from .model import (
    Breadboard, PlacedComponent, TieHole, RailHole, Terminal, ModulePin, Hole,
    ALL_ROWS, ALL_RAIL_NAMES, TERMINAL_NAMES, ALL_DEFS, TO92_PINOUT_VARIANTS, LED_COLORS,
    Netlist, parse_netlist, guess_type_id, validate,
    save_session, load_session, simulate, initial_terminal_voltages,
)
from .model import rules
from .prefs import Preferences, load_prefs

DEFAULT_WIRE_COLOR = '#e8c020'


class CliError(Exception):
    """A request that cannot be carried out; the message says why."""


# ---------------------------------------------------------------------------
# Hole notation
# ---------------------------------------------------------------------------

_TIE_RE = re.compile(r'^([a-jA-J])(\d+)(?:@(\d+))?$')
_RAIL_RE = re.compile(r'^([a-z_]+):(\d+)(?:@(\d+))?$')
_MODULE_RE = re.compile(r'^([A-Za-z_]+\d+)\.(\d+)$')


def parse_hole(text: str) -> Hole:
    t = text.strip()
    if t.upper() in TERMINAL_NAMES:
        return Terminal(t.upper())
    try:
        m = _TIE_RE.match(t)
        if m:
            return TieHole(int(m.group(2)), m.group(1).lower(), int(m.group(3) or 0))
        m = _RAIL_RE.match(t)
        if m and m.group(1) in ALL_RAIL_NAMES:
            return RailHole(m.group(1), int(m.group(2)), int(m.group(3) or 0))
        m = _MODULE_RE.match(t)
        if m:
            return ModulePin(m.group(1), int(m.group(2)))
    except AssertionError:
        pass    # e.g. column 0
    raise CliError(f'Not a hole: {text!r}. Use e10 / e10@1, top_plus:3 / top_plus:3@1, '
                   f'{"/".join(TERMINAL_NAMES)}, or MCU1.5.')


def format_hole(h: Hole) -> str:
    if isinstance(h, TieHole):
        return f'{h.row}{h.col}' + (f'@{h.section}' if h.section else '')
    if isinstance(h, RailHole):
        return f'{h.rail}:{h.index}' + (f'@{h.section}' if h.section else '')
    if isinstance(h, Terminal):
        return h.name
    if isinstance(h, ModulePin):
        return f'{h.ref}.{h.pin}'
    raise TypeError(h)


def _as_hole(h) -> Hole:
    return parse_hole(h) if isinstance(h, str) else h


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------

class Session:
    def __init__(self, path: str, board: Breadboard, netlist_path: str,
                 annotations: list, prefs: Preferences):
        self.path = path
        self.board = board
        self.netlist_path = netlist_path
        self.annotations = annotations
        self.prefs = prefs
        if not os.path.isfile(netlist_path):
            raise CliError(f'Netlist not found: {netlist_path}. Export it from the '
                           f'schematic first (cli.py netlist).')
        self.netlist: Netlist = parse_netlist(netlist_path)
        # The canvas draws (and a person builds) the rails as the preferences
        # say; load_session() always rebuilds the board with rails split.
        self.board.set_rail_split(prefs.rail_split)
        self.terminals = rules.active_terminals(board.layout, prefs.binding_post_side,
                                                prefs.num_terminals)

    @classmethod
    def create(cls, path: str, netlist_path: str, layout: Optional[str] = None,
               prefs: Optional[Preferences] = None) -> 'Session':
        prefs = prefs or load_prefs()
        layout = layout or prefs.board_layout
        if layout not in ('mini', 'half', 'full', 'double', 'triple', 'double_rails', 'sunny-11'):
            raise CliError(f'Unknown layout {layout!r}.')
        board = Breadboard(layout=layout, rail_split=prefs.rail_split)
        return cls(path, board, os.path.abspath(netlist_path), [], prefs)

    @classmethod
    def open(cls, path: str, netlist: Optional[str] = None,
             prefs: Optional[Preferences] = None) -> 'Session':
        if not os.path.isfile(path):
            raise CliError(f'Session not found: {path}')
        try:
            data = load_session(path)
        except (ValueError, KeyError, TypeError, AssertionError) as exc:
            raise CliError(f'Cannot read session {path}: {exc}') from exc
        net = netlist or data['netlist_path'] or ''
        if net and not os.path.isabs(net):
            net = os.path.join(os.path.dirname(os.path.abspath(path)), net)
        if not net:
            raise CliError('The session names no netlist; pass one with --netlist.')
        return cls(path, data['board'], os.path.abspath(net),
                   data.get('annotations', []), prefs or load_prefs())

    def save(self) -> None:
        # The netlist is named relative to the session, so a session kept in
        # a project folder survives moving or cloning it (absolute when the
        # two are on different drives).
        try:
            net = os.path.relpath(self.netlist_path,
                                  os.path.dirname(os.path.abspath(self.path)))
        except ValueError:
            net = self.netlist_path
        save_session(self.board, net, self.path, self.annotations)

    # -- lookups -----------------------------------------------------------

    def _type_id(self, ref: str) -> Optional[str]:
        c = self.netlist.components[ref]
        return guess_type_id(ref, c.value, c.symbol, c.lib, c.description,
                             c.pin_count, c.properties)

    def _occupants(self, skip_ref: str = '') -> Dict[Hole, List[str]]:
        """Who has a lead in each hole: 'R1.2' for a pin, 'wire a10-b20' for a
        wire end. Binding posts are left out — they take any number of wires."""
        occ: Dict[Hole, List[str]] = {}
        for ref, p in self.board.placements.items():
            if ref == skip_ref:
                continue
            for pin, h in p.pin_holes.items():
                if not isinstance(h, ModulePin):
                    occ.setdefault(h, []).append(f'{ref}.{pin}')
        for w in self.board.wires:
            label = f'wire {format_hole(w.h1)}-{format_hole(w.h2)}'
            for h in (w.h1, w.h2):
                if not isinstance(h, Terminal):
                    occ.setdefault(h, []).append(label)
        return occ

    def _covered_by(self, ref: str) -> set:
        p = self.board.get_placement(ref)
        comp_def = ALL_DEFS.get(p.type_id) if p else None
        if comp_def is None:
            return set()
        return rules.covered_holes(self.board.layout, comp_def, p.pin_holes, p.flipped)

    def _bodies(self, skip_ref: str = '') -> Dict[Hole, str]:
        """Holes under a placed part's body → that part's ref."""
        return {h: ref for ref in self.board.placements if ref != skip_ref
                for h in self._covered_by(ref)}

    def _require_free(self, holes, skip_ref: str = '', covered=()) -> None:
        """Every hole in `holes` must exist and be free of leads and bodies;
        every hole in `covered` (the new part's body) free of leads and bodies."""
        occ = self._occupants(skip_ref)
        bodies = self._bodies(skip_ref)
        for h in holes:
            if not rules.hole_exists(self.board, h, self.terminals):
                raise CliError(f'{format_hole(h)} is not a hole on this {self.board.layout} '
                               f'board' + (f' (binding posts: {", ".join(self.terminals)})'
                                           if isinstance(h, Terminal) else '') + '.')
            if h in occ and not isinstance(h, Terminal):
                raise CliError(f'{format_hole(h)} is taken by {", ".join(occ[h])}.')
            if h in bodies:
                raise CliError(f'{format_hole(h)} is under the body of {bodies[h]}.')
        for h in covered:
            if h in occ:
                raise CliError(f'The body would cover {format_hole(h)}, '
                               f'taken by {", ".join(occ[h])}.')
            if h in bodies:
                raise CliError(f'The body would overlap {bodies[h]} at {format_hole(h)}.')

    # -- editing -----------------------------------------------------------

    def place(self, ref: str, *, pins: Optional[Dict[int, str]] = None,
              anchor: Optional[str] = None, rot: int = 0, pinout: Optional[str] = None,
              led_color: Optional[str] = None, at: Optional[Tuple[int, int]] = None,
              replace: bool = False) -> PlacedComponent:
        if ref not in self.netlist.components:
            raise CliError(f'{ref} is not in the netlist.')
        type_id = self._type_id(ref)
        if type_id is None or type_id not in ALL_DEFS:
            raise CliError(f'{ref} is not a breadboard part (no component type recognised).')
        comp_def = ALL_DEFS[type_id]
        if self.board.get_placement(ref) and not replace:
            raise CliError(f'{ref} is already placed; use replace to move it.')

        if comp_def.is_module:
            if at is None:
                raise CliError(f'{ref} is a module: give its canvas position (at x,y).')
            pin_holes: Dict[int, Hole] = {p: ModulePin(ref, p) for p in comp_def.pin_offsets}
        elif comp_def.pin_count == 2 and not comp_def.is_dip:
            if anchor is not None or not pins or set(pins) != {1, 2}:
                raise CliError(f'{ref} has two pins: give both, e.g. 1=a10 2=a14 '
                               f'(pin names: {comp_def.pin_names}).')
            pin_holes = {int(p): _as_hole(h) for p, h in pins.items()}
            if pin_holes[1] == pin_holes[2]:
                raise CliError('Both pins in the same hole.')
        else:
            if anchor is None or pins:
                raise CliError(f'{ref} is placed from its anchor hole (pin 1): give anchor.')
            anchor_hole = _as_hole(anchor)
            if not isinstance(anchor_hole, TieHole):
                raise CliError('The anchor must be a tie-strip hole (e.g. e10).')
            if pinout is not None:
                variants = dict(TO92_PINOUT_VARIANTS.get(type_id, []))
                if pinout not in variants:
                    raise CliError(f'{ref} pinouts: {", ".join(variants) or "none"}.')
                comp_def = dataclasses.replace(comp_def, pin_offsets=variants[pinout])
            try:
                pin_holes = rules.resolve_pin_holes(self.board.layout, comp_def,
                                                    anchor_hole, rot)
            except (AssertionError, IndexError, KeyError):
                raise CliError(f'{ref} does not fit at {anchor} (rotation {rot}).')

        self._require_free([h for h in pin_holes.values() if not isinstance(h, ModulePin)],
                           skip_ref=ref,
                           covered=rules.covered_holes(self.board.layout, comp_def,
                                                       pin_holes, rot))

        color = ''
        if type_id == 'LED':
            color = comp_def.color
            if led_color:
                by_name = {name.lower(): hexc for hexc, name in LED_COLORS}
                if led_color.lower() not in by_name:
                    raise CliError(f'LED colours: {", ".join(n for _, n in LED_COLORS)}.')
                color = by_name[led_color.lower()]

        self.board.remove(ref)
        placed = PlacedComponent(ref=ref, type_id=type_id, pin_holes=pin_holes,
                                 flipped=rot, led_color=color)
        self.board.place(placed)
        if comp_def.is_module:
            self.board.set_module_position(ref, int(at[0]), int(at[1]))
        return placed

    def omit(self, refs) -> None:
        """Declare parts as not mounted on the breadboard (see Breadboard.omitted)."""
        unknown = [r for r in refs if r not in self.netlist.components]
        if unknown:
            raise CliError(f'Not in the netlist: {", ".join(unknown)}.')
        placed = [r for r in refs if self.board.get_placement(r)]
        if placed:
            raise CliError(f'Placed on the board: {", ".join(placed)}; remove them first.')
        self.board.omitted.update(refs)

    def unomit(self, refs) -> None:
        self.board.omitted.difference_update(refs)

    def remove(self, ref: str) -> None:
        if self.board.remove(ref) is None:
            raise CliError(f'{ref} is not placed.')

    def wire(self, a, b, color: Optional[str] = None) -> None:
        h1, h2 = _as_hole(a), _as_hole(b)
        if h1 == h2:
            raise CliError('A wire needs two different holes.')
        if self.board.wire_at(h1, h2):
            raise CliError(f'There is already a wire {format_hole(h1)}-{format_hole(h2)}.')
        for h in (h1, h2):
            if isinstance(h, ModulePin) and not rules.hole_exists(self.board, h):
                raise CliError(f'{format_hole(h)}: no such module pin is placed.')
        self._require_free([h for h in (h1, h2) if not isinstance(h, ModulePin)])
        self.board.add_wire(h1, h2, color or DEFAULT_WIRE_COLOR)

    def unwire(self, a, b) -> None:
        w = self.board.wire_at(_as_hole(a), _as_hole(b))
        if w is None:
            raise CliError(f'No wire between {a} and {b}.')
        self.board.remove_wire(w)

    def assign_terminal(self, name: str, net: str) -> None:
        name = name.upper()
        if name not in self.terminals:
            raise CliError(f'Binding posts on this board: {", ".join(self.terminals)}.')
        if net and self.netlist.net_by_name(net) is None:
            raise CliError(f'No net {net!r} in the netlist.')
        self.board.assign_terminal(name, net)

    # -- reports -----------------------------------------------------------

    def info(self) -> dict:
        comps = []
        for ref, c in sorted(self.netlist.components.items()):
            type_id = self._type_id(ref)
            comp_def = ALL_DEFS.get(type_id) if type_id else None
            nets = self.netlist.nets_for_ref(ref)
            fn = self.netlist.pinfunction_map(ref)
            placed = self.board.get_placement(ref)
            entry = {
                'ref': ref, 'value': c.value, 'type_id': type_id,
                'placeable': comp_def is not None,
                'pins': {str(p): {'name': (comp_def.pin_names.get(p) if comp_def else None)
                                  or fn.get(p, ''),
                                  'net': nets[p].name if p in nets else ''}
                         for p in sorted(set(nets) | set(comp_def.pin_offsets if comp_def else ()))},
                'holes': ({str(p): format_hole(h) for p, h in sorted(placed.pin_holes.items())}
                          if placed else None),
            }
            if comp_def is not None:
                entry['placement'] = ('module' if comp_def.is_module else
                                      'pins' if comp_def.pin_count == 2 and not comp_def.is_dip
                                      else 'anchor')
                entry['pinouts'] = [n for n, _ in TO92_PINOUT_VARIANTS.get(type_id, [])]
                if placed:
                    covers = sorted(self._covered_by(ref),
                                    key=lambda h: (h.section, h.col, h.row))
                    if covers:
                        entry['covers'] = [format_hole(h) for h in covers]
            comps.append(entry)
        return {
            'session': os.path.abspath(self.path),
            'netlist': self.netlist_path,
            'layout': self.board.layout,
            'prefs': {'rail_split': self.prefs.rail_split,
                      'binding_post_side': self.prefs.binding_post_side,
                      'num_terminals': self.prefs.num_terminals},
            'terminals_available': list(self.terminals),
            'terminals': {t: n for t, n in self.board.terminal_nets.items()},
            'omitted': sorted(self.board.omitted),
            'components': comps,
            'wires': [{'from': format_hole(w.h1), 'to': format_hole(w.h2), 'color': w.color}
                      for w in self.board.wires],
            'nets': [n.name for n in self.netlist.nets],
        }

    def conflicts(self) -> List[dict]:
        """Physical problems the schematic check cannot see: two leads in one
        hole, and leads in holes this board does not have."""
        out = []
        leads = self._occupants()
        for h, who in leads.items():
            if len(who) > 1:
                out.append({'kind': 'shared_hole', 'hole': format_hole(h), 'by': who})
        bodies: Dict[Hole, List[str]] = {}
        for ref in self.board.placements:
            for h in self._covered_by(ref):
                bodies.setdefault(h, []).append(ref)
        for h, refs in bodies.items():
            if h in leads or len(refs) > 1:
                out.append({'kind': 'covered_hole', 'hole': format_hole(h),
                            'by': [f'{r} body' for r in refs] + leads.get(h, [])})
        seen = set()
        for ref, p in self.board.placements.items():
            for pin, h in p.pin_holes.items():
                if not rules.hole_exists(self.board, h, self.terminals) and h not in seen:
                    seen.add(h)
                    out.append({'kind': 'no_such_hole', 'hole': format_hole(h),
                                'by': [f'{ref}.{pin}']})
        for w in self.board.wires:
            for h in (w.h1, w.h2):
                if not rules.hole_exists(self.board, h, self.terminals) and h not in seen:
                    seen.add(h)
                    out.append({'kind': 'no_such_hole', 'hole': format_hole(h),
                                'by': [f'wire {format_hole(w.h1)}-{format_hole(w.h2)}']})
        return out

    def check(self) -> dict:
        result = validate(self.board, self.netlist)
        conflicts = self.conflicts()
        return {
            'ok': result.ok and not conflicts,
            'issues': [{'kind': i.kind.value, 'net': i.net_name, 'description': i.description,
                        'holes': [format_hole(h) for h in i.holes]}
                       for i in result.issues],
            'conflicts': conflicts,
        }

    def simulate_op(self, voltages: Optional[Dict[str, float]] = None) -> dict:
        if not self.board.get_terminal_net('GND'):
            raise CliError('Assign the GND binding post to a net before simulating.')
        report = self.check()
        if not report['ok']:
            raise CliError('The build does not match the schematic; run check first.')
        v = initial_terminal_voltages(self.board, self.netlist)
        v.update(voltages or {})
        res = simulate(self.board, self.netlist, v)
        return {
            'ok': res.error is None,
            'error': res.error,
            'terminal_voltages': v,
            'net_voltages': res.net_voltages,
            'branch_currents': res.branch_currents,
            'warnings': res.warnings,
        }
