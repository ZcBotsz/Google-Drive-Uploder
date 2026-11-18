"""The upload flow: intake → job card → folder / naming / sequence → progress."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery
from ..ui.buttons import auto as B
from aiogram.types import InlineKeyboardMarkup as KM
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.types import Message

from ...core import naming as naming_mod
from ...core.access import human_bytes, unlock_at_text
from ...config import effective_max_file_mb
from ...core.preflight import destination_problem, storage_problem
from ...net import fetch
from ...core.sequence import apply_mode, parse_message_link, parse_range
from ...db import repo
from ...strings import esc, t
from ..keyboards import (cb, job_card_kb, naming_kb, progress_kb, queue_kb,
                         sequence_kb)
from ..ui.cards import job_card, progress_card, summary_card

log = logging.getLogger("driveup.handlers.upload")
router = Router(name="upload")

FILE_ATTRS = ("document", "video", "audio", "photo", "voice", "video_note", "animation")


# ── intake helpers ─────────────────────────────────────────────────────────
def file_of(msg: Message) -> Optional[dict]:
    """Normalise any Telegram media into {file_id, unique_id, size, mime, name}."""
    d = msg.document
    if d:
        return {"file_id": d.file_id, "unique": d.file_unique_id, "size": d.file_size or 0,
                "mime": d.mime_type or "application/octet-stream",
                "name": d.file_name or f"document_{msg.message_id}"}
    v = msg.video
    if v:
        return {"file_id": v.file_id, "unique": v.file_unique_id, "size": v.file_size or 0,
                "mime": v.mime_type or "video/mp4",
                "name": v.file_name or f"video_{msg.message_id}.mp4"}
    a = msg.audio
    if a:
        ext = naming_mod.ext_from_mime(a.mime_type or "") or "mp3"
        return {"file_id": a.file_id, "unique": a.file_unique_id, "size": a.file_size or 0,
                "mime": a.mime_type or "audio/mpeg",
                "name": a.file_name or f"{a.title or 'audio'}_{msg.message_id}.{ext}"}
    p = msg.photo
    if p:
        big = p[-1]
        return {"file_id": big.file_id, "unique": big.file_unique_id,
                "size": big.file_size or 0, "mime": "image/jpeg",
                "name": f"photo_{msg.message_id}.jpg"}
    vo = msg.voice
    if vo:
        return {"file_id": vo.file_id, "unique": vo.file_unique_id, "size": vo.file_size or 0,
                "mime": vo.mime_type or "audio/ogg", "name": f"voice_{msg.message_id}.ogg"}
    an = msg.animation
    if an:
        return {"file_id": an.file_id, "unique": an.file_unique_id, "size": an.file_size or 0,
                "mime": an.mime_type or "video/mp4", "name": an.file_name or f"anim_{msg.message_id}.mp4"}
    vn = msg.video_note
    if vn:
        return {"file_id": vn.file_id, "unique": vn.file_unique_id, "size": vn.file_size or 0,
                "mime": "video/mp4", "name": f"note_{msg.message_id}.mp4"}
    # forwarded copy with no media we recognise
    if msg.document is None and msg.caption and msg.forward_origin:
        return None
    return None


def caption_of(msg: Message) -> str:
    return (msg.caption or "")[:400]


# ── generic file intake ────────────────────────────────────────────────────
@router.message(F.document | F.video | F.audio | F.photo | F.voice | F.video_note | F.animation)
async def on_file(msg: Message, **data):
    msgs = data.get("album") or [msg]
    await intake(app_of(data), data, msgs)


def app_of(data: dict):
    return data["_app"]


# ── link intake ────────────────────────────────────────────────────────────
@router.message(F.text & ~F.text.startswith("/"))
async def on_link(msg: Message, **data):
    """A pasted link becomes a job item, like a file would.

    Learned from the reference bots (all three accept links); the safety is our
    own — see net/fetch.py. Anything that is not a clean http(s) link is left
    for other handlers, including normal chit-chat.
    """
    text = (msg.text or "").strip()
    # A folder-link paste is a *reply* to the "send me a Drive folder link"
    # prompt. This handler is registered before the folder one, so without this
    # bail-out the pasted folder URL was queued as an upload job and the folder
    # silently never changed. SkipHandler hands the message to the next handler.
    if data["user"]["user_id"] in getattr(app_of(data), "pending_link", set()):
        raise SkipHandler()
    if not fetch.looks_like_url(text) and " | " not in text:
        return
    url, name = fetch.parse_url_line(text)
    if not fetch.looks_like_url(url):
        return
    app = app_of(data)
    lang = data["lang"]
    uid = data["user"]["user_id"]
    if not app.cfg.url_upload_enabled:
        return await msg.answer("Link uploads are switched off on this bot.")
    try:
        fetch.assert_public_url(url)
    except fetch.UnsafeURL as e:
        return await msg.answer(f"🚫 {esc(str(e))}")
    plan = await repo.effective_plan(app.db, uid)
    cap_mb = effective_max_file_mb(app.cfg, requested_mb=plan.get("max_file_mb", 2000))
    job_id = app.draft_jobs.get(uid)
    job = await repo.get_job(app.db, job_id) if job_id else None
    if not job or job["status"] != "draft":
        dest = await repo.get_user(app.db, uid)
        st = dest.get("settings", {})
        folder = st.get("default_folder") or {"folder_id": "root", "path": "My Drive"}
        naming = naming_mod.NamingOptions.from_dict(st.get("naming") or {}).to_dict()
        job_id = await repo.create_job(app.db, uid, destination=folder, naming=naming,
                                       sequence_mode=st.get("sequence_mode", "telegram_order"),
                                       conflict_policy=st.get("conflict", "rename"),
                                       share_mode=st.get("share", "restricted"),
                                       source={"chat_id": msg.chat.id, "url": True})
        app.draft_jobs[uid] = job_id
    label = name or fetch.filename_from_url(url)
    # One item, written once with its link — the worker streams it to Drive and
    # fills in the real size as soon as the response headers arrive.
    item_id = await repo.add_item(app.db, job_id, file_id="", file_unique_id="",
                                  msg_id=msg.message_id, size=0, mime="", name=label,
                                  chat_id=msg.chat.id, caption="")
    await repo.set_item(app.db, item_id,
                        tg={"url": url, "name": label, "size": 0, "file_id": "",
                            "file_unique_id": "", "msg_id": msg.message_id, "mime": ""})
    job = await repo.get_job(app.db, job_id)
    items = await repo.items_of(app.db, job_id)
    opt = naming_mod.NamingOptions.from_dict(job.get("naming") or {})
    await recompute(app, job, items, opt)
    totals = await repo.recompute_totals(app.db, job_id)
    await app.say(msg.chat.id,
                  f"🔗 Link added — <b>{esc(label)}</b>\n"
                  f"The real size is read when the download starts (limit {cap_mb} MB).",
                  reply_markup=job_card_kb(job_id, lang=lang,
                                           studio_url=app.studio_url(job_id, uid)))
    await app.log_event("#LinkAdded", {"user": uid, "host":
                                       (fetch.urlparse(url).hostname or "")[:60]})


async def intake(app, data: dict, msgs: list[Message]) -> None:
    lang = data["lang"]
    uid = data["user"]["user_id"]
    plan = await repo.effective_plan(app.db, uid)
    # The plan may promise 2000 MB, but the cloud Bot API refuses getFile above
    # 20 MB, so anything larger can never be fetched. Show the limit we can keep.
    cap_mb = effective_max_file_mb(app.cfg, requested_mb=plan.get("max_file_mb", 2000))

    files = []
    for m in msgs:
        f = file_of(m)
        if not f:
            continue
        if f["size"] > cap_mb * 1024 ** 2:
            await app.say(m.chat.id, t("too_big", lang, name=esc(f["name"]),
                                       size=human_bytes(f["size"]),
                                       limit=f"{cap_mb} MB")
                          + "\n\n" + t("file_limit_hint", lang))
            continue
        if f["size"] == 0:
            await app.say(m.chat.id, f"⚠️ <b>{esc(f['name'])}</b> is empty (0 bytes) — skipped.")
            continue
        files.append((m, f))

    if not files:
        return

    # reuse the user's draft job if it is still a draft, else make a new one
    job_id = app.draft_jobs.get(uid)
    job = await repo.get_job(app.db, job_id) if job_id else None
    if not job or job["status"] != "draft":
        dest = await repo.get_user(app.db, uid)
        st = dest.get("settings", {})
        folder = st.get("default_folder") or {"folder_id": "root", "path": "My Drive"}
        naming = naming_mod.NamingOptions.from_dict(st.get("naming") or {}).to_dict()
        job_id = await repo.create_job(app.db, uid, destination=folder, naming=naming,
                                       sequence_mode=st.get("sequence_mode", "telegram_order"),
                                       conflict_policy=st.get("conflict", "rename"),
                                       share_mode=st.get("share", "restricted"),
                                       source={"chat_id": files[0][0].chat.id})
        app.draft_jobs[uid] = job_id

    items = await repo.items_of(app.db, job_id)
    existing = {(i["tg"] or {}).get("file_unique_id") for i in items}
    added = 0
    for m, f in files:
        if f["unique"] in existing:            # de-dupe (Flow 3)
            continue
        await repo.add_item(app.db, job_id, file_id=f["file_id"], file_unique_id=f["unique"],
                            msg_id=m.message_id, size=f["size"], mime=f["mime"],
                            name=f["name"], chat_id=m.chat.id, caption=caption_of(m))
        await repo.remember_file(app.db, file_unique_id=f["unique"], chat_id=m.chat.id,
                                 message_id=m.message_id, file_id=f["file_id"],
                                 name=f["name"], size=f["size"], mime=f["mime"])
        added += 1

    job = await repo.get_job(app.db, job_id)
    items = await repo.items_of(app.db, job_id)
    opt = naming_mod.NamingOptions.from_dict(job.get("naming") or {})
    await recompute(app, job, items, opt)
    items = await repo.items_of(app.db, job_id)
    totals = await repo.recompute_totals(app.db, job_id)
    dest_path = (job.get("destination") or {}).get("path", "My Drive")

    text = job_card(files=len(items), size=totals["bytes"], dest=dest_path, lang=lang)
    if job.get("status_message", {}).get("message_id"):
        sm = job["status_message"]
        try:
            await app.bot.edit_message_text(chat_id=sm["chat_id"], message_id=sm["message_id"],
                                            text=text, parse_mode="HTML",
                                            reply_markup=job_card_kb(job_id, lang=lang,
                                                                     studio_url=app.studio_url(job_id, data["user"]["user_id"])))
            return
        except Exception:                                  # noqa: BLE001
            pass
    mid = await app.say(data["user"]["user_id"] if False else msgs[0].chat.id, text,
                          reply_markup=job_card_kb(job_id, lang=lang,
                                                   studio_url=app.studio_url(job_id, data["user"]["user_id"])))
    if mid:
        await repo.update_job(app.db, job_id,
                              status_message={"chat_id": msgs[0].chat.id, "message_id": mid})
    if added:
        await app.log_event("#JobDraft", {"user": uid, "files": len(items),
                                          "size": human_bytes(totals["bytes"])})


async def recompute(app, job: dict, items: list, opt) -> None:
    total = len(items)
    names = []
    for i, it in enumerate(items, 1):
        tg = it["tg"] or {}
        names.append(naming_mod.build_name(
            original=tg.get("name", "file"), mime=tg.get("mime", ""),
            seq=opt.start + (i - 1) * opt.step, index=i, total=total, opt=opt,
            size=tg.get("size", 0), caption=tg.get("caption", ""),
            message_id=tg.get("msg_id", 0)))
    resolved = naming_mod.dedupe_names(names, job.get("conflict_policy") or "rename")
    for i, it in enumerate(items):
        name = resolved[i] if i in resolved else names[i]
        if name != it["final_name"]:
            await repo.set_item(app.db, it["id"], final_name=name)


# ── job-card buttons ───────────────────────────────────────────────────────
@router.callback_query(F.data.startswith("v1:folder:"))
async def cb_folder(call: CallbackQuery, **data):
    await show_folder_menu(call, data)


async def show_folder_menu(call: CallbackQuery, data: dict) -> None:
    app = app_of(data)
    job = data.get("job")
    uid = data["user"]["user_id"]
    if not job:
        return await call.answer("job not found", show_alert=True)
    s = await repo.get_user(app.db, uid)
    st = s.get("settings", {})
    favs = await repo.list_favourites(app.db, uid)
    rows = [[B(text=f"{'✅ ' if (job['destination'] or {}).get('folder_id') == f['folder_id'] else '📁'} "
                     f"{f['label'] or f['folder_id'][:18]}",
               callback_data=cb("setfav", f["folder_id"]))] for f in favs]
    rows.append([B(text="⌂ My Drive", callback_data=cb("setfav", "root"))])
    rows.append([B(text="🔗 Paste a folder link", callback_data=cb("setlink"))])
    rows.append([B(text="🌐 Browse in Studio", url=app.studio_url(job["id"], uid))])
    await call.message.answer(
        f"📂 <b>Destination</b>\n\nNow: {esc((job['destination'] or {}).get('path', 'My Drive'))}\n\n"
        "Pick a favourite, or paste any Drive folder link.",
        reply_markup=KM(inline_keyboard=rows))
    await call.answer()


@router.callback_query(F.data.startswith("v1:setfav:"))
async def cb_set_fav(call: CallbackQuery, **data):
    app = app_of(data)
    job = data.get("job")
    fid = (call.data or "").split(":", 2)[2]
    path = "My Drive"
    if fid != "root":
        try:
            client = await app.accounts.client(job["user_id"])
            path, _ = await client.path_of(fid)
        except Exception:                                 # noqa: BLE001
            path = "Drive folder"
    await repo.update_job(app.db, job["id"], destination={"folder_id": fid, "path": path})
    await call.message.edit_text(
        f"✅ Destination: <b>{esc(path)}</b>\n\nTap /queue or the job card to continue.")
    await call.answer("saved")


@router.callback_query(F.data == cb("setlink"))
async def cb_set_link(call: CallbackQuery, **data):
    app = app_of(data)
    await call.message.answer(
        "🔗 Paste the Google Drive folder link here (it contains <code>/folders/…</code>).")
    await call.answer()
    # the next text message is consumed by on_link_text via a tiny state flag
    app.pending_link = getattr(app, "pending_link", set())
    app.pending_link.add(data["user"]["user_id"])


@router.message(F.text.regexp(r"https?://|folder|drive\.google\.com"))
async def on_link_text(msg: Message, **data):
    from ...drive.client import folder_id_from_url
    app = app_of(data)
    uid = data["user"]["user_id"]
    pending = getattr(app, "pending_link", set())
    link = msg.text or ""
    if uid in pending:
        pending.discard(uid)
        fid = folder_id_from_url(link)
        if not fid:
            return await msg.answer("❌ That is not a Drive folder link. It should look like\n"
                                    "<code>https://drive.google.com/drive/folders/1AbC…</code>")
        job_id = app.draft_jobs.get(uid)
        if not job_id:
            return await msg.answer(t("no_files", data["lang"]))
        path = "Drive folder"
        try:
            client = await app.accounts.client(uid)
            path, _ = await client.path_of(fid)
        except Exception:                                 # noqa: BLE001
            pass
        await repo.update_job(app.db, job_id, destination={"folder_id": fid, "path": path})
        await repo.add_favourite(app.db, uid, fid, path)
        return await msg.answer(f"✅ Destination: <b>{esc(path)}</b>")
    # a message link is a bulk-intake request (Flow 3)
    parsed = parse_message_link(link)
    if parsed:
        await intake_from_link(app, msg, data, parsed)


async def intake_from_link(app, msg: Message, data: dict, parsed: dict) -> None:
    """`t.me/c/<id>/100-150` → collect those messages into one job."""
    chat_id = parsed.get("chat_id")
    ids = parsed.get("ids") or []
    if not ids:
        return await msg.answer("❌ No message numbers in that link.")
    if len(ids) > 200:
        return await msg.answer("❌ Keep a range to 200 messages or fewer.")
    got = 0
    failed = 0
    for mid in ids:
        try:
            fwd = await app.bot.forward_message(msg.chat.id, chat_id, mid) if chat_id else None
            if fwd:
                await intake(app, data, [fwd])
                got += 1
                try:
                    await app.bot.delete_message(msg.chat.id, fwd.message_id)
                except Exception:                          # noqa: BLE001
                    pass
        except Exception:                                  # noqa: BLE001
            failed += 1
    await msg.answer(f"📥 Collected <b>{got}</b> message(s)"
                     + (f", {failed} failed (deleted or protected)" if failed else ""))


@router.callback_query(F.data.startswith("v1:naming:"))
async def cb_naming(call: CallbackQuery, **data):
    app = app_of(data)
    job = data.get("job")
    opt = naming_mod.NamingOptions.from_dict(job.get("naming") or {})
    await call.message.answer(
        f"✏️ <b>Naming</b>\n\nCurrent template:\n<code>{esc(opt.template)}</code>\n\n"
        "Variables: {original} {ext} {seq:03d} {index} {total} {date} {time} {size} {type}",
        reply_markup=naming_kb(data["lang"], job["id"] if job else ""))
    await call.answer()


TEMPLATES = ["{original}.{ext}", "{seq:03d} - {original}.{ext}",
             "Module 1 - {index:02d} {original}", "{date} {original}.{ext}"]


@router.callback_query(F.data.startswith("v1:ntpl"))
async def cb_naming_preset(call: CallbackQuery, **data):
    app = app_of(data)
    uid = data["user"]["user_id"]
    job_id = (data.get("job") or {}).get("id") or app.draft_jobs.get(uid)
    action = (call.data or "").split(":")[1] if (call.data or "").count(":") >= 1 else ""
    tail = action.replace("ntpl", "", 1)
    if tail == "custom":
        await call.message.answer(
            "✍️ Send the template as your next message, e.g.\n"
            "<code>{seq:03d} - {original}.{ext}</code>")
        app.pending_template = getattr(app, "pending_template", set())
        app.pending_template.add(uid)
        return await call.answer()
    if not job_id or not tail.isdigit():
        return await call.answer("Open a job card first", show_alert=True)
    tmpl = TEMPLATES[int(tail) % len(TEMPLATES)]
    job = await repo.get_job(app.db, job_id)
    opt = naming_mod.NamingOptions.from_dict(job.get("naming") or {})
    opt.template = tmpl
    await repo.update_job(app.db, job_id, naming=opt.to_dict())
    items = await repo.items_of(app.db, job_id)
    await recompute(app, job, items, opt)
    items = await repo.items_of(app.db, job_id)
    preview = "\n".join(f"· {esc(i['final_name'])}" for i in items[:6])
    await call.message.edit_text(f"✅ Template set:\n<code>{esc(tmpl)}</code>\n\n{preview}")
    await call.answer("saved")


@router.message(F.text.contains("{original}") | F.text.contains("{seq"))
async def on_template_text(msg: Message, **data):
    app = app_of(data)
    uid = data["user"]["user_id"]
    pending = getattr(app, "pending_template", set())
    if uid not in pending:
        return
    pending.discard(uid)
    job_id = app.draft_jobs.get(uid)
    if not job_id:
        return await msg.answer(t("no_files", data["lang"]))
    job = await repo.get_job(app.db, job_id)
    opt = naming_mod.NamingOptions.from_dict(job.get("naming") or {})
    opt.template = (msg.text or "{original}.{ext}")[:120]
    await repo.update_job(app.db, job_id, naming=opt.to_dict())
    items = await repo.items_of(app.db, job_id)
    await recompute(app, job, items, opt)
    items = await repo.items_of(app.db, job_id)
    await msg.answer("✅ Template saved:\n<code>" + esc(opt.template) + "</code>\n\n" +
                     "\n".join(f"· {esc(i['final_name'])}" for i in items[:6]))


@router.callback_query(F.data.startswith("v1:seq:"))
async def cb_sequence(call: CallbackQuery, **data):
    job = data.get("job")
    await call.message.answer(
        f"🔢 <b>Sequence</b>\n\nCurrent: <b>{esc(job.get('sequence_mode', 'telegram_order'))}</b>\n"
        "Strict mode uploads one by one in this order.",
        reply_markup=sequence_kb(job.get("sequence_mode", "telegram_order"), job["id"]))
    await call.answer()


@router.callback_query(F.data.startswith("v1:seqmode:"))
async def cb_seq_mode(call: CallbackQuery, **data):
    app = app_of(data)
    job = data.get("job")
    parts = (call.data or "").split(":")
    mode = next((p for p in parts[2:] if p in naming_mod.SEQUENCE_MODES), "")
    if mode not in naming_mod.SEQUENCE_MODES:
        return await call.answer("unknown order", show_alert=True)
    job = job or await repo.get_job(app.db, parts[-1] if len(parts) > 3 else "")
    items = await repo.items_of(app.db, job["id"])
    payload = [{"id": i["id"], "name": (i["tg"] or {}).get("name", ""),
                "size": (i["tg"] or {}).get("size", 0),
                "msg_id": (i["tg"] or {}).get("msg_id", 0)} for i in items]
    ordered = apply_mode(payload, mode)
    await repo.reorder_items(app.db, job["id"], [o["id"] for o in ordered])
    await repo.update_job(app.db, job["id"], sequence_mode=mode)
    assert (await repo.get_job(app.db, job["id"]))["sequence_mode"] == mode
    job = await repo.get_job(app.db, job["id"])
    items = await repo.items_of(app.db, job["id"])
    opt = naming_mod.NamingOptions.from_dict(job.get("naming") or {})
    await recompute(app, job, items, opt)
    items = await repo.items_of(app.db, job["id"])
    await call.message.edit_text(
        f"✅ Order: <b>{esc(mode)}</b>\n\n" +
        "\n".join(f"{n}. {esc(i['final_name'])}" for n, i in enumerate(items[:8], 1)),
        reply_markup=sequence_kb(mode, job["id"]))
    await call.answer("saved")


# ── start / pause / cancel ────────────────────────────────────────────────
@router.callback_query(F.data.startswith("v1:start:"))
async def cb_start(call: CallbackQuery, **data):
    app = app_of(data)
    job = data.get("job")
    lang = data["lang"]
    if not job:
        return await call.answer("job not found", show_alert=True)
    decision = await app.access.gate(job["user_id"], scope="upload", need_connect=True)
    if not decision:
        return await call.message.answer(decision.screen)
    items = await repo.items_of(app.db, job["id"])
    if not items:
        return await call.answer(t("no_files", lang), show_alert=True)
    problem = await destination_problem(app, job["user_id"], job)
    if problem:
        return await call.message.answer(problem)
    # A full Drive fails every file with the same 403, so check the space once
    # here instead of letting the job die one red item at a time.
    full = await storage_problem(app, job["user_id"], job)
    if full:
        return await call.message.answer(full)
    await repo.update_job(app.db, job["id"], status="queued",
                          status_message={"chat_id": call.message.chat.id,
                                          "message_id": call.message.message_id})
    app.draft_jobs.pop(job["user_id"], None)
    app.worker.spawn(job["id"])
    totals = await repo.recompute_totals(app.db, job["id"])
    state = app.worker.hub.get(job["id"])
    text = progress_card(index=1, count=len(items), name=items[0]["final_name"],
                         folder=(job.get("destination") or {}).get("path", "My Drive"),
                         tg_pct=0, drive_pct=0, done=0, total=totals["bytes"],
                         overall_pct=0, chunk_mb=app.cfg.chunk_bytes // 1024 // 1024,
                         style=app.cfg.progress_style)
    await call.message.edit_text(text, reply_markup=progress_kb(job["id"],
                                                              studio_url=app.studio_url(job["id"], job["user_id"])))
    await call.answer(t("job_ready", lang, files=len(items), size=human_bytes(totals["bytes"]),
                        dest="…").split("\n")[0])
    await app.log_event("#UploadStart", {"job": job["id"], "files": len(items)})


@router.callback_query(F.data.startswith("v1:pauseres:"))
async def cb_pause_resume(call: CallbackQuery, **data):
    app = app_of(data)
    job = data.get("job")
    if not job:
        return await call.answer("job not found", show_alert=True)
    if job["status"] == "paused":
        await app.worker.resume(job["id"])
        await call.answer(t("resumed", data["lang"]))
    else:
        await app.worker.pause(job["id"])
        await call.answer(t("paused", data["lang"]))
    st = app.worker.hub.get(job["id"])
    await call.message.edit_reply_markup(
        reply_markup=progress_kb(job["id"], paused=job["status"] != "paused",
                                 studio_url=app.studio_url(job["id"], job["user_id"])))


@router.callback_query(F.data.startswith("v1:cancel:"))
async def cb_cancel(call: CallbackQuery, **data):
    app = app_of(data)
    job = data.get("job")
    if not job:
        return await call.answer("job not found", show_alert=True)
    await app.worker.cancel(job["id"])
    await call.message.edit_text("🚫 Job cancelled.")
    await call.answer(t("cancelled_ok", data["lang"]))
    await app.log_event("#UploadCancel", {"job": job["id"]})


@router.callback_query(F.data.startswith("v1:retry:"))
async def cb_retry(call: CallbackQuery, **data):
    app = app_of(data)
    job = data.get("job")
    n = await repo.retry_failed(app.db, job["id"])
    await repo.update_job(app.db, job["id"], status="queued")
    app.worker.spawn(job["id"])
    await call.answer(f"retrying {n} file(s)")
    await call.message.edit_text(f"↻ Retrying <b>{n}</b> file(s)…")


@router.callback_query(F.data.startswith("v1:drop:"))
async def cb_drop(call: CallbackQuery, **data):
    app = app_of(data)
    job = data.get("job")
    if job:
        await repo.update_job(app.db, job["id"], status="cancelled")
        app.draft_jobs.pop(job["user_id"], None)
    await call.message.edit_text("🗑 Draft discarded.")
    await call.answer()


# ── /queue /history /mystats /plan /cancel ────────────────────────────────
@router.message(Command("queue"))
async def cmd_queue(msg: Message, **data):
    app = app_of(data)
    jobs = await repo.jobs_for_user(app.db, data["user"]["user_id"], 5)
    if not jobs:
        return await msg.answer("📭 No jobs yet. Send files to start one.")
    lines = ["<b>📦 Your jobs</b>"]
    kb_rows = []
    for j in jobs[:5]:
        t_ = j.get("totals") or {}
        lines.append(f"· <code>{j['id'][:8]}</code> <b>{j['status']}</b> · "
                     f"{t_.get('files', 0)} files · {human_bytes(t_.get('bytes', 0))}")
        kb_rows.append([B(text=f"{j['status']} · {j['id'][:8]}",
                          callback_data=cb("jobview", j["id"]))])
    await msg.answer("\n".join(lines), reply_markup=KM(inline_keyboard=kb_rows))


@router.callback_query(F.data.startswith("v1:jobview:"))
async def cb_job_view(call: CallbackQuery, **data):
    app = app_of(data)
    job = data.get("job")
    items = await repo.items_of(app.db, job["id"])
    totals = job.get("totals") or {}
    lines = [f"<b>Job {job['id'][:8]}</b> · {esc(job['status'])}",
             f"{totals.get('done', 0)} done · {totals.get('failed', 0)} failed "
             f"of {totals.get('files', 0)}",
             f"📦 {human_bytes(totals.get('bytes_done', 0))} / {human_bytes(totals.get('bytes', 0))}"]
    for it in items[:10]:
        mark = {"done": "✅", "failed": "❌", "skipped": "⚠️"}.get(it["state"], "·")
        lines.append(f"{mark} {esc(it['final_name'])}")
    await call.message.answer("\n".join(lines),
                              reply_markup=queue_kb(job["id"], job["status"]))
    await call.answer()


@router.message(Command("cancel"))
async def cmd_cancel(msg: Message, **data):
    app = app_of(data)
    uid = data["user"]["user_id"]
    job_id = app.draft_jobs.get(uid)
    job = await repo.get_job(app.db, job_id) if job_id else None
    if job and job["status"] == "draft":
        await repo.update_job(app.db, job_id, status="cancelled")
        app.draft_jobs.pop(uid, None)
        return await msg.answer("🗑 Draft discarded.")
    running = [j for j in await repo.jobs_for_user(app.db, uid, 10)
               if j["status"] in ("running", "queued", "paused")]
    if not running:
        return await msg.answer(t("nothing_running", data["lang"]))
    for j in running:
        await app.worker.cancel(j["id"])
    await msg.answer(f"🚫 Cancelled {len(running)} job(s).")


@router.message(Command("history"))
async def cmd_history(msg: Message, **data):
    app = app_of(data)
    jobs = await repo.jobs_for_user(app.db, data["user"]["user_id"], 10)
    if not jobs:
        return await msg.answer("No history yet.")
    lines = ["<b>🕘 History</b>"]
    for j in jobs:
        t_ = j.get("totals") or {}
        when = time.strftime("%d %b %H:%M", time.localtime(j["created_at"]))
        lines.append(f"· {when} · <b>{esc(j['status'])}</b> · {t_.get('files', 0)} files · "
                     f"{human_bytes(t_.get('bytes_done', 0))}")
    await msg.answer("\n".join(lines))


@router.message(Command("mystats"))
async def cmd_mystats(msg: Message, **data):
    app = app_of(data)
    uid = data["user"]["user_id"]
    s = await repo.user_stats(app.db, uid)
    usage = await repo.quota_used(app.db, uid)
    plan = await repo.effective_plan(app.db, uid)
    await msg.answer(
        f"<b>📊 Your stats</b>\n\n"
        f"📦 files uploaded: <b>{s['files']}</b>\n"
        f"💾 total: <b>{human_bytes(s['bytes'])}</b>\n"
        f"🎬 videos: {s['videos']} · 📄 docs: {s['docs']}\n"
        f"📅 today: {human_bytes(usage['bytes'])} / {plan.get('daily_gb')} GB\n"
        f"💎 plan: <b>{esc(plan.get('name') or plan.get('id'))}</b>")


@router.message(Command("plan"))
async def cmd_plan(msg: Message, **data):
    app = app_of(data)
    plan = await repo.effective_plan(app.db, data["user"]["user_id"])
    usage = await repo.quota_used(app.db, data["user"]["user_id"])
    g = await repo.get_global(app.db)
    text = (f"<b>💎 {esc(plan.get('name') or plan.get('id'))}</b>\n\n"
            f"· max file: <b>{plan.get('max_file_mb')} MB</b>\n"
            f"· daily: <b>{plan.get('daily_gb')} GB</b> (used {human_bytes(usage['bytes'])})\n"
            f"· files per job: {plan.get('max_files_job')}\n"
            f"· concurrent: {plan.get('concurrent')}\n")
    if plan.get("expires_at"):
        text += f"\n⏳ valid till {unlock_at_text(plan['expires_at'], app.cfg.timezone)}"
    else:
        text += ("\n\n⭐ <b>MAX · ₹499 / month</b>"
                 "\nUp to 750 GB daily plan allowance · up to 4 concurrent jobs"
                 "\nUp to 5,000 files per job · deployment-configured file ceiling"
                 "\nStorage remains subject to your Google account quota."
                 "\nContact: @ZCQXx")
    await msg.answer(text)
