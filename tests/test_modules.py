"""
Real modules and single-row parts, recognised by footprint:

  NodeMCU Amica V2   on the board, pin rows 9 pitches apart (0.9 in)
  PinHeader_1xN      SIP-N (ADS1115 module: SIP-10 whose board lies flat)
  Xinya XY308-2.54   SIP-N terminal block whose body covers the next row

Their bodies cover holes; the CLI refuses leads in covered holes.
Run with KiCad's Python:
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

from breadboard import headless as hl  # noqa: E402
from breadboard.model import rules  # noqa: E402
from breadboard.model.breadboard import TieHole  # noqa: E402
from breadboard.model.components import ALL_DEFS, guess_type_id  # noqa: E402
from breadboard.model.netlist import parse as parse_netlist  # noqa: E402
from breadboard.prefs import Preferences  # noqa: E402

NODEMCU_FP = 'BikainGarden:NodeMCU_Amica_V2_2x15_P2.54mm_22.86mm'
PARTS = [
    # ref, value, footprint, pin count
    ('U2', 'NodeMCU Amica V2 ESP8266', NODEMCU_FP, 30),
    ('U3', 'ADS1115', 'Connector_PinHeader_2.54mm:PinHeader_1x10_P2.54mm_Vertical', 10),
    ('J3', 'CAP 5cm', 'TerminalBlock:TerminalBlock_Xinya_XY308-2.54-3P_1x03_P2.54mm_Horizontal', 3),
    ('JP1', 'SELECTOR', 'Connector_PinHeader_2.54mm:PinHeader_1x03_P2.54mm_Vertical', 3),
    ('R1', '4k7', 'Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P7.62mm_Horizontal', 2),
]


def write_netlist(path):
    """Every pin on its own net, as KiCad does for unconnected pins."""
    comps, nets, code = [], [], 1
    for ref, value, fp, n in PARTS:
        comps.append(f'(comp (ref "{ref}") (value "{value}") (footprint "{fp}")'
                     f' (libsource (lib "BikainGarden") (part "{ref}_sym") (description "")))')
        for pin in range(1, n + 1):
            nets.append(f'(net (code "{code}") (name "/{ref}_{pin}")'
                        f' (node (ref "{ref}") (pin "{pin}") (pintype "passive")))')
            code += 1
    with open(path, 'w', encoding='utf-8') as f:
        f.write('(export (version "E") (components ' + ' '.join(comps) + ') (nets '
                + ' '.join(nets) + '))')


def _type(ref, value, fp, n):
    return guess_type_id(ref, value, f'{ref}_sym', 'BikainGarden', '', n, {'Footprint': fp})


class RecognitionTest(unittest.TestCase):
    def test_parser_exposes_the_footprint(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        write_netlist(os.path.join(d, 'm.net'))
        comps = parse_netlist(os.path.join(d, 'm.net')).components
        self.assertEqual(comps['U2'].properties['Footprint'], NODEMCU_FP)

    def test_types_come_from_the_footprint(self):
        got = {ref: _type(ref, v, fp, n) for ref, v, fp, n in PARTS}
        self.assertEqual(got, {'U2': 'NodeMCU_Amica_V2', 'U3': 'ADS1115_Module',
                               'J3': 'XY308_3', 'JP1': 'SIP3', 'R1': 'R'})

    def test_two_row_headers_stay_unplaceable(self):
        self.assertIsNone(_type('J11', 'EXP', 'Connector_PinHeader_2.54mm:'
                                'PinHeader_2x06_P2.54mm_Vertical', 12))

    def test_without_a_footprint_nothing_changes(self):
        self.assertEqual(guess_type_id('U1', 'NE555', 'NE555', 'Timer', '', 8, {}), 'DIP8')


class DynamicTypesTest(unittest.TestCase):
    """Types created on the fly (SIP-N, XY308-N, DIP-N, transistor pin-order
    variants) must resolve from their name alone: a session reopened in a
    fresh process names them before any guess_type_id call registers them."""

    def test_resolved_from_the_name(self):
        self.assertEqual(ALL_DEFS.get('SIP7').pin_count, 7)
        self.assertEqual(ALL_DEFS.get('XY308_5').body, (0, 0, 1, 1))
        self.assertTrue(ALL_DEFS.get('DIP22').is_dip)
        ecb = ALL_DEFS.get('PNP_ECB')
        self.assertEqual(ecb.pin_names, {1: 'E', 2: 'C', 3: 'B'})
        self.assertIn('SIP9', ALL_DEFS)

    def test_unknown_names_stay_unknown(self):
        self.assertIsNone(ALL_DEFS.get('SIP0'))
        self.assertIsNone(ALL_DEFS.get('DIP7'))          # odd
        self.assertIsNone(ALL_DEFS.get('NPN_XYZ'))
        self.assertNotIn('FOO', ALL_DEFS)
        with self.assertRaises(KeyError):
            ALL_DEFS['FOO']


class NodeMcuPlacementTest(unittest.TestCase):
    d = ALL_DEFS['NodeMCU_Amica_V2']

    def test_rows_nine_pitches_apart(self):
        # a..e are pitches 0-4 and f..j 7-11 (the centre gap is 3 pitches):
        # b (1) + 9 = i (10), leaving a and j free — one hole each side.
        holes = self.d.place(TieHole(10, 'b'))
        self.assertEqual((holes[1], holes[15]), (TieHole(10, 'b'), TieHole(24, 'b')))
        self.assertEqual((holes[16], holes[30]), (TieHole(10, 'i'), TieHole(24, 'i')))
        self.assertEqual(self.d.place(TieHole(10, 'a'))[16], TieHole(10, 'h'))
        self.assertEqual(self.d.place(TieHole(10, 'c'))[16], TieHole(10, 'j'))

    def test_too_low_an_anchor_does_not_fit(self):
        with self.assertRaises(IndexError):
            self.d.place(TieHole(10, 'd'))

    def test_rotated_180(self):
        holes = self.d.place(TieHole(30, 'b'), flipped=1)
        self.assertEqual((holes[1], holes[15]), (TieHole(30, 'i'), TieHole(16, 'i')))
        self.assertEqual(holes[16], TieHole(30, 'b'))

    def test_not_on_sunny11_upper_blocks(self):
        with self.assertRaises(IndexError):
            rules.resolve_pin_holes('sunny-11', self.d, TieHole(10, 'b', 0), 0)

    def test_body_covers_the_rows_between_and_two_columns_past_each_end(self):
        holes = self.d.place(TieHole(10, 'b'))
        covered = rules.covered_holes('full', self.d, holes, 0)
        for h in ('c10', 'h24', 'e17', 'f17', 'b8', 'b9', 'i26', 'e25'):
            self.assertIn(hl.parse_hole(h), covered, h)
        for h in ('a10', 'j10', 'b7', 'i27', 'b10'):     # free, or a pin
            self.assertNotIn(hl.parse_hole(h), covered, h)


class SipPlacementTest(unittest.TestCase):
    def test_sip_is_one_row(self):
        holes = ALL_DEFS['SIP3'].place(TieHole(5, 'a'))
        self.assertEqual(sorted(holes.values(), key=lambda h: h.col),
                         [TieHole(5, 'a'), TieHole(6, 'a'), TieHole(7, 'a')])

    def test_header_covers_nothing(self):
        d = ALL_DEFS['SIP3']
        self.assertEqual(rules.covered_holes('full', d, d.place(TieHole(5, 'c')), 0), set())

    def test_xy308_covers_the_row_on_each_side(self):
        d = ALL_DEFS['XY308_3']
        covered = rules.covered_holes('full', d, d.place(TieHole(5, 'c')), 0)
        self.assertEqual(covered, {TieHole(c, r) for c in (5, 6, 7) for r in 'bd'})

    def test_xy308_turned_90_covers_the_columns_beside_it(self):
        d = ALL_DEFS['XY308_3']
        holes = d.place(TieHole(5, 'a'), flipped=1)       # down rows a, b, c
        self.assertEqual(set(holes.values()), {TieHole(5, r) for r in 'abc'})
        covered = rules.covered_holes('full', d, holes, 1)
        self.assertEqual(covered, {TieHole(c, r) for c in (4, 6) for r in 'abc'})


class Ads1115BodyTest(unittest.TestCase):
    """28 x 17 mm board lying flat, header ~1.3 mm from one long edge
    (measured 2026-09-27): it reaches 15.7 mm across → 6 pitches on one side,
    ~2.5 mm past each end pin → the next column (edge right over it)."""
    d = ALL_DEFS['ADS1115_Module']

    def covered(self, anchor, rot=0):
        return rules.covered_holes('full', self.d, self.d.place(anchor, flipped=rot), rot)

    def test_from_row_a_it_covers_the_rest_of_the_bank(self):
        cov = self.covered(TieHole(30, 'a'))
        expected = ({TieHole(c, r) for c in range(29, 41) for r in 'bcde'}
                    | {TieHole(29, 'a'), TieHole(40, 'a')})
        self.assertEqual(cov, expected)

    def test_from_row_e_it_reaches_across_the_gap(self):
        rows = {h.row for h in self.covered(TieHole(30, 'e'))}
        self.assertEqual(rows, set('efghi'))       # e: only the end columns

    def test_turned_180_it_covers_toward_row_a(self):
        rows = {h.row for h in self.covered(TieHole(40, 'j'), rot=2)}
        self.assertEqual(rows, set('fghij'))


class CliCoveredHolesTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)
        net = os.path.join(self.dir, 'm.net')
        write_netlist(net)
        self.s = hl.Session.create(os.path.join(self.dir, 'm.kicad_bbrd'), net,
                                   layout='full', prefs=Preferences())
        self.s.place('U2', anchor='b10')

    def test_placement_reports_the_module(self):
        info = {c['ref']: c for c in self.s.info()['components']}
        self.assertEqual(info['U2']['holes']['16'], 'i10')
        self.assertIn('c10', info['U2']['covers'])

    def test_no_lead_under_the_body(self):
        with self.assertRaises(hl.CliError) as cm:
            self.s.place('R1', pins={1: 'c12', 2: 'a30'})
        self.assertIn('U2', str(cm.exception))
        with self.assertRaises(hl.CliError):
            self.s.wire('e20', 'a40')

    def test_free_row_beside_the_module_is_usable(self):
        self.s.place('R1', pins={1: 'a12', 2: 'a30'})
        self.s.wire('j15', 'bot_minus:3')

    def test_a_body_may_not_land_on_existing_leads(self):
        self.s.remove('U2')
        self.s.place('R1', pins={1: 'd12', 2: 'd40'})
        with self.assertRaises(hl.CliError) as cm:
            self.s.place('U2', anchor='b10')
        self.assertIn('R1', str(cm.exception))

    def test_check_reports_a_lead_under_a_body(self):
        # as a hand-edited file might have it
        from breadboard.model.breadboard import PlacedComponent
        self.s.board.place(PlacedComponent('R1', 'R', {1: TieHole(12, 'c'), 2: TieHole(30, 'a')}))
        kinds = {(c['kind'], c['hole']) for c in self.s.check()['conflicts']}
        self.assertIn(('covered_hole', 'c12'), kinds)


if __name__ == '__main__':
    unittest.main()
