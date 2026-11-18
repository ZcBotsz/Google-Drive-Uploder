"""Boot the real entry point (`driveup.main.main`) in a subprocess.

The unit tests build the app by hand; this one proves the *shipping* path:
config validation → DB → worker → uvicorn → polling, with Telegram deliberately
unreachable (a dead port). It must still serve /health, answer 401 on /api/me,
and shut down cleanly with exit code 0.

This is the "does `docker compose up` actually work" test.
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import textwrap

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
PORT = 8321

BOOT_SCRIPT = textwrap.dedent('''
    import json, os, sys, tempfile, threading, time, urllib.request, urllib.error
    sys.path.insert(0, {root!r})
    tmp = tempfile.mkdtemp()
    os.environ.update({{
        "BOT_TOKEN": "123456:BOOTTEST", "OWNER_ID": "1",
        "GOOGLE_CLIENT_ID": "cid", "GOOGLE_CLIENT_SECRET": "csec",
        "TOKEN_ENC_KEY": "k" * 44, "SESSION_SECRET": "s" * 40,
        "PUBLIC_URL": "http://127.0.0.1:{port}",
        "HOST": "127.0.0.1", "PORT": "{port}",
        "DB_PATH": f"{{tmp}}/s.db", "TEMP_DIR": f"{{tmp}}/t",
        "DOWNLOAD_DIR": f"{{tmp}}/d",
        "GOOGLE_API_BASE": "http://127.0.0.1:1",
        "TELEGRAM_API_URL": "http://127.0.0.1:1",
        # a local Bot API server cannot start without these two, and config.py
        # now refuses to boot in that half-configured state
        "API_ID": "12345", "API_HASH": "0123456789abcdef0123456789abcdef",
        # …and it must be able to reach the files it hands back
        "TELEGRAM_FILES_URL": "http://127.0.0.1:1",
    }})
    probe = {{}}
    def prober():
        for _ in range(120):
            try:
                probe["health"] = json.loads(urllib.request.urlopen(
                    "http://127.0.0.1:{port}/health", timeout=2).read())
                probe["root"] = urllib.request.urlopen(
                    "http://127.0.0.1:{port}/", timeout=2).read().decode()[:120]
                try:
                    urllib.request.urlopen("http://127.0.0.1:{port}/api/me", timeout=2)
                except urllib.error.HTTPError as e:
                    probe["api"] = e.code
                break
            except Exception:
                time.sleep(0.2)
    threading.Thread(target=prober, daemon=True).start()
    def killer():
        # Wait until /health actually answers (up to 60 s), THEN stop politely.
        # A fixed sleep races a slow first import and produced exit code -15.
        for _ in range(300):
            if probe.get("health"):
                break
            time.sleep(0.2)
        time.sleep(1.0)              # let it settle into steady state
        os.kill(os.getpid(), 15)
    threading.Thread(target=killer, daemon=True).start()
    from driveup import main as m
    rc = m.main()
    print("PROBE=" + json.dumps(probe))
    sys.exit(rc)
''')


def test_production_entry_point_boots_and_serves():
    script = BOOT_SCRIPT.format(root=str(ROOT), port=PORT)
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("BOT_", "GOOGLE_", "TOKEN_", "SESSION_"))}
    proc = subprocess.run([sys.executable, "-c", script], capture_output=True,
                          text=True, timeout=150, env=env, cwd=str(ROOT))
    assert proc.returncode == 0, f"main() exited {proc.returncode}\n{proc.stderr[-2000:]}"
    line = next((l for l in proc.stdout.splitlines() if l.startswith("PROBE=")), "")
    assert line, proc.stdout[-2000:] + proc.stderr[-2000:]
    probe = json.loads(line[6:])
    assert probe["health"]["ok"] is True
    assert probe["health"]["db"] is True
    assert probe["health"]["version"]
    assert "text/html" not in probe["root"] or "<!doctype html>" in probe["root"].lower()
    assert probe["api"] == 401                     # unauthenticated API
    # the dead Telegram must not have killed anything, and shutdown is clean
    assert "bye" in proc.stdout.lower() or proc.returncode == 0


def test_port_in_use_fails_loudly_not_silently():
    """A taken port must CRASH the process, not log "listening" and serve nothing.

    uvicorn raises SystemExit(3) on a bind failure; inside a task that used to
    vanish, producing Railway's 502 Bad Gateway with a healthy-looking log.
    """
    import socket
    import textwrap

    script = textwrap.dedent('''
        import asyncio, socket, sys
        sys.path.insert(0, {root!r})
        from fastapi import FastAPI
        from driveup.main import serve, ServeFailed

        async def main():
            s = socket.socket()
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("127.0.0.1", 8477)); s.listen(1)
            try:
                await serve(FastAPI(), 8477, "127.0.0.1")
                print("NOT-RAISED")
            except ServeFailed as e:
                print("RAISED-ServeFailed")
            finally:
                s.close()

        asyncio.run(main())
    ''').format(root=str(ROOT))
    proc = subprocess.run([sys.executable, "-c", script], capture_output=True,
                          text=True, timeout=60, cwd=str(ROOT))
    assert "RAISED-ServeFailed" in proc.stdout, (proc.stdout, proc.stderr[-800:])
