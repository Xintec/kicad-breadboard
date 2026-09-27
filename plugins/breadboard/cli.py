"""
Command-line access to a breadboard session, without opening the window.

Run with KiCad's Python (it has wx, which only `render` needs):

    python cli.py --session board.kicad_bbrd <command> [args]

Commands (holes as in headless.py: e10, e10@1, top_plus:3, GND, MCU1.5):
    new --netlist X.net [--layout full] [--[no-]rail-split]   create the session file
    netlist --schematic X.kicad_sch       re-export the .net with kicad-cli
    info                                  parts, pins, nets, placements, wires
    place REF 1=a10 2=a14                 two-pin part, pin by pin
    place REF --anchor e20 [--rot N] [--pinout E-B-C]   DIP / 3+ pins
    place REF --at X,Y                    free-floating module (canvas px)
          [--led-color green] [--replace]
    remove REF
    omit REF... / unomit REF...            parts deliberately not mounted
    wire A B [--color #rrggbb]  /  unwire A B
    terminal NAME NET                     assign a binding post ('' clears)
    check                                 schematic match + physical conflicts
    simulate [V1=5 V2=-5]                 DC operating point (ngspice)
    render OUT.png [--no-check]           draw the board as the window does
    batch                                 one command per stdin line; saved
                                          only if every line succeeds

Output is JSON on stdout. Exit status: 0 ok, 1 check found problems,
2 the request was refused (the JSON says why).
"""
from __future__ import annotations

import os
import sys

if __name__ == '__main__' and not __package__:
    # Run as a script: import the package properly, and block pcbnew first —
    # breadboard/__init__.py would otherwise register the action plugin, which
    # outside a running KiCad fails an assert shown as a modal dialog.
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    sys.modules.setdefault('pcbnew', None)
    from breadboard.cli import main
    sys.exit(main())

import argparse  # noqa: E402
import json  # noqa: E402
import shlex  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402
from typing import List, Optional  # noqa: E402

from .headless import CliError, Session  # noqa: E402

EXIT_OK, EXIT_PROBLEMS, EXIT_REFUSED = 0, 1, 2


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog='cli.py', description=__doc__.split('\n\n')[0])
    p.add_argument('--session', help='.kicad_bbrd file')
    p.add_argument('--netlist', help='override the netlist named in the session')
    sub = p.add_subparsers(dest='cmd', required=True)

    s = sub.add_parser('new')
    s.add_argument('--layout')
    s.add_argument('--rail-split', action=argparse.BooleanOptionalAction, default=None,
                   help="the board's power rails are split in the middle (default: prefs)")
    s.add_argument('--force', action='store_true', help='overwrite an existing session')

    s = sub.add_parser('netlist')
    s.add_argument('--schematic', required=True)
    s.add_argument('--out', help='default: next to the schematic, .net')

    sub.add_parser('info')

    s = sub.add_parser('place')
    s.add_argument('ref')
    s.add_argument('pins', nargs='*', help='PIN=HOLE for two-pin parts')
    s.add_argument('--anchor')
    s.add_argument('--rot', type=int, default=0)
    s.add_argument('--pinout')
    s.add_argument('--led-color')
    s.add_argument('--at')
    s.add_argument('--replace', action='store_true')

    s = sub.add_parser('remove')
    s.add_argument('ref')

    for name in ('omit', 'unomit'):
        s = sub.add_parser(name)
        s.add_argument('refs', nargs='+')

    s = sub.add_parser('wire')
    s.add_argument('a')
    s.add_argument('b')
    s.add_argument('--color')

    s = sub.add_parser('unwire')
    s.add_argument('a')
    s.add_argument('b')

    s = sub.add_parser('terminal')
    s.add_argument('name')
    s.add_argument('net')

    sub.add_parser('check')

    s = sub.add_parser('simulate')
    s.add_argument('voltages', nargs='*', help='TERMINAL=VOLTS')

    s = sub.add_parser('render')
    s.add_argument('out')
    s.add_argument('--no-check', action='store_true', help='omit validation markers')

    sub.add_parser('batch')
    return p


class _ArgError(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    """argparse that raises instead of exiting, for batch lines."""
    def error(self, message):
        raise _ArgError(message)


def _kicad_cli() -> str:
    exe = 'kicad-cli.exe' if sys.platform == 'win32' else 'kicad-cli'
    beside = os.path.join(os.path.dirname(sys.executable), exe)   # KiCad's own Python
    return beside if os.path.isfile(beside) else (shutil.which('kicad-cli') or exe)


def _export_netlist(schematic: str, out: Optional[str]) -> dict:
    if not os.path.isfile(schematic):
        raise CliError(f'Schematic not found: {schematic}')
    out = out or os.path.splitext(schematic)[0] + '.net'
    try:
        r = subprocess.run([_kicad_cli(), 'sch', 'export', 'netlist', '--format',
                            'kicadsexpr', '-o', out, schematic],
                           capture_output=True, text=True, timeout=60)
    except FileNotFoundError:
        raise CliError('kicad-cli not found.')
    if r.returncode != 0:
        raise CliError(f'kicad-cli failed: {(r.stderr or r.stdout).strip()}')
    return {'ok': True, 'netlist': os.path.abspath(out)}


def _render(session: Session, out: str, with_check: bool) -> dict:
    import wx
    from .canvas import BreadboardCanvas, CanvasLayout
    from .model import validate

    app = wx.App(False)  # noqa: F841 — must exist while the canvas lives
    frame = wx.Frame(None)
    try:
        p = session.prefs
        canvas = BreadboardCanvas(frame, session.board, session.netlist)
        # As BreadboardWindow._init_canvas_from_prefs + session load do.
        canvas.show_net_labels = p.show_net_labels
        canvas.show_binding_posts = p.show_binding_posts
        canvas.show_baseboard = p.show_baseboard
        canvas.baseboard_color = p.baseboard_color
        canvas.show_branding = p.show_branding
        canvas.branding_image = p.branding_image
        canvas.rail_style = p.rail_style
        canvas.layout = CanvasLayout(session.board.layout, p.binding_post_side,
                                     p.show_branding, session.board.rail_split, p.num_terminals)
        canvas._annotations = [a for d in session.annotations
                               if (a := canvas._ann_from_json(d)) is not None]
        canvas._populate_module_pins()
        if with_check:
            canvas.set_validation_result(validate(session.board, session.netlist))
        if out.lower().endswith('.svg'):
            canvas.render_to_svg(out, include_net_labels=False)
        else:
            canvas.render_to_bitmap(include_net_labels=False).SaveFile(out, wx.BITMAP_TYPE_PNG)
    finally:
        frame.Destroy()
    return {'ok': True, 'image': os.path.abspath(out)}


def _pins(items: List[str]) -> dict:
    out = {}
    for it in items:
        pin, sep, hole = it.partition('=')
        if not sep or not pin.isdigit():
            raise CliError(f'Expected PIN=HOLE, got {it!r}.')
        out[int(pin)] = hole
    return out


def _run(args, session: Optional[Session]) -> tuple:
    """Execute one parsed command. Returns (result_dict, exit_code, dirty)."""
    c = args.cmd
    s = session
    if c == 'info':
        return s.info(), EXIT_OK, False
    if c == 'place':
        at = None
        if args.at:
            try:
                x, y = (int(v) for v in args.at.split(','))
            except ValueError:
                raise CliError('--at wants X,Y in canvas pixels.')
            at = (x, y)
        placed = s.place(args.ref, pins=_pins(args.pins) or None, anchor=args.anchor,
                         rot=args.rot, pinout=args.pinout, led_color=args.led_color,
                         at=at, replace=args.replace)
        from .headless import format_hole
        return ({'ok': True, 'ref': placed.ref,
                 'holes': {str(p): format_hole(h) for p, h in sorted(placed.pin_holes.items())}},
                EXIT_OK, True)
    if c == 'remove':
        s.remove(args.ref)
        return {'ok': True}, EXIT_OK, True
    if c in ('omit', 'unomit'):
        getattr(s, c)(args.refs)
        return {'ok': True, 'omitted': sorted(s.board.omitted)}, EXIT_OK, True
    if c == 'wire':
        s.wire(args.a, args.b, args.color)
        return {'ok': True}, EXIT_OK, True
    if c == 'unwire':
        s.unwire(args.a, args.b)
        return {'ok': True}, EXIT_OK, True
    if c == 'terminal':
        s.assign_terminal(args.name, args.net)
        return {'ok': True}, EXIT_OK, True
    if c == 'check':
        r = s.check()
        return r, (EXIT_OK if r['ok'] else EXIT_PROBLEMS), False
    if c == 'simulate':
        volts = {}
        for it in args.voltages:
            name, sep, v = it.partition('=')
            try:
                volts[name.upper()] = float(v)
            except ValueError:
                raise CliError(f'Expected TERMINAL=VOLTS, got {it!r}.')
        r = s.simulate_op(volts)
        return r, (EXIT_OK if r['ok'] else EXIT_PROBLEMS), False
    if c == 'render':
        return _render(s, args.out, not args.no_check), EXIT_OK, False
    raise CliError(f'{c} is not allowed here.')


def _batch(session: Session, lines) -> tuple:
    parser = _parser()
    parser.__class__ = _Parser
    for sp in parser._subparsers._group_actions[0].choices.values():
        sp.__class__ = _Parser
    results, dirty = [], False
    for n, line in enumerate(lines, 1):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        try:
            args = parser.parse_args(shlex.split(line))
            if args.cmd in ('new', 'netlist', 'batch', 'render'):
                raise CliError(f'{args.cmd} is not allowed in a batch.')
            res, _, d = _run(args, session)
        except (CliError, _ArgError) as exc:
            return ({'ok': False, 'error': f'line {n}: {line}: {exc}',
                     'done_before': len(results), 'saved': False}, EXIT_REFUSED, False)
        results.append(res)
        dirty = dirty or d
    return {'ok': True, 'lines': len(results), 'results': results}, EXIT_OK, dirty


def main(argv: Optional[List[str]] = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.cmd == 'netlist':
            result, code = _export_netlist(args.schematic, args.out), EXIT_OK
        else:
            if not args.session:
                raise CliError('--session is required.')
            if args.cmd == 'new':
                if os.path.exists(args.session) and not args.force:
                    raise CliError(f'{args.session} exists; pass --force to overwrite.')
                if not args.netlist:
                    raise CliError('new needs --netlist.')
                session = Session.create(args.session, args.netlist, layout=args.layout,
                                         rail_split=args.rail_split)
                session.save()
                result, code = session.info(), EXIT_OK
            else:
                session = Session.open(args.session, netlist=args.netlist)
                if args.cmd == 'batch':
                    result, code, dirty = _batch(session, sys.stdin)
                else:
                    result, code, dirty = _run(args, session)
                if dirty:
                    session.save()
    except CliError as exc:
        result, code = {'ok': False, 'error': str(exc)}, EXIT_REFUSED
    json.dump(result, sys.stdout, indent=1, ensure_ascii=False)
    sys.stdout.write('\n')
    return code


if __name__ == '__main__':
    sys.exit(main())
