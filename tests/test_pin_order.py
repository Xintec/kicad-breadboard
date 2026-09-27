"""
Transistor pin order from the netlist's pin names when the symbol has no
Sim.Pins, and physical pinout variants for every pin-order type.

An IRLZ44N (TO-220) numbers its pins 1=G 2=D 3=S; the plugin's NMOS assumes
1=G 2=S 3=D. Its symbol carries no Sim.Pins, but the netlist names each pin
(G_1, D_2, S_3), which is enough to recognise NMOS_GDS — and its physical
G-D-S order must then be selectable as a pinout variant.
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
from breadboard.model.breadboard import TieHole  # noqa: E402
from breadboard.model.components import (  # noqa: E402
    ALL_DEFS, TO92_PINOUT_VARIANTS, _PIN_ORDER_CANONICAL, guess_type_id,
)
from breadboard.model.netlist import parse as parse_netlist  # noqa: E402
from breadboard.prefs import Preferences  # noqa: E402

NET = '''(export (version "E")
  (components
    (comp (ref "Q1") (value "IRLZ44N")
      (libsource (lib "BikainGarden") (part "Q_NMOS_GDS") (description "N-MOSFET")))
    (comp (ref "Q2") (value "BC547")
      (libsource (lib "Device") (part "Q_NPN_BCE") (description "NPN transistor"))))
  (nets
    (net (code "1") (name "/G") (node (ref "Q1") (pin "1") (pinfunction "G_1") (pintype "input")))
    (net (code "2") (name "/D") (node (ref "Q1") (pin "2") (pinfunction "D_2") (pintype "passive")))
    (net (code "3") (name "/S") (node (ref "Q1") (pin "3") (pinfunction "S_3") (pintype "passive")))
    (net (code "4") (name "/B") (node (ref "Q2") (pin "1") (pinfunction "B") (pintype "input")))
    (net (code "5") (name "/C") (node (ref "Q2") (pin "2") (pinfunction "C") (pintype "passive")))
    (net (code "6") (name "/E") (node (ref "Q2") (pin "3") (pinfunction "E") (pintype "passive")))))
'''


class PinOrderTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)
        self.net = os.path.join(self.dir, 'q.net')
        with open(self.net, 'w', encoding='utf-8') as f:
            f.write(NET)
        self.comps = parse_netlist(self.net).components

    def _type(self, ref):
        c = self.comps[ref]
        return guess_type_id(ref, c.value, c.symbol, c.lib, c.description, c.pin_count,
                             c.properties)

    def test_pin_names_give_the_order_without_sim_pins(self):
        self.assertEqual(self._type('Q1'), 'NMOS_GDS')
        self.assertEqual(ALL_DEFS['NMOS_GDS'].pin_names, {1: 'G', 2: 'D', 3: 'S'})
        self.assertEqual(self._type('Q2'), 'NPN_BCE')

    def test_sim_pins_still_wins(self):
        c = self.comps['Q1']
        props = dict(c.properties, **{'Sim.Pins': '1=G 2=S 3=D'})
        self.assertEqual(guess_type_id('Q1', c.value, c.symbol, c.lib, c.description, 3, props),
                         'NMOS')

    def test_variants_by_function_match_the_hand_written_ones(self):
        # the base types' table, rebuilt from pin functions, must come out the same
        from breadboard.model.components import _variants_by_function
        for base in _PIN_ORDER_CANONICAL:
            with self.subTest(base=base):
                self.assertEqual(_variants_by_function(ALL_DEFS[base], TO92_PINOUT_VARIANTS[base]),
                                 TO92_PINOUT_VARIANTS[base])

    def test_derived_types_get_the_variants_too(self):
        names = [n for n, _ in TO92_PINOUT_VARIANTS['NMOS']]
        self.assertEqual([n for n, _ in TO92_PINOUT_VARIANTS['NMOS_GDS']], names)
        gds = dict(TO92_PINOUT_VARIANTS['NMOS_GDS'])['G-D-S']
        self.assertEqual({p: o.col_delta for p, o in gds.items()}, {1: 0, 2: 1, 3: 2})
        self.assertIn('NMOS_GDS', TO92_PINOUT_VARIANTS)
        self.assertNotIn('NMOS_XYZ', TO92_PINOUT_VARIANTS)

    def test_cli_places_a_to220_in_its_own_order(self):
        s = hl.Session.create(os.path.join(self.dir, 'q.kicad_bbrd'), self.net,
                              prefs=Preferences())
        info = {c['ref']: c for c in s.info()['components']}
        self.assertIn('G-D-S', info['Q1']['pinouts'])
        self.assertEqual(info['Q1']['pins']['2']['name'], 'D')
        holes = s.place('Q1', anchor='a10', pinout='G-D-S').pin_holes
        self.assertEqual(holes, {1: TieHole(10, 'a'), 2: TieHole(11, 'a'), 3: TieHole(12, 'a')})


if __name__ == '__main__':
    unittest.main()
