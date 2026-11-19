"""Admin bot commands (B8 mirror). Every command re-checks the role itself."""
from __future__ import annotations

import time

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery
from ..ui.buttons import auto as B
from aiogram.types import InlineKeyboardMarkup as KM
from aiogram.types import Message
from aiogram.types import WebAppInfo

from ...bot.ui.cards import human_secs
from ...core.access import human_bytes, unlock_at_text
from ...db import repo
from ...strings import esc
from ..keyboards import cb
from ..ui.stream import stream_reply

router = Router(name="admin")

ADMIN = ("owner", "admin")
OWNER = ("owner",)


def app_of(data: dict):
    return data["_app"]


def is_admin(data: dict) -> bool:
    app = data.get("_app")
    owner = getattr(getattr(app, "cfg", None), "owner_id", 0)
    if owner and data.get("event_from_user") and data["event_from_user"].id == owner:
        return True
    return data.get("role") in ADMIN


def is_owner(data: dict) -> bool:
    app = data.get("_app")
    owner = getattr(getattr(app, "cfg", None), "owner_id", 0)
    if owner and data.get("event_from_user") and data["event_from_user"].id == owner:
        return True
    return data.get("role") in OWNER


async def _silent(msg: Message) -> None:
    """Admin commands are invisible to normal users — but not confusing."""
    return await msg.answer("🔒 This command is for admins.<br>Type /help for what you can use."
                            .replace("<br>", "\n"))


@router.message(Command("admin"))
async def cmd_admin(msg: Message, **data):
    d = dict(data)
    app = app_of(d)
    if not is_admin(d):
        return await _silent(msg)
    # The raw /admin URL has no session in a phone browser (initData only exists
    # inside a Mini-App), so the panel used to answer 401 "Panel locked" forever.
    # A signed link carries the session in.
    uid = data["user"]["user_id"]
    url = app.studio_url(user_id=uid, admin=True).replace("/start?", "/start?to=admin&")
    rows = [[B(text="🛠 Open Admin Panel", url=url)]]
    try:
        rows.insert(0, [B(text="📱 Open in Telegram", web_app=WebAppInfo(url=url))])
    except Exception:                          # noqa: BLE001
        pass
    await msg.answer(f"🛠 <b>Admin</b>\n\nLink is valid for 15 minutes, then send /admin again.\n\n"
                     f"Commands: /stats /users /userinfo /lock /unlock /locks /addplan "
                     f"/removeplan /expiring /queue /pause_all /resume_all /broadcast "
                     f"/maintenance /audit /system /addadmin /removeadmin",
                     reply_markup=KM(inline_keyboard=rows))


@router.message(Command("stats"))
async def cmd_stats(msg: Message, **data):
    app = app_of(dict(data))
    if not is_admin(dict(data)):
        return await _silent(msg)
    s = await repo.global_stats(app.db)
    text = (f"<b>📊 {esc('System stats')}</b>\n\n"
            f"👥 users: <b>{s['users']}</b> (24 h: {s['active_24h']})\n"
            f"⚙️ running: {s['jobs_running']} · queued: {s['jobs_queued']} · "
            f"failed: {s['jobs_failed']}\n"
            f"📤 today: {s['uploads_today']} uploads · {human_bytes(s['bytes_today'])}\n"
            f"🔒 locked: {s['locked']} · 💎 plans: {s['plans_active']}\n"
            f"⚠️ errors 24 h: {s['errors_today']}")
    await stream_reply(app.bot, msg.chat.id, text, delay=0.07)


@router.message(Command("users"))
async def cmd_users(msg: Message, **data):
    app = app_of(dict(data))
    if not is_admin(dict(data)):
        return await _silent(msg)
    parts = (msg.text or "").split()
    page = 1
    if len(parts) > 1:
        try:
            page = max(1, int(parts[1]))
        except ValueError:
            page = 1
    per = 8
    rows = await repo.list_users(app.db, limit=per, offset=(page - 1) * per)
    total = await repo.count_users(app.db)
    lines = [f"<b>👥 Users</b> — page {page} · {total} total"]
    for r in rows:
        lock = await repo.get_lock(app.db, r["user_id"])
        mark = "🔒" if lock else "·"
        lines.append(f"{mark} <code>{r['user_id']}</code> {esc(r['name'])}"
                     + (f" @{esc(r['username'])}" if r["username"] else ""))
    kb = KM(inline_keyboard=[[
        B(text="‹ prev", callback_data=f"v1:adm:users:{max(1, page - 1)}"),
        B(text=f"{page}", callback_data="v1:noop"),
        B(text="next ›", callback_data=f"v1:adm:users:{page + 1}"),
    ]])
    await msg.answer("\n".join(lines), reply_markup=kb)


@router.callback_query(F.data.startswith("v1:adm:users:"))
async def cb_users_page(call: CallbackQuery, **data):
    if not is_admin(dict(data)):
        return await call.answer("not for you", show_alert=True)
    page = int((call.data or "").split(":")[3])
    fake = call.message
    fake.text = f"/users {page}"
    await cmd_users(fake, data)
    await call.answer()


@router.message(Command("userinfo"))
async def cmd_userinfo(msg: Message, **data):
    app = app_of(dict(data))
    if not is_admin(dict(data)):
        return await _silent(msg)
    parts = (msg.text or "").split(maxsplit=1)
    if len(parts) < 2:
        return await msg.answer("Usage: /userinfo <id|@username>")
    target = parts[1].strip()
    if target.startswith("@"):
        row = await app.db.one("SELECT * FROM users WHERE username=?", (target[1:],))
        uid = row["user_id"] if row else 0
    else:
        try:
            uid = int(target)
        except ValueError:
            return await msg.answer("❌ Send a numeric id or @username.")
    u = await repo.get_user(app.db, uid)
    if not u:
        return await msg.answer("❌ No such user.")
    st = await repo.user_stats(app.db, uid)
    plan = await repo.effective_plan(app.db, uid)
    acc = await repo.get_drive_account(app.db, uid)
    lock = await repo.get_lock(app.db, uid)
    usage = await repo.quota_used(app.db, uid)
    storage_lines = ""
    if acc and not acc.get("needs_reconnect"):
        try:
            client = await app.accounts.client(uid)
            about = await client.about()
            total = human_bytes(about["limit"]) if about.get("limit") else "no fixed limit reported"
            overall_used = human_bytes(about.get("usage", 0)) if about.get("usage_reported") else "not reported"
            drive_used = human_bytes(about["drive_used"]) if about.get("drive_used") is not None else "not reported"
            other_used = human_bytes(about["other_services_used"]) if about.get("other_services_used") is not None else "not reported"
            free = human_bytes(about["free"]) if about.get("free", -1) >= 0 else "not reported"
            google_name = about.get("name") or ""
            storage_lines = (
                (f"\n📛 Google name: {esc(google_name)}" if google_name else "")
                + f"\n💾 total: {overall_used} used / {total} · {free} free"
                + f"\n📁 Drive: {drive_used} used"
                + f"\n🌐 other Google services: {other_used} combined"
                "\n<i>Gmail and Photos are not split out by this API.</i>"
            )
        except Exception:  # noqa: BLE001 - account details may be temporarily unavailable
            storage_lines = "\n💾 Drive quota details are temporarily unavailable."
    await msg.answer(
        f"<b>👤 {esc(u['name'])}</b> · <code>{uid}</code>\n"
        f"@{esc(u['username']) or '—'} · {esc(u['role'])} · {esc(u['lang'])}\n\n"
        f"💎 {esc(plan.get('name') or plan.get('id'))}\n"
        f"📧 Drive account: {esc((acc or {}).get('email') or 'not connected')}\n"
        f"📦 {st['files']} files · {human_bytes(st['bytes'])}\n"
        f"📅 today: {human_bytes(usage['bytes'])}"
        f"{storage_lines}\n"
        f"🔒 {('until ' + unlock_at_text(lock['expires_at'], app.cfg.timezone)) if lock else 'not locked'}")


@router.message(Command("lock"))
async def cmd_lock(msg: Message, **data):
    app = app_of(dict(data))
    if not is_admin(dict(data)):
        return await _silent(msg)
    parts = (msg.text or "").split(maxsplit=3)
    if len(parts) < 2 or not parts[1].lstrip("-").isdigit():
        return await msg.answer("Usage: /lock <user_id> [minutes] [reason]")
    uid = int(parts[1])
    minutes = app.cfg.lock_default_minutes
    reason = "Admin decision"
    if len(parts) > 2:
        if parts[2].isdigit():
            minutes = int(parts[2])
            if len(parts) > 3:
                reason = parts[3]
        else:
            reason = " ".join(parts[2:])
    target_role = await repo.role_of(app.db, uid, app.cfg.owner_id)
    if target_role in ("owner", "admin"):
        return await msg.answer("❌ Admins cannot be locked.")
    row = await repo.lock_user(app.db, uid, reason=reason, by_id=data["user"]["user_id"],
                               minutes=minutes)
    killed = await app.worker.cancel_user_jobs(uid, why="locked")
    await repo.kill_user_sessions(app.db, uid)
    await repo.audit(app.db, data["user"]["user_id"], "user.lock", str(uid),
                     {"reason": reason, "minutes": minutes, "jobs": killed})
    await app.lock_card("locked", uid, reason, row["expires_at"], data["user"]["user_id"],
                        minutes=minutes)
    await msg.answer(f"🔒 Locked <code>{uid}</code> for {minutes} min · "
                     f"{killed} job(s) cancelled · reason: {esc(reason)}")


@router.message(Command("unlock"))
async def cmd_unlock(msg: Message, **data):
    app = app_of(dict(data))
    if not is_admin(dict(data)):
        return await _silent(msg)
    parts = (msg.text or "").split()
    if len(parts) < 2 or not parts[1].lstrip("-").isdigit():
        return await msg.answer("Usage: /unlock <user_id>")
    uid = int(parts[1])
    ok = await repo.unlock_user(app.db, uid)
    await repo.audit(app.db, data["user"]["user_id"], "user.unlock", str(uid))
    if ok:
        await app.lock_card("unlocked", uid, "", 0, data["user"]["user_id"])
    await msg.answer(f"{'🔓 Unlocked' if ok else 'ℹ️ Was not locked'} <code>{uid}</code>")


@router.message(Command("locks"))
async def cmd_locks(msg: Message, **data):
    app = app_of(dict(data))
    if not is_admin(dict(data)):
        return await _silent(msg)
    rows = await repo.list_locks(app.db)
    if not rows:
        return await msg.answer("🔓 No active locks.")
    lines = ["<b>🔒 Active locks</b>"]
    for r in rows:
        u = await repo.get_user(app.db, r["user_id"])
        lines.append(f"· <code>{r['user_id']}</code> {esc((u or {}).get('name', ''))} · "
                     f"{esc(r['reason'])} · till {unlock_at_text(r['expires_at'], app.cfg.timezone)}")
    await msg.answer("\n".join(lines))


@router.message(Command("addplan"))
async def cmd_addplan(msg: Message, **data):
    app = app_of(dict(data))
    if not is_admin(dict(data)):
        return await _silent(msg)
    parts = (msg.text or "").split()
    if len(parts) < 3 or not parts[1].lstrip("-").isdigit():
        return await msg.answer("Usage: /addplan <user_id> <days> [plan_id]")
    uid, days = int(parts[1]), int(parts[2])
    plan_id = parts[3] if len(parts) > 3 else "pro"
    row = await repo.grant_plan(app.db, uid, plan_id, days)
    await repo.audit(app.db, data["user"]["user_id"], "plan.grant", str(uid),
                     {"plan": plan_id, "days": days})
    await msg.answer(f"💎 Gave <b>{esc(plan_id)}</b> to <code>{uid}</code> for {days} days "
                     f"(till {unlock_at_text(row['expires_at'], app.cfg.timezone)})")


@router.message(Command("removeplan"))
async def cmd_removeplan(msg: Message, **data):
    app = app_of(dict(data))
    if not is_admin(dict(data)):
        return await _silent(msg)
    parts = (msg.text or "").split()
    if len(parts) < 2:
        return await msg.answer("Usage: /removeplan <user_id>")
    uid = int(parts[1]) if parts[1].lstrip("-").isdigit() else 0
    ok = await repo.revoke_plan(app.db, uid)
    await repo.audit(app.db, data["user"]["user_id"], "plan.revoke", str(uid))
    await msg.answer(f"{'💎 Plan removed' if ok else 'ℹ️ No plan'} for <code>{uid}</code>")


@router.message(Command("expiring"))
async def cmd_expiring(msg: Message, **data):
    app = app_of(dict(data))
    if not is_admin(dict(data)):
        return await _silent(msg)
    rows = await repo.expiring_plans(app.db, 7)
    if not rows:
        return await msg.answer("No plans expiring this week.")
    lines = ["<b>⏳ Expiring in 7 days</b>"]
    for r in rows:
        lines.append(f"· <code>{r['user_id']}</code> {esc(r.get('name') or r['plan_id'])} · "
                     f"{unlock_at_text(r['expires_at'], app.cfg.timezone)}")
    await msg.answer("\n".join(lines))


@router.message(Command("pause_all"))
async def cmd_pause_all(msg: Message, **data):
    app = app_of(dict(data))
    if not is_admin(dict(data)):
        return await _silent(msg)
    app.worker.paused_global = True
    await repo.set_global(app.db, {"uploads_paused": True})
    for j in await repo.running_jobs(app.db):
        await app.worker.pause(j["id"])
    await msg.answer("⏸ All jobs paused.")


@router.message(Command("resume_all"))
async def cmd_resume_all(msg: Message, **data):
    app = app_of(dict(data))
    if not is_admin(dict(data)):
        return await _silent(msg)
    app.worker.paused_global = False
    await repo.set_global(app.db, {"uploads_paused": False})
    for j in await repo.running_jobs(app.db, statuses=("paused",)):
        await app.worker.resume(j["id"])
    await msg.answer("▶️ All jobs resumed.")


@router.message(Command("maintenance"))
async def cmd_maintenance(msg: Message, **data):
    app = app_of(dict(data))
    if not is_admin(dict(data)):
        return await _silent(msg)
    parts = (msg.text or "").split(maxsplit=2)
    if len(parts) < 2 or parts[1] not in ("on", "off"):
        return await msg.answer("Usage: /maintenance on|off [message]")
    on = parts[1] == "on"
    patch = {"maintenance": on}
    if on and len(parts) > 2:
        patch["maintenance_msg"] = parts[2][:300]
    await repo.set_global(app.db, patch)
    await repo.audit(app.db, data["user"]["user_id"], "settings.maintenance", str(on))
    await msg.answer(f"🛠 Maintenance <b>{'ON' if on else 'OFF'}</b>")


@router.message(Command("broadcast"))
async def cmd_broadcast(msg: Message, **data):
    app = app_of(dict(data))
    if not is_admin(dict(data)):
        return await _silent(msg)
    text = (msg.text or "").split(maxsplit=1)
    if len(text) < 2:
        return await msg.answer("Usage: /broadcast <HTML message>\n"
                                "Use the panel for audiences and preview.")
    body = text[1]
    if len(body) > 3000:
        return await msg.answer("⚠️ Keep the broadcast under 3000 characters.")
    count = await repo.count_users(app.db)
    app.pending_broadcast = getattr(app, "pending_broadcast", {})
    app.pending_broadcast[data["user"]["user_id"]] = body
    await msg.answer(
        f"📣 <b>Preview</b>\n\n{body}\n\nSend to <b>{count}</b> users?",
        reply_markup=KM(inline_keyboard=[[
            B(text="✅ Confirm send", callback_data=cb("adm", "bcsend")),
            B(text="✖️ Cancel", callback_data=cb("adm", "bcdrop"))]]))


@router.callback_query(F.data == cb("adm", "bcsend"))
async def cb_broadcast_send(call: CallbackQuery, **data):
    app = app_of(dict(data))
    if not is_admin(dict(data)):
        return await call.answer("admin only", show_alert=True)
    body = (getattr(app, "pending_broadcast", {}) or {}).pop(data["user"]["user_id"], "")
    if not body:
        return await call.answer("Nothing to send", show_alert=True)
    await call.message.edit_text("📣 Sending…")
    import asyncio
    rows = await app.db.q("SELECT user_id FROM users")
    sent = failed = 0
    for r in rows:
        try:
            await app.bot.send_message(r["user_id"], body, parse_mode="HTML")
            sent += 1
        except Exception:                                  # noqa: BLE001
            failed += 1
        await asyncio.sleep(0.05)
    await repo.audit(app.db, data["user"]["user_id"], "broadcast.send", "all",
                     {"sent": sent, "failed": failed})
    await call.message.edit_text(f"📣 sent {sent} · failed {failed}")
    await call.answer("done")


@router.callback_query(F.data == cb("adm", "bcdrop"))
async def cb_broadcast_drop(call: CallbackQuery, **data):
    (getattr(app_of(dict(data)), "pending_broadcast", {}) or {}).pop(data["user"]["user_id"], None)
    await call.message.edit_text("✖️ Broadcast cancelled.")
    await call.answer()


@router.message(Command("audit"))
async def cmd_audit(msg: Message, **data):
    app = app_of(dict(data))
    if not is_admin(dict(data)):
        return await _silent(msg)
    rows = await repo.list_audit(app.db, 15)
    lines = ["<b>📜 Audit</b>"]
    for r in rows:
        when = time.strftime("%d %b %H:%M", time.localtime(r["ts"]))
        lines.append(f"· {when} <code>{r['actor']}</code> {esc(r['action'])} → {esc(r['target'])}")
    await msg.answer("\n".join(lines) if rows else "Audit is empty.")


@router.message(Command("system"))
async def cmd_system(msg: Message, **data):
    app = app_of(dict(data))
    if not is_admin(dict(data)):
        return await _silent(msg)
    import platform
    import shutil
    import sys
    parts = [f"<b>🖥 System</b>",
             f"Python {platform.python_version()} · {platform.system()} {platform.release()}",
             f"⏱ uptime: {human_secs(time.time() - app.started_at)}"]
    try:                                   # psutil is optional, never required
        import psutil
        vm = psutil.virtual_memory()
        cpu = psutil.cpu_percent(interval=None)
        disk = psutil.disk_usage("/")
        parts.append(f"CPU {cpu:.0f}% · RAM {vm.percent:.0f}% "
                     f"({human_bytes(vm.used)}/{human_bytes(vm.total)})")
        parts.append(f"Disk {disk.percent:.0f}% ({human_bytes(disk.used)}/{human_bytes(disk.total)})")
    except Exception:                      # noqa: BLE001
        total, used, free = shutil.disk_usage("/")
        parts.append(f"Disk {human_bytes(used)}/{human_bytes(total)}")
    pool = app.worker
    parts.append(f"⚙️ jobs running: {len(getattr(pool, '_jobs', {}))} · "
                 f"paused: {getattr(pool, 'paused_global', False)}")
    # Which Bot API are we on? This single line decides whether a 2 GB file can
    # ever arrive, so it belongs on the diagnostic screen, not in a log file.
    from ...config import effective_max_file_mb
    local = bool(app.cfg.uses_local_api)
    parts.append(f"🔌 Bot API: {'self-hosted (local)' if local else 'Telegram cloud'}")
    parts.append(f"🌐 {esc(app.cfg.api_base)}")
    if app.cfg.telegram_files_url:
        parts.append(f"📥 files: {esc(app.cfg.telegram_files_url)}")
    parts.append(f"📦 per-file cap: {effective_max_file_mb(app.cfg)} MB"
                 + ("" if local else "  ← 20 MB cloud ceiling"))
    if not local:
        parts.append("<i>Badi files ke liye docs/RENDER-LOCAL-API.md dekhiye.</i>")
    await msg.answer("\n".join(parts))


@router.message(Command("addadmin"))
async def cmd_addadmin(msg: Message, **data):
    app = app_of(dict(data))
    if not is_owner(dict(data)):
        return
    parts = (msg.text or "").split()
    if len(parts) < 2 or not parts[1].lstrip("-").isdigit():
        return await msg.answer("Usage: /addadmin <user_id> [role]")
    role = parts[2] if len(parts) > 2 and parts[2] in ("admin", "support") else "admin"
    await repo.add_admin(app.db, int(parts[1]), role)
    await repo.audit(app.db, data["user"]["user_id"], "admin.add", parts[1], {"role": role})
    await msg.answer(f"✅ <code>{parts[1]}</code> is now <b>{role}</b>")


@router.message(Command("removeadmin"))
async def cmd_removeadmin(msg: Message, **data):
    app = app_of(dict(data))
    if not is_owner(dict(data)):
        return
    parts = (msg.text or "").split()
    if len(parts) < 2:
        return await msg.answer("Usage: /removeadmin <user_id>")
    await repo.remove_admin(app.db, int(parts[1]))
    await repo.audit(app.db, data["user"]["user_id"], "admin.remove", parts[1])
    await msg.answer(f"✅ <code>{parts[1]}</code> is no longer an admin")
