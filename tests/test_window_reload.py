"""
The window follows its session file: when another program (the CLI) rewrites
it, the board reloads — as one undoable step — unless there are local edits,
in which case it asks first. Needs wx; run with KiCad's Python. The window is
created but never shown; the timer's handler is called directly.
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

import wx  # noqa: E402

from breadboard.headless import Session  # noqa: E402
from breadboard.model.breadboard import TieHole  # noqa: E402
from breadboard.prefs import Preferences  # noqa: E402
from breadboard.window import BreadboardWindow  # noqa: E402


class WindowReloadTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = wx.App(False)

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        net = os.path.join(self.dir, 'demo.net')
        shutil.copy(os.path.join(HERE, 'fixtures', 'demo.net'), net)
        self.path = os.path.join(self.dir, 'demo.kicad_bbrd')
        s = Session.create(self.path, net, layout='full', prefs=Preferences())
        s.place('R1', pins={1: 'a10', 2: 'a14'})
        s.save()
        self.win = BreadboardWindow(parent=None)
        self.win.Hide()
        self.addCleanup(self.win.Destroy)
        self.asked = []
        self.win._ask_reload = lambda path: self.asked.append(path) or self.answer
        self.answer = True
        self.win._on_load(path=self.path)

    def external_edit(self, **place):
        """What the CLI does: open the file, change it, save it."""
        s = Session.open(self.path, prefs=Preferences())
        s.place(**place)
        s.save()
        st = os.stat(self.path)   # make sure the mtime moves even on coarse clocks
        os.utime(self.path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))

    def test_a_rail_split_change_on_disk_is_followed(self):
        s = Session.open(self.path, prefs=Preferences())
        s.board.set_rail_split(not self.win.board.rail_split)
        s.save()
        st = os.stat(self.path)
        os.utime(self.path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
        self.win._check_session_on_disk()
        self.assertEqual(self.win.board.rail_split, s.board.rail_split)
        self.assertEqual(self.win.canvas.layout.rail_split, s.board.rail_split)

    def test_image_export_leaves_out_the_screen_legend(self):
        # the legend is placed by window size and drawn semi-transparent: in an
        # exported bitmap it came out as an empty white box in a corner
        from breadboard.canvas import BreadboardCanvas

        def boom(canvas, dc):
            raise AssertionError('net legend drawn')
        orig = BreadboardCanvas._draw_net_labels
        BreadboardCanvas._draw_net_labels = boom
        try:
            for ext in ('png', 'svg'):
                out = os.path.join(self.dir, 'board.' + ext)
                self.win._export_image_to(out)
                self.assertTrue(os.path.getsize(out) > 0)
        finally:
            BreadboardCanvas._draw_net_labels = orig

    def test_relative_netlist_path_resolves_against_the_session(self):
        # setUp's session names its netlist relative to itself ('demo.net')
        self.assertIsNotNone(self.win.netlist)
        self.assertIn('U1', self.win.netlist.components)

    def test_untouched_board_follows_the_file(self):
        self.external_edit(ref='R2', pins={1: 'a20', 2: 'a24'})
        self.win._check_session_on_disk()
        self.assertIsNotNone(self.win.board.get_placement('R2'))
        self.assertEqual(self.asked, [])

    def test_the_reload_can_be_undone(self):
        self.external_edit(ref='R2', pins={1: 'a20', 2: 'a24'})
        self.win._check_session_on_disk()
        self.win.canvas.undo()
        self.assertIsNone(self.win.board.get_placement('R2'))
        self.assertIsNotNone(self.win.board.get_placement('R1'))

    def test_nothing_happens_while_the_file_is_unchanged(self):
        self.win._check_session_on_disk()
        self.assertEqual(self.asked, [])
        self.assertFalse(self.win.canvas._undo_stack)

    def test_local_edits_are_not_overwritten_without_asking(self):
        self.win.canvas.push_undo()
        self.win.board.remove('R1')                       # a local, unsaved edit
        self.answer = False
        self.external_edit(ref='R2', pins={1: 'a20', 2: 'a24'})
        self.win._check_session_on_disk()
        self.assertEqual(self.asked, [self.path])
        self.assertIsNone(self.win.board.get_placement('R2'))   # kept mine
        self.win._check_session_on_disk()                        # …and does not nag
        self.assertEqual(len(self.asked), 1)

    def test_local_edits_give_way_when_the_user_agrees(self):
        self.win.canvas.push_undo()
        self.win.board.remove('R1')
        self.external_edit(ref='R2', pins={1: 'a20', 2: 'a24'})
        self.win._check_session_on_disk()
        self.assertEqual(self.asked, [self.path])
        self.assertIsNotNone(self.win.board.get_placement('R2'))

    def test_own_save_is_not_taken_for_an_external_change(self):
        self.win.canvas.push_undo()
        self.win.board.remove('R1')
        self.win._save_session_to(self.path)
        self.win._check_session_on_disk()
        self.assertEqual(self.asked, [])
        self.assertIsNone(self.win.board.get_placement('R1'))

    def test_waits_while_the_user_is_mid_action(self):
        self.win.canvas._wire_start = TieHole(5, 'a')     # drawing a wire
        self.external_edit(ref='R2', pins={1: 'a20', 2: 'a24'})
        self.win._check_session_on_disk()
        self.assertIsNone(self.win.board.get_placement('R2'))
        self.win.canvas._wire_start = None
        self.win._check_session_on_disk()
        self.assertIsNotNone(self.win.board.get_placement('R2'))

    def test_a_half_written_file_is_retried(self):
        with open(self.path, 'w', encoding='utf-8') as f:
            f.write('{"version": 1, "placem')
        self.win._check_session_on_disk()                # unreadable: keep board, retry
        self.assertIsNotNone(self.win.board.get_placement('R1'))
        # …then the writer finishes
        s = Session.create(self.path, os.path.join(self.dir, 'demo.net'), layout='full',
                           prefs=Preferences())
        s.place('R2', pins={1: 'a20', 2: 'a24'})
        s.save()
        self.win._check_session_on_disk()
        self.assertIsNotNone(self.win.board.get_placement('R2'))

    def test_saving_over_an_external_change_asks(self):
        self.external_edit(ref='R2', pins={1: 'a20', 2: 'a24'})
        confirmations = []
        self.win._confirm_overwrite = lambda path: confirmations.append(path) or False
        self.assertFalse(self.win._save_session_to(self.path))
        self.assertEqual(confirmations, [self.path])
        self.assertIsNotNone(Session.open(self.path, prefs=Preferences())
                             .board.get_placement('R2'))    # file untouched


if __name__ == '__main__':
    unittest.main()
