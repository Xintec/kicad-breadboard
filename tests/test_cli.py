"""
End to end: cli.py run as a separate process with KiCad's Python, the way an
agent drives it. Run with KiCad's Python:
    "C:\\Program Files\\KiCad\\10.0\\bin\\python.exe" -m unittest discover -s tests
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
CLI = os.path.join(HERE, '..', 'plugins', 'breadboard', 'cli.py')
DEMO_NET = os.path.join(HERE, 'fixtures', 'demo.net')


class CliTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)
        self.session = os.path.join(self.dir, 'demo.kicad_bbrd')
        # An empty APPDATA/XDG dir: the user's saved prefs must not leak in.
        self.env = dict(os.environ, APPDATA=self.dir, XDG_CONFIG_HOME=self.dir)

    def cli(self, *args, stdin=None):
        r = subprocess.run([sys.executable, CLI, '--session', self.session, *args],
                           input=stdin, capture_output=True, text=True, timeout=60,
                           env=self.env)
        try:
            out = json.loads(r.stdout)
        except ValueError:
            self.fail(f'not JSON (exit {r.returncode}): {r.stdout!r} {r.stderr!r}')
        return r.returncode, out

    def new(self, layout='full'):
        code, out = self.cli('--netlist', DEMO_NET, 'new', '--layout', layout)
        self.assertEqual(code, 0, out)
        return out

    def test_new_writes_a_session_and_reports_it(self):
        out = self.new()
        self.assertTrue(os.path.isfile(self.session))
        self.assertEqual(out['layout'], 'full')

    def test_place_saves_to_the_file(self):
        self.new()
        code, out = self.cli('place', 'R1', '1=a10', '2=a14')
        self.assertEqual((code, out['holes']), (0, {'1': 'a10', '2': 'a14'}))
        with open(self.session, encoding='utf-8') as f:
            self.assertEqual([p['ref'] for p in json.load(f)['placements']], ['R1'])

    def test_refusal_is_json_with_exit_2_and_changes_nothing(self):
        self.new()
        before = open(self.session, encoding='utf-8').read()
        code, out = self.cli('place', 'U1', '--anchor', 'e62')
        self.assertEqual(code, 2)
        self.assertFalse(out['ok'])
        self.assertIn('not a hole', out['error'])
        self.assertEqual(open(self.session, encoding='utf-8').read(), before)

    def test_check_exits_1_while_the_build_is_incomplete(self):
        self.new()
        code, out = self.cli('check')
        self.assertEqual(code, 1)
        self.assertFalse(out['ok'])

    def test_batch_is_all_or_nothing(self):
        self.new()
        code, out = self.cli('batch', stdin='place R1 1=a10 2=a14\n'
                                            'place R2 1=a14 2=a18\n')   # a14 taken
        self.assertEqual(code, 2)
        self.assertIn('line 2', out['error'])
        _, info = self.cli('info')
        self.assertIsNone({p['ref']: p for p in info['components']}['R1']['holes'])

    def test_batch_applies_every_line(self):
        self.new()
        code, out = self.cli('batch', stdin='# divider\n'
                                            'place R1 1=a10 2=a14\n'
                                            'wire a11 top_plus:3\n'
                                            'terminal GND GND\n')
        self.assertEqual((code, out['lines']), (0, 3))
        _, info = self.cli('info')
        self.assertEqual(info['terminals'], {'GND': 'GND'})
        self.assertEqual(len(info['wires']), 1)

    def test_render_writes_a_png(self):
        self.new()
        self.cli('place', 'U1', '--anchor', 'e20')
        png = os.path.join(self.dir, 'board.png')
        code, out = self.cli('render', png)
        self.assertEqual(code, 0, out)
        with open(png, 'rb') as f:
            self.assertEqual(f.read(8), b'\x89PNG\r\n\x1a\n')


if __name__ == '__main__':
    unittest.main()
