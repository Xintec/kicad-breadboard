"""
Parts declared as not mounted on the breadboard (board.omitted), e.g. a
2x6 expansion header. The validator asks for a binding post on a supply net
that has schematic nodes off the board, taking them for a virtual source
(power symbol, SPICE source) the post must stand in for; nodes of omitted
parts are real parts left out, and no longer count. The list travels with
the board, so the window and the CLI validate alike, and is saved.
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
from breadboard.model import validate  # noqa: E402
from breadboard.model.session import load_session, save_session  # noqa: E402
from breadboard.prefs import Preferences  # noqa: E402

# U1 is fed by the 5V post; J11 is an expansion header on the same supply net.
NET = '''(export (version "E")
  (components
    (comp (ref "C1") (value "100n") (libsource (lib "Device") (part "C") (description "")))
    (comp (ref "C2") (value "100n") (libsource (lib "Device") (part "C") (description "")))
    (comp (ref "J11") (value "EXP") (footprint "Connector_PinHeader_2.54mm:PinHeader_2x02_P2.54mm_Vertical")
      (libsource (lib "Connector") (part "Conn_02x02") (description ""))))
  (nets
    (net (code "1") (name "/VCC")
      (node (ref "C1") (pin "1") (pintype "power_in"))
      (node (ref "C2") (pin "1") (pintype "passive"))
      (node (ref "J11") (pin "1") (pintype "passive")))
    (net (code "2") (name "GND")
      (node (ref "C1") (pin "2") (pintype "passive"))
      (node (ref "C2") (pin "2") (pintype "passive"))
      (node (ref "J11") (pin "2") (pintype "passive")))
    (net (code "3") (name "/X") (node (ref "J11") (pin "3") (pintype "passive")))
    (net (code "4") (name "/Y") (node (ref "J11") (pin "4") (pintype "passive")))))
'''


class OmittedTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)
        self.net = os.path.join(self.dir, 'o.net')
        with open(self.net, 'w', encoding='utf-8') as f:
            f.write(NET)
        self.s = hl.Session.create(os.path.join(self.dir, 'o.kicad_bbrd'), self.net,
                                   prefs=Preferences())
        # C1 and C2 side by side on the same two strips: /VCC col 10, GND col 11
        self.s.place('C1', pins={1: 'a10', 2: 'a11'})
        self.s.place('C2', pins={1: 'b10', 2: 'b11'})

    def vcc_issues(self, board):
        return [i for i in validate(board, self.s.netlist).issues if i.net_name == '/VCC']

    def test_a_left_out_header_looks_like_a_missing_source(self):
        self.assertTrue(self.vcc_issues(self.s.board))       # the heuristic, unchanged

    def test_omitting_it_clears_that(self):
        self.s.omit(['J11'])
        self.assertEqual(self.vcc_issues(self.s.board), [])

    def test_omitted_parts_are_not_reported_unplaced_either(self):
        self.s.omit(['J11'])
        self.assertTrue(self.s.check()['ok'], self.s.check())

    def test_only_netlist_parts_can_be_omitted(self):
        with self.assertRaises(hl.CliError):
            self.s.omit(['J99'])

    def test_unomit(self):
        self.s.omit(['J11'])
        self.s.unomit(['J11'])
        self.assertTrue(self.vcc_issues(self.s.board))

    def test_saved_with_the_session(self):
        self.s.omit(['J11'])
        self.s.save()
        self.assertEqual(load_session(self.s.path)['board'].omitted, {'J11'})
        self.assertEqual(self.s.info()['omitted'], ['J11'])

    def test_a_save_that_knows_nothing_of_it_keeps_it(self):
        # the window saves through save_session with the board it loaded
        self.s.omit(['J11'])
        self.s.save()
        board = load_session(self.s.path)['board']
        save_session(board, self.net, self.s.path)
        self.assertEqual(load_session(self.s.path)['board'].omitted, {'J11'})


if __name__ == '__main__':
    unittest.main()
