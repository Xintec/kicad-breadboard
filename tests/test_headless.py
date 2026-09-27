"""
Headless editing of a breadboard session (plugins/breadboard/headless.py),
the layer under the CLI. Run with KiCad's Python:
    "C:\\Program Files\\KiCad\\10.0\\bin\\python.exe" -m unittest discover -s tests
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'plugins'))
sys.modules['pcbnew'] = None    # see make_golden.py: avoids KiCad's modal assert

from breadboard import headless as hl  # noqa: E402
from breadboard.model.breadboard import TieHole, RailHole, Terminal, ModulePin  # noqa: E402
from breadboard.prefs import Preferences  # noqa: E402

DEMO_NET = os.path.join(HERE, 'fixtures', 'demo.net')


class HoleNotationTest(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(hl.parse_hole('e10'), TieHole(10, 'e', 0))
        self.assertEqual(hl.parse_hole('J63@2'), TieHole(63, 'j', 2))
        self.assertEqual(hl.parse_hole('top_plus:3'), RailHole('top_plus', 3, 0))
        self.assertEqual(hl.parse_hole('bot_minus:40@1'), RailHole('bot_minus', 40, 1))
        self.assertEqual(hl.parse_hole('GND'), Terminal('GND'))
        self.assertEqual(hl.parse_hole('MCU1.5'), ModulePin('MCU1', 5))

    def test_format_round_trips(self):
        for text in ('e10', 'j63@2', 'top_plus:3', 'bot_minus:40@1', 'V1', 'MCU1.5'):
            self.assertEqual(hl.format_hole(hl.parse_hole(text)), text)

    def test_garbage_is_a_clean_error(self):
        for text in ('', 'z10', 'e0', 'nope:3', 'e10@x', 'V9'):
            with self.subTest(text=text), self.assertRaises(hl.CliError):
                hl.parse_hole(text)


class _SessionCase(unittest.TestCase):
    layout = 'full'

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)
        self.path = os.path.join(self.dir, 'demo.kicad_bbrd')
        self.s = hl.Session.create(self.path, DEMO_NET, layout=self.layout,
                                   prefs=Preferences())

    def reopen(self):
        return hl.Session.open(self.path, prefs=Preferences())


class PlaceTest(_SessionCase):
    def test_two_pin_part_goes_where_its_pins_say(self):
        self.s.place('R1', pins={1: 'a10', 2: 'a14'})
        self.assertEqual(self.s.board.get_placement('R1').pin_holes,
                         {1: TieHole(10, 'a'), 2: TieHole(14, 'a')})

    def test_dip_from_anchor_straddles_the_gap(self):
        self.s.place('U1', anchor='e20')
        holes = self.s.board.get_placement('U1').pin_holes
        # pin 1 bottom-left, as a real chip seen from above with the notch left
        self.assertEqual(holes[1], TieHole(20, 'f'))
        self.assertEqual(holes[4], TieHole(23, 'f'))
        self.assertEqual(holes[5], TieHole(23, 'e'))
        self.assertEqual(holes[8], TieHole(20, 'e'))

    def test_to92_pinout_variant_changes_the_holes(self):
        self.s.place('Q1', anchor='a30', pinout='E-B-C')
        holes = self.s.board.get_placement('Q1').pin_holes
        self.assertEqual(holes[3], TieHole(30, 'a'))   # E first
        self.assertEqual(holes[1], TieHole(32, 'a'))

    def test_unknown_pinout_lists_the_valid_ones(self):
        with self.assertRaises(hl.CliError) as cm:
            self.s.place('Q1', anchor='a30', pinout='X-Y-Z')
        self.assertIn('E-B-C', str(cm.exception))

    def test_off_board_is_refused(self):
        with self.assertRaises(hl.CliError):
            self.s.place('U1', anchor='e62')          # pins reach column 65
        self.assertIsNone(self.s.board.get_placement('U1'))

    def test_hole_taken_by_another_part_is_refused(self):
        self.s.place('R1', pins={1: 'a10', 2: 'a14'})
        with self.assertRaises(hl.CliError) as cm:
            self.s.place('R2', pins={1: 'a14', 2: 'a18'})
        self.assertIn('R1', str(cm.exception))

    def test_hole_taken_by_a_wire_is_refused(self):
        self.s.wire('a10', 'top_plus:3')
        with self.assertRaises(hl.CliError):
            self.s.place('R1', pins={1: 'a10', 2: 'a14'})

    def test_placing_twice_needs_replace(self):
        self.s.place('R1', pins={1: 'a10', 2: 'a14'})
        with self.assertRaises(hl.CliError):
            self.s.place('R1', pins={1: 'b10', 2: 'b14'})
        self.s.place('R1', pins={1: 'a10', 2: 'a15'}, replace=True)  # may reuse its own hole
        self.assertEqual(self.s.board.get_placement('R1').pin_holes[2], TieHole(15, 'a'))

    def test_ref_not_in_netlist_is_refused(self):
        with self.assertRaises(hl.CliError):
            self.s.place('R9', pins={1: 'a10', 2: 'a14'})

    def test_two_pin_part_needs_both_pins(self):
        with self.assertRaises(hl.CliError):
            self.s.place('R1', pins={1: 'a10'})

    def test_led_colour_is_kept(self):
        self.s.place('D1', pins={1: 'a40', 2: 'a43'}, led_color='green')
        self.assertTrue(self.s.board.get_placement('D1').led_color)

    def test_remove(self):
        self.s.place('R1', pins={1: 'a10', 2: 'a14'})
        self.s.remove('R1')
        self.assertIsNone(self.s.board.get_placement('R1'))
        with self.assertRaises(hl.CliError):
            self.s.remove('R1')


class WireTest(_SessionCase):
    def test_wire_and_unwire(self):
        self.s.wire('a10', 'top_plus:3', color='#ff0000')
        self.assertEqual(len(self.s.board.wires), 1)
        self.s.unwire('top_plus:3', 'a10')             # either order
        self.assertEqual(self.s.board.wires, [])

    def test_wire_end_on_a_used_hole_is_refused(self):
        self.s.wire('a10', 'top_plus:3')
        with self.assertRaises(hl.CliError):
            self.s.wire('a10', 'b20')

    def test_binding_posts_take_many_wires(self):
        self.s.wire('GND', 'bot_minus:1')
        self.s.wire('GND', 'bot_minus:30')
        self.assertEqual(len(self.s.board.wires), 2)

    def test_inactive_binding_post_is_refused(self):
        # default prefs: 3 posts on the left side → GND, V1, V2
        with self.assertRaises(hl.CliError):
            self.s.wire('V3', 'a10')


class TerminalTest(_SessionCase):
    def test_assign_needs_a_real_net(self):
        self.s.assign_terminal('GND', 'GND')
        self.assertEqual(self.s.board.get_terminal_net('GND'), 'GND')
        with self.assertRaises(hl.CliError):
            self.s.assign_terminal('V1', 'NOPE')
        self.s.assign_terminal('GND', '')               # clears
        self.assertIsNone(self.s.board.get_terminal_net('GND'))


class PersistenceTest(_SessionCase):
    def test_save_and_reopen(self):
        self.s.place('U1', anchor='e20')
        self.s.wire('a10', 'top_plus:3')
        self.s.assign_terminal('GND', 'GND')
        self.s.save()
        s2 = self.reopen()
        self.assertEqual(s2.board.get_placement('U1').pin_holes,
                         self.s.board.get_placement('U1').pin_holes)
        self.assertEqual(len(s2.board.wires), 1)
        self.assertEqual(s2.board.get_terminal_net('GND'), 'GND')
        self.assertEqual(os.path.normcase(s2.netlist_path), os.path.normcase(DEMO_NET))

    def test_fields_the_cli_does_not_edit_survive(self):
        self.s.save()
        with open(self.path, encoding='utf-8') as f:
            doc = json.load(f)
        doc['annotations'] = [{'kind': 'text', 'x': 1, 'y': 2, 'text': 'hola'}]
        with open(self.path, 'w', encoding='utf-8') as f:
            json.dump(doc, f)
        s2 = self.reopen()
        s2.place('R1', pins={1: 'a10', 2: 'a14'})
        s2.save()
        with open(self.path, encoding='utf-8') as f:
            self.assertEqual(json.load(f)['annotations'], doc['annotations'])

    def test_layout_comes_from_the_session(self):
        s = hl.Session.create(os.path.join(self.dir, 'h.kicad_bbrd'), DEMO_NET,
                              layout='half', prefs=Preferences())
        s.save()
        self.assertEqual(hl.Session.open(s.path, prefs=Preferences()).board.layout, 'half')


class InfoTest(_SessionCase):
    def test_lists_parts_with_pins_nets_and_placement(self):
        self.s.place('R1', pins={1: 'a10', 2: 'a14'})
        info = self.s.info()
        self.assertEqual(info['layout'], 'full')
        self.assertEqual(info['terminals_available'], ['GND', 'V1', 'V2'])
        parts = {p['ref']: p for p in info['components']}
        self.assertEqual(parts['R1']['holes'], {'1': 'a10', '2': 'a14'})
        self.assertIsNone(parts['U1']['holes'])
        self.assertEqual(parts['U1']['type_id'], 'TL081')
        self.assertEqual(parts['U1']['pins']['3'], {'name': 'IN+', 'net': '/MID'})
        self.assertEqual(parts['Q1']['pinouts'], ['C-B-E', 'E-B-C'])
        self.assertIn('/MID', info['nets'])


class CheckTest(_SessionCase):
    def _build_demo(self):
        s = self.s
        for t, n in (('GND', 'GND'), ('V1', 'VCC')):
            s.assign_terminal(t, n)
        s.wire('V1', 'top_plus:1')
        s.wire('GND', 'top_minus:1')
        s.wire('top_minus:2', 'bot_minus:2')
        # U1 straddles cols 20-23: pin1 f20 … pin4 f23, pin5 e23 … pin8 e20
        s.place('U1', anchor='e20')
        s.place('R1', pins={1: 'top_plus:10', 2: 'j22'})     # VCC → /MID (U1.3 = f22)
        s.place('R2', pins={1: 'i22', 2: 'bot_plus:10'})     # R2.2 on a rail nobody feeds: open
        return s

    def test_open_nets_and_unplaced_parts_are_reported(self):
        report = self._build_demo().check()
        self.assertFalse(report['ok'])
        kinds = {i['kind'] for i in report['issues']}
        self.assertIn('unplaced', kinds)
        self.assertIn('open_net', kinds)

    def test_physical_conflicts_in_a_hand_edited_file_are_reported(self):
        self.s.save()
        with open(self.path, encoding='utf-8') as f:
            doc = json.load(f)
        doc['placements'] = [
            {'ref': 'R1', 'type_id': 'R', 'flipped': 0,
             'pins': [[1, ['tie', 10, 'a']], [2, ['tie', 14, 'a']]]},
            {'ref': 'R2', 'type_id': 'R', 'flipped': 0,
             'pins': [[1, ['tie', 14, 'a']], [2, ['tie', 80, 'a']]]},
        ]
        with open(self.path, 'w', encoding='utf-8') as f:
            json.dump(doc, f)
        report = self.reopen().check()
        problems = {(c['kind'], c['hole']) for c in report['conflicts']}
        self.assertIn(('shared_hole', 'a14'), problems)
        self.assertIn(('no_such_hole', 'a80'), problems)
        self.assertFalse(report['ok'])


class Sunny11Test(_SessionCase):
    layout = 'sunny-11'

    def test_dip_uses_the_sunny11_gutter_rule(self):
        self.s.place('U1', anchor='j10')        # nearest gutter: between the two blocks
        holes = self.s.board.get_placement('U1').pin_holes
        self.assertEqual({h.section for h in holes.values()}, {0, 1})

    def test_all_five_posts_exist(self):
        self.s.wire('V4', 'a5@2')


if __name__ == '__main__':
    unittest.main()
