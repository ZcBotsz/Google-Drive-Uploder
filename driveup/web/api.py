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
import os
import secrets
import uuid
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, File, Form, Query, Request, Response, UploadFile
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse,
                               RedirectResponse, StreamingResponse)
from pathlib import Path

from ..config import BRAND_NAME, settings as default_settings
from ..core import naming as naming_mod
from ..core.access import human_bytes, unlock_at_text
from ..core.groups import safe_folder_name
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
              plan={"id": plan.get("id"), "name": plan.get("name"),
                    "max_files_job": plan.get("max_files_job"),
                    "max_file_mb": plan.get("max_file_mb"),
                    "daily_gb": plan.get("daily_gb")},
              usage_today=await repo.quota_used(st.db, user["user_id"]),
              upload_stats=await repo.user_stats(st.db, user["user_id"]),
              job=job, blocked=blocked)


async def job_payload(st, job: dict) -> dict:
    items = await repo.items_of(st.db, job["id"])
    return {
        "id": job["id"], "status": job["status"],
        "source": job.get("source") or {},
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
@router.get("/api/jobs")
async def api_jobs(request: Request, limit: int = Query(25, ge=1, le=100),
                   user=Depends(session_user)):
    """Private upload history. The user ID is always taken from the verified session."""
    st = state_of(request)
    rows = await repo.jobs_for_user(st.db, user["user_id"], limit=limit)
    return ok(jobs=[{
        "id": row["id"], "status": row["status"], "created_at": row.get("created_at"),
        "destination": row.get("destination") or {}, "totals": row.get("totals") or {},
    } for row in rows])


@router.post("/api/uploads")
async def api_browser_upload(request: Request, files: List[UploadFile] = File(...),
                             job_id: str = Form(""), user=Depends(session_user)):
    """Stage browser-selected files for the existing Drive worker pipeline.

    Job ownership, account connection, plan/file limits, and daily byte ceilings
    are checked server-side. Browser-supplied user IDs are never accepted.
    """
    st = state_of(request)
    if not files or len(files) > 100:
        return fail("file_count", "Send 1–100 files per batch. The Studio combines batches into one job.", 422)
    target_job = None
    if job_id:
        target_job = await owned_job(request, job_id, user)
        if not target_job:
            return fail("not_found", "job not found", 404)
        if await st.db.one("SELECT job_id FROM channel_batches WHERE job_id=? AND state='open'", (job_id,)):
            return fail("collection_open", "Finish the channel batch before editing it.", 409)
        if target_job["status"] != "draft":
            return fail("locked", "Files can only be added to a draft upload.", 409)
    decision = await st.access.gate(user["user_id"], scope="upload", need_connect=True,
                                    feature="web_studio")
    if not decision:
        return fail(decision.reason, decision.screen, 403)
    plan = decision.extra.get("plan") or await repo.effective_plan(st.db, user["user_id"])
    existing = await repo.items_of(st.db, job_id) if target_job else []
    max_files = int(plan.get("max_files_job") or 5000)
    if len(existing) + len(files) > max_files:
        return fail("plan", f"Your plan allows {max_files} files per job.", 403)
    max_bytes = int(plan.get("max_file_mb") or getattr(st.cfg, "default_max_file_mb", 2000)) * 1024 ** 2
    daily_cap = int(float(plan.get("daily_gb") or 0) * 1024 ** 3)
    used = await repo.quota_used(st.db, user["user_id"])
    batch_cap = max(0, daily_cap - int(used.get("bytes", 0))) if daily_cap else 0
    root = Path(st.cfg.download_dir) / "studio"
    root.mkdir(parents=True, exist_ok=True)
    staged: list[dict] = []
    temporary: list[Path] = []
    total_bytes = 0
    try:
        for upload in files:
            original = os.path.basename((upload.filename or "file").replace("\\", "/")).strip() or "file"
            original = original[:240]
            token = uuid.uuid4().hex
            final_path = root / f"{token}.upload"
            temp_path = root / f"{token}.part"
            temporary.append(temp_path)
            size = 0
            with temp_path.open("wb") as out:
                while True:
                    chunk = await upload.read(1024 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    total_bytes += len(chunk)
                    if size > max_bytes:
                        raise ValueError(f"{original} exceeds the {max_bytes // (1024 ** 2)} MB per-file limit.")
                    if daily_cap and total_bytes > batch_cap:
                        raise ValueError("This batch exceeds the remaining daily upload allowance.")
                    out.write(chunk)
            if size == 0:
                temp_path.unlink(missing_ok=True)
                continue
            os.replace(temp_path, final_path)
            staged.append({"path": final_path, "name": original, "size": size,
                           "mime": upload.content_type or "application/octet-stream", "token": token})
        if not staged:
            raise ValueError("The selected files are empty.")
        if target_job:
            jid = job_id
        else:
            jid = await repo.create_job(
                st.db, user["user_id"], destination={"folder_id": "root", "path": "My Drive"},
                naming={"template": "{original}.{ext}"}, sequence_mode="manual",
                source={"kind": "web_studio"})
        for entry in staged:
            item_id = await repo.add_item(
                st.db, jid, file_id=f"web:{entry['token']}",
                file_unique_id=f"web:{entry['token']}", msg_id=0,
                size=entry["size"], mime=entry["mime"], name=entry["name"],
                chat_id=user["user_id"])
            item = await repo.get_item(st.db, item_id)
            tg = item.get("tg") or {}
            tg["source_path"] = str(entry["path"])
            await repo.set_item(st.db, item_id, tg=tg)
        await repo.recompute_totals(st.db, jid)
        fresh = await repo.get_job(st.db, jid)
        await repo.audit(st.db, user["user_id"], "studio.browser_upload", jid,
                         {"files": len(staged), "bytes": total_bytes})
        return ok(job=await job_payload(st, fresh))
    except ValueError as e:
        for entry in staged:
            entry["path"].unlink(missing_ok=True)
        for path in temporary:
            path.unlink(missing_ok=True)
        return fail("upload_limit", str(e), 413)
    except Exception:
        for entry in staged:
            entry["path"].unlink(missing_ok=True)
        for path in temporary:
            path.unlink(missing_ok=True)
        raise
    finally:
        for upload in files:
            await upload.close()


@router.get("/api/jobs/{job_id}")
async def api_job(request: Request, job_id: str, user=Depends(session_user)):
    st = state_of(request)
    job = await owned_job(request, job_id, user)
    if not job:
        return fail("not_found", "job not found", 404)
    if job["status"] in ("queued", "running", "paused"):
        job["totals"] = await repo.recompute_totals(st.db, job_id)
        job = await repo.get_job(st.db, job_id)
    return ok(**await job_payload(st, job))


@router.patch("/api/jobs/{job_id}")
async def api_patch_job(request: Request, job_id: str, body: dict = Body(...),
                        user=Depends(session_user)):
    st = state_of(request)
    job = await owned_job(request, job_id, user)
    if not job:
        return fail("not_found", "job not found", 404)
    if await st.db.one("SELECT job_id FROM channel_batches WHERE job_id=? AND state='open'", (job_id,)):
        return fail("collection_open", "Finish the channel batch before editing it.", 409)
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
            if mode not in ("none", "type", "season", "date", "custom", "batches", "selected", "tree"):
                return fail("bad_split", "unknown split mode", 422)
            raw_plan = split.get("plan") or {}
            if not isinstance(raw_plan, dict):
                return fail("bad_split", "Invalid folder assignment plan.", 422)
            route_plan = dict(raw_plan)
            groups = []
            if mode == "batches":
                items = await repo.items_of(st.db, job_id)
                allowed = {item["id"] for item in items}
                raw_groups = split.get("groups") or []
                seen_names = set()
                for group in raw_groups[:20]:
                    raw_name = str((group or {}).get("name") or "").strip()
                    if not raw_name:
                        return fail("bad_split", "Name every destination folder.", 422)
                    name = safe_folder_name(raw_name, limit=80)
                    if name.casefold() in seen_names:
                        return fail("bad_split", "Each destination folder needs a unique name.", 422)
                    seen_names.add(name.casefold())
                    count = int((group or {}).get("count") or 0)
                    if count < 1:
                        return fail("bad_split", "Each folder group needs a positive file count.", 422)
                    groups.append({"name": name, "count": count})
                if not groups or sum(g["count"] for g in groups) != len(items):
                    return fail("bad_split", "Folder counts must add up to every file in the job.", 422)
                if set(route_plan) != allowed:
                    return fail("bad_split", "Every job file needs exactly one folder assignment.", 422)
                clean_plan = {}
                for item_id, chain in route_plan.items():
                    if not isinstance(chain, list) or len(chain) != 1:
                        return fail("bad_split", "Each batch item must map to one child folder.", 422)
                    clean_plan[item_id] = [safe_folder_name(str(chain[0]), limit=80)]
                route_plan = clean_plan
            destinations = {}
            if mode == "selected":
                items = await repo.items_of(st.db, job_id)
                allowed = {item["id"] for item in items}
                if not isinstance(route_plan, dict) or set(route_plan) != allowed or not allowed:
                    return fail("bad_split", "Assign every job file to a folder.", 422)
                if any(not isinstance(fid, str) or not fid or len(fid) > 256 for fid in route_plan.values()):
                    return fail("bad_split", "Invalid folder assignment.", 422)
                folder_ids = set(route_plan.values())
                if len(folder_ids) > 100:
                    return fail("bad_split", "Use at most 100 destination folders per job.", 422)
                client = await _client(request, user)
                if client is None:
                    return fail("not_connected", "Connect Google Drive first.", 409)
                for fid in folder_ids:
                    if fid == "root":
                        destinations[fid] = "My Drive"
                        continue
                    try:
                        folder = await client.get(fid)
                    except DriveError:
                        return fail("bad_split", "A destination is not accessible in your connected Drive.", 422)
                    if (folder.get("mimeType") != "application/vnd.google-apps.folder"
                            or folder.get("trashed") or folder.get("capabilities", {}).get("canAddChildren") is False):
                        return fail("bad_split", "A destination is not a writable Drive folder.", 422)
                    destinations[fid] = str(folder.get("name") or "Folder")[:200]
            tree_folders = []
            if mode == "tree":
                items = await repo.items_of(st.db, job_id)
                if not items or set(route_plan) != {item["id"] for item in items}:
                    return fail("bad_split", "Every job file needs a tree destination.", 422)
                raw_folders = split.get("folders")
                if not isinstance(raw_folders, list) or len(raw_folders) > 200:
                    return fail("bad_split", "Use at most 200 folders in the tree.", 422)
                def valid_chain(chain):
                    return (isinstance(chain, list) and len(chain) <= 12 and
                            all(isinstance(n, str) and 0 < len(n) <= 80 and
                                safe_folder_name(n, limit=80) == n for n in chain))
                if any(not valid_chain(c) or not c for c in raw_folders):
                    return fail("bad_split", "Invalid folder name or tree depth (maximum 12 levels).", 422)
                keys = [tuple(c) for c in raw_folders]
                if len(keys) != len(set(keys)):
                    return fail("bad_split", "Duplicate tree folder.", 422)
                folder_set = set(keys)
                if any(len(c) > 1 and c[:-1] not in folder_set for c in keys):
                    return fail("bad_split", "A tree folder is missing its parent.", 422)
                if any(not valid_chain(chain) or (chain and tuple(chain) not in folder_set)
                       for chain in route_plan.values()):
                    return fail("bad_split", "A file points to a missing or invalid folder.", 422)
                root_id = new_dest["folder_id"]
                if not isinstance(root_id, str) or not root_id or len(root_id) > 256:
                    return fail("bad_split", "Invalid parent destination.", 422)
                client = await _client(request, user)
                if client is None:
                    return fail("not_connected", "Connect Google Drive first.", 409)
                if root_id != "root":
                    try:
                        folder = await client.get(root_id)
                    except DriveError:
                        return fail("bad_split", "The parent folder is not accessible in your connected Drive.", 422)
                    if (folder.get("mimeType") != "application/vnd.google-apps.folder"
                            or folder.get("trashed") or folder.get("capabilities", {}).get("canAddChildren") is False):
                        return fail("bad_split", "The parent destination is not a writable folder.", 422)
                tree_folders = sorted(raw_folders, key=len)
            new_dest["split"] = {
                "mode": mode,
                "rules": [{k: str(v) for k, v in (r or {}).items()}
                          for r in (split.get("rules") or [])][:20],
                "else": str(split.get("else") or "Other")[:120],
                "plan": route_plan,
                "groups": groups if mode == "batches" else [],
                "destinations": destinations if mode == "selected" else {},
                "folders": tree_folders if mode == "tree" else [],
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
    resolved = naming_mod.dedupe_job_names(names, items, job, job.get("conflict_policy") or "rename")
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
    split = (job.get("destination") or {}).get("split") or {}
    if split.get("mode") == "tree":
        for row, item in zip(payload, items):
            row["folder_key"] = (split.get("plan") or {}).get(item["id"], [])
    rows = naming_mod.preview_rows(payload, opt, start=opt.start)
    return ok(rows=rows)


@router.delete("/api/jobs/{job_id}/items/{item_id}")
async def api_delete_item(request: Request, job_id: str, item_id: str,
                          user=Depends(session_user)):
    st = state_of(request)
    job = await owned_job(request, job_id, user)
    if not job:
        return fail("not_found", "job not found", 404)
    if await st.db.one("SELECT job_id FROM channel_batches WHERE job_id=? AND state='open'", (job_id,)):
        return fail("collection_open", "Finish the channel batch before editing it.", 409)
    items = await repo.items_of(st.db, job_id)
    target_item = next((i for i in items if i["id"] == item_id), None)
    if not target_item:
        return fail("not_found", "item not found", 404)
    staged_path = (target_item.get("tg") or {}).get("source_path")
    if staged_path:
        staged_root = (Path(st.cfg.download_dir) / "studio").resolve()
        candidate = Path(staged_path).resolve()
        if candidate.parent == staged_root:
            candidate.unlink(missing_ok=True)
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
    if await st.db.one("SELECT job_id FROM channel_batches WHERE job_id=? AND state='open'", (job_id,)):
        return fail("collection_open", "Finish the channel batch before editing it.", 409)
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
    if await st.db.one("SELECT job_id FROM channel_batches WHERE job_id=? AND state='open'", (job_id,)):
        return fail("collection_open", "Finish the channel batch in the bot before starting this upload.", 409)
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
    if await st.db.one(
            "SELECT job_id FROM channel_batches WHERE job_id=? AND state='open'", (job_id,)):
        return fail("collection_open", "Finish the channel batch in the bot first.", 409)
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
                    payload = await asyncio.wait_for(queue.get(), timeout=5)
                except asyncio.TimeoutError:
                    current = st.worker.hub.get(job_id)
                    if current:
                        yield f"data: {json.dumps(current.snapshot())}\n\n"
                    else:
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
        return fail("not_connected", "Google Drive is not connected — sign in with Google to reconnect", 409)
    try:
        data = await client.list_folders(parent, page_token=page)
        return ok(parent=parent, next=data["next"], folders=data["files"])
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
@router.get("/auth/google/login")
async def google_web_login(request: Request):
    """Start a bot-free Studio session using Google Drive OAuth directly."""
    st = state_of(request)
    sid = request.cookies.get(SESSION_COOKIE, "")
    existing = await repo.get_session(st.db, sid) if sid else None
    if existing:
        user = await repo.get_user(st.db, existing["user_id"])
        account = await repo.get_drive_account(st.db, existing["user_id"])
        if user and account:
            return RedirectResponse("/studio", status_code=303)
        if user:
            state = oauth.new_state()
            verifier = oauth.new_verifier()
            await repo.create_state(st.db, state, user["user_id"], verifier)
            resp = RedirectResponse(oauth.build_authorize_url(st.cfg, state, verifier), status_code=303)
            resp.set_cookie("du_oauth_state", state, max_age=900, httponly=True,
                            secure=request.headers.get("x-forwarded-proto", request.url.scheme).split(",")[0].strip() == "https",
                            samesite="lax", path="/")
            return resp
    # Negative, high-entropy web-only IDs cannot collide with Telegram's positive IDs.
    user_id = -(secrets.randbelow(2**50) + 2**40)
    while await repo.get_user(st.db, user_id):
        user_id = -(secrets.randbelow(2**50) + 2**40)
    await repo.upsert_user(st.db, user_id, name="Web user", username="web", lang="en")
    state = oauth.new_state()
    verifier = oauth.new_verifier()
    await repo.create_state(st.db, state, user_id, verifier)
    resp = RedirectResponse(oauth.build_authorize_url(st.cfg, state, verifier), status_code=303)
    resp.set_cookie("du_oauth_state", state, max_age=900, httponly=True,
                    secure=request.headers.get("x-forwarded-proto", request.url.scheme).split(",")[0].strip() == "https",
                            samesite="lax", path="/")
    return resp


@router.get("/auth/google/start")
async def google_start(request: Request, user=Depends(session_user)):
    st = state_of(request)
    state = oauth.new_state()
    verifier = oauth.new_verifier()
    await repo.create_state(st.db, state, user["user_id"], verifier)
    resp = RedirectResponse(oauth.build_authorize_url(st.cfg, state, verifier), status_code=303)
    resp.set_cookie("du_oauth_state", state, max_age=900, httponly=True,
                    secure=request.headers.get("x-forwarded-proto", request.url.scheme).split(",")[0].strip() == "https",
                            samesite="lax", path="/")
    return resp


@router.get("/auth/google/callback")
async def google_callback(request: Request, code: str = "", state: str = "",
                          error: str = ""):
    st = state_of(request)
    if error:
        return HTMLResponse(f"<h2>Google said no</h2><p>{error}</p>", status_code=400)
    row = await repo.take_state(st.db, state)
    if not row:
        return HTMLResponse("<h2>This sign-in link expired</h2><p>Return to ZC Drive Upload and sign in again.</p>",
                            status_code=400)
    if int(row.get("user_id") or 0) < 0 and request.cookies.get("du_oauth_state") != state:
        return HTMLResponse("<h2>Sign-in could not be verified</h2><p>Restart Google sign-in from this browser.</p>",
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
        if int(row["user_id"]) > 0:
            await st.bot.send_message(row["user_id"],
                                      f"✅ Connected as <b>{info.get('email')}</b> · {free} free of {total}",
                                      parse_mode="HTML")
    except Exception:                                       # noqa: BLE001
        pass
    sid = await mint_session(
        st.db, int(row["user_id"]),
        request.client.host if request.client else "",
        request.headers.get("user-agent", ""))
    proto = request.headers.get("x-forwarded-proto", request.url.scheme).split(",")[0].strip()
    # Return directly to the private workspace with an explicit success marker so
    # the dashboard can confirm the Drive connection and show its upload action.
    response = RedirectResponse("/studio?connected=1", status_code=303)
    set_session_cookie(response, sid, secure=proto == "https")
    response.delete_cookie("du_oauth_state", path="/")
    return response


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
