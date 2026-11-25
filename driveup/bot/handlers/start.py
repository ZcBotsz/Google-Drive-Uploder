"""Commands that shape the account: start, help, connect, account, settings."""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery
from ..ui.buttons import auto as B, success
from aiogram.types import InlineKeyboardMarkup as KM
from aiogram.types import Message
from aiogram.types import WebAppInfo

from ...config import BRAND_NAME
from ...core.access import human_bytes, unlock_at_text
from ...db import repo
from ...core import naming as naming_mod
from ...core.naming import NamingOptions
from ...drive import auth as oauth
from ...drive.endpoints import parse_folder_id
from ...strings import esc, sc, t
from ..keyboards import cb, connect_kb, force_sub_kb, naming_kb, sequence_kb
from ..ui.stream import stream_reply

log = logging.getLogger("driveup.handlers.start")
router = Router(name="start")


def app_of(data: dict):
    return data["_app"]


def app_user_id(data: dict) -> int:
    """The Telegram id from the access middleware (never from user input)."""
    try:
        return int(data["user"]["user_id"])
    except (KeyError, TypeError, ValueError):
        return 0


@router.message(CommandStart())
async def cmd_start(msg: Message, **data):
    app = app_of(data)
    lang = data["lang"]
    user = data["user"]
    from ...config import BRAND_NAME, effective_max_file_mb
    cap_mb = effective_max_file_mb(app.cfg)
    global_settings = await repo.get_global(app.db)
    drive_account = await repo.get_drive_account(app.db, user["user_id"])
    current_plan = await repo.effective_plan(app.db, user["user_id"])
    max_plan = await repo.get_plan(app.db, "max") or {}
    brand = esc(global_settings.get("brand_name") or BRAND_NAME)
    price = esc(global_settings.get("plan_price_text") or app.cfg.plan_price_text)
    support = esc(global_settings.get("support_contact") or app.cfg.support_contact)
    connected_line = (f"🔗 <b>{sc('DRIVE')}:</b> {esc(drive_account.get('email') or sc('CONNECTED'))}"
                      if drive_account and not drive_account.get("needs_reconnect")
                      else f"🔌 <b>{sc('DRIVE')}:</b> {sc('NOT CONNECTED')}")
    welcome = (
        f"╭━━━━〔 🚀 <b>{sc(brand)}</b> 🚀 〕━━━━╮\n"
        f"│  <i>{sc('TELEGRAM · STUDIO · GOOGLE DRIVE')}</i>  │\n"
        "╰━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╯\n\n"
        f"👋 <b>{sc('WELCOME')}</b>\n"
        "Connect Google Drive, send a supported file or link, and follow progress. "
        "You can also upload directly in the browser Studio.\n\n"
        f"{connected_line}\n"
        f"⭐ <b>{sc('PLAN')}:</b> {esc(current_plan.get('name') or current_plan.get('id'))}\n\n"
        f"🚀 <b>{sc('QUICK FEATURES')}</b>\n"
        f"• {sc('FOLDERS · BATCHES · FILE NAMING')}\n"
        f"• {sc('UPLOAD ORDER · THUMBNAILS · STUDIO')}\n"
        f"• {sc('LIVE PROGRESS · QUEUE · HISTORY · STATS')}\n\n"
        f"⭐ <b>{sc('MAX')} · {price}</b>\n"
        f"{sc('UP TO')} {max_plan.get('daily_gb', 750):g} {sc('GB / DAY')} · "
        f"{max_plan.get('concurrent', 4)} {sc('CONCURRENT JOBS')} · "
        f"{max_plan.get('max_files_job', 5000):,} {sc('FILES / JOB')}\n"
        f"📦 {sc('FILE CEILING')}: {cap_mb} MB · "
        f"{sc('DRIVE QUOTA AND SERVICE LIMITS APPLY')}\n\n"
        f"💬 {sc('SUPPORT')}: {support}\n"
        f"<i>{sc('CHOOSE AN ACTION BELOW')}</i>"
    )
    missing = await app.access.force_sub_missing(user["user_id"])
    payload = (msg.text or "").partition(" ")[2].strip().lower()
    if missing:
        kb = force_sub_kb(missing)          # must join before anything else
    elif payload == "studio":
        # Landing-page CTA deep-links back into Telegram, then gives the user
        # a fresh signed link to the private Studio workspace.
        kb = KM(inline_keyboard=[[
            success("Open Upload Studio", icon="🌐",
                    url=app.studio_url(user_id=user["user_id"]))
        ]])
    else:
        # Every command is a button — nobody should have to remember /syntax.
        from ..menus import quick_kb
        is_admin = data.get("role") in ("owner", "admin", "support") or await repo.is_admin(
            app.db, user["user_id"], app.cfg.owner_id)
        kb = quick_kb(is_admin=is_admin,
                      is_connected=bool(drive_account and not drive_account.get("needs_reconnect")))
    from ..ui import emoji as animated
    welcome = animated.apply(welcome, app.cfg, global_settings)
    await msg.answer(welcome, reply_markup=kb)
    await repo.audit(app.db, user["user_id"], "bot.start")
    await app.log_event("#NewUser", {"id": user["user_id"], "name": user.get("name", "")})


@router.message(Command("storage"))
async def cmd_storage(msg: Message, **data):
    """How much Drive space is left — the question behind every quota failure."""
    from ...core.preflight import quota_line
    app = app_of(data)
    if not await repo.get_drive_account(app.db, msg.from_user.id):
        return await msg.answer("No Google Drive connected yet — send /connect first.")
    try:
        line = await quota_line(app, msg.from_user.id)
    except Exception as e:                              # noqa: BLE001
        log.debug("storage check failed: %s", e)
        return await msg.answer("Could not read Drive storage just now — try again in a moment.")
    await msg.answer(line)


@router.message(Command("copy"))
async def cmd_copy(msg: Message, **data):
    """Copy a Drive file or folder into your Drive — server-side, no download.

    Adapted from `cdfxscrq/GDrive-Uploader-TG-Bot`. The reference walked the
    tree with no cycle guard, no file cap and no rate-limit handling; ours stops
    on a visited set, a file ceiling and Drive's own quota errors.
    """
    app = app_of(data)
    uid = msg.from_user.id
    if not await repo.get_drive_account(app.db, uid):
        return await msg.answer("Connect Google Drive first — send /connect.")
    parts = (msg.text or "").split(maxsplit=1)
    if len(parts) < 2:
        return await msg.answer(
            "<b>Copy a Drive file or folder</b>\n\n"
            "Send <code>/copy &lt;drive link&gt;</code> — it lands in your "
            "destination folder without being downloaded and re-uploaded.\n\n"
            "<i>Only links your connected account can see will work.</i>")
    from ...drive.client import folder_id_from_url
    fid = folder_id_from_url(parts[1].strip())
    if not fid:
        return await msg.answer("That does not look like a Google Drive link.")
    client = await app.accounts.client(uid)
    status = await msg.answer("🔎 Looking up the link…")
    try:
        meta = await client.get(fid)
    except Exception as e:                              # noqa: BLE001
        log.debug("copy lookup failed: %s", e)
        return await status.edit_text(
            "❗ Could not read that link. Make sure the account you connected "
            "can open it, then try again.")
    user = await repo.get_user(app.db, uid)
    dest = (user.get("settings") or {}).get("default_folder") or {"folder_id": "root"}
    parent = dest.get("folder_id") or "root"
    try:
        if meta.get("mimeType") == "application/vnd.google-apps.folder":
            await status.edit_text("🗂️ Copying the folder tree…")
            done = await client.copy_tree(fid, parent)
            await status.edit_text(
                f"✅ Copied <b>{esc(meta.get('name', 'folder'))}</b> — "
                f"{done['files']} file(s).")
        else:
            made = await client.copy_file(fid, parent)
            await status.edit_text(
                f"✅ Copied <b>{esc(made.get('name', 'file'))}</b>\n"
                f"{made.get('webViewLink', '')}")
    except Exception as e:                              # noqa: BLE001
        log.warning("copy failed: %s", e)
        await status.edit_text("❗ Copy failed — Drive quota or permissions. "
                               "Nothing was changed.")


# Confirmation snapshots are deliberately process-local, user-bound and short-lived.
_protection_previews: dict = {}


@router.message(Command("protect"))
async def cmd_protect(msg: Message, **data):
    """Preview a recursive, non-sharing-changing download restriction."""
    import secrets
    import time
    if msg.chat.type != "private":
        return await msg.answer("For privacy, send /protect in a private chat with the bot.")
    app, uid = app_of(data), msg.from_user.id
    if not await repo.get_drive_account(app.db, uid):
        return await msg.answer("Connect Google Drive first — send /connect.")
    from ...drive.client import folder_id_from_url
    parts = (msg.text or "").split(maxsplit=1)
    fid = folder_id_from_url(parts[1]) if len(parts) > 1 else ""
    if not fid:
        return await msg.answer("Send <code>/protect YOUR_FOLDER_LINK</code> to preview protection of existing files and subfolders.")
    status = await msg.answer("Scanning folder and subfolders. No changes yet…")
    try:
        client = await app.accounts.client(uid)
        meta = await client.get(fid)
        files = await client.protection_inventory(fid)
    except Exception as e:
        log.warning("protection scan failed: %s", e)
        return await status.edit_text("Could not scan this folder. No files were changed. Check access, OAuth scope, and the 2,000-item safety limit.")
    if not files:
        return await status.edit_text("No eligible files found. Shortcuts are not followed; no changes were made.")
    now = time.monotonic()
    for key, value in list(_protection_previews.items()):
        if value["expires"] < now:
            _protection_previews.pop(key, None)
    token = secrets.token_urlsafe(12)
    _protection_previews[token] = {"uid": uid, "files": files, "expires": now + 600}
    kb = KM(inline_keyboard=[[B("Apply protection", callback_data=f"prot:{token}"),
                              B("Cancel", callback_data=f"protx:{token}")]])
    await status.edit_text(
        f"<b>Protection preview</b>\nFolder: {esc(meta.get('name', 'folder'))}\n"
        f"Files found: <b>{len(files)}</b> (including subfolders). Shortcuts are skipped.\n\n"
        "This attempts to restrict download, copy and print for readers and writers. "
        "It does not remove existing sharing. Some files may fail if you lack permission. "
        "Confirm within 10 minutes.", reply_markup=kb)


@router.callback_query(F.data.startswith("protx:"))
async def cancel_protection(cbq: CallbackQuery, **data):
    token = (cbq.data or "").split(":", 1)[1]
    preview = _protection_previews.get(token)
    if not preview or preview["uid"] != cbq.from_user.id:
        return await cbq.answer("Preview expired or belongs to another user.", show_alert=True)
    _protection_previews.pop(token, None)
    await cbq.answer("Cancelled")
    await cbq.message.edit_text("Cancelled. No files were changed.")


@router.callback_query(F.data.startswith("prot:"))
async def apply_protection(cbq: CallbackQuery, **data):
    import time
    token = (cbq.data or "").split(":", 1)[1]
    preview = _protection_previews.get(token)
    if not preview or preview["uid"] != cbq.from_user.id or preview["expires"] < time.monotonic():
        return await cbq.answer("Preview expired or belongs to another user. Run /protect again.", show_alert=True)
    _protection_previews.pop(token, None)  # one-shot; concurrent clicks cannot duplicate work
    await cbq.answer("Applying protection")
    await cbq.message.edit_text("Applying protection. Please wait…")
    client = await app_of(data).accounts.client(cbq.from_user.id)
    ok, failures = 0, []
    for f in preview["files"]:
        try:
            await client.restrict_downloads(f["id"])
            ok += 1
        except Exception as e:
            log.warning("protect failed for %s: %s", f["id"], e)
            failures.append(f)
    lines = [f"✅ Protected: <b>{ok}</b>", f"❌ Failed: <b>{len(failures)}</b>"]
    if failures:
        lines.append("Some files could not be protected. Check ownership, permissions and OAuth scope, then run /protect again.")
        lines += [f"• {esc(f['name'][:80])} (<code>{esc(f['id'])}</code>)" for f in failures[:10]]
        if len(failures) > 10:
            lines.append(f"…and {len(failures)-10} more.")
    lines.append("Existing sharing is unchanged. Screenshots and external recording cannot be prevented.")
    await cbq.message.edit_text("\n".join(lines))


@router.message(Command("help"))
async def cmd_help(msg: Message, **data):
    app = app_of(data)
    is_admin = await repo.is_admin(app.db, msg.from_user.id, app.cfg.owner_id) \
        or data.get("role") in ("owner", "admin", "support")
    text = HELP_ADMIN if is_admin else HELP_USER
    if is_admin:
        await stream_reply(app.bot, msg.chat.id, text, delay=0.012)
    else:
        await msg.answer(text)


def is_admin_for(app, user_id: int, data: dict) -> bool:
    if app.cfg.owner_id and user_id == app.cfg.owner_id:
        return True
    return data.get("role") in ("owner", "admin", "support")


HELP_USER = """<b>How it works</b>
1. Open <b>/connect</b> and allow Google Drive.
2. Send videos, photos or documents — or paste a direct link — and a job card appears.
   A link can be renamed as you send it: <code>https://…/f.zip | my movie.mkv</code>
3. Tap <b>📂 Choose folder</b>, <b>✏️ Naming</b>, <b>🔢 Sequence</b>.
4. Tap <b>🚀 Start</b>, or <b>🌐 Open Upload Studio</b> to drag-and-drop in the browser.

<b>Thumbnails</b>
/thumb_set then send a photo (under 2 MB) — every file you upload afterwards
carries it as the Drive preview image. /thumb shows it, /delthumb removes it.

<b>Commands</b>
/start · main menu
/connect · link Google Drive
/disconnect · unlink Drive
/account · plan, quota and Drive status
/copy · duplicate a Drive file or folder (no download)
/protect · restrict downloads for files in a Drive folder
/thumb_set · set your custom upload thumbnail
/delthumb · remove it
/storage · how much Google Drive space is left
/folders · destination folders
/setfolder · pick a folder without a job
/naming · default naming template
/sequence · default ordering
/queue · running and waiting jobs
/history · your last 15 jobs
/mystats · your totals
/plan · plans and limits
/settings · language and options
/studio · open the web Studio
/cancel · stop the current job
/help · this message"""

HELP_ADMIN = HELP_USER + """

<b>Admin</b>
/admin · panel · /stats · /users · /userinfo · /lock · /unlock · /locks
/addplan · /removeplan · /expiring · /queue · /pause_all · /resume_all
/broadcast · /maintenance · /audit · /export · /system
/addadmin · /removeadmin"""


@router.message(Command("export"))
async def cmd_export(msg: Message, **data):
    """Email-less export: a CSV of the last 1000 jobs, straight in the chat."""
    app = app_of(data)

    if not is_admin_for(app, msg.from_user.id, data):
        return await msg.answer("🔒 This command is for admins.")
    rows = await repo.all_rows(app.db, "SELECT id,user_id,status,created_at FROM jobs "
                                      "ORDER BY created_at DESC LIMIT 1000")
    body = "job_id,user_id,status,created\n" + "\n".join(
        f"{r['id']},{r['user_id']},{r['status']},{int(r['created_at'] or 0)}" for r in rows)
    from aiogram.types import BufferedInputFile
    await app.bot.send_document(
        msg.chat.id, BufferedInputFile(body.encode(), filename="driveup-jobs.csv"),
        caption=f"📄 {len(rows)} jobs")


@router.callback_query(F.data == cb("subok"))
async def cb_sub_ok(call: CallbackQuery, **data):
    app = app_of(data)
    uid = data["user"]["user_id"]
    app.access.forget_sub_cache(uid)
    missing = await app.access.force_sub_missing(uid)
    if missing:
        await call.answer()
        await call.message.edit_text(t("sub_still", data["lang"]),
                                     reply_markup=force_sub_kb(missing))
        return
    await call.answer(t("sub_ok", data["lang"]))
    await call.message.edit_reply_markup(reply_markup=None)
    await call.message.answer(t("welcome", data["lang"], brand=BRAND_NAME))


# ── Google Drive connect ───────────────────────────────────────────────────
@router.message(Command("connect"))
async def cmd_connect(msg: Message, **data):
    app = app_of(data)
    lang = data["lang"]
    user = data["user"]
    acc = await repo.get_drive_account(app.db, user["user_id"])
    if acc and not acc.get("needs_reconnect"):
        return await msg.answer(t("connected", lang, email=esc(acc["email"]),
                                  free="—", total="—"))
    state = oauth.new_state()
    verifier = oauth.new_verifier()
    await repo.create_state(app.db, state, user["user_id"], verifier, ttl=900)
    url = oauth.build_authorize_url(app.cfg, state, verifier)
    await msg.answer(
        "<b>Link Google Drive</b>\n\n"
        "1. Tap the button below\n"
        "2. Pick the Google account\n"
        "3. Allow Drive access\n"
        "4. Come back here — I'll confirm.\n\n"
        f"<i>Scope: {'full Drive' if app.cfg.scope_mode_full else 'only files this app creates'}</i>",
        reply_markup=connect_kb(url, lang))
    await repo.audit(app.db, user["user_id"], "drive.connect_start")


@router.message(Command("disconnect"))
async def cmd_disconnect(msg: Message, **data):
    app = app_of(data)
    ok = await app.accounts.disconnect(data["user"]["user_id"])
    await msg.answer(t("disconnected", data["lang"]) if ok else t("not_connected", data["lang"]))


@router.message(Command("account"))
async def cmd_account(msg: Message, **data):
    app = app_of(data)
    lang = data["lang"]
    uid = data["user"]["user_id"]
    acc = await repo.get_drive_account(app.db, uid)
    plan = await repo.effective_plan(app.db, uid)
    usage = await repo.quota_used(app.db, uid)
    lock = await repo.get_lock(app.db, uid)
    if not acc:
        return await msg.answer(t("not_connected", lang))
    try:
        client = await app.accounts.client(uid)
        about = await client.about()
        total = human_bytes(about["limit"]) if about["limit"] else "no fixed limit reported"
        total_used = human_bytes(about["usage"]) if about.get("usage_reported") else "not reported"
        drive_used = human_bytes(about["drive_used"]) if about.get("drive_used") is not None else "not reported"
        other_used = human_bytes(about["other_services_used"]) if about.get("other_services_used") is not None else "not reported"
        display_name = about.get("name") or ""
        free = human_bytes(about["free"]) if about["free"] >= 0 else "not reported"
    except Exception as e:                                # noqa: BLE001
        total, total_used, drive_used, other_used, free, display_name = "?", "?", "?", "?", "?", ""

    lines = [
        f"<b>👤 {sc('ACCOUNT OVERVIEW')}</b>",
        (f"📛 {sc('NAME')}: <b>{esc(display_name)}</b>\n" if display_name else "") +
        f"📧 {sc('GOOGLE ACCOUNT')}: <b>{esc(acc['email'])}</b>" + (f"  ⚠️ {sc('RECONNECT NEEDED')}" if acc.get("needs_reconnect") else ""),
        f"💾 {sc('TOTAL GOOGLE STORAGE')}: <b>{total_used} used</b> / {total}",
        f"📁 {sc('DRIVE FILES')}: <b>{drive_used} used</b>",
        f"🌐 {sc('OTHER GOOGLE SERVICES')}: <b>{other_used} combined</b>\n<i>{sc('GMAIL AND PHOTOS ARE NOT SPLIT OUT BY THIS API')}</i>",
        f"🆓 {sc('AVAILABLE')}: <b>{free}</b>",
        f"💎 {sc('PLAN')}: <b>{esc(plan.get('name') or plan.get('id'))}</b>"
        + (f" · {sc('UNTIL')} {unlock_at_text(plan['expires_at'], app.cfg.timezone)}" if plan.get("expires_at") else ""),
        f"📅 {sc('TODAY')}: {human_bytes(usage['bytes'])} / {plan.get('daily_gb')} GB",
        f"🔐 {sc('DRIVE ACCESS')}: {'full Drive' if app.cfg.scope_mode_full else 'app-created files only'}",
    ]
    if lock:
        lines.append(f"🔒 locked until {unlock_at_text(lock['expires_at'], app.cfg.timezone)}")
    kb = KM(inline_keyboard=[
        [B(text="🔄 Reconnect", callback_data=cb("reconnect"))],
        [B(text="🌐 Studio", url=app.studio_url(user_id=app_user_id(data)))],
    ])
    await msg.answer("\n".join(lines), reply_markup=kb)


@router.callback_query(F.data == cb("reconnect"))
async def cb_reconnect(call: CallbackQuery, **data):
    app = app_of(data)
    state = oauth.new_state()
    verifier = oauth.new_verifier()
    await repo.create_state(app.db, state, data["user"]["user_id"], verifier, ttl=900)
    url = oauth.build_authorize_url(app.cfg, state, verifier)
    await call.message.edit_text(
        "Tap to reconnect Google Drive:", reply_markup=connect_kb(url, data["lang"]))
    await call.answer()


# ── settings ───────────────────────────────────────────────────────────────
SETTINGS_KB_ROWS = [
    ("conflict", "⚔️ Conflict policy"),
    ("share", "🔗 Shared link"),
    ("emoji", "✨ Animated emoji"),
    ("notify", "🔔 Notify on finish"),
]


@router.message(Command("folders"))
async def cmd_folders(msg: Message, **data):
    app = app_of(data)
    uid = msg.from_user.id
    favs = await repo.list_favourites(app.db, uid)
    if not favs:
        return await msg.answer(
            "📂 No saved folders yet.\n\nOpen a job card → <b>📂 Choose folder</b> → "
            "<b>🔗 Paste a folder link</b>, and it will be saved here.",
            reply_markup=KM(inline_keyboard=[[
                B(text="⭐ Save a folder", callback_data=cb("setlink"))]]))
    rows = [[B(text=f"📁 {f['label'] or f['folder_id'][:20]}",
               callback_data=cb("setfav", f["folder_id"]))] for f in favs]
    await msg.answer("📂 <b>Your folders</b>\nTap one to make it the default destination.",
                     reply_markup=KM(inline_keyboard=rows))


@router.message(Command("setfolder"))
async def cmd_setfolder(msg: Message, **data):
    app = app_of(data)
    parts = (msg.text or "").split(maxsplit=1)
    if len(parts) < 2:
        return await cmd_folders(msg, **data)
    folder_id = parse_folder_id(parts[1])
    path = "My Drive"
    if folder_id == "root" and "root" not in (parts[1] or "").lower():
        if not (parts[1] or "").strip():
            return await msg.answer("⚠️ Paste a Drive folder link, e.g. "
                                    "https://drive.google.com/drive/folders/…")
    if folder_id != "root":
        try:
            client = await app.accounts.client(msg.from_user.id)
            path, _ = await client.path_of(folder_id)
        except Exception as e:                             # noqa: BLE001
            # the link is still valid — we just cannot name it yet
            log.info("setfolder: path lookup failed (%s)", e)
            path = "Drive folder"
    await repo.upsert_user(app.db, msg.from_user.id,
                           msg.from_user.full_name or "", msg.from_user.username or "")
    await repo.set_setting(app.db, msg.from_user.id, "default_folder",
                           {"folder_id": folder_id, "path": path})
    await msg.answer(f"✅ Default destination: <b>{esc(path)}</b>")


@router.message(Command("naming"))
async def cmd_naming(msg: Message, **data):
    app = app_of(data)
    parts = (msg.text or "").split(maxsplit=1)
    s = await repo.get_user(app.db, msg.from_user.id)
    current = (s.get("settings", {}).get("naming") or {})
    if len(parts) >= 2 and "{" in parts[1]:
        opt = NamingOptions.from_dict({**current, "template": parts[1].strip()})
        await repo.set_setting(app.db, msg.from_user.id, "naming", opt.to_dict())
        return await msg.answer(f"✅ Default naming: <code>{esc(opt.template)}</code>\n\n"
                                "This applies to every new job.")
    tmpl = current.get("template") or "{original}.{ext}"
    await msg.answer(
        f"✏️ <b>Default naming</b>\n\n<code>{esc(tmpl)}</code>\n\n"
        "Send /naming &lt;template&gt; to change it, e.g.\n"
        "<code>/naming {seq:03d} - {original}.{ext}</code>",
        reply_markup=naming_kb(data["lang"]))


@router.message(Command("sequence"))
async def cmd_sequence(msg: Message, **data):
    app = app_of(data)
    parts = (msg.text or "").split(maxsplit=1)
    s = await repo.get_user(app.db, msg.from_user.id)
    mode = s.get("settings", {}).get("sequence_mode", "telegram_order")
    if len(parts) >= 2:
        mode = parts[1].strip().lower()
        if mode not in naming_mod.SEQUENCE_MODES:
            return await msg.answer("⚠️ Use one of: " + ", ".join(naming_mod.SEQUENCE_MODES))
        await repo.set_setting(app.db, msg.from_user.id, "sequence_mode", mode)
    await msg.answer(f"🔢 Default order: <b>{esc(mode)}</b>",
                     reply_markup=sequence_kb(mode))


@router.message(Command("settings"))
async def cmd_settings(msg: Message, **data):
    app = app_of(data)
    uid = data["user"]["user_id"]
    s = await repo.get_user(app.db, uid)
    st = s.get("settings", {})
    text = (
        f"<b>⚙️ {esc(app_title())}</b>\n\n"
        "🌐 language: <b>English</b>\n"
        f"⚔️ conflicts: <b>{esc(st.get('conflict', 'rename'))}</b>\n"
        f"🔗 sharing: <b>{esc(st.get('share', 'restricted'))}</b>\n"
        f"✨ animated emoji: <b>{'on' if st.get('emoji') else 'off'}</b>\n"
        f"🔔 notify: <b>{'on' if st.get('notify', True) else 'off'}</b>"
    )
    rows = [[B(text=label, callback_data=cb("set", key))] for key, label in SETTINGS_KB_ROWS]
    await msg.answer(text, reply_markup=KM(inline_keyboard=rows))


def app_title() -> str:
    return "Settings"


@router.callback_query(F.data.startswith("v1:set:"))
async def cb_setting(call: CallbackQuery, **data):
    app = app_of(data)
    key = (call.data or "").split(":")[2]
    uid = data["user"]["user_id"]
    user = await repo.get_user(app.db, uid)
    st = user.get("settings", {})
    if key == "lang":
        await repo.set_lang(app.db, uid, "en")
        await call.answer("The bot language is English.")
    elif key == "conflict":
        order = ["rename", "skip", "overwrite"]
        cur = st.get("conflict", "rename")
        await repo.set_user_settings(app.db, uid,
                                     {"conflict": order[(order.index(cur) + 1) % len(order)]})
        await call.answer()
    elif key == "share":
        cur = st.get("share", "restricted")
        await repo.set_user_settings(app.db, uid,
                                     {"share": "anyone" if cur == "restricted" else "restricted"})
        await call.answer()
    elif key == "emoji":
        await repo.set_user_settings(app.db, uid, {"emoji": not st.get("emoji")})
        await call.answer()
    elif key == "notify":
        await repo.set_user_settings(app.db, uid, {"notify": not st.get("notify", True)})
        await call.answer()
    await cmd_settings(call.message, data)


@router.callback_query(F.data == cb("noop"))
async def cb_noop(call: CallbackQuery):
    await call.answer()


@router.callback_query(F.data == cb("openstudio"))
async def cb_openstudio(call: CallbackQuery, **data):
    await call.answer()
    app = app_of(data)
    uid = app_user_id(data)
    plain = app.studio_url(user_id=uid)
    rows = [[B(text="🚀 Open Studio", url=plain)]]
    try:                       # inside Telegram: a Mini-App keeps initData + fullscreen
        rows.insert(0, [B(text="📱 Open in Telegram", web_app=WebAppInfo(url=plain))])
    except Exception:                          # noqa: BLE001 - older clients
        pass
    await call.message.answer("🌐 <b>Upload Studio</b>\n\nPick the folder, fix the names, "
                              "drag the order.", reply_markup=KM(inline_keyboard=rows))


@router.message(Command("studio"))
async def cmd_studio(msg: Message, **data):
    app = app_of(data)
    uid = data["user"]["user_id"]
    # the feature flag is enforced here too: hiding it in the UI is not access control
    if not await app.access.feature_enabled(uid, "web_studio"):
        d = await app.access.gate(uid, feature="web_studio", count_request=False)
        return await msg.answer(d.screen)
    job_id = app.draft_jobs.get(uid, "")
    url = app.studio_url(job_id, uid)
    rows = [[B(text="🚀 Open Studio", url=url)]]
    try:
        rows.insert(0, [B(text="📱 Open in Telegram", web_app=WebAppInfo(url=url))])
    except Exception:                          # noqa: BLE001
        pass
    await msg.answer(
        f"🌐 <b>{esc(BRAND_NAME)} Studio</b>\n\nPick the folder, fix the names, drag the order — "
        f"then start. Works inside Telegram and in any browser.",
        reply_markup=KM(inline_keyboard=rows))
