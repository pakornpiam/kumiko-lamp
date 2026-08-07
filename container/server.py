#!/usr/bin/env python3
"""
Export service: turn a configurator parameter set into a ZIP of print-ready STLs.

This is the paid path.  The browser's own lattices are non-manifold overlapping
solids -- a deliberate trade for slider speed -- so they are not what anyone
should print.  Here the real CSG generator runs, every part is reloaded from its
STL and checked, and nothing is returned unless all of them pass.

It runs `kumiko_lamp.py` as a subprocess rather than importing it.  That keeps
one entry point authoritative: the whole tested sequence -- check_fits, emit,
check_part, the clearance pass, the exit code -- is the same one a local CLI run
takes, so what a customer downloads is byte-identical to what the maintainer can
reproduce on a laptop.  It also means a segfault deep in a native CSG library
kills a child process instead of the server.

No Stripe knowledge lives here, and this port is never exposed publicly.  The
Worker in front owns entitlement; this owns geometry.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GENERATOR = ROOT / "kumiko_lamp.py"

# A curved pattern at the finest pitch and the largest body measured 21 s, and
# that is the worst case reachable from the sliders.  Triple it: past that
# something is wrong, and a stuck child must not hold a worker thread forever.
BUILD_TIMEOUT_S = float(os.environ.get("BUILD_TIMEOUT_S", "60"))

# CSG is CPU-bound, so concurrency past the core count only makes every request
# slower and risks the OOM killer taking the whole container down.
MAX_CONCURRENT = int(os.environ.get("MAX_CONCURRENT", "2"))
_slots = threading.BoundedSemaphore(MAX_CONCURRENT)

MAX_BODY_BYTES = 64 * 1024

# The generator writes stl/<name>.stl; only ever ship those, never the preview
# SVGs and never the assembly preview, which is explicitly not printable.
SKIP_PARTS = {"assembly_preview"}

PATTERN_RE = re.compile(r"^[a-z0-9_]{1,40}$")

# Asked of the generator once at startup rather than hardcoded here, because the
# offered set is not static: LAITHAI_ENABLED hides a whole family, and Modern
# accepts only the kumiko ones.  A stale copy in this file would either refuse a
# pattern the app offers or accept one argparse then rejects on stderr with exit
# 2 -- which is a usage error, not a geometry failure, and must not be reported
# to a customer as "cannot be built".
_OFFERED = {}


def _load_offered():
    probe = ("import json, kumiko_lamp as k; "
             "print(json.dumps({'classic': list(k.pattern_names()), "
             "'modern': list(k.kumiko_pattern_names())}))")
    try:
        out = subprocess.run([sys.executable, "-c", probe], cwd=str(ROOT),
                             capture_output=True, text=True, timeout=120)
        if out.returncode == 0:
            return json.loads(out.stdout)
        sys.stderr.write(f"pattern probe failed: {out.stderr.strip()[:300]}\n")
    except Exception as e:                           # noqa: BLE001
        sys.stderr.write(f"pattern probe failed: {e!r}\n")
    # Empty means "cannot vouch for any name"; the generator still has the final
    # say, so this degrades to its own rejection rather than opening the gate.
    return {"classic": [], "modern": []}


class BuildError(Exception):
    def __init__(self, status, message, detail=None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.detail = detail or []


def build(params, pattern, style, part=None):
    """Run the generator in a scratch dir; return (bytes, manifest).

    `part` narrows the result to one STL; without it the whole set comes back
    as a ZIP.
    """
    if not PATTERN_RE.match(pattern):
        raise BuildError(400, "unknown pattern")
    if style not in ("classic", "modern"):
        raise BuildError(400, "unknown lantern style")
    offered = _OFFERED.get(style) or []
    if offered and pattern not in offered:
        # 400, not 422: the configuration is not unbuildable, the name is not on
        # offer for this style at all.  Modern takes kumiko patterns only.
        raise BuildError(400, f"pattern {pattern!r} is not available for {style}",
                         [f"available: {', '.join(offered)}"])

    work = Path(tempfile.mkdtemp(prefix="kumiko-"))
    try:
        pjson = work / "params.json"
        pjson.write_text(json.dumps(params), "utf-8")
        argv = [sys.executable, str(GENERATOR),
                "--pattern", pattern, "--style", style,
                "--params-json", str(pjson), "--out", str(work)]
        try:
            proc = subprocess.run(argv, capture_output=True, text=True,
                                  timeout=BUILD_TIMEOUT_S, cwd=str(work))
        except subprocess.TimeoutExpired:
            raise BuildError(504, "the build took too long", [
                f"exceeded {BUILD_TIMEOUT_S:.0f}s"])

        if proc.returncode != 0:
            # check_fits rejections, bad parameters and failed part checks all
            # land here.  Pass the generator's own reasons through: they are the
            # same wording checkFits shows in the browser, so a customer is not
            # told two different stories about one configuration.
            raise BuildError(422, "this configuration cannot be built",
                             _reasons(proc.stdout, proc.stderr))

        stl_dir = work / "stl"
        parts = sorted(p for p in stl_dir.glob("*.stl")
                       if p.stem not in SKIP_PARTS)
        if not parts:
            raise BuildError(500, "the build produced no parts")

        if part is not None:
            # The generator builds the whole set regardless, so a single part is
            # a filter on the result rather than a cheaper build.  Matched
            # against what was actually produced, so a name cannot escape the
            # directory however it is spelled.
            wanted = {p.stem: p for p in parts}
            if part not in wanted:
                raise BuildError(404, f"this lamp has no part named {part!r}",
                                 [f"parts: {', '.join(sorted(wanted))}"])
            p = wanted[part]
            return p.read_bytes(), [{"name": p.name, "bytes": p.stat().st_size}]

        buf = _zip(parts)
        manifest = [{"name": p.name, "bytes": p.stat().st_size} for p in parts]
        return buf, manifest
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _reasons(stdout, stderr=""):
    """Pull the generator's own failure lines out of its report."""
    out, capture = [], False
    for line in (stdout or "").splitlines():
        s = line.strip()
        if s in ("PROBLEMS:", "Parameter check FAILED:"):
            capture = True
            continue
        if capture and s.startswith("- "):
            out.append(s[2:])
        elif s.startswith("--params-json"):
            out.append(s)
    if not out and stderr:
        # argparse writes usage errors here and exits 2, so a stdout-only parse
        # produced an empty list -- and "cannot be built, no reasons given" is
        # the worst thing to hand someone who has just paid.
        tail = [l.strip() for l in stderr.strip().splitlines() if l.strip()]
        for l in tail:
            if ": error:" in l:
                out.append(l.split(": error:", 1)[1].strip())
        if not out and tail:
            out.append(tail[-1][:200])
    return out[:12] or ["the generator rejected this configuration"]


def _zip(parts):
    import io
    bio = io.BytesIO()
    # Deflate: STL is extremely repetitive and these run 8:1 or better, which
    # matters on a mobile connection.
    with zipfile.ZipFile(bio, "w", zipfile.ZIP_DEFLATED) as z:
        for p in parts:
            z.write(p, p.name)
    return bio.getvalue()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, status, body, ctype="application/json", extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            return self._send(200, {"ok": True})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/generate":
            return self._send(404, {"error": "not found"})
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return self._send(400, {"error": "bad content length"})
        if n <= 0 or n > MAX_BODY_BYTES:
            return self._send(413, {"error": "request body too large"})
        try:
            req = json.loads(self.rfile.read(n))
        except ValueError:
            return self._send(400, {"error": "body must be JSON"})
        if not isinstance(req, dict):
            return self._send(400, {"error": "body must be a JSON object"})

        params = req.get("params") or {}
        if not isinstance(params, dict):
            return self._send(400, {"error": "params must be an object"})

        # Fail fast rather than queue: a caller waiting behind a full semaphore
        # has no way to know how long that will be, and the Worker in front can
        # retry a 503 far more usefully than it can a hung socket.
        if not _slots.acquire(blocking=False):
            return self._send(503, {"error": "busy, retry shortly"},
                              extra={"Retry-After": "5"})
        try:
            part = req.get("part")
            if part is not None:
                part = str(part)
                if not PATTERN_RE.match(part):
                    raise BuildError(400, "unknown part")
            body, manifest = build(params,
                                   str(req.get("pattern") or "asanoha"),
                                   str(req.get("style") or "classic"), part)
        except BuildError as e:
            return self._send(e.status, {"error": e.message, "reasons": e.detail})
        except Exception as e:                       # noqa: BLE001
            # Never surface an internal traceback to a paying customer.
            sys.stderr.write(f"build failed: {e!r}\n")
            return self._send(500, {"error": "the build failed unexpectedly"})
        finally:
            _slots.release()

        single = len(manifest) == 1 and manifest[0]["name"].endswith(".stl") and part
        self._send(200, body,
                   "model/stl" if single else "application/zip",
                   {"X-Kumiko-Parts": str(len(manifest)),
                    "Content-Disposition": 'attachment; filename="%s"'
                                           % (manifest[0]["name"] if single else "kumiko-lamp.zip")})

    def log_message(self, fmt, *a):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % a))


def main():
    global _OFFERED
    port = int(os.environ.get("PORT", "8080"))
    _OFFERED = _load_offered()
    srv = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    sys.stderr.write(f"kumiko export service on :{port} "
                     f"(max {MAX_CONCURRENT} concurrent, {BUILD_TIMEOUT_S:.0f}s timeout, "
                     f"{len(_OFFERED.get('classic', []))} classic / "
                     f"{len(_OFFERED.get('modern', []))} modern patterns)\n")
    srv.serve_forever()


if __name__ == "__main__":
    main()
