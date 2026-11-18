"""FastAPI application — Studio, admin, API, static, security headers."""
from __future__ import annotations

import logging
import time
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware

from ..config import BRAND_NAME, Settings
from . import admin as admin_api
from . import api as studio_api
from ..db import repo

log = logging.getLogger("driveup.web")
STATIC = Path(__file__).parent / "static"


def security_headers(app: FastAPI) -> None:
    @app.middleware("http")
    async def _headers(request: Request, call_next):
        resp = await call_next(request)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "ALLOW-FROM https://telegram.org")
        resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        resp.headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
        # Telegram Mini-Apps are embedded in an iframe served by telegram.org;
        # CSP must allow that frame-ancestor or the Studio does not load.
        resp.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data: blob: https:; "
            "style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline' "
            "https://telegram.org; connect-src 'self'; "
            "frame-ancestors 'self' https://telegram.org https://*.telegram.org; "
            "base-uri 'self'; form-action 'self'")
        return resp


def _maybe_async(fn):
    """Hooks come from main.py as sync helpers; callers always await them."""
    async def call(*a, **k):
        if fn is None:
            return None
        out = fn(*a, **k)
        if hasattr(out, "__await__"):
            return await out
        return out
    return call


def create_app(*, db, cfg: Settings, bot, worker, accounts, access, hub=None,
               version: str = "1.0.0", log_event=None, lock_card=None) -> FastAPI:
    app = FastAPI(title=f"{BRAND_NAME} Studio", docs_url=None, redoc_url=None,
                  openapi_url=None)
    app.state.db = db
    app.state.cfg = cfg
    app.state.bot = bot
    app.state.worker = worker
    app.state.accounts = accounts
    app.state.access = access
    app.state.hub = hub
    app.state.started_at = time.time()
    app.state.version = version
    app.state.log_event = _maybe_async(log_event)
    app.state.lock_card = _maybe_async(lock_card)

    # ── test fixture hook (only when explicitly enabled) ───────────────────
    if getattr(cfg, "e2e_fixtures", False):
        @app.post("/__fixture/job")
        async def _fixture_job():
            """Creates a fresh draft job for the E2E suite. Never enabled in prod."""
            uid = 555
            job = await repo.create_job(app.state.db, uid,
                                        destination={"folder_id": "root", "path": "My Drive"},
                                        naming={"template": "{original}.{ext}"},
                                        sequence_mode="manual")
            session = getattr(app.state.bot, "session", None)
            for i, name in enumerate(["lecture 10.mp4", "lecture 2.mp4", "lecture 1.mp4"]):
                data = (b"DRIVEUP-FIXTURE-" + name.encode()) * 8_000
                fid = f"fx{job[:6]}{i}"
                if hasattr(session, "add_file"):      # real bytes for the fake Bot
                    session.add_file(name, data, file_id=fid)
                await repo.add_item(app.state.db, job, file_id=fid,
                                    file_unique_id=f"fux{job[:6]}{i}", msg_id=100 + i,
                                    size=len(data), mime="video/mp4", name=name, chat_id=uid)
            await app.state.worker._renumber(await repo.get_job(app.state.db, job),
                                             await repo.items_of(app.state.db, job))
            return {"ok": True, "job": job}

    # CSS/JS/JSON compress ~5x — Lighthouse counts every wasted KiB.
    app.add_middleware(GZipMiddleware, minimum_size=512)
    security_headers(app)
    app.include_router(studio_api.router)
    app.include_router(admin_api.router)

    if STATIC.exists():
        class _Assets(StaticFiles):
            """Long cache for hashed-in-name assets, gzip for everything text."""

            async def get_response(self, path, scope):
                resp = await super().get_response(path, scope)
                if resp.status_code == 200 and path.endswith((".css", ".js", ".svg", ".woff2")):
                    resp.headers.setdefault("Cache-Control", "public, max-age=86400")
                return resp

        app.mount("/static", _Assets(directory=str(STATIC)), name="static")

    @app.get("/start")
    async def start(request: Request, t: str = "", job: str = "", to: str = ""):
        """Redeem a signed Studio/Admin link: mint a session, then go to the page.

        Telegram *url* buttons open the system browser where `initData` is empty,
        so this is the only way a browser session can begin. Tokens are HMAC'd
        with SESSION_SECRET, bound to one user and expire in 15 minutes.
        """
        from .auth import SESSION_COOKIE, mint_session, set_session_cookie, verify_link
        st = request.app.state
        claim = verify_link(st.cfg.session_secret, t)
        if not claim:
            return HTMLResponse(
                '<!doctype html><meta charset=utf-8>'
                '<meta name=viewport content="width=device-width,initial-scale=1">'
                '<meta name=theme-color content="#081014">'
                '<title>Link expired</title>'
                '<body style="background:#081014;color:#e8f6fa;font:16px system-ui;'
                'display:grid;place-items:center;min-height:100vh;margin:0;text-align:center">'
                '<div style="max-width:340px;padding:24px"><div style="font-size:46px">⌛</div>'
                '<h2 style="margin:10px 0 6px">This link expired</h2>'
                '<p style="color:#7fa3ad;font-size:.9rem">Links last 15 minutes. '
                'Open the bot and send <b>/studio</b> — or <b>/admin</b> for the panel — '
                'to get a fresh one.</p></div></body>', status_code=400)

        # An admin link is only honoured for a role that really is an admin.
        if claim["admin"]:
            role = await repo.role_of(st.db, claim["user_id"], st.cfg.owner_id)
            if role not in ("owner", "admin", "support"):
                return HTMLResponse(
                    '<!doctype html><meta charset=utf-8><title>403</title>'
                    '<body style="background:#081014;color:#e8f6fa;font:16px system-ui;'
                    'padding:40px;text-align:center"><h2>403 · not an admin</h2>'
                    '<p style="color:#7fa3ad">This account has no admin role.</p></body>',
                    status_code=403)

        sid = await mint_session(st.db, claim["user_id"],
                                 request.client.host if request.client else "",
                                 request.headers.get("user-agent", ""))
        proto = request.headers.get("x-forwarded-proto", request.url.scheme).split(",")[0].strip()
        target = "/admin" if claim["admin"] or to == "admin" else "/"
        if job:
            target += f"?job={job}"
        resp = RedirectResponse(target, status_code=303)
        set_session_cookie(resp, sid, secure=proto == "https")
        return resp

    @app.get("/connect")
    async def connect(request: Request):
        """Landing route for the bot's /connect button — explains, never 401s."""
        st = request.app.state
        sid = request.cookies.get("du_session")
        if sid and await repo.get_session(st.db, sid):
            return RedirectResponse("/auth/google/start", status_code=303)
        return HTMLResponse(
            '<!doctype html><meta charset=utf-8>'
            '<meta name=viewport content="width=device-width,initial-scale=1">'
            '<meta name=theme-color content="#081014"><title>Connect Drive</title>'
            '<body style="background:#081014;color:#e8f6fa;font:16px system-ui;'
            'display:grid;place-items:center;min-height:100vh;margin:0;text-align:center">'
            '<div style="max-width:340px;padding:24px"><div style="font-size:46px">🔗</div>'
            '<h2 style="margin:10px 0 6px">Link Google Drive from the bot</h2>'
            '<p style="color:#7fa3ad;font-size:.9rem">Send <b>/connect</b> to the bot and tap '
            'the button it replies with — that link carries your session. '
            'Opening this page directly cannot work, by design.</p></div></body>',
            status_code=200)

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        """Studio, but only with a session — otherwise explain how to get one."""
        sid = request.cookies.get("du_session")
        if not sid:
            html = f"""<!doctype html>
            <html lang="en" data-bar="glow"><head><meta charset=utf-8>
            <meta name=viewport content="width=device-width,initial-scale=1">
            <meta name=theme-color content="#081014">
            <title>{BRAND_NAME} · Studio</title>
            <link rel=icon href="/static/favicon.svg" type="image/svg+xml">
            <link rel=stylesheet href="/static/app.css"></head><body>
            <div class="blocker"><div class="inner">
              <div class="icon">🚀</div>
              <h1>{BRAND_NAME} Studio</h1>
              <p>Open this page from the bot — tap <b>🌐 Open Upload Studio</b> or send
                 <code>/studio</code>. The link carries a signed session, one link per job.</p>
              <p class="hint">Already have a link? Open it again — sessions last 7 days.</p>
            </div></div></body></html>"""
            return HTMLResponse(html, status_code=200)
        return HTMLResponse((STATIC / "index.html").read_text(encoding="utf-8"))

    @app.exception_handler(404)
    async def not_found(request: Request, exc):
        if request.url.path.startswith("/api/"):
            return JSONResponse({"ok": False, "code": "not_found", "message": "no such route"},
                                status_code=404)
        return HTMLResponse(
            '<!doctype html><html lang="en"><head><meta charset=utf-8>'
            '<meta name=viewport content="width=device-width,initial-scale=1">'
            '<title>404</title></head><body style="font:16px system-ui;padding:40px">'
            '<h1>404</h1><p>No such page. <a href="/">Back to the Studio</a>.</p>'
            '</body></html>', status_code=404)

    return app
