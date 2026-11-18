"""Access control, evaluated outermost-first (B9):

    maintenance → ban/lock → force-subscribe (fail OPEN) → plan/feature gate
    → quota (daily GB / file size / files-per-job) → abuse rate-limit

Every entry point (bot message, bot callback, web request) calls `gate()` and
gets back a `Decision`. A denied decision carries the screen to show, so the
caller never invents a message.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

from ..config import Settings
from ..db import repo
from ..db.sql import Database

ADMIN_BYPASS = ("owner", "admin", "support")


@dataclass
class Decision:
    ok: bool
    reason: str = ""                 # locked | maintenance | force_sub | plan | quota | abuse | feature
    screen: str = ""                 # what to show the user (HTML)
    extra: dict = field(default_factory=dict)

    def __bool__(self) -> bool:
        return self.ok


def human_bytes(n: float) -> str:
    n = float(n or 0)
    if n < 0:
        return "unlimited"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.2f} {unit}"
        n /= 1024
    return f"{n:.2f} TB"


def unlock_at_text(ts: float, tz: str = "Asia/Kolkata") -> str:
    try:
        from zoneinfo import ZoneInfo
        import datetime as dt
        return dt.datetime.fromtimestamp(ts, ZoneInfo(tz)).strftime("%d %b %Y, %H:%M")
    except Exception:
        return time.strftime("%d %b %Y, %H:%M", time.localtime(ts))


class Access:
    def __init__(self, db: Database, cfg: Settings, bot=None) -> None:
        self.db = db
        self.cfg = cfg
        self.bot = bot
        self._sub_cache: dict[int, tuple[float, bool]] = {}

    # ── individual checks ────────────────────────────────────────────────
    async def glob(self) -> dict:
        return await repo.get_global(self.db)

    async def role(self, user_id: int) -> str:
        return await repo.role_of(self.db, user_id, self.cfg.owner_id)

    async def maintenance(self) -> bool:
        g = await self.glob()
        return bool(g.get("maintenance"))

    async def lock_of(self, user_id: int, scope: str = "all") -> Optional[dict]:
        lock = await repo.get_lock(self.db, user_id)
        if not lock:
            return None
        if lock["scope"] not in ("all", scope):
            return None
        return lock

    async def force_sub_missing(self, user_id: int) -> list[str]:
        """Channels the user has not joined. FAILS OPEN: any API error → []."""
        g = await self.glob()
        raw = g.get("force_sub_channels") or ",".join(self.cfg.force_sub_channels)
        channels = [c.strip() for c in str(raw).replace("\n", ",").split(",") if c.strip()]
        if not channels or not self.bot:
            return []
        cached = self._sub_cache.get(user_id)
        if cached and time.time() - cached[0] < 600:
            return [] if cached[1] else list(channels)
        # a stale "allowed" cache entry must not survive the "I joined" tap
        self._sub_cache.pop(user_id, None)
        missing = []
        for ch in channels:
            try:
                member = await self.bot.get_chat_member(ch, user_id)
                status = getattr(member, "status", "")
                status = getattr(status, "value", status)
                if status in ("left", "kicked"):
                    missing.append(ch)
            except Exception as e:                    # noqa: BLE001 — fail open by design
                import logging
                logging.getLogger("driveup.access").warning(
                    "force-sub check failed for %s (%s) — allowing the user", ch, type(e).__name__)
                self._sub_cache[user_id] = (time.time(), True)
                return []
        self._sub_cache[user_id] = (time.time(), not missing)
        return missing

    def forget_sub_cache(self, user_id: int) -> None:
        """Called by the "I joined" button so the next check re-queries Telegram."""
        self._sub_cache.pop(user_id, None)

    async def plan_for(self, user_id: int) -> dict:
        return await repo.effective_plan(self.db, user_id)

    async def quota_ok(self, user_id: int, plan: dict, add_bytes: int = 0) -> tuple[bool, dict]:
        used = await repo.quota_used(self.db, user_id)
        limit = float(plan.get("daily_gb", 20)) * 1024 ** 3
        return (used["bytes"] + add_bytes) <= limit, {"used": used["bytes"], "limit": limit}

    async def abuse_hit(self, user_id: int) -> tuple[bool, int]:
        """False when the user is over the auto-lock limit (and gets locked)."""
        count = await repo.bump_abuse(self.db, user_id, self.cfg.auto_lock_window)
        limit = self.cfg.auto_lock_limit
        if limit and count > limit:
            await repo.lock_user(self.db, user_id, reason="Automatic: too many requests",
                                 by_id=0, minutes=self.cfg.auto_lock_minutes)
            await repo.audit(self.db, 0, "lock.auto", str(user_id),
                             {"count": count, "window": self.cfg.auto_lock_window})
            return False, 0
        if limit and count == int(limit * 0.7):
            return True, 0            # caller may warn once
        return True, 0

    async def feature_enabled(self, user_id: int, feature: str) -> bool:
        g = await self.glob()
        feats = g.get("features", {})
        if not feats.get(feature, True):
            return False
        plan = await self.plan_for(user_id)
        plan_feats = plan.get("features") or []
        if isinstance(plan_feats, list) and plan_feats and feature not in plan_feats:
            return False
        return True

    # ── the one call every entry point makes ─────────────────────────────
    async def gate(self, user_id: int, *, scope: str = "all", need_connect: bool = False,
                   feature: str = "", count_request: bool = True,
                   file_bytes: int = 0, skip_force_sub: bool = False) -> Decision:
        from ..strings import t

        role = await self.role(user_id)
        admin = role in ADMIN_BYPASS
        user = await repo.get_user(self.db, user_id) or {}
        lang = user.get("lang", "hinglish")

        if not admin:
            g = await self.glob()
            if g.get("maintenance"):
                return Decision(False, "maintenance",
                                t("maintenance", lang, msg=g.get("maintenance_msg", "")))

            lock = await self.lock_of(user_id, scope)
            if lock:
                return Decision(False, "locked", t("locked", lang,
                                                   reason=lock["reason"] or "—",
                                                   until=unlock_at_text(lock["expires_at"], self.cfg.timezone),
                                                   support=self.cfg.support_contact),
                                {"lock": lock})

            missing = [] if skip_force_sub else await self.force_sub_missing(user_id)
            if missing:
                links = "\n".join(f"• {c}" for c in missing)
                return Decision(False, "force_sub", t("force_sub", lang, channels=links),
                                {"channels": missing})

            if count_request:
                allowed, _ = await self.abuse_hit(user_id)
                if not allowed:
                    return Decision(False, "locked", t("locked", lang,
                                                      reason="Too many requests",
                                                      until=unlock_at_text(
                                                          time.time() + self.cfg.auto_lock_minutes * 60,
                                                          self.cfg.timezone),
                                                      support=self.cfg.support_contact))

            if feature and not await self.feature_enabled(user_id, feature):
                plan = await self.plan_for(user_id)
                return Decision(False, "feature",
                                t("feature_locked", lang, feature=feature.replace("_", " "),
                                  plan=self.cfg.plan_price_text), {"feature": feature})

            plan = await self.plan_for(user_id)
            ok, info = await self.quota_ok(user_id, plan, file_bytes)
            if not ok:
                return Decision(False, "quota", t("quota_hit", lang,
                                                  used=human_bytes(info["used"]),
                                                  limit=human_bytes(info["limit"]),
                                                  tz=self.cfg.timezone), info)

            if file_bytes:
                limit = int(plan.get("max_file_mb", 2000)) * 1024 ** 2
                if file_bytes > limit:
                    return Decision(False, "plan", t("too_big", lang,
                                                     name="file", size=human_bytes(file_bytes),
                                                     limit=human_bytes(limit)), {"limit": limit})

        if need_connect:
            acc = await repo.get_drive_account(self.db, user_id)
            if not acc:
                return Decision(False, "not_connected", t("not_connected", lang))
            if acc.get("needs_reconnect"):
                return Decision(False, "needs_reconnect", t("needs_reconnect", lang))

        return Decision(True, extra={"role": role, "plan": await self.plan_for(user_id)})
