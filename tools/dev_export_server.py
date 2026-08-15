#!/usr/bin/env python3
"""
Local development front end for the export path.  Not deployed, ever.

`wrangler dev` cannot start on Windows while `[[containers]]` is configured, so
there is no way to drive the real Worker here without the scratch-wrangler.toml
dance.  This stands in for the Worker's two browser-facing routes and nothing
else, so the page's Download buttons work against a checkout of the repo:

    /api/me      always signed in and subscribed
    /api/export  forwarded verbatim to container/server.py at /generate

Entitlement is the only thing being stubbed.  Geometry is not: the request is
passed through untouched to the same export service the deployed Worker calls,
which shells out to the same `kumiko_lamp.py`, which runs the same `check_fits`,
`check_part` and clearance pass.  A configuration this refuses is one the
production stack refuses too, and the bytes it returns are the bytes a customer
would get from the same machine.

That also means this is not a way to sell files -- it is a way to test the page
without a Stripe account.  It binds to localhost by default for that reason.

    python container/server.py                  # the real export service, :8080
    python tools/dev_export_server.py            # this, :8790

    --port        listen port                       (default 8790)
    --export      export service origin             (default http://127.0.0.1:8080)
    --host        bind address; 0.0.0.0 exposes it to your LAN (default 127.0.0.1)
"""

import argparse
import json
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "web" / "index.html"

# Long enough to cover the export service's own 60 s build timeout plus the
# handful of seconds a cold pattern probe can add, so a slow-but-working build
# is never reported to the page as a dead service.
FORWARD_TIMEOUT_S = 120

ARGS = None


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "kumiko-dev"

    def _send(self, status, body, ctype="application/json", extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # The page is edited and reloaded constantly in dev; a cached copy is
        # indistinguishable from a change that did not take.
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            try:
                return self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
            except OSError as e:
                return self._send(500, {"error": f"cannot read {PAGE}: {e}"})
        if path == "/api/me":
            return self._send(200, {
                "signedIn": True,
                "email": "dev@localhost",
                "subscribed": True,
                # False, so the page does not offer a Subscribe button that
                # would only fail: there is no Stripe behind this.
                "priceConfigured": False,
            }, extra={"Cache-Control": "private, no-store"})
        if path == "/health":
            return self._send(200, {"ok": True, "export": ARGS.export})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path == "/api/auth/signout":
            return self._send(200, {"ok": True})
        if path != "/api/export":
            return self._send(404, {"error": "not found"})

        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return self._send(400, {"error": "bad content length"})
        if n <= 0 or n > 64 * 1024:
            return self._send(413, {"error": "request body too large"})
        raw = self.rfile.read(n)

        # Forwarded as received.  The deployed Worker revalidates pattern, style
        # and params before calling the service, but re-implementing that here
        # would be a second copy of the rules that could disagree with the first
        # -- and the service validates everything again regardless.
        req = urllib.request.Request(
            ARGS.export.rstrip("/") + "/generate", data=raw,
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=FORWARD_TIMEOUT_S) as r:
                body = r.read()
                ctype = r.headers.get("Content-Type", "application/octet-stream")
                disp = r.headers.get("Content-Disposition")
                return self._send(r.status, body, ctype,
                                  {"Content-Disposition": disp} if disp else None)
        except urllib.error.HTTPError as e:
            # The service's own status and reasons, passed through untouched --
            # the page renders those strings, and rewording them here is how one
            # configuration ends up with two explanations.
            body = e.read()
            ctype = e.headers.get("Content-Type", "application/json")
            return self._send(e.code, body, ctype)
        except urllib.error.URLError as e:
            return self._send(503, {
                "error": "the export service is not reachable",
                "reasons": [f"{ARGS.export}: {e.reason}",
                            "start it with: python container/server.py"],
            })

    def log_message(self, fmt, *a):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % a))


def main():
    global ARGS
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8790)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--export", default="http://127.0.0.1:8080")
    ARGS = ap.parse_args()

    if not PAGE.exists():
        sys.exit(f"page not found: {PAGE}")

    srv = ThreadingHTTPServer((ARGS.host, ARGS.port), Handler)
    sys.stderr.write(
        f"kumiko dev server on http://{ARGS.host}:{ARGS.port}/index.html\n"
        f"  page:   {PAGE}\n"
        f"  export: {ARGS.export}  (entitlement stubbed; geometry is real)\n")
    if ARGS.host == "0.0.0.0":
        sys.stderr.write("  NOTE: bound to all interfaces -- reachable from your network\n")
    srv.serve_forever()


if __name__ == "__main__":
    main()
