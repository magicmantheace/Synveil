#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Graphical browser + persistent downloads acceptance in QEMU
(Priorities 3 and 4).

Drives the browser proof disk assembled by
``tools/native_ab.py --build --browser``: generations that carry
guest networking (QEMU user networking / slirp — the guest reaches
the host at 10.0.2.2), the sealed NetSurf/GTK3 browser stage, and
the graphical file-access window. Hardware networking is NOT
exercised by this proof and remains unverified (docs/BROWSER.md).

The controlled HTTPS endpoint is a local test server on the host:

  * port 8443 — certificate for test.synveil.invalid / 10.0.2.2
    signed by a per-run test CA. That CA — and nothing else — is
    what the image build provisioned as the generations' trust
    bundle (the builder records its sha256; this driver refuses to
    run against a disk whose receipt names a different CA).
  * port 8444 — the same name/address, but a certificate from a
    DIFFERENT CA. A correctly verifying browser must refuse it:
    the gate requires zero GET requests at this port.

TLS verification is never disabled anywhere: not in the browser
(libcurl defaults), not in the fixture (real certificates, real
hostname), not in this driver (it speaks to the guest only through
the disk image and the QMP console).

Scenarios (one receipt, verdicts re-derived by validate_results):

  B0  provision + enroll + login -> SYNVEIL_NETWORK_READY, the
      browser autostarts from the desktop launcher path and loads
      the start page over verified HTTPS (server saw GET /; the
      page's proof color is on screen). The launcher's sandbox
      probe has already recorded a renderer living in its own
      user namespace with seccomp filtering on (kernel evidence
      from /proc — the sandbox is a hard gate, never a flag)
  B1  click the payload link -> payload.bin downloads directly
      into ~/Downloads with the fixture digest (digest computed
      INSIDE the guest, file bytes read back from the disk image
      host-side); the Chromium profile exists on the persistent
      DATA partition with its download directory pinned there
  B2  collision: download payload.bin again -> Chromium
      uniquifies to 'payload (1).bin'; the original file's digest
      is unchanged (no silent overwrite, ever)
  B3  insufficient storage: the 448 MiB payload exceeds the DATA
      partition's free space -> no complete big.bin remains and
      the browser stays alive (page proof again)
  B4  bad TLS: follow the :8444 link -> zero GETs reach the bad
      server (verification refused the handshake), the
      interstitial replaces the good page; Back returns to it
  B5  cold reboot -> login -> page loads again (GET count grows),
      in-guest digest still matches, the files window's scan lists
      payload.bin (graphical file access evidence, read back from
      its session log on DATA)
  B6  A/B update to v2 (agent path) -> confirm -> downloads,
      profile and sandbox records survive; payload2.bin downloads
      and verifies on v2
  B7  A/B rollback to v1 -> confirm -> both payloads still
      verify, and the cumulative sandbox record holds at least
      three sandboxed-renderer entries (one per browser start)

The gate re-derives every verdict from recorded fields: server
request counts per port/path, digests read from the disk image,
in-guest digest files, control-record generations, the files
window log, and the build receipt's CA/image identities. Missing
artifacts fail; a None digest never equals an expected digest.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import ssl
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from qmp_input import QMPError, send_keys, type_test_secret  # noqa: E402
import qmp_input as _qmp  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "smoke_native_ab", ROOT / "tests" / "smoke-native-ab.py")
ab = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ab)
weston_smoke = ab.weston_smoke

OUT = ROOT / "out" / "browser-proof"
EVIDENCE = OUT / "browser-evidence"
TLS_DIR = OUT / "tls-fixture"
DISK = OUT / "synveil-ab.img"
BUILD_RECEIPT = OUT / "build-receipt.json"
ab.EVIDENCE = EVIDENCE  # Boot logs land with this proof's evidence

HOST_NAME = "test.synveil.invalid"
HOST_IP = "10.0.2.2"
GOOD_PORT = 8443
BAD_PORT = 8444
START_URL = f"https://{HOST_NAME}:{GOOD_PORT}/"

# Frame-proof colors (RGB). Chosen to collide with nothing else in
# the session: generation wallpapers (0x101820 / 0x0B3D2E /
# 0x3D0B0B), the update GUI (title 0x5B2E6E), the files window
# (title 0x1D5C57), terminal black.
PAGE_BG = (0x1F, 0x4E, 0x79)
LINK_PAYLOAD = (0xE0, 0x7B, 0x24)
LINK_PAYLOAD2 = (0x27, 0xAE, 0x60)
LINK_BIG = (0x7D, 0x3C, 0x98)
LINK_BAD = (0xC0, 0x39, 0x2B)
FILES_TITLE = (0x1D, 0x5C, 0x57)

PAYLOAD_SIZE = 256 * 1024
BIG_SIZE = 448 * 1024 * 1024  # exceeds the 384 MiB DATA partition's
# free space once a browser profile exists — the download must fail


def payload_bytes(tag):
    out = bytearray()
    counter = 0
    while len(out) < PAYLOAD_SIZE:
        out += hashlib.sha256(
            f"synveil-browser-proof-{tag}-{counter}".encode()).digest()
        counter += 1
    return bytes(out[:PAYLOAD_SIZE])


PAYLOAD = payload_bytes("payload")
PAYLOAD_SHA256 = hashlib.sha256(PAYLOAD).hexdigest()
PAYLOAD2 = payload_bytes("payload2")
PAYLOAD2_SHA256 = hashlib.sha256(PAYLOAD2).hexdigest()

PAGE_HTML = f"""<!DOCTYPE html>
<html><head><title>Synveil Browser Proof</title></head>
<body style="background:#1f4e79; margin:0; font-family:sans-serif;">
<h1 style="color:#ffffff; text-align:center;">SYNVEIL BROWSER PROOF</h1>
<a href="/payload.bin"><div style="background:#e07b24; width:70%;
height:90px; margin:26px auto;"></div></a>
<a href="/payload2.bin"><div style="background:#27ae60; width:70%;
height:90px; margin:26px auto;"></div></a>
<a href="/big.bin"><div style="background:#7d3c98; width:70%;
height:90px; margin:26px auto;"></div></a>
<a href="https://{HOST_NAME}:{BAD_PORT}/"><div style="background:#c0392b;
width:70%; height:90px; margin:26px auto;"></div></a>
</body></html>""".encode()


# ------------------------------------------------------------------
# TLS fixture (run-local; keys never leave out/, never in Git)
# ------------------------------------------------------------------

def _openssl(*argv, cwd):
    subprocess.run(["openssl", *argv], cwd=cwd, check=True,
                   capture_output=True, text=True)


def prepare_tls(directory=TLS_DIR):
    """Create (once) the run-local CA + good/bad server certs."""
    directory.mkdir(parents=True, exist_ok=True)
    ca = directory / "ca.pem"
    if (directory / "ready.json").exists():
        return json.loads((directory / "ready.json").read_text())
    for name, cert_name in (("ca", "Synveil Browser Proof CA"),
                            ("badca", "Synveil Unrelated CA")):
        _openssl("req", "-x509", "-newkey", "rsa:2048", "-nodes",
                 "-keyout", f"{name}.key", "-out", f"{name}.pem",
                 "-days", "3", "-subj", f"/CN={cert_name}",
                 "-addext", "basicConstraints=critical,CA:TRUE",
                 "-addext", "keyUsage=critical,keyCertSign,cRLSign",
                 cwd=directory)
        _openssl("req", "-newkey", "rsa:2048", "-nodes",
                 "-keyout", f"server-{name}.key", "-out", f"server-{name}.csr",
                 "-subj", f"/CN={HOST_NAME}", cwd=directory)
        (directory / f"server-{name}.ext").write_text(
            f"subjectAltName=DNS:{HOST_NAME},IP:{HOST_IP}\n"
            "extendedKeyUsage=serverAuth\n")
        _openssl("x509", "-req", "-in", f"server-{name}.csr",
                 "-CA", f"{name}.pem", "-CAkey", f"{name}.key",
                 "-CAcreateserial", "-out", f"server-{name}.pem",
                 "-days", "3", "-extfile", f"server-{name}.ext",
                 cwd=directory)
    ready = {"ca_sha256": ab.sha256(ca),
             "host_name": HOST_NAME, "host_ip": HOST_IP,
             "good_port": GOOD_PORT, "bad_port": BAD_PORT}
    (directory / "ready.json").write_text(json.dumps(ready, indent=1) + "\n")
    return ready


# ------------------------------------------------------------------
# Controlled HTTPS endpoint
# ------------------------------------------------------------------

class RequestLog:
    def __init__(self):
        self.entries = []
        self._lock = threading.Lock()

    def add(self, **entry):
        entry.setdefault("t", round(time.time(), 3))
        with self._lock:
            self.entries.append(entry)

    def count(self, port, path=None):
        with self._lock:
            return sum(1 for e in self.entries
                       if e.get("port") == port and e.get("kind") == "GET"
                       and (path is None or e.get("path") == path))

    def handshake_errors(self, port):
        with self._lock:
            return sum(1 for e in self.entries
                       if e.get("port") == port
                       and e.get("kind") == "handshake-error")

    def snapshot(self):
        with self._lock:
            return list(self.entries)


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # keep the driver log clean
        pass

    def do_GET(self):
        log = self.server.request_log
        log.add(kind="GET", port=self.server.server_port, path=self.path)
        if self.path == "/":
            body = PAGE_HTML
            self._reply(200, "text/html", body)
        elif self.path == "/payload.bin":
            self._reply(200, "application/octet-stream", PAYLOAD)
        elif self.path == "/payload2.bin":
            self._reply(200, "application/octet-stream", PAYLOAD2)
        elif self.path == "/big.bin":
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(BIG_SIZE))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            chunk = b"\0" * (1024 * 1024)
            try:
                remaining = BIG_SIZE
                while remaining > 0:
                    piece = chunk[:min(len(chunk), remaining)]
                    self.wfile.write(piece)
                    remaining -= len(piece)
            except (BrokenPipeError, ConnectionResetError):
                pass  # the guest aborts when its disk fills; expected
        else:
            self._reply(404, "text/plain", b"not found\n")

    def _reply(self, code, ctype, body):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # Downloads must be real fetches every time — a repeat
        # click in the collision leg must not be cache-served.
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


class _TLSServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port, certfile, keyfile, log):
        self.request_log = log
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certfile, keyfile)
        self._context = context
        super().__init__(("0.0.0.0", port), _Handler)

    def get_request(self):
        sock, address = super().get_request()
        try:
            return self._context.wrap_socket(sock, server_side=True), address
        except (ssl.SSLError, ConnectionResetError, OSError):
            self.request_log.add(kind="handshake-error",
                                 port=self.server_port)
            raise


def start_servers(tls_dir, log):
    servers = []
    for port, stem in ((GOOD_PORT, "ca"), (BAD_PORT, "badca")):
        server = _TLSServer(port, str(tls_dir / f"server-{stem}.pem"),
                            str(tls_dir / f"server-{stem}.key"), log)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        servers.append(server)
    return servers


# ------------------------------------------------------------------
# Boot + frame driving
# ------------------------------------------------------------------

class NetBoot(ab.Boot):
    """The A/B proof Boot with QEMU user networking instead of
    `-net none`: slirp gives the guest an address by DHCP and routes
    10.0.2.2 to this host, where the fixture servers listen. The
    browser generations also need real RAM (uncompressed UKI plus
    a Chromium process tree): 768M becomes 3072M."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        argv = []
        index = 0
        while index < len(self.argv):
            if self.argv[index] == "-net" and self.argv[index + 1] == "none":
                argv += ["-netdev", "user,id=synveil_net",
                         "-device", "virtio-net-pci,netdev=synveil_net"]
                index += 2
            elif self.argv[index] == "-m":
                argv += ["-m", "3072M"]
                index += 2
            else:
                argv.append(self.argv[index])
                index += 1
        self.argv = argv


def _near(rgb, target, tol=6):
    return all(abs(a - b) <= tol for a, b in zip(rgb, target))


def color_fraction(width, height, data, target, tol=6):
    total = width * height
    hits = 0
    for y in range(0, height, 3):
        row = y * width * 3
        for x in range(0, width, 3):
            if _near(ab._px(data, width, x, y), target, tol):
                hits += 1
    return hits * 9 / total


def color_block(width, height, data, target, tol=8, min_hits=200):
    """Bounding box + centroid of a solid color block, or None."""
    min_x, min_y, max_x, max_y = width, height, -1, -1
    sum_x = sum_y = hits = 0
    for y in range(height):
        for x in range(width):
            if _near(ab._px(data, width, x, y), target, tol):
                hits += 1
                sum_x += x
                sum_y += y
                min_x = min(min_x, x)
                max_x = max(max_x, x)
                min_y = min(min_y, y)
                max_y = max(max_y, y)
    if hits < min_hits:
        return None
    return {"rect": (min_x, min_y, max_x - min_x + 1, max_y - min_y + 1),
            "center": (sum_x // hits, sum_y // hits), "hits": hits}


def click(boot, x, y):
    width, height, _data = ab.next_frame(boot)
    weston_smoke.pointer(boot.qmp, width, height, x, y, button=True)
    weston_smoke.pointer(boot.qmp, width, height, x, y, button=False)
    time.sleep(0.5)


def page_proof(boot, timeout=300):
    """Wait until the proof page's background covers a real share
    of the frame (the browser window showing the loaded page)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            frame = ab.next_frame(boot)
        except (ValueError, OSError):
            time.sleep(0.5)
            continue
        if color_fraction(*frame, PAGE_BG) >= 0.10:
            return True
        time.sleep(0.5)
    ab.save_failure_frame(boot, "page-proof")
    return False


def page_absent(boot, timeout=120):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            frame = ab.next_frame(boot)
        except (ValueError, OSError):
            time.sleep(0.5)
            continue
        if color_fraction(*frame, PAGE_BG) < 0.02:
            return True
        time.sleep(0.5)
    return False


def send_chord(boot, *qcodes, timeout=20.0):
    """Send a real key chord over QMP. The harness's send_keys
    passes each string to _hold_keys as ONE qcode, so a combo
    string like "ctrl+shift+w" is rejected by QEMU as an unknown
    qcode (the earlier browser-close failures); chords are tuples
    of individual qcodes held together, the shape _hold_keys (and
    the qmp_input test-suite) actually uses."""
    import socket as _socket
    deadline = time.monotonic() + timeout
    with _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM) as sock:
        while True:
            try:
                sock.settimeout(max(0.1, min(1.0,
                                             deadline - time.monotonic())))
                sock.connect(str(boot.qmp))
                break
            except (FileNotFoundError, ConnectionRefusedError):
                if time.monotonic() >= deadline:
                    raise QMPError("QMP socket unavailable")
                time.sleep(0.05)
        sock.settimeout(max(0.1, deadline - time.monotonic()))
        with sock.makefile("rb") as reader:
            _qmp._read_reply(reader, greeting=True)
            _qmp.emit_command(sock, reader,
                              {"execute": "qmp_capabilities"})
            _qmp._hold_keys(sock, reader, qcodes)


def click_link(boot, color, tag):
    """Click the page's link block identified by its color."""
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        try:
            frame = ab.next_frame(boot)
        except (ValueError, OSError):
            time.sleep(0.5)
            continue
        block = color_block(*frame, color)
        if block:
            save_frame(frame, f"link-{tag}")
            click(boot, *block["center"])
            return True
        time.sleep(0.5)
    ab.save_failure_frame(boot, f"link-{tag}")
    return False


def save_frame(frame, tag):
    """Keep one driver frame in the evidence dir for diagnosis."""
    try:
        width, height, data = frame
        ppm = EVIDENCE / f"{tag}.ppm"
        with ppm.open("wb") as handle:
            handle.write(f"P6\n{width} {height}\n255\n".encode())
            handle.write(bytes(data))
    except Exception:
        pass


def click_link_and_wait(boot, color, tag, log, port, path, count,
                        budget=480):
    """Click a download link until the fixture server records the
    GET. A first click on this stack can be consumed raising and
    focusing the browser window (observed: the effect of a click
    landing one interaction late), so the click repeats against a
    fresh frame until the server-side evidence appears — the
    verdict never rests on the click itself, only on the GET."""
    deadline = time.monotonic() + budget
    while time.monotonic() < deadline:
        if log.count(port, path) >= count:
            return True
        if not click_link(boot, color, tag):
            return False
        if wait_download_get(log, port, path, count, timeout=45):
            return True
    return log.count(port, path) >= count


INTERSTITIAL_BLUE = (0x1A, 0x73, 0xE8)


def back_from_interstitial(boot):
    """Leave Chromium's certificate-error interstitial: click its
    blue 'Back to safety' button when it can be located by color,
    else use the keyboard history shortcut."""
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            frame = ab.next_frame(boot)
        except (ValueError, OSError):
            time.sleep(0.5)
            continue
        block = color_block(*frame, INTERSTITIAL_BLUE, tol=30,
                            min_hits=300)
        if block:
            click(boot, *block["center"])
            time.sleep(1.0)
            return True
        time.sleep(0.5)
    try:
        send_chord(boot, "alt", "left", timeout=10)
        time.sleep(1.0)
        return True
    except (QMPError, OSError, ValueError):
        return False


def wait_download_get(log, port, path, count, timeout=240):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if log.count(port, path) >= count:
            return True
        time.sleep(0.5)
    return False


def _teal_title_block(width, height, data):
    """The files app's own title bar (COL_TITLE teal, drawn at the
    top of its content). The desktop's top panel is a similar
    dark teal and unions into any full-frame color search —
    observed rect (0, 0, 1038, ...) spanning panel + window —
    so only scan below the panel band (y >= 40) and take the
    title pixels' own bounding box."""
    min_x, min_y, max_x, max_y = width, height, -1, -1
    hits = 0
    for y in range(40, height):
        for x in range(width):
            if _near(ab._px(data, width, x, y), FILES_TITLE, 6):
                hits += 1
                min_x = min(min_x, x)
                max_x = max(max_x, x)
                min_y = min(min_y, y)
                max_y = max(max_y, y)
    if hits < 400 or max_x < 0:
        return None
    return (min_x, min_y, max_x - min_x + 1, max_y - min_y + 1)


def files_window_rect(width, height, data):
    """The files window by its app-drawn title bar: (x, y, w,
    content_h_nominal, title_h). The client requests 720x520
    (files-window.c WIN_W/WIN_H) but the shell may grant more;
    only x/y/w/title_h are measured — geometry that needs the
    real content height must measure it, not assume."""
    block = _teal_title_block(width, height, data)
    if not block:
        return None
    x, y, w, title_h = block
    return (x, y, max(w, 720), 520, title_h)


def close_files_window(boot):
    try:
        frame = ab.next_frame(boot)
    except (ValueError, OSError):
        return False
    rect = files_window_rect(*frame)
    if not rect:
        return True  # already closed
    x, y, w, _h, _title_h = rect
    click(boot, x + w - 14, y + 14)
    time.sleep(0.8)
    return True


def focus_terminal(boot, timeout=240):
    """ab.focus_terminal plus closing this session's extra windows
    (files window by its drawn close box; the browser window is
    closed by the caller before terminal work — see run order)."""
    deadline = time.monotonic() + timeout
    closed_files = False
    while time.monotonic() < deadline:
        if ab.focus_terminal(boot, timeout=20):
            return True
        if not closed_files:
            close_files_window(boot)
            closed_files = True
        time.sleep(0.5)
    ab.save_failure_frame(boot, "browser-focus-terminal")
    return False


def terminal_command(boot, command_id, wait_marker=None, timeout=300):
    if not focus_terminal(boot):
        ab.save_failure_frame(boot, f"cmd-{command_id}-focus")
        return False
    from qmp_input import type_ab_command
    mark = len(boot.lines)
    try:
        type_ab_command(boot.qmp, command_id, timeout=30)
    except (QMPError, OSError, ValueError):
        ab.save_failure_frame(boot, f"cmd-{command_id}-input")
        return False
    if wait_marker is None:
        time.sleep(2)
        return True
    ok = boot.wait(wait_marker, timeout, since=mark)
    if not ok:
        ab.save_failure_frame(boot, f"cmd-{command_id}-wait")
    return ok


# Browser window geometry, estimated from the loaded page's
# viewport (the only part whose pixels we control): GTK chrome
# above the viewport is titlebar + menubar + toolbar + tab strip.
# Constants are calibration points for this exact stack (Weston
# SSD + NetSurf GTK3 at 1280x720), verified against saved frames.
CHROME_ABOVE = 126
CHROME_BELOW = 30
CHROME_SIDE = 6
_BROWSER_RECT = {}


def remember_browser_rect(boot):
    try:
        frame = ab.next_frame(boot)
    except (ValueError, OSError):
        return None
    block = color_block(*frame, PAGE_BG)
    if not block:
        return None
    x, y, w, h = block["rect"]
    rect = (x - CHROME_SIDE, y - CHROME_ABOVE,
            w + 2 * CHROME_SIDE, h + CHROME_ABOVE + CHROME_BELOW)
    _BROWSER_RECT[id(boot)] = rect
    return rect


def close_browser_window(boot):
    """Close Chromium's window: focus it with a page click, then
    Ctrl+Shift+W (close window). The earlier pixel approach —
    deriving the SSD close box from the viewport with estimated
    chrome heights — put the click on the Weston panel for
    Chromium's real geometry (tab strip + toolbar ≈ 90 px, not
    the estimated 126), leaving the window open and the terminal
    behind it unreachable."""
    if not page_proof(boot, timeout=60):
        ab.save_failure_frame(boot, "browser-close-nopage")
        return False
    try:
        frame = ab.next_frame(boot)
        block = color_block(*frame, PAGE_BG)
    except (ValueError, OSError):
        block = None
    if block:
        bx, by, _bw, _bh = block["rect"]
        click(boot, bx + 30, by + 30)  # page title area: focus only
        time.sleep(0.6)
    try:
        send_chord(boot, "ctrl", "shift", "w")
    except (QMPError, OSError, ValueError):
        ab.save_failure_frame(boot, "browser-close-keys")
        return False
    time.sleep(1.0)
    return page_absent(boot, timeout=30)


def ext4_list(image, path):
    proc = subprocess.run(["debugfs", "-R", f"ls -p {path}", str(image)],
                          capture_output=True, text=True)
    names = []
    for line in proc.stdout.splitlines():
        fields = line.split("/")
        if len(fields) >= 6 and fields[5] not in ("", ".", ".."):
            names.append(fields[5])
    return names


def _digest(raw):
    return hashlib.sha256(raw).hexdigest() if raw is not None else None


def _first_token(raw):
    if raw is None:
        return None
    parts = raw.decode("utf-8", "replace").split()
    return parts[0] if parts else None


# Net-log readback (informational): proof builds run
# Chromium with --log-net-log into ~/net-logs; the readback
# enumerates the browser's actual egress into the receipt —
# dns_hosts (names carried by real DNS wire transactions),
# sockets (literal remote endpoints, IPv4 and IPv6), and
# attempted_hosts (hosts named by URL requests). Since the
# owner's 2026-10-27 decision removed all connection
# blocking, egress is DISCLOSED, never gated: no value in
# these sets can fail a run. (The parser covers IPv6 as well
# as IPv4 since the 2026-10-27 review correction — an
# IPv4-only version silently dropped IPv6 endpoints from
# this record.)


def _netlog_extract(blob):
    text = blob.decode("utf-8", "replace")
    dns_hosts = set()
    for match in re.finditer(r'"qname":\s*"([a-zA-Z0-9.-]+)"', text):
        dns_hosts.add(match.group(1).lower().rstrip("."))
    for match in re.finditer(
            r'"hostname":\s*"([a-zA-Z0-9.-]+)",\s*"query_type"',
            text):
        dns_hosts.add(match.group(1).lower().rstrip("."))
    attempted = set()
    for match in re.finditer(
            r'"url":\s*"(?:[a-zA-Z][a-zA-Z0-9+.-]*://)?'
            r'([a-zA-Z0-9.-]+\.[a-zA-Z]{2,})', text):
        attempted.add(match.group(1).lower().rstrip("."))
    sockets = set()
    for match in re.finditer(
            r'"(?:remote_endpoint|address)":\s*"'
            r'(\d{1,3}(?:\.\d{1,3}){3}):(\d{1,5})"', text):
        sockets.add(f"{match.group(1)}:{int(match.group(2))}")
    # IPv6 endpoints are written bracketed with a port.
    for match in re.finditer(
            r'"(?:remote_endpoint|address)":\s*"'
            r'\[([0-9a-fA-F:]+)\]:(\d{1,5})"', text):
        sockets.add(f"[{match.group(1).lower()}]"
                    f":{int(match.group(2))}")
    # Bare IPv6 addresses (resolver answers carry no port).
    for match in re.finditer(
            r'"address":\s*"([0-9a-fA-F]*:[0-9a-fA-F:]+)"',
            text):
        sockets.add(match.group(1).lower())
    return dns_hosts, sockets, attempted


def browser_artifacts(disk, workdir):
    """Read the persistent DATA partition back host-side: A/B
    control facts (via the A/B driver's reader) plus everything
    the browser proof leaves behind."""
    # extract_partition writes into these dirs without creating them.
    Path(workdir).mkdir(parents=True, exist_ok=True)
    (Path(workdir) / "browser").mkdir(exist_ok=True)
    facts = ab.state_artifacts(disk, workdir)
    data_copy = ab.extract_partition(disk, 5, Path(workdir) / "browser")
    image = ab.replayed_image(data_copy)

    def read(path):
        data = ab.ext4_read(image, path)
        if data:
            return data
        # debugfs splits its -R argument on whitespace, so a
        # spaced name (the collision download 'payload (1).bin')
        # reads back empty even though the guest file is intact
        # (verified by inode on a real run disk: 262144 bytes,
        # fixture digest). Resolve the exact name to an inode
        # and cat that instead.
        parent, _, name = path.rpartition("/")
        out = subprocess.run(["debugfs", "-R", f"ls -l {parent}",
                              str(image)], capture_output=True)
        for line in out.stdout.decode("utf-8", "replace").splitlines():
            parts = line.split(None, 8)
            if len(parts) == 9 and parts[8] == name:
                got = subprocess.run(
                    ["debugfs", "-R", f"cat <{parts[0]}>", str(image)],
                    capture_output=True)
                if got.returncode == 0:
                    return got.stdout
        return data

    downloads = ext4_list(image, "/synveil/Downloads")
    payload = read("/synveil/Downloads/payload.bin")
    payload1 = read("/synveil/Downloads/payload (1).bin")
    payload2 = read("/synveil/Downloads/payload2.bin")
    big = read("/synveil/Downloads/big.bin")
    big_partial = read("/synveil/Downloads/big.bin.crdownload")
    preferences = read("/synveil/.config/chromium/Default/Preferences")
    sandbox = read("/synveil/sandbox-probe-status.txt")
    files_log = read("/synveil/files-window.log")
    browser_log = read("/synveil/browser.log")
    files_entries = 0
    files_payload_scans = 0
    files_payload2_scans = 0
    if files_log:
        text = files_log.decode("utf-8", "replace")
        files_entries = text.count("SYNVEIL_FILES_ENTRY")
        for line in text.splitlines():
            if line.startswith("SYNVEIL_FILES_ENTRY scope=downloads"):
                if f"name=payload.bin size={PAYLOAD_SIZE} " in line:
                    files_payload_scans += 1
                if f"name=payload2.bin size={PAYLOAD_SIZE} " in line:
                    files_payload2_scans += 1
    facts.update({
        "downloads_listing": downloads,
        "payload_sha256": _digest(payload),
        "payload_size": len(payload) if payload is not None else None,
        "payload_guest_sha256": _first_token(
            read("/synveil/Downloads/payload.sha256")),
        "payload1_sha256": _digest(payload1),
        "payload1_size": len(payload1) if payload1 is not None else None,
        "payload2_sha256": _digest(payload2),
        "payload2_size": len(payload2) if payload2 is not None else None,
        "payload2_guest_sha256": _first_token(
            read("/synveil/Downloads/payload2.sha256")),
        "big_size": len(big) if big is not None else None,
        "big_partial_size": len(big_partial)
        if big_partial is not None else None,
        "profile_preferences": preferences.decode("utf-8", "replace")
        if preferences is not None else None,
        "sandbox_status": sandbox.decode("utf-8", "replace")
        if sandbox is not None else None,
        "browser_started_count": browser_log.decode("utf-8", "replace")
        .count("SYNVEIL_BROWSER_STARTED") if browser_log else 0,
        "files_entry_lines": files_entries,
        "files_payload_scans": files_payload_scans,
        "files_payload2_scans": files_payload2_scans,
    })
    netlog_names = sorted(name for name in
                          ext4_list(image, "/synveil/net-logs")
                          if name.endswith(".json"))
    netlog_dns, netlog_sockets, netlog_attempted = set(), set(), set()
    for name in netlog_names:
        blob = read(f"/synveil/net-logs/{name}")
        if blob:
            dns, sockets, attempted = _netlog_extract(blob)
            netlog_dns |= dns
            netlog_sockets |= sockets
            netlog_attempted |= attempted
    facts.update({
        "netlog_files": len(netlog_names),
        "netlog_dns_hosts": sorted(netlog_dns),
        "netlog_sockets": sorted(netlog_sockets),
        "netlog_attempted_hosts": sorted(netlog_attempted),
    })
    return facts


def _gets(entries, port, path, until=None):
    return sum(1 for e in entries
               if e.get("kind") == "GET" and e.get("port") == port
               and (path is None or e.get("path") == path)
               and (until is None or e.get("t", 0) <= until))


def validate_browser_results(results):
    """Re-derive the verdict from the recorded fields. Returns
    (ok, failures). Every expected value is checked against the
    fixture constants or the build receipt — never against another
    recorded copy of itself."""
    failures = []

    def need(scenario, key):
        entry = results.get(scenario)
        if not isinstance(entry, dict) or key not in entry:
            failures.append(f"{scenario}: missing recorded field {key}")
            return None
        return entry[key]

    build = results.get("build", {})
    tls = results.get("tls", {})
    if build.get("browser_test_ca_sha256") != tls.get("ca_sha256") \
            or not tls.get("ca_sha256"):
        failures.append("build: disk trust bundle is not the fixture CA")
    entries = results.get("server_log")
    if not isinstance(entries, list) or not entries:
        failures.append("server_log: missing — no endpoint evidence")
        entries = []
    if _gets(entries, BAD_PORT, "/") != 0:
        failures.append("server_log: the bad-CA endpoint received a GET — "
                        "TLS verification must have failed open")
    if _gets(entries, BAD_PORT, None) != 0:
        failures.append("server_log: bad-CA endpoint received requests")

    def page_gets_at(scenario):
        at = need(scenario, "at")
        return _gets(entries, GOOD_PORT, "/", until=at) if at else 0

    def digest_pair(scenario, prefix, expected):
        facts = need(scenario, "artifacts") or {}
        if facts.get(f"{prefix}_sha256") != expected:
            failures.append(f"{scenario}: {prefix} bytes on DATA do not "
                            "match the fixture digest")
        if facts.get(f"{prefix}_guest_sha256") != expected:
            failures.append(f"{scenario}: in-guest digest of {prefix} "
                            "does not match the fixture digest")
        if facts.get(f"{prefix}_size") != PAYLOAD_SIZE:
            failures.append(f"{scenario}: {prefix} size wrong on DATA")

    def survival(scenario, baseline_credential):
        facts = need(scenario, "artifacts") or {}
        if not facts.get("credential_sha256") \
                or facts.get("credential_sha256") != baseline_credential:
            failures.append(f"{scenario}: credential did not survive")
        prefs = facts.get("profile_preferences") or ""
        if "/home/synveil/Downloads" not in prefs:
            failures.append(f"{scenario}: browser profile missing or "
                            "download directory not the persistent home")

    def sandbox_ok(scenario, minimum):
        facts = need(scenario, "artifacts") or {}
        good = 0
        for line in (facts.get("sandbox_status") or "").splitlines():
            if not line.startswith("SYNVEIL_SANDBOX_RENDERER user_ns="):
                continue
            fields = dict(part.split("=", 1) for part in line.split()
                          if "=" in part)
            if fields.get("seccomp") == "2" \
                    and fields.get("user_ns") \
                    and fields.get("user_ns") != fields.get(
                        "session_user_ns"):
                good += 1
        if good < minimum:
            failures.append(
                f"{scenario}: only {good} sandboxed renderer records "
                f"(need {minimum}) — a renderer in the session's own "
                "user namespace or without seccomp is NOT a sandbox")
        return good

    b0 = results.get("B0", {})
    if not b0.get("network_ready"):
        failures.append("B0: SYNVEIL_NETWORK_READY not seen")
    if not b0.get("page_proof"):
        failures.append("B0: start page never rendered")
    if page_gets_at("B0") < 1:
        failures.append("B0: server saw no GET / for the first login")

    baseline = (results.get("B1", {}).get("artifacts") or {})
    baseline_credential = baseline.get("credential_sha256")
    if not baseline_credential:
        failures.append("B1: no baseline credential recorded")
    digest_pair("B1", "payload", PAYLOAD_SHA256)
    survival("B1", baseline_credential)
    sandbox_ok("B1", 1)

    b2 = results.get("B2", {})
    if _gets(entries, GOOD_PORT, "/payload.bin",
             until=b2.get("at")) < 2:
        failures.append("B2: the second payload fetch never happened")
    facts2 = b2.get("artifacts") or {}
    if facts2.get("payload1_sha256") != PAYLOAD_SHA256 \
            or facts2.get("payload1_size") != PAYLOAD_SIZE:
        failures.append("B2: the colliding download was not preserved "
                        "as 'payload (1).bin' with the fixture digest — "
                        "Chromium uniquifies rather than overwriting")
    if b2.get("payload_sha256_after") != PAYLOAD_SHA256:
        failures.append("B2: the original payload changed after the "
                        "collision — a silent overwrite occurred")

    b3 = results.get("B3", {})
    if _gets(entries, GOOD_PORT, "/big.bin", until=b3.get("at")) < 1:
        failures.append("B3: the big payload was never requested")
    facts3 = b3.get("artifacts") or {}
    if facts3.get("big_size") == BIG_SIZE \
            or facts3.get("big_partial_size") == BIG_SIZE:
        failures.append("B3: a complete big.bin exists — the "
                        "insufficient-storage path is unproven")
    if not b3.get("browser_alive_after"):
        failures.append("B3: browser did not survive the failed download")

    b4 = results.get("B4", {})
    bad_shakes = sum(1 for e in entries
                     if e.get("kind") == "handshake-error"
                     and e.get("port") == BAD_PORT
                     and (b4.get("at") is None or e.get("t", 0) <= b4["at"]))
    if bad_shakes < 1:
        failures.append("B4: no refused handshake at the bad endpoint — "
                        "the browser may never have attempted it")
    if not b4.get("page_left"):
        failures.append("B4: good page still shown after bad-TLS navigation")
    if not b4.get("back_restored"):
        failures.append("B4: Back did not restore the good page")

    b5 = results.get("B5", {})
    if page_gets_at("B5") < 2:
        failures.append("B5: page did not load again after cold reboot")
    digest_pair("B5", "payload", PAYLOAD_SHA256)
    survival("B5", baseline_credential)
    facts5 = b5.get("artifacts") or {}
    if facts5.get("files_payload_scans", 0) < 1:
        failures.append("B5: files window never listed payload.bin — "
                        "graphical file access unproven")

    b6 = results.get("B6", {})
    facts6 = b6.get("artifacts") or {}
    if not ab.confirmed_matches(facts6.get("control"), 2):
        failures.append("B6: control record does not confirm generation 2")
    if page_gets_at("B6") < 4:
        failures.append("B6: page loads missing around the update")
    digest_pair("B6", "payload", PAYLOAD_SHA256)
    digest_pair("B6", "payload2", PAYLOAD2_SHA256)
    survival("B6", baseline_credential)
    if facts6.get("files_payload2_scans", 0) < 1:
        failures.append("B6: files window never listed payload2.bin")

    b7 = results.get("B7", {})
    facts7 = b7.get("artifacts") or {}
    if not ab.confirmed_matches(facts7.get("control"), 1):
        failures.append("B7: control record does not confirm generation 1")
    if page_gets_at("B7") < 6:
        failures.append("B7: page loads missing around the rollback")
    digest_pair("B7", "payload", PAYLOAD_SHA256)
    digest_pair("B7", "payload2", PAYLOAD2_SHA256)
    survival("B7", baseline_credential)
    sandbox_ok("B7", 3)

    for scenario in ("B1", "B5", "B6", "B7"):
        facts = need(scenario, "artifacts") or {}
        if facts.get("browser_started_count", 0) < 1:
            failures.append(f"{scenario}: browser session log missing")
    return not failures, failures


def ensure_page(boot):
    """Make sure the browser page (not a covering window) is what a
    link click will hit: if the proof page's color is on screen,
    click a NEUTRAL point of it to raise that window. The neutral
    point is the page block's top-left interior (the title area) —
    the block's centroid is NOT safe: the background pixels are
    concentrated below the link blocks, so their centroid lands
    on the big-download link and starts a 448 MiB download that
    fills the data partition (this silently ate the B2 collision
    download in an earlier run)."""
    try:
        frame = ab.next_frame(boot)
    except (ValueError, OSError):
        return False
    if color_fraction(*frame, PAGE_BG) >= 0.05:
        block = color_block(*frame, PAGE_BG)
        if block:
            bx, by, _bw, _bh = block["rect"]
            click(boot, bx + 24, by + 24)
            time.sleep(0.6)
            return True
    return page_proof(boot, timeout=60)


def _find_refresh_button(width, height, data, x, y, w, h, title_h):
    """Locate the files app's Refresh button by its fill color
    (COL_BTN 0x2E7D4F in files-window.c) below the window's
    measured title bar, returning the centroid or None. Other
    elements share the color (the update window's banner), so
    matching pixels are clustered into row bands and the band
    shaped like the button (90-170 wide, 24-48 tall, thousands
    of pixels) wins — measured on a real session frame: band
    rows 472-507, x 362-489, 4459 px."""
    target = (0x2E, 0x7D, 0x4F)
    x0 = max(0, x - 4)
    x1 = min(width, x + w + 4)
    y0 = max(0, y + title_h)
    y1 = min(height, y + 900)
    rows = {}
    for yy in range(y0, y1):
        pts = [xx for xx in range(x0, x1)
               if _near(ab._px(data, width, xx, yy), target, 6)]
        if len(pts) >= 8:
            rows[yy] = pts
    if not rows:
        return None
    bands = []
    band = [min(rows), min(rows), rows[min(rows)]]
    prev = min(rows)
    for yy in sorted(rows):
        if yy == prev + 1:
            band[1] = yy
            band[2] = band[2] + rows[yy]
        elif yy != prev:
            bands.append(tuple(band))
            band = [yy, yy, rows[yy]]
        prev = yy
    bands.append(tuple(band))
    for top, bottom, pts in sorted(bands, key=lambda b: -len(b[2])):
        if len(pts) < 1500:
            return None
        bh = bottom - top + 1
        bw = max(pts) - min(pts) + 1
        if 90 <= bw <= 170 and 24 <= bh <= 48:
            return (sum(pts) // len(pts),
                    sum(yy for yy in rows
                        for _ in rows[yy] if top <= yy <= bottom)
                    // len(pts))
    return None


def refresh_files_window(boot):
    """Click the files window's Refresh with it raised, so its log
    records a fresh scan of Downloads."""
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        try:
            frame = ab.next_frame(boot)
        except (ValueError, OSError):
            time.sleep(0.5)
            continue
        rect = files_window_rect(*frame)
        if rect:
            x, y, w, h, title_h = rect
            click(boot, x + 60, y + 14)          # raise via title bar
            time.sleep(0.5)
            try:
                frame = ab.next_frame(boot)
            except (ValueError, OSError):
                pass
            btn = _find_refresh_button(*frame, x, y, w, h, title_h)
            if btn:
                click(boot, btn[0], btn[1])
            else:
                ab.save_failure_frame(boot, "files-refresh-nobtn")
                click(boot, x + 16 + 64, y + title_h + h - 92 + 18)
            time.sleep(1.5)
            return True
        time.sleep(0.5)
    ab.save_failure_frame(boot, "files-refresh")
    return False


def finish(build_info, tls_info, results, code):
    ok, failures = validate_browser_results(results)
    receipt = {
        "schema": "synveil.browser-qemu-proof/v1",
        "build": build_info,
        "tls_fixture": {k: tls_info[k] for k in
                        ("ca_sha256", "host_name", "host_ip",
                         "good_port", "bad_port")},
        "results": results,
        "fixture": {"payload_sha256": PAYLOAD_SHA256,
                    "payload2_sha256": PAYLOAD2_SHA256,
                    "payload_size": PAYLOAD_SIZE,
                    "big_size": BIG_SIZE,
                    "start_url": START_URL},
        "gate": {"validated": ok, "failures": failures},
        "demonstrated": bool(ok and code == 0),
        "physical_boot_validated": False,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "browser-receipt.json").write_text(
        json.dumps(receipt, indent=1, sort_keys=True) + "\n")
    print("SYNVEIL_BROWSER_QEMU_PROOF_" + ("PASSED" if receipt["demonstrated"]
                                           else "FAILED"))
    for failure in failures:
        print("gate:", failure)
    return 0 if receipt["demonstrated"] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare-tls", action="store_true",
                        help="only create the run-local TLS fixture and "
                             "print the matching builder command")
    args = parser.parse_args()
    if os.geteuid() == 0:
        raise ValueError("browser proof harness must run unprivileged")
    tls_info = prepare_tls()
    if args.prepare_tls:
        print("TLS fixture ready:", json.dumps(tls_info))
        print("Build the proof disk with:")
        print(f"  python3 tools/native_ab.py --build --browser "
              f"--browser-test-ca {TLS_DIR / 'ca.pem'} "
              f"--browser-test-host {HOST_IP} {HOST_NAME} "
              f"--browser-start-url {START_URL}")
        return 0
    if not BUILD_RECEIPT.exists() or not DISK.exists():
        print("browser proof disk missing — build it first "
              "(see --prepare-tls output)")
        return 1
    build_receipt = json.loads(BUILD_RECEIPT.read_text())
    browser_record = build_receipt.get("browser") or {}
    if browser_record.get("test_ca_sha256") != tls_info["ca_sha256"]:
        print("disk trust bundle does not match the TLS fixture CA — "
              "rebuild the proof disk (see --prepare-tls output)")
        return 1
    if build_receipt["disk"]["sha256"] != ab.sha256(DISK):
        print("disk image does not match its build receipt")
        return 1
    releases = {entry["version"]: entry["media"]
                for entry in build_receipt["releases"].values()
                if not entry["corrupt_signature"]}
    build_info = {
        "source_commit": build_receipt["source_commit"],
        "disk_sha256": build_receipt["disk"]["sha256"],
        "browser_test_ca_sha256": browser_record.get("test_ca_sha256"),
        "browser_stage_validation_sha256":
            browser_record.get("stage_validation_sha256"),
        "chromium_version": browser_record.get("chromium_version"),
        "generations": {str(v): g["uki_sha256"]
                        for v, g in build_receipt["generations"].items()},
    }
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    # The QMP scanout helper refuses group/other-accessible output
    # directories; the workspace umask (0007) would leave 0770.
    os.chmod(EVIDENCE, 0o700)
    workdir = OUT / "browser-work"
    if workdir.exists():
        shutil.rmtree(workdir)
    workdir.mkdir(parents=True)
    os.chmod(workdir, 0o700)  # boot.dir: scanout privacy check too
    log = RequestLog()
    servers = start_servers(TLS_DIR, log)
    results = {}
    code = 1
    secret = secrets.token_hex(16)
    _code, vars_template = ab.ovmf_paths()

    def record(name, **fields):
        fields.setdefault("at", round(time.time(), 3))
        results[name] = fields

    try:
        _run_boots(results, record, log, workdir, vars_template, secret,
                   releases)
        results["server_log"] = log.snapshot()
        results["build"] = {"browser_test_ca_sha256":
                             browser_record.get("test_ca_sha256")}
        results["tls"] = {"ca_sha256": tls_info["ca_sha256"]}
        code = 0
    except Exception as exc:  # a crashed driver is a FAILED proof,
        results["error"] = repr(exc)  # never a missing receipt
        results.setdefault("server_log", log.snapshot())
        results.setdefault("build", {"browser_test_ca_sha256":
                                     browser_record.get("test_ca_sha256")})
        results.setdefault("tls", {"ca_sha256": tls_info["ca_sha256"]})
    finally:
        for server in servers:
            server.shutdown()
    return finish(build_info, tls_info, results, code)


def enroll_first_user(boot, secret):
    """First boot on a fresh disk: the greeter IS the graphical
    enrollment — create the password, confirm it, completion —
    exactly the A/B proof's S0 protocol. Later boots (same DATA
    partition) go straight to login."""
    if not boot.wait("SYNVEIL_ENROLL_READY", 240):
        return False
    try:
        ab.type_test_secret(boot.qmp, secret, timeout=18)
        if boot.wait("SYNVEIL_ENROLL_CONFIRM_READY", 60):
            ab.type_test_secret(boot.qmp, secret, timeout=18)
    except (ab.QMPError, OSError, ValueError):
        return False
    return boot.wait("SYNVEIL_ENROLL_COMPLETED", 120)


def terminal_command_verified(boot, command_id, marker, probe,
                              timeout=420, attempts=3):
    """Marker-verified terminal command with retype attempts.

    QMP key holds can stretch under host load; the terminal's key
    repeat then doubles a character and garbles the command (seen
    in a saved frame: '/usr/sbbin/veil-ab requestt staage' ->
    'not found'). The serial marker is the only trustworthy sign
    the command actually ran, so a missing marker inside the
    probe window means: Ctrl+C the line and type it again. The
    probe is generous — interrupting a genuinely still-running
    stage would be worse than waiting."""
    for attempt in range(attempts):
        last = attempt == attempts - 1
        if terminal_command(boot, command_id, marker,
                            timeout=timeout if last else probe):
            return True
        try:
            send_chord(boot, "ctrl", "c")
        except (QMPError, OSError, ValueError):
            pass
        time.sleep(0.5)
    return False


def terminal_command_twice(boot, command_id):
    """Idempotent unmarked commands (digest + sync) are typed
    twice: a garbled first attempt errors out at the prompt, the
    second lands the effect; a clean first attempt makes the
    second a harmless rewrite of the same file."""
    terminal_command(boot, command_id)
    return terminal_command(boot, command_id)


def _run_boots(results, record, log, workdir, vars_template, secret,
               releases):
    if True:
        # Work on a copy: a guest boot mutates STATE/DATA, and the
        # committed build receipt hashes the pristine image.
        disk = workdir / "synveil-ab-run.img"
        shutil.copyfile(DISK, disk)
        # ---------------- Boot 1: B0-B5 ----------------
        vars1 = workdir / "vars1.bin"
        shutil.copyfile(vars_template, vars1)
        boot = NetBoot(workdir, "browser1", disk, vars1)
        boot.start(secret)
        try:
            network = boot.wait("SYNVEIL_NETWORK_READY", 600)
            enrolled = enroll_first_user(boot, secret) if network else False
            logged_in = ab.login(boot, secret) if enrolled else False
            page = page_proof(boot) if logged_in else False
            if page:
                remember_browser_rect(boot)
            record("B0", network_ready=bool(network and logged_in),
                   enrolled=bool(enrolled), page_proof=bool(page))
            if page:
                # B1: first download (Chromium downloads directly to
                # ~/Downloads — no dialog; evidence is the file itself).
                got = click_link_and_wait(
                    boot, LINK_PAYLOAD, "payload", log, GOOD_PORT,
                    "/payload.bin", 1)
                if got:
                    time.sleep(10)
                record("B1", download_started=bool(got))
                # B2: collision — Chromium must uniquify, never
                # overwrite: 'payload (1).bin' appears alongside the
                # untouched original (verified at readback).
                got2 = False
                if ensure_page(boot):
                    got2 = click_link_and_wait(
                        boot, LINK_PAYLOAD, "payload-again", log,
                        GOOD_PORT, "/payload.bin", 2)
                    if got2:
                        time.sleep(10)
                record("B2", second_download=bool(got2))
                # B3: insufficient storage — the 448 MiB payload cannot
                # fit the DATA partition; the download must fail and
                # the browser must stay alive.
                alive = False
                if ensure_page(boot):
                    if click_link_and_wait(
                            boot, LINK_BIG, "big", log, GOOD_PORT,
                            "/big.bin", 1):
                        time.sleep(120)
                        alive = page_proof(boot, timeout=120)
                record("B3", browser_alive_after=bool(alive))
                # B4: bad TLS endpoint.
                page_left = back_restored = False
                if ensure_page(boot):
                    if click_link(boot, LINK_BAD, "bad"):
                        page_left = page_absent(boot, timeout=180)
                        if back_from_interstitial(boot):
                            back_restored = page_proof(boot, timeout=120)
                            if back_restored:
                                remember_browser_rect(boot)
                record("B4", page_left=bool(page_left),
                       back_restored=bool(back_restored))
                # Graphical file access + in-guest digest, then reboot.
                close_browser_window(boot)
                refresh_files_window(boot)
                terminal_command_twice(boot, "dl-sha256")
                mark = len(boot.lines)
                terminal_command(boot, "reboot")
                network2 = boot.wait_count("SYNVEIL_NETWORK_READY", 2, 600)
                logged2 = ab.login(boot, secret, since=mark)
                page2 = page_proof(boot) if logged2 else False
                record("B5", network_ready_again=bool(network2 and logged2),
                       page_proof=bool(page2))
                close_browser_window(boot)
                terminal_command_twice(boot, "dl-sha256")
            else:
                record("B1", download_started=False)
                record("B2", second_download=False)
                record("B3", browser_alive_after=False)
                record("B4", page_left=False, back_restored=False)
                record("B5", network_ready_again=False,
                       page_proof=False)
        finally:
            boot.quit()
        facts1 = browser_artifacts(disk, workdir / "readback1")
        results["B1"]["artifacts"] = facts1
        results["B2"]["artifacts"] = facts1
        results["B2"]["payload_sha256_after"] = facts1["payload_sha256"]
        results["B3"]["artifacts"] = facts1
        results["B5"]["artifacts"] = facts1

        # ---------------- Boot 2: B6 (update to v2) ----------------
        vars2 = workdir / "vars2.bin"
        shutil.copyfile(vars_template, vars2)
        boot = NetBoot(workdir, "browser2", disk, vars2,
                       extra_drives=[releases[2]])
        boot.start(secret)
        try:
            # A failed login (greeter typing can flake under host
            # load) must not send the rest of the session's steps
            # grinding against the greeter — the terminal/stage
            # retry stack alone burns ~45 minutes of worst-case
            # waits (observed in a real run). Record and stop.
            logged0 = ab.login(boot, secret)
            staged = False
            if logged0:
                page_proof(boot)
                remember_browser_rect(boot)
                close_browser_window(boot)
                staged = terminal_command_verified(
                    boot, "stage", "SYNVEIL_AB_STAGED", probe=240,
                    timeout=420)
            if logged0:
                mark = len(boot.lines)
                terminal_command(boot, "reboot")
                # Login BEFORE the health wait, as the A/B proof
                # driver does: the guest's session health check needs
                # the authenticated session, so waiting for health
                # first deadlocks the trial boot (observed: attempt 1
                # HEALTH_FAILED check=session, attempts burning).
                logged = ab.login(boot, secret, since=mark)
                healthy = boot.wait("SYNVEIL_AB_HEALTH_PASSED", 600, since=mark)
                page = page_proof(boot) if logged else False
                if page:
                    remember_browser_rect(boot)
                got2 = False
                if page:
                    got2 = click_link_and_wait(
                        boot, LINK_PAYLOAD2, "payload2", log, GOOD_PORT,
                        "/payload2.bin", 1)
                    if got2:
                        time.sleep(10)
                record("B6", staged=bool(staged), healthy=bool(healthy),
                       page_proof=bool(page), download_started=bool(got2))
                close_browser_window(boot)
                refreshed = refresh_files_window(boot)
                close_files_window(boot)
                terminal_command_twice(boot, "dl-sha256")
                terminal_command_twice(boot, "dl2-sha256")
                confirmed = terminal_command_verified(
                    boot, "confirm", "SYNVEIL_AB_CONFIRMED", probe=60,
                    timeout=300)
                results["B6"]["files_refreshed"] = bool(refreshed)
                results["B6"]["confirm_ok"] = bool(confirmed)
            else:
                record("B6", staged=False, healthy=False,
                       page_proof=False, download_started=False)
        finally:
            boot.quit()
        results["B6"]["artifacts"] = browser_artifacts(
            disk, workdir / "readback2")

        # ---------------- Boot 3: B7 (rollback to v1) ----------------
        vars3 = workdir / "vars3.bin"
        shutil.copyfile(vars_template, vars3)
        boot = NetBoot(workdir, "browser3", disk, vars3)
        boot.start(secret)
        try:
            logged0 = ab.login(boot, secret)
            armed = False
            if logged0:
                page_proof(boot)
                remember_browser_rect(boot)
                close_browser_window(boot)
                armed = terminal_command_verified(
                    boot, "rollback", "SYNVEIL_AB_ROLLBACK", probe=60,
                    timeout=300)
            if logged0:
                mark = len(boot.lines)
                terminal_command(boot, "reboot")
                logged = ab.login(boot, secret, since=mark)
                healthy = boot.wait("SYNVEIL_AB_HEALTH_PASSED", 600, since=mark)
                page = page_proof(boot) if logged else False
                record("B7", rollback_armed=bool(armed), healthy=bool(healthy),
                       page_proof=bool(page))
                close_browser_window(boot)
                refreshed = refresh_files_window(boot)
                close_files_window(boot)
                terminal_command_twice(boot, "dl-sha256")
                terminal_command_twice(boot, "dl2-sha256")
                confirmed = terminal_command_verified(
                    boot, "confirm", "SYNVEIL_AB_CONFIRMED", probe=60,
                    timeout=300)
                results["B7"]["files_refreshed"] = bool(refreshed)
                results["B7"]["confirm_ok"] = bool(confirmed)
            else:
                record("B7", rollback_armed=False,
                       healthy=False, page_proof=False)
        finally:
            boot.quit()
        results["B7"]["artifacts"] = browser_artifacts(
            disk, workdir / "readback3")


if __name__ == "__main__":
    raise SystemExit(main())
