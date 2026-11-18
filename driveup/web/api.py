"""Web API — every route from B11, all authenticated, all ownership-checked.

Route map
---------
auth     POST /auth/telegram   GET /auth/google/start   GET /auth/google/callback
me       GET  /api/me
jobs     GET/PATCH /api/jobs/{id}     POST /api/jobs/{id}/start|pause|resume|cancel|retry-failed
         POST /api/jobs/{id}/preview-names   POST /api/jobs/{id}/items/reorder
         DELETE /api/jobs/{id}/items/{item}  GET /api/jobs/{id}/events  (SSE)
         GET  /api/jobs/{id}/export.csv
drive    GET  /api/drive/children  /api/drive/search  /api/drive/about  /api/drive/parent
         POST /api/drive/folders
misc     GET|POST|DELETE /api/presets  /api/favourites
admin    /admin/api/*  (role-checked, CSRF on writes)
health   GET  /health
"""
from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import secrets
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, Query, Request, Response
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse,
                               RedirectResponse, StreamingResponse)

from ..config import BRAND_NAME, settings as default_settings
from ..core import naming as naming_mod
from ..core.access import human_bytes, unlock_at_text
from ..db import repo
from ..drive import auth as oauth
from ..drive.client import FOLDER_MIME, folder_id_from_url
from ..drive.errors import DriveError
from ..strings import t
from .auth import (AuthError, SESSION_COOKIE, admin_user, clear_session_cookie,
                   mint_session, session_user, set_session_cookie, sign_payload,
                   verify_initdata, verify_widget)

log = logging.getLogger("driveup.web.api")
router = APIRouter()


# ── helpers ─────────────────────────────────────────────────────────────────
def ok(**kw) -> JSONResponse:
    return JSONResponse({"ok": True, **kw})


def fail(code: str, message: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"ok": False, "code": code, "message": message}, status_code=status)


def state_of(request: Request):
    return request.app.state


async def initdata_user(request: Request) -> dict:
    """POST /auth/telegram — the only way a Mini-App gets a session."""
    st = state_of(request)
    body = await request.json()
    init_data = body.get("initData") or request.headers.get("X-Telegram-Init-Data", "")
    try:
        verified = verify_initdata(init_data, st.cfg.bot_token)
    except AuthError as e:
        return fail("bad_initdata", e.detail, 401)
    u = verified["user"]
    user = await repo.upsert_user(st.db, int(u["id"]),
                                  name=f"{u.get('first_name','')} {u.get('last_name','')}".strip(),
                                  username=u.get("username", ""))
    sid = await mint_session(st.db, user["user_id"], request.client.host if request.client else "",
                             request.headers.get("user-agent", ""))
    resp = ok(user={"id": user["user_id"], "name": user["name"], "username": user["username"]})
    # Behind Render/Railway/Caddy the browser sees https but FastAPI sees http,
    # so trust the proxy's X-Forwarded-Proto — otherwise the Secure flag is lost.
    proto = request.headers.get("x-forwarded-proto", request.url.scheme).split(",")[0].strip()
    set_session_cookie(resp, sid, secure=proto == "https")
    return resp


# ── /api/me ─────────────────────────────────────────────────────────────────
@router.get("/api/me")
async def api_me(request: Request, user=Depends(session_user)):
    st = state_of(request)
    role = user["role"]
    lock = await repo.get_lock(st.db, user["user_id"])
    gate = await st.access.gate(user["user_id"], count_request=False)
    blocked = None
    if lock and role not in ("owner", "admin", "support"):
        blocked = {"kind": "locked", "title": "Account locked",
                   "message": lock["reason"] or "Contact support",
                   "until": lock["expires_at"],
                   "support": st.cfg.support_contact}
    elif gate.reason == "maintenance":
        g = await repo.get_global(st.db)
        blocked = {"kind": "maintenance", "title": "Maintenance",
                   "message": g.get("maintenance_msg", "")}
    job = None
    job_id = request.query_params.get("job", "")
    if job_id:
        row = await repo.get_job(st.db, job_id)
        if row and row["user_id"] == user["user_id"]:
            job = await job_payload(st, row)
    acc = await repo.get_drive_account(st.db, user["user_id"])
    plan = await repo.effective_plan(st.db, user["user_id"])
    glob = await repo.get_global(st.db)
    return ok(userId=user["user_id"],
              user={"id": user["user_id"], "name": user["name"], "username": user["username"]},
              role=role, brand=glob.get("brand_name", BRAND_NAME),
              progress_style=glob.get("progress_style", "glow"),
              drive_connected=bool(acc) and not acc.get("needs_reconnect"),
              drive_email=(acc or {}).get("email", ""),
              plan={"id": plan.get("id"), "name": plan.get("name")},
              job=job, blocked=blocked)


async def job_payload(st, job: dict) -> dict:
    items = await repo.items_of(st.db, job["id"])
    return {
        "id": job["id"], "status": job["status"],
        "destination": job.get("destination") or {},
        "naming": job.get("naming") or {},
        "sequence_mode": job.get("sequence_mode"),
        "strict_order": bool(job.get("strict_order")),
        "conflict_policy": job.get("conflict_policy"),
        "share_mode": job.get("share_mode"),
        "totals": job.get("totals") or {},
        "items": [{"id": it["id"], "name": (it["tg"] or {}).get("name", ""),
                   "final_name": it["final_name"], "size": (it["tg"] or {}).get("size", 0),
                   "mime": (it["tg"] or {}).get("mime", ""),
                   "msg_id": (it["tg"] or {}).get("msg_id", 0),
                   "caption": (it["tg"] or {}).get("caption", ""),
                   "state": it["state"], "bytes_done": it["bytes_done"],
                   "error": it["error"], "link": (it["drive"] or {}).get("link", ""),
                   "drive": it["drive"]} for it in items],
    }


async def owned_job(request: Request, job_id: str, user: dict) -> Optional[dict]:
    """Every job call goes through here — ownership is not optional (B12)."""
    st = state_of(request)
    job = await repo.get_job(st.db, job_id)
    if not job:
        return None
    if job["user_id"] != user["user_id"] and user["role"] not in ("owner", "admin", "support"):
        return None
    return job


# ── jobs ────────────────────────────────────────────────────────────────────
@router.get("/api/jobs/{job_id}")
async def api_job(request: Request, job_id: str, user=Depends(session_user)):
    st = state_of(request)
    job = await owned_job(request, job_id, user)
    if not job:
        return fail("not_found", "job not found", 404)
    return ok(**await job_payload(st, job))


@router.patch("/api/jobs/{job_id}")
async def api_patch_job(request: Request, job_id: str, body: dict = Body(...),
                        user=Depends(session_user)):
    st = state_of(request)
    job = await owned_job(request, job_id, user)
    if not job:
        return fail("not_found", "job not found", 404)
    if job["status"] in ("running", "queued"):
        return fail("locked", "the job is already running", 409)
    patch: Dict[str, Any] = {}
    if "destination" in body:
        dest = body["destination"] or {}
        new_dest = {"folder_id": (dest.get("id") or dest.get("folder_id") or "root"),
                    "path": dest.get("path") or "My Drive"}
        split = dest.get("split")
        if split:
            mode = str(split.get("mode") or "none").lower()
            if mode not in ("none", "type", "season", "date", "custom"):
                return fail("bad_split", "unknown split mode", 422)
            new_dest["split"] = {
                "mode": mode,
                "rules": [{k: str(v) for k, v in (r or {}).items()}
                          for r in (split.get("rules") or [])][:20],
                "else": str(split.get("else") or "Other")[:120],
                # a precomputed plan lets the worker skip re-deriving it
                "plan": {k: v for k, v in (split.get("plan") or {}).items()} if split.get("plan") else {},
            }
        patch["destination"] = new_dest
    if "naming" in body:
        opts = naming_mod.NamingOptions.from_dict(body["naming"])
        opts.max_bytes = min(255, max(32, int(opts.max_bytes or 255)))
        patch["naming"] = opts.to_dict()
    for key in ("sequence_mode", "conflict_policy", "share_mode"):
        if key in body:
            patch[key] = str(body[key])[:32]
    if "strict_order" in body:
        patch["strict_order"] = bool(body["strict_order"])
    if patch:
        await repo.update_job(st.db, job_id, **patch)
    if body.get("order"):
        await repo.reorder_items(st.db, job_id, [str(x) for x in body["order"]])
    # recompute the final names so what the user saw is what gets uploaded
    items = await repo.items_of(st.db, job_id)
    fresh = await repo.get_job(st.db, job_id)
    await recompute_names(st, fresh, items)
    return ok(**await job_payload(st, await repo.get_job(st.db, job_id)))


async def recompute_names(st, job: dict, items: list) -> None:
    opt = naming_mod.NamingOptions.from_dict(job.get("naming") or {})
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
        new = resolved[i] if i in resolved else names[i]
        if new != it["final_name"]:
            await repo.set_item(st.db, it["id"], final_name=new)


@router.post("/api/jobs/{job_id}/preview-names")
async def api_preview_names(request: Request, job_id: str, body: dict = Body(default={}),
                            user=Depends(session_user)):
    st = state_of(request)
    job = await owned_job(request, job_id, user)
    if not job:
        return fail("not_found", "job not found", 404)
    opt = naming_mod.NamingOptions.from_dict(body or job.get("naming") or {})
    items = await repo.items_of(st.db, job_id)
    payload = [{"name": (i["tg"] or {}).get("name", ""), "size": (i["tg"] or {}).get("size", 0),
                "mime": (i["tg"] or {}).get("mime", ""),
                "caption": (i["tg"] or {}).get("caption", "")} for i in items]
    rows = naming_mod.preview_rows(payload, opt, start=opt.start)
    return ok(rows=rows)


@router.delete("/api/jobs/{job_id}/items/{item_id}")
async def api_delete_item(request: Request, job_id: str, item_id: str,
                          user=Depends(session_user)):
    st = state_of(request)
    job = await owned_job(request, job_id, user)
    if not job:
        return fail("not_found", "job not found", 404)
    items = await repo.items_of(st.db, job_id)
    if not any(i["id"] == item_id for i in items):
        return fail("not_found", "item not found", 404)
    await repo.delete_item(st.db, item_id)
    rest = await repo.items_of(st.db, job_id)
    await repo.reorder_items(st.db, job_id, [i["id"] for i in rest])
    await recompute_names(st, job, rest)
    return ok(count=len(rest))


@router.post("/api/jobs/{job_id}/items/reorder")
async def api_reorder(request: Request, job_id: str, body: dict = Body(...),
                      user=Depends(session_user)):
    st = state_of(request)
    job = await owned_job(request, job_id, user)
    if not job:
        return fail("not_found", "job not found", 404)
    order = [str(x) for x in (body.get("order") or [])]
    items = await repo.items_of(st.db, job_id)
    ids = [i["id"] for i in items]
    if sorted(order) != sorted(ids):
        return fail("bad_order", "order must list every item exactly once", 422)
    await repo.reorder_items(st.db, job_id, order)
    rest = await repo.items_of(st.db, job_id)
    await recompute_names(st, job, rest)
    return ok(order=order)


@router.post("/api/jobs/{job_id}/start")
async def api_start(request: Request, job_id: str, body: dict = Body(default={}),
                    user=Depends(session_user)):
    st = state_of(request)
    job = await owned_job(request, job_id, user)
    if not job:
        return fail("not_found", "job not found", 404)
    decision = await st.access.gate(user["user_id"], scope="upload", need_connect=True,
                                    feature="web_studio")
    if not decision:
        return fail(decision.reason, decision.screen, 403)
    plan = decision.extra.get("plan") or {}
    items = await repo.items_of(st.db, job_id)
    if not items:
        return fail("empty", "this job has no files", 422)
    if plan.get("max_files_job") and len(items) > int(plan["max_files_job"]):
        return fail("plan", f"your plan allows {plan['max_files_job']} files per job", 403)
    over = [i for i in items if (i["tg"] or {}).get("size", 0) > int(plan.get("max_file_mb", 2000)) * 1024 ** 2]
    if over:
        return fail("plan", f"{len(over)} file(s) exceed your {plan.get('max_file_mb')} MB limit", 403)
    if body.get("naming"):
        opts = naming_mod.NamingOptions.from_dict(body["naming"])
        await repo.update_job(st.db, job_id, naming=opts.to_dict())
    job = await repo.get_job(st.db, job_id)
    await recompute_names(st, job, await repo.items_of(st.db, job_id))
    await repo.update_job(st.db, job_id, status="queued")
    st.worker.spawn(job_id)
    preset_saved = False
    if body.get("preset_name"):
        await repo.save_preset(st.db, user["user_id"], str(body["preset_name"])[:60],
                               {"destination": job.get("destination"),
                                "naming": job.get("naming"),
                                "sequence_mode": job.get("sequence_mode")})
        preset_saved = True
    await repo.audit(st.db, user["user_id"], "job.start", job_id,
                     {"files": len(items)})
    return ok(status="queued", preset_saved=preset_saved)


async def _job_control(request: Request, job_id: str, action: str) -> JSONResponse:
    st = state_of(request)
    job = await repo.get_job(st.db, job_id)
    if not job:
        return fail("not_found", "job not found", 404)
    if action == "pause":
        await st.worker.pause(job_id)
    elif action == "resume":
        await st.worker.resume(job_id)
    elif action == "cancel":
        await st.worker.cancel(job_id)
    elif action == "retry-failed":
        n = await repo.retry_failed(st.db, job_id)
        await repo.update_job(st.db, job_id, status="queued")
        st.worker.spawn(job_id)
        return ok(retried=n)
    await repo.audit(st.db, job["user_id"], f"job.{action}", job_id)
    return ok(status=action)


@router.post("/api/jobs/{job_id}/{action}")
async def api_job_action(request: Request, job_id: str, action: str,
                         user=Depends(session_user)):
    if action not in ("start", "pause", "resume", "cancel", "retry-failed"):
        return fail("bad_action", "unknown action", 404)
    if not await owned_job(request, job_id, user):
        return fail("not_found", "job not found", 404)
    if action == "start":
        return await api_start(request, job_id, {}, user)     # type: ignore[arg-type]
    return await _job_control(request, job_id, action)


# ── SSE ─────────────────────────────────────────────────────────────────────
@router.get("/api/jobs/{job_id}/events")
async def api_events(request: Request, job_id: str, user=Depends(session_user)):
    st = state_of(request)
    job = await owned_job(request, job_id, user)
    if not job:
        return fail("not_found", "job not found", 404)
    queue = st.worker.hub.subscribe(job_id)
    st_initial = st.worker.hub.get(job_id)

    async def gen():
        try:
            if st_initial:
                yield f"data: {json.dumps(st_initial.snapshot())}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
                    continue
                yield f"data: {json.dumps(payload)}\n\n"
                if payload.get("stage") == "job-finished":
                    break
        finally:
            st.worker.hub.unsubscribe(job_id, queue)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no",
                                      "Connection": "keep-alive"})


@router.get("/api/jobs/{job_id}/export.csv")
async def api_csv(request: Request, job_id: str, user=Depends(session_user)):
    st = state_of(request)
    job = await owned_job(request, job_id, user)
    if not job:
        return fail("not_found", "job not found", 404)
    items = await repo.items_of(st.db, job_id)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["order", "name", "size", "state", "drive_id", "link"])
    for n, it in enumerate(items, 1):
        w.writerow([n, it["final_name"], (it["tg"] or {}).get("size", 0), it["state"],
                    (it["drive"] or {}).get("file_id", ""), (it["drive"] or {}).get("link", "")])
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="driveup-{job_id}.csv"'})


# ── drive ───────────────────────────────────────────────────────────────────
async def _client(request: Request, user: dict):
    st = state_of(request)
    try:
        return await st.accounts.client(user["user_id"])
    except Exception as e:                                  # noqa: BLE001
        log.info("drive client unavailable for %s: %s", user["user_id"], e)
        return None


@router.get("/api/drive/children")
async def api_children(request: Request, parent: str = "root", page: str = "",
                       user=Depends(session_user)):
    client = await _client(request, user)
    if client is None:
        return fail("not_connected", "Google Drive is not connected — send /connect to the bot", 409)
    try:
        data = await client.list_folders(parent, page_token=page)
        about = {}
        try:
            about = await client.about()
        except DriveError:
            pass
        return ok(parent=parent, next=data["next"], folders=data["files"], about=about)
    except DriveError as e:
        return fail(e.code, str(e), 502)


@router.get("/api/drive/search")
async def api_search(request: Request, q: str = Query("", max_length=120),
                     user=Depends(session_user)):
    client = await _client(request, user)
    if client is None:
        return fail("not_connected", "Google Drive is not connected", 409)
    try:
        files = await client.search(q, folders_only=True)
        return ok(folders=[f for f in files if f.get("mimeType") == FOLDER_MIME], files=files)
    except DriveError as e:
        return fail(e.code, str(e), 502)


@router.get("/api/drive/about")
async def api_about(request: Request, user=Depends(session_user)):
    client = await _client(request, user)
    if client is None:
        return fail("not_connected", "Google Drive is not connected", 409)
    try:
        return ok(**await client.about())
    except DriveError as e:
        return fail(e.code, str(e), 502)


@router.get("/api/drive/parent")
async def api_parent(request: Request, child: str = "root", user=Depends(session_user)):
    client = await _client(request, user)
    if client is None:
        return fail("not_connected", "Google Drive is not connected", 409)
    if child in ("", "root"):
        return ok(id="root", name="My Drive")
    try:
        meta = await client.get(child)
        parents = meta.get("parents") or ["root"]
        pid = parents[0]
        if pid == "root":
            return ok(id="root", name="My Drive")
        parent = await client.get(pid)
        return ok(id=pid, name=parent.get("name", "folder"))
    except DriveError as e:
        return fail(e.code, str(e), 502)


@router.post("/api/drive/folders")
async def api_new_folder(request: Request, body: dict = Body(...),
                         user=Depends(session_user)):
    name = naming_mod.sanitize(str(body.get("name", "")).strip())[:200]
    if not name:
        return fail("bad_name", "folder name is empty", 422)
    parent = folder_id_from_url(str(body.get("parent", "root"))) or "root"
    client = await _client(request, user)
    if client is None:
        return fail("not_connected", "Google Drive is not connected", 409)
    try:
        res = await client.create_folder(name, parent)
        return ok(id=res.get("id", ""), name=res.get("name", name))
    except DriveError as e:
        return fail(e.code, str(e), 502)


# ── presets / favourites ────────────────────────────────────────────────────
@router.get("/api/presets")
async def api_presets(request: Request, user=Depends(session_user)):
    st = state_of(request)
    return ok(presets=await repo.list_presets(st.db, user["user_id"]))


@router.post("/api/presets")
async def api_save_preset(request: Request, body: dict = Body(...), user=Depends(session_user)):
    st = state_of(request)
    name = naming_mod.sanitize(str(body.get("name", "preset")).strip())[:60] or "preset"
    pid = await repo.save_preset(st.db, user["user_id"], name, body.get("config") or {})
    return ok(id=pid)


@router.delete("/api/presets/{preset_id}")
async def api_del_preset(request: Request, preset_id: str, user=Depends(session_user)):
    st = state_of(request)
    return ok(deleted=await repo.delete_preset(st.db, user["user_id"], preset_id))


@router.get("/api/favourites")
async def api_favourites(request: Request, user=Depends(session_user)):
    st = state_of(request)
    return ok(favourites=await repo.list_favourites(st.db, user["user_id"]))


@router.post("/api/favourites")
async def api_add_favourite(request: Request, body: dict = Body(...),
                            user=Depends(session_user)):
    st = state_of(request)
    fid = folder_id_from_url(str(body.get("folder_id", "")))
    if not fid:
        return fail("bad_folder", "invalid folder id", 422)
    await repo.add_favourite(st.db, user["user_id"], fid, str(body.get("label", ""))[:120])
    return ok()


@router.delete("/api/favourites/{folder_id}")
async def api_del_favourite(request: Request, folder_id: str, user=Depends(session_user)):
    st = state_of(request)
    return ok(deleted=await repo.delete_favourite(st.db, user["user_id"], folder_id))


# ── Google OAuth ────────────────────────────────────────────────────────────
@router.get("/auth/google/start")
async def google_start(request: Request, user=Depends(session_user)):
    st = state_of(request)
    state = oauth.new_state()
    verifier = oauth.new_verifier()
    await repo.create_state(st.db, state, user["user_id"], verifier)
    return RedirectResponse(oauth.build_authorize_url(st.cfg, state, verifier))


@router.get("/auth/google/callback")
async def google_callback(request: Request, code: str = "", state: str = "",
                          error: str = ""):
    st = state_of(request)
    if error:
        return HTMLResponse(f"<h2>Google said no</h2><p>{error}</p>", status_code=400)
    row = await repo.take_state(st.db, state)
    if not row:
        return HTMLResponse("<h2>This link expired</h2><p>Open /connect in the bot again.</p>",
                            status_code=400)
    try:
        info = await st.accounts.connect_from_code(row["user_id"], code, row["code_verifier"])
    except Exception as e:                                  # noqa: BLE001
        log.warning("oauth callback failed: %s", e)
        return HTMLResponse(f"<h2>Connecting failed</h2><p>{str(e)[:300]}</p>",
                            status_code=400)
    free = human_bytes(info.get("free")) if info.get("free", -1) >= 0 else "unlimited"
    total = human_bytes(info.get("limit")) if info.get("limit") else "—"
    html = f"""<!doctype html><meta charset=utf-8>
    <meta name=viewport content="width=device-width,initial-scale=1">
    <style>body{{background:#081014;color:#e8f6fa;font-family:-apple-system,sans-serif;
      display:grid;place-items:center;min-height:100vh;margin:0;text-align:center}}
      .c{{max-width:380px;padding:26px}}h2{{margin:12px 0 6px}}
      p{{color:#7fa3ad;font-size:.9rem}}a{{color:#22d3ee}}</style>
    <div class=c><div style="font-size:52px">✅</div>
    <h2>Google Drive connected</h2>
    <p><b>{info.get('email') or 'your account'}</b><br>{free} free of {total}</p>
    <p>You can close this tab and go back to Telegram.</p></div>"""
    try:
        await st.bot.send_message(row["user_id"],
                                  f"✅ Connected as <b>{info.get('email')}</b> · {free} free of {total}",
                                  parse_mode="HTML")
    except Exception:                                       # noqa: BLE001
        pass
    return HTMLResponse(html)


@router.post("/auth/telegram")
async def auth_telegram(request: Request):
    return await initdata_user(request)


# ── health ──────────────────────────────────────────────────────────────────
@router.get("/health")
async def health(request: Request):
    st = state_of(request)
    t0 = time.time()
    try:
        await st.db.scalar("SELECT 1", (), 1)
        db_ok, db_ms = True, round((time.time() - t0) * 1000, 1)
    except Exception:
        db_ok, db_ms = False, -1
    stats = await repo.global_stats(st.db)
    return ok(status="ok" if db_ok else "degraded", uptime=round(time.time() - st.started_at, 1),
              db_ms=db_ms, db=db_ok,
              queue=stats["jobs_running"] + stats["jobs_queued"],
              workers=st.cfg.max_global_uploads, version=st.version)
