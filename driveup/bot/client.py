"""Bot wiring: aiogram Bot + Dispatcher + the access middleware.

The middleware is the single load-bearing piece of access control on the
Telegram side. For EVERY message and callback it:

  1. upserts the user and resolves their role/plan
  2. runs `Access.gate()` (maintenance → lock → force-sub → plan → quota → abuse)
  3. denies with the screen from the decision, or injects `data["user"]`
  4. ownership-checks callback payloads that carry a job id

Handlers can therefore assume an authorised user and never repeat those checks.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any, Awaitable, Callable, Dict, Optional

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, ErrorEvent, Message, User

from ..config import Settings
from ..core import links
from ..core.access import Access
from ..db import repo
from ..strings import esc, t
from ..tg.ratelimit import FloodGate

log = logging.getLogger("driveup.bot")

Handler = Callable[[Any, Dict[str, Any]], Awaitable[Any]]


class BotApp:
    """Everything a handler needs, in one object."""

    def __init__(self, *, bot: Bot, dp: Dispatcher, db, cfg: Settings,
                 access: Access, accounts, worker, hub, fg: FloodGate,
                 admin_cache: Optional[dict] = None) -> None:
        self.bot = bot
        self.dp = dp
        self.db = db
        self.cfg = cfg
        self.access = access
        self.accounts = accounts
        self.worker = worker
        self.hub = hub
        self.fg = fg
        # in-flight job cards per user: user_id → job_id (draft being built)
        self.draft_jobs: Dict[int, str] = {}
        # users who asked /thumb_set and are waiting for one photo
        self.thumb_await: set = set()
        # album aggregation: (chat_id, media_group_id) → [messages]
        self.albums: Dict[tuple, list] = {}
        self.album_tasks: Dict[tuple, asyncio.Task] = {}
        self.started_at = 0.0

    def studio_url(self, job_id: str = "", user_id: int = 0, *, admin: bool = False) -> str:
        """A Studio link the phone's browser can actually open.

        Without a signed token the browser has no session (Telegram `initData`
        does not exist outside the Mini-App), so every Studio button landed on
        the "open this from the bot" blocker. Minting here fixes the whole class.
        """
        base = self.cfg.public_url or f"http://127.0.0.1:{self.cfg.port}"
        if not user_id:
            return f"{base}/?job={job_id}" if job_id else base
        token = links.mint_token(self.cfg.session_secret, int(user_id),
                                 job_id=job_id, admin=admin)
        tail = f"&job={job_id}" if job_id else ""
        return f"{base}/start?t={token}{tail}"

    async def lang(self, user_id: int) -> str:
        u = await repo.get_user(self.db, user_id) or {}
        return u.get("lang", "hinglish")

    async def say(self, chat_id: int, text: str, **kw) -> Optional[int]:
        try:
            from .ui import emoji as emoji_ui
            text = emoji_ui.apply(text, self.cfg, await repo.get_global(self.db))
            msg = await self.bot.send_message(chat_id, text, parse_mode="HTML",
                                              disable_web_page_preview=True, **kw)
            return msg.message_id
        except Exception as e:                            # noqa: BLE001
            log.debug("send failed: %s", e)
            return None

    # ── log / lock channels (fire-and-forget, throttled) ──────────────────
    async def channel_send(self, channel: str, text: str) -> None:
        if not channel:
            return
        try:
            await self.bot.send_message(channel, text, parse_mode="HTML",
                                        disable_web_page_preview=True)
        except Exception as e:                            # noqa: BLE001
            log.debug("channel %s: %s", channel, e)

    async def log_event(self, tag: str, data: Optional[dict] = None) -> None:
        g = await repo.get_global(self.db)
        ch = g.get("log_channel_id") or self.cfg.log_channel_id
        if not ch:
            return
        body = "\n".join(f"· <b>{esc(k)}</b>: {esc(v)}" for k, v in (data or {}).items())
        await self.channel_send(ch, f"{esc(tag)}\n{body}")

    async def lock_card(self, kind: str, user_id: int, reason: str, until: float,
                        by_id: int, minutes: int = 0) -> None:
        g = await repo.get_global(self.db)
        ch = g.get("lock_channel_id") or self.cfg.lock_channel_id or \
            g.get("log_channel_id") or self.cfg.log_channel_id
        if not ch:
            return
        icon = {"locked": "🔒", "unlocked": "🔓", "blocked": "⛔"}.get(kind, "🔒")
        text = (f"{icon} <b>#User{kind.title()}</b>\n"
                f"👤 <a href=\"tg://user?id={user_id}\">user</a> · <code>{user_id}</code>\n"
                f"📝 {esc(reason or '—')}\n"
                f"⏳ by <code>{by_id}</code>")
        if until:
            from ..core.access import unlock_at_text
            text += f" · until <b>{esc(unlock_at_text(until, self.cfg.timezone))}</b>"
        await self.channel_send(ch, text)


# ── middlewares ─────────────────────────────────────────────────────────────
class GateMiddleware:
    """Outer middleware for Message and CallbackQuery."""

    def __init__(self, app: BotApp) -> None:
        self.app = app

    async def __call__(self, handler: Handler, event, data: Dict[str, Any]) -> Any:
        app = self.app
        # every handler expects the app on the data dict (also under polling)
        data["_app"] = app
        tg_user: Optional[User] = data.get("event_from_user")
        if tg_user is None or tg_user.is_bot:
            return await handler(event, data)

        user = await repo.upsert_user(
            app.db, tg_user.id,
            name=(tg_user.full_name or "").strip(),
            username=tg_user.username or "")
        log.debug("gate user=%s role_pending", tg_user.id)
        role = await repo.role_of(app.db, tg_user.id, app.cfg.owner_id)
        user["role"] = role
        data["user"] = user
        data["lang"] = user.get("lang", "hinglish")
        data["role"] = role

        g = await repo.get_global(app.db)
        if not g.get("registration", True) and not user.get("first_seen"):
            await app.say(tg_user.id, t("registration_closed", data["lang"]))
            return None

        # The "I joined" button must be reachable while the gate is closed,
        # otherwise force-sub can never be satisfied.
        exempt = isinstance(event, CallbackQuery) and (event.data or "").split(":")[:2] == ["v1", "subok"]
        scope = "all"
        decision = await app.access.gate(tg_user.id, scope=scope,
                                         skip_force_sub=exempt)
        if not decision:
            await self._deny(event, decision, data)
            return None

        if isinstance(event, CallbackQuery):
            problem = await self._check_ownership(event, data)
            if problem:
                await event.answer(problem, show_alert=True)
                return None

        try:
            return await handler(event, data)
        except Exception as e:                            # noqa: BLE001
            log.exception("handler failed: %s", e)
            await repo.log_error(app.db, "handler", repr(e))
            with contextlib.suppress(Exception):
                chat = event.message.chat.id if getattr(event, "message", None) else tg_user.id
                await app.say(chat, f"⚠️ Something broke: <code>{esc(str(e)[:200])}</code>")
            return None

    async def _deny(self, event, decision, data) -> None:
        app = self.app
        kb = None
        if decision.reason == "force_sub":
            from .keyboards import force_sub_kb
            kb = force_sub_kb(decision.extra.get("channels", []))
        if isinstance(event, CallbackQuery):
            await event.answer("Blocked", show_alert=False)
            chat = event.message.chat.id if event.message else data["event_from_user"].id
            await app.say(chat, decision.screen, reply_markup=kb)
        else:
            await app.say(event.chat.id, decision.screen, reply_markup=kb)

    # actions whose 3rd field is NOT a job id
    MULTI_PART = {"seqmode", "ntpl0", "ntpl1", "ntpl2", "ntpl3", "adm", "plan"}

    async def _check_ownership(self, event: CallbackQuery, data: Dict[str, Any]) -> str:
        """A callback may only touch the caller's own job."""
        parts = (event.data or "").split(":")
        if len(parts) < 3 or parts[0] != "v1":
            return ""
        action = parts[1]
        arg = parts[-1] if action in self.MULTI_PART else parts[2]
        if action in ("subok", "noop", "ntplcustom", "openstudio", "admin", "help"):
            return ""
        # ntpl / seqmode / folder / … carry a job id in position 2 → fall through
        # to the ownership check below so it always runs.
        if action == "adm":                    # admin actions carry their own check
            return ""
        if not arg:
            return ""
        job = await repo.get_job(self.app.db, arg)
        if not job:
            return "That job no longer exists."
        role = data.get("role", "user")
        if job["user_id"] != data["event_from_user"].id and role not in ("owner", "admin"):
            return "That job belongs to another account."
        data["job"] = job
        return ""


class AlbumMiddleware:
    """Collects media groups so an album becomes ONE job card.

    aiogram delivers each photo/video of an album as its own update; without
    this the user would get five job cards for one album.
    """

    DELAY = 0.9

    def __init__(self, app: BotApp) -> None:
        self.app = app

    async def __call__(self, handler, event: Message, data: Dict[str, Any]) -> Any:
        gid = getattr(event, "media_group_id", None)
        if not gid:
            return await handler(event, data)
        key = (event.chat.id, gid)
        app = self.app
        bucket = app.albums.setdefault(key, [])
        bucket.append(event)
        task = app.album_tasks.get(key)
        if task is None or task.done():
            app.album_tasks[key] = asyncio.create_task(self._flush(key, handler, data))
        return None

    async def _flush(self, key, handler, data) -> None:
        await asyncio.sleep(self.DELAY)
        msgs = self.app.albums.pop(key, [])
        self.app.album_tasks.pop(key, None)
        if not msgs:
            return
        msgs.sort(key=lambda m: m.message_id)
        data = dict(data)
        data["album"] = msgs
        await handler(msgs[0], data)


# ── construction ────────────────────────────────────────────────────────────
class ConflictAwareSession(AiohttpSession):
    """AiohttpSession that records Telegram's 409 `Conflict`.

    `getUpdates` is exclusive per token: a second poller makes Telegram reject
    BOTH with HTTP 409, and aiogram silently retries, so the bot looks online
    while ignoring every message. The flag read here is what `main.py`'s
    watchdog turns into a plain-English error (and a DM to the owner).
    """

    conflict_seen = False

    async def make_request(self, bot, method, timeout=None):   # type: ignore[override]
        try:
            return await super().make_request(bot, method, timeout)
        except Exception as e:                                  # noqa: BLE001
            name = type(e).__name__
            if "Conflict" in name or "409" in str(e):
                self.conflict_seen = True
            raise


def _port_of(url: str) -> str:
    """`host:8081` → "8081"; an https URL → "443"; anything else → ''."""
    text = (url or "").strip()
    if not text:
        return ""
    head = text.split("://")[-1].split("/")[0]
    if ":" in head:
        return head.rsplit(":", 1)[-1]
    return "443" if text.startswith("https") else "80"


def _files_port(api_url: str, files_url: str) -> str:
    """Resolve Render's shared `hostport` to its separate file-server port.

    The gramiojs image exposes Telegram's API on 8081 and its token-less file
    endpoint on 8080. Render's two `fromService: hostport` references both
    inject `host:8081`, so detect that duplicate and route file fetches to 8080.
    An explicitly configured `host:8080` remains untouched.
    """
    api_port = _port_of(api_url)
    file_port = _port_of(files_url) or "8080"
    return "8080" if api_port == "8081" and file_port == api_port else file_port


def build_bot(cfg: Settings) -> Bot:
    session = ConflictAwareSession(
        api=TelegramAPIServer.from_base(cfg.api_base),
        timeout=600,
    )
    bot = Bot(token=cfg.bot_token, session=session,
              default=DefaultBotProperties(parse_mode="HTML",
                                           link_preview_is_disabled=True))
    # Carried on the bot (rather than threaded through every call) so the
    # downloader can resolve files when a local Bot API server sits in another
    # container and we simply cannot see its disk — see tg/download.py.
    bot.files_base_url = cfg.telegram_files_url
    bot.files_work_dir = cfg.telegram_work_dir
    # Ports differ on the gramiojs image (API 8081, file server 8080); a host
    # that injects one host:port has to be corrected for downloads.
    bot.api_port = _port_of(cfg.api_base)
    bot.files_port = _files_port(cfg.api_base, cfg.telegram_files_url)
    return bot


def build_dispatcher(app: BotApp) -> Dispatcher:
    dp = Dispatcher(storage=MemoryStorage())
    dp.message.outer_middleware(GateMiddleware(app))
    dp.callback_query.outer_middleware(GateMiddleware(app))
    dp.message.outer_middleware(AlbumMiddleware(app))

    @dp.errors()
    async def on_error(event: ErrorEvent) -> bool:
        log.exception("dispatcher error", exc_info=event.exception)
        await repo.log_error(app.db, "dispatcher", repr(event.exception))
        return True

    from .handlers import admin as admin_h
    from .handlers import start as start_h
    from .handlers import upload as upload_h
    from .handlers import thumb as thumb_h
    from .handlers import menu as menu_h

    # Building the dispatcher twice (a restart, or a second app object in a
    # test) must work: detach the module routers from any previous parent.
    for r in (menu_h.router, start_h.router, thumb_h.router, upload_h.router, admin_h.router):
        r._parent_router = None

    dp.include_router(menu_h.router)      # m:<command> buttons + /hello
    dp.include_router(start_h.router)
    dp.include_router(thumb_h.router)     # before upload: photos are thumbnails
    dp.include_router(upload_h.router)
    dp.include_router(admin_h.router)
    return dp
