"""Command line tools:

    python -m moza_fcd list                     # MOZA serial ports + HID interfaces (find the MB7F's PID here)
    python -m moza_fcd demo --port sim          # run the demo against the simulated device
    python -m moza_fcd demo                     # run the demo on real hardware (auto-detect)
    python -m moza_fcd demo --port COM7 -v      # explicit port, with protocol trace
    python -m moza_fcd demo --pid 0x1306        # auto-detect a different product ID
    python -m moza_fcd gui                      # desktop CDU emulator running the demo app
    python -m moza_fcd gui --app my_app.py      # ...running your app (a file with main(screen))
    python -m moza_fcd run --app my_app.py      # the same app on real hardware
    python -m moza_fcd hid                      # watch the device's keys (MB7F key names)
    python -m moza_fcd lights --scan            # find which index lights which lamp
    python -m moza_fcd lights --set EXEC=on     # control status lights
"""

from __future__ import annotations

import argparse
import sys
import time

from . import protocol as p
from .device import FcdScreen, list_moza_ports
from .page import Page


def _int(s: str) -> int:
    return int(s, 0)


def cmd_list(_args) -> int:
    try:
        ports = list_moza_ports()
        print(f"Serial ports with VID 0x{p.VENDOR_ID:04X}:")
        for port in ports or []:
            known = "  <- known FCD PID" if port.pid in p.KNOWN_FCD_PRODUCT_IDS else ""
            print(f"  {port}{known}")
        if not ports:
            print("  (none)")
    except RuntimeError as e:
        print(f"(serial listing skipped: {e})")
    if sys.platform == "win32":
        from . import hidwin
        interfaces = hidwin.enumerate_devices(vid=p.VENDOR_ID)
        print(f"\nHID interfaces with VID 0x{p.VENDOR_ID:04X}:")
        for i in interfaces or []:
            print(f"  {i}{'  <- HID game controller' if i.is_game_controller else ''}")
        if not interfaces:
            print("  (none)")
    print("\nThe FCD exposes a CDC serial port (screen + main-board commands) and a HID game controller "
          "(keys + light sensors) with the same PID.")
    return 0


def demo_page(t: float) -> Page:
    pg = Page()
    pg.center(0, "MOZA FCD PYTHON", "cyan")
    pg.write(1, 0, " CLOCK", small=True).right(1, "DATE ", small=True)
    pg.write(2, 0, time.strftime("%H:%M:%S"), "green").right(2, time.strftime("%d %b").upper(), "green")
    pg.write(3, 0, " COLOURS", small=True)
    for i, name in enumerate(("cyan", "red", "yellow", "green", "magenta", "amber", "white", "grey")):
        pg.write(4 + i // 4, (i % 4) * 6, name[:5].upper(), name)
    pg.write(6, 0, " SMALL / LARGE", small=True)
    pg.write(7, 0, "large", "white").write(7, 6, "small", "white", small=True)
    bar = int((t % 6) / 6 * 24)
    pg.write(9, 0, "#" * bar, "amber")
    pg.write(11, 0, "<RETURN", "white").right(11, "NEXT>", "white")
    pg.fill(13, " ").write(13, 0, "[ SCRATCHPAD ]", "white")
    return pg


def cmd_demo(args) -> int:
    trace = (lambda m: print(f"[trace] {m}")) if args.verbose else None
    screen = FcdScreen(port=args.port, product_ids=args.pid or p.KNOWN_FCD_PRODUCT_IDS,
                       on_trace=trace, on_error=lambda m: print(f"[error] {m}", file=sys.stderr))
    screen.on_cabin_position = lambda pos: print(f"Cabin position: {'captain' if pos == 0 else 'first officer'} ({pos})")
    with screen:
        print(f"Connected on {screen.port_name}; waiting for MCDU mode (settings window ~4 s)...")
        start = time.monotonic()
        screen.show(demo_page(0))
        if not screen.wait_ready(args.timeout):
            print(f"Device never became ready (state={screen.state.value}). Try -v for a trace.", file=sys.stderr)
            return 1
        print(f"Ready. MCU id={screen.mcu_id.hex() if screen.mcu_id else '?'}  "
              f"supported settings={[hex(x) for x in (screen.supported_setting_ids or b'')]}")
        print("Updating every 0.5 s - Ctrl+C to stop.")
        try:
            while args.seconds <= 0 or time.monotonic() - start < args.seconds:
                screen.show(demo_page(time.monotonic() - start))
                time.sleep(0.5)
                if screen.simulator is not None:
                    sys.stdout.write("\x1b[H\x1b[2J" + screen.simulator.render_ansi() + "\n")
                    sys.stdout.flush()
        except KeyboardInterrupt:
            pass
        if args.restore:
            screen.set_display_mode(0)
            time.sleep(0.5)
    return 0


def cmd_hid(args) -> int:
    from . import hidwin
    from . import keys as K
    from .buttons import FcdKeyReader
    vid = args.vid if args.vid is not None else p.VENDOR_ID
    found = [d for d in hidwin.enumerate_devices(vid=vid, pids=args.pid or None) if d.is_game_controller]
    if not found:
        print(f"No HID game controller found for VID 0x{vid:04X}. Try `python -m moza_fcd list`.", file=sys.stderr)
        return 1
    for i, d in enumerate(found):
        print(f"  [{i}] {d}")
    iface = found[args.index]
    print(f"Watching [{args.index}] - press keys (names use the MB7F map). Ctrl+C to stop.")
    numbers = {name: n for n, name in K.MB7F_HID_BUTTONS.items()}

    def on_key(name: str, pressed: bool) -> None:
        label = f"button {numbers[name]:3d} = {name}" if name in numbers else name
        print(f"  {label:28s} {'DOWN' if pressed else 'up'}")

    def on_axis(name: str, value: int) -> None:
        if args.axes:
            print(f"  {name} = {value}")

    with FcdKeyReader(on_key, on_axis, interface=iface, on_error=lambda m: print(f"[error] {m}")):
        try:
            while True:
                time.sleep(0.2)
        except KeyboardInterrupt:
            pass
    return 0


def cmd_lights(args) -> int:
    from . import keys as K
    with FcdScreen(port=args.port, product_ids=args.pid or p.KNOWN_FCD_PRODUCT_IDS,
                   on_error=lambda m: print(f"[error] {m}", file=sys.stderr)) as screen:
        if not screen.wait_ready(args.timeout):
            print(f"Device never became ready (state={screen.state.value}).", file=sys.stderr)
            return 1
        if args.scan:
            print("Lighting CD_LedEnable indices 0-20 one at a time - note which lamp lights for each.")
            for index in range(21):
                screen.set_light_raw(index, True)
                print(f"  index {index:2d} ON")
                time.sleep(args.delay)
                screen.set_light_raw(index, False)
        for item in args.set or []:
            name, _, state = item.partition("=")
            ok = screen.set_light(name.upper(), state.lower() in ("1", "on", "true"))
            note = "(sent)" if ok else f"(not sent - lights are {', '.join(K.LIGHTS)})"
            print(f"  {name.upper()} -> {state} {note}")
        if args.test is not None:
            screen.send_device_command(p.CMD_LED_TEST_ALL, (1 if args.test == "on" else 0,))
        if args.brightness is not None:
            screen.set_brightness(args.brightness, args.group)
        time.sleep(0.5)  # let queued frames go out before closing
        if screen.simulator is not None:
            print(f"  simulator lights={screen.simulator.lights} brightness={screen.simulator.brightness}")
    return 0


def load_app(spec: str):
    """'path/to/app.py' or 'package.module' -> its main(screen) function."""
    import importlib
    import importlib.util
    import os
    if spec.endswith(".py") or os.path.sep in spec or "/" in spec:
        path = os.path.abspath(spec)
        sys.path.insert(0, os.path.dirname(path))
        name = os.path.splitext(os.path.basename(path))[0]
        module_spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(module_spec)
        sys.modules[name] = module  # must be registered before exec: dataclasses etc. look the module up
        module_spec.loader.exec_module(module)
    else:
        module = importlib.import_module(spec)
    main_fn = getattr(module, "main", None)
    if not callable(main_fn):
        raise RuntimeError(f"{spec} has no main(screen) function")
    import inspect
    try:
        inspect.signature(main_fn).bind(None)
    except TypeError:
        raise RuntimeError(f"{spec}: main() must accept one argument, the screen - def main(screen): ...")
    return main_fn


def cmd_gui(args) -> int:
    from . import gui
    app_main = None if args.app == "keyecho" else load_app(args.app)
    gui.run(app_main)
    return 0


def _log_exit(message: str) -> None:
    print(f"[run] {time.strftime('%H:%M:%S')} {message}", file=sys.stderr, flush=True)


def cmd_run(args) -> int:
    import faulthandler
    faulthandler.enable()            # a native crash (e.g. in a COM/ctypes call) prints a stack instead of vanishing
    app_main = load_app(args.app)
    trace = (lambda m: print(f"[trace] {m}")) if args.verbose else None
    started = time.monotonic()
    with FcdScreen(port=args.port, product_ids=args.pid or p.KNOWN_FCD_PRODUCT_IDS, on_trace=trace,
                   on_error=lambda m: print(f"[error] {m}", file=sys.stderr)) as screen:
        print(f"Connected on {screen.port_name}; waiting for MCDU mode...")
        if not screen.wait_ready(args.timeout):
            print(f"Device never became ready (state={screen.state.value}).", file=sys.stderr)
            return 1
        reader = None
        if screen.simulator is None and not args.no_keys:
            reader = _start_key_reader(screen, args.pid or p.KNOWN_FCD_PRODUCT_IDS)
        reason = "app returned"
        try:
            app_main(screen)
        except KeyboardInterrupt:
            reason = "stopped by Ctrl+C (KeyboardInterrupt)"
        except BaseException as e:   # Python prints the traceback after this
            reason = f"app raised {type(e).__name__}: {e}"
            raise
        finally:
            if reader:
                reader.stop()
            minutes = (time.monotonic() - started) / 60
            _log_exit(f"exiting after {minutes:.1f} min - {reason}; device state {screen.state.value}")
    return 0


def _start_key_reader(screen: FcdScreen, product_ids):
    """Feed the device's HID keys into screen.on_key (and light sensors into screen.light_levels);
    warn (don't fail) if unavailable."""
    try:
        from .buttons import FcdKeyReader
        reader = FcdKeyReader(screen.emit_key, screen.emit_axis, product_ids=product_ids,
                              on_error=lambda m: print(f"[keys] {m}", file=sys.stderr)).start()
        print(f"Keys: reading {reader.interface}")
        return reader
    except (RuntimeError, OSError) as e:
        print(f"Keys unavailable ({e}); the screen still works.", file=sys.stderr)
        return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m moza_fcd", description="MOZA FCD display tools")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="list MOZA serial ports and HID interfaces").set_defaults(func=cmd_list)

    d = sub.add_parser("demo", help="draw a live demo page")
    d.add_argument("--port", help='serial port (e.g. COM7) or "sim"; default auto-detect')
    d.add_argument("--pid", type=_int, action="append", help="product ID to auto-detect (repeatable)")
    d.add_argument("--seconds", type=float, default=0, help="stop after N seconds (default: run until Ctrl+C)")
    d.add_argument("--timeout", type=float, default=15, help="ready timeout in seconds")
    d.add_argument("--restore", action="store_true", help="switch back to the device's own UI on exit")
    d.add_argument("-v", "--verbose", action="store_true", help="print protocol trace")
    d.set_defaults(func=cmd_demo)

    g = sub.add_parser("gui", help="open the desktop CDU emulator (no hardware needed)")
    g.add_argument("--app", default="moza_fcd.demo_app",
                   help='app file/module defining main(screen); "keyecho" just shows key presses '
                        "(default: built-in demo)")
    g.set_defaults(func=cmd_gui)

    r = sub.add_parser("run", help="run an app (main(screen)) on real hardware or --port sim")
    r.add_argument("--app", default="moza_fcd.demo_app")
    r.add_argument("--port", help='serial port (e.g. COM7) or "sim"; default auto-detect')
    r.add_argument("--pid", type=_int, action="append", help="product ID to auto-detect (repeatable)")
    r.add_argument("--timeout", type=float, default=15)
    r.add_argument("--no-keys", action="store_true", help="don't read the device's HID keys")
    r.add_argument("-v", "--verbose", action="store_true")
    r.set_defaults(func=cmd_run)

    li = sub.add_parser("lights", help="control status lights / brightness (main-board commands)")
    li.add_argument("--port", help='serial port (e.g. COM7) or "sim"; default auto-detect')
    li.add_argument("--pid", type=_int, action="append")
    li.add_argument("--timeout", type=float, default=15)
    li.add_argument("--scan", action="store_true", help="light indices 0-20 one by one to find the real light IDs")
    li.add_argument("--delay", type=float, default=1.5, help="seconds per index when scanning")
    li.add_argument("--set", action="append", metavar="NAME=on|off", help="e.g. --set EXEC=on (repeatable)")
    li.add_argument("--test", choices=("on", "off"), help="all-lights test mode (CD_LedTestAll)")
    li.add_argument("--brightness", type=int, help="0-255")
    li.add_argument("--group", type=int, default=0, help="lighting group for --brightness (0 = all, 1-3)")
    li.set_defaults(func=cmd_lights)

    h = sub.add_parser("hid", help="show key presses / light sensors from the device's HID interface")
    h.add_argument("--pid", type=_int, action="append")
    h.add_argument("--vid", type=_int, help="vendor ID (default MOZA 0x346E); any HID game controller works for testing")
    h.add_argument("--index", type=int, default=0, help="which matching interface to open")
    h.add_argument("--axes", action="store_true", help="also print light-sensor/axis changes")
    h.set_defaults(func=cmd_hid)

    args = ap.parse_args(argv)
    try:
        return args.func(args)
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
