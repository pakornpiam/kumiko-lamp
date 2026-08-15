#!/usr/bin/env python3
"""
Kumiko Lamp desktop application: the configurator and its export service in one
process, served to the local browser.

The page is unchanged -- it is the same `web/index.html` that deploys -- and so
is the export path behind it.  What the Worker does in production (own
entitlement, forward geometry to the export service) is done here by
`tools/dev_export_server.py` with entitlement standing open, because a desktop
install has no customers to charge.  Everything downstream of that is the real
thing: `container/server.py` runs `kumiko_lamp.py` as a child process, which
runs `check_fits`, `emit`, `check_part` and the clearance pass, and refuses to
return anything that fails them.  A lamp this app declines to export is one the
hosted app would decline too.

Three entry points, selected by the first argument:

    (none)                 start the servers, open the browser, show the window
    --kumiko-generator     run kumiko_lamp.main() on the remaining arguments
    --kumiko-patterns      print the offered pattern names as JSON

The last two exist because a frozen build has no python.exe to shell out to.
`container/server.py` re-enters this executable through them, which keeps its
subprocess boundary intact rather than trading it for an in-process import that
would put a native CSG segfault in the server's own address space.
"""

import argparse
import json
import socket
import sys
import threading
import webbrowser
from http.server import ThreadingHTTPServer
from pathlib import Path

APP_NAME = "Kumiko Lamp"

# Tried in order, so a returning user finds the app where they left it; port 0
# is the last resort and always succeeds.
PREFERRED_PORTS = (8790, 8791, 8792, 8793, 8794, 0)


def hide_own_console():
    """Hide the console window, but only when it is ours alone.

    The app is built as a console binary on purpose: a windowed PyInstaller
    build sets sys.stdout and sys.stderr to None, which would break the export
    service's request log and -- far worse -- the child process handshake, since
    the generator reports its refusals on stdout and `_reasons` parses them.
    Keeping real streams and hiding the window afterwards gets both.

    GetConsoleProcessList tells us whether we were double-clicked (this process
    alone owns a fresh console) or launched from someone's terminal, which must
    never be hidden out from under them.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        wnd = k32.GetConsoleWindow()
        if not wnd:
            return
        buf = (ctypes.c_uint * 8)()
        if k32.GetConsoleProcessList(buf, 8) == 1:
            ctypes.windll.user32.ShowWindow(wnd, 0)      # SW_HIDE
    except Exception:                                    # noqa: BLE001
        pass


def log(msg):
    """stderr is not guaranteed to exist in a packaged build."""
    try:
        if sys.stderr is not None:
            sys.stderr.write(msg)
            sys.stderr.flush()
    except Exception:                                    # noqa: BLE001
        pass


def bundle_root():
    """Where the bundled data lives: the PyInstaller temp dir, or the repo."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


# --------------------------------------------------------------- re-entry

def run_generator():
    """Dispatch to the generator, as `python kumiko_lamp.py ...` would."""
    _add_root_to_path()
    import kumiko_lamp
    # Drop the sentinel so argparse sees the flags the export service passed.
    return kumiko_lamp.main(sys.argv[2:])


def print_patterns():
    _add_root_to_path()
    import kumiko_lamp as k
    print(json.dumps({"classic": list(k.pattern_names()),
                      "modern": list(k.kumiko_pattern_names())}))
    return 0


# --------------------------------------------------------------- servers

def _listen(handler, host, ports):
    last = None
    for p in ports:
        try:
            return ThreadingHTTPServer((host, p), handler)
        except OSError as e:                          # port in use
            last = e
    raise last


def _add_root_to_path():
    """Run as a script, sys.path[0] is desktop/, so the siblings are invisible.

    Frozen, the modules are already in the bundle and this changes nothing.
    """
    root = str(bundle_root())
    if root not in sys.path:
        sys.path.insert(0, root)


def start_servers(host="127.0.0.1"):
    """Bring up the export service and the page front end.  Returns the URL."""
    _add_root_to_path()
    from container import server as export_service
    from tools import dev_export_server as front

    # The pattern list is probed from the generator rather than hardcoded, for
    # the same reason it is in production: the offered set is not static.
    export_service._OFFERED = export_service._load_offered()
    svc = _listen(export_service.Handler, "127.0.0.1", (0,))
    svc_port = svc.server_address[1]
    threading.Thread(target=svc.serve_forever, daemon=True,
                     name="export-service").start()

    root = bundle_root()
    front.PAGE = root / "web" / "index.html"
    front.ARGS = argparse.Namespace(
        export=f"http://127.0.0.1:{svc_port}", host=host, port=None)

    web = _listen(front.Handler, host, PREFERRED_PORTS)
    port = web.server_address[1]
    threading.Thread(target=web.serve_forever, daemon=True, name="page").start()

    return f"http://{host}:{port}/index.html", svc_port


# --------------------------------------------------------------- window

def show_window(url, on_quit):
    """A small control window, so the app can be seen and closed like an app.

    Tkinter is in the standard library, so this costs no dependency.  If it is
    unavailable the app still runs -- it just has to be closed from the task
    manager, which is why the failure is reported rather than swallowed.
    """
    import tkinter as tk

    win = tk.Tk()
    win.title(APP_NAME)
    win.resizable(False, False)
    frame = tk.Frame(win, padx=22, pady=18)
    frame.pack()

    tk.Label(frame, text=APP_NAME, font=("Segoe UI", 15, "bold")).pack(anchor="w")
    tk.Label(frame, text="Running on this computer. Nothing is sent anywhere.",
             font=("Segoe UI", 9), fg="#5c5046").pack(anchor="w", pady=(2, 12))

    entry = tk.Entry(frame, width=38, font=("Consolas", 9), justify="center")
    entry.insert(0, url)
    entry.configure(state="readonly")
    entry.pack(pady=(0, 12))

    row = tk.Frame(frame)
    row.pack()
    tk.Button(row, text="Open designer", width=16, default="active",
              command=lambda: webbrowser.open(url)).pack(side="left", padx=(0, 8))

    def quit_app():
        on_quit()
        win.destroy()

    tk.Button(row, text="Quit", width=10, command=quit_app).pack(side="left")
    win.protocol("WM_DELETE_WINDOW", quit_app)

    # Centre on the primary display rather than the OS default corner.
    win.update_idletasks()
    w, h = win.winfo_width(), win.winfo_height()
    win.geometry("+%d+%d" % ((win.winfo_screenwidth() - w) // 2,
                             (win.winfo_screenheight() - h) // 3))
    win.mainloop()


# --------------------------------------------------------------- main

def main():
    if len(sys.argv) > 1:
        if sys.argv[1] == "--kumiko-generator":
            return run_generator()
        if sys.argv[1] == "--kumiko-patterns":
            return print_patterns()

    ap = argparse.ArgumentParser(prog=APP_NAME)
    ap.add_argument("--no-browser", action="store_true",
                    help="start the servers but do not open a browser")
    ap.add_argument("--headless", action="store_true",
                    help="no control window; serve until interrupted")
    ap.add_argument("--host", default="127.0.0.1",
                    help="bind address; 0.0.0.0 exposes the app to your network")
    opts = ap.parse_args(sys.argv[1:])

    if getattr(sys, "frozen", False) and not opts.headless:
        hide_own_console()

    try:
        url, svc_port = start_servers(opts.host)
    except OSError as e:
        log(f"{APP_NAME}: could not start ({e})\n")
        return 1

    log(f"{APP_NAME}\n  page:   {url}\n"
        f"  export: http://127.0.0.1:{svc_port}\n")
    if not opts.no_browser:
        webbrowser.open(url)

    if opts.headless:
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            pass
        return 0

    try:
        show_window(url, on_quit=lambda: None)
    except Exception as e:                            # noqa: BLE001
        log(f"{APP_NAME}: no window ({e}); serving until interrupted\n")
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
