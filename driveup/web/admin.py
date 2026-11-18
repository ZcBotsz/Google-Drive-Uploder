"""Admin panel — API + server-rendered pages.

Security (B8/B12), all enforced in one place:
  * `admin_user` dependency: role must be owner/admin/support; owner-only routes
    check again; sessions auto-lock after ADMIN_IDLE_MINUTES.
  * every state-changing call needs the CSRF token from `GET /admin/api/csrf`
    (double-submit: cookie + `X-CSRF` header must match).
  * every write lands in the audit log with actor, action, target and IP.
"""
from __future__ import annotations

import csv
import io
import json
import secrets
import time
from typing import Any, Dict, List

from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response, RedirectResponse

from ..config import effective_max_file_mb, BRAND_NAME
from ..core.access import human_bytes
from ..db import repo
from ..strings import esc
from .auth import (SESSION_COOKIE, admin_user, clear_session_cookie, session_user,
                  admin_unlock_token, verify_admin_unlock, set_session_cookie)

router = APIRouter()
CSRF_COOKIE = "du_csrf"
OWNER_ONLY = {"add_admin", "remove_admin", "danger", "settings_write", "export"}


def _ok(**kw) -> JSONResponse:
    return JSONResponse({"ok": True, **kw})


def _fail(code: str, message: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"ok": False, "code": code, "message": message}, status_code=status)


def _csrf_ok(request: Request) -> bool:
    cookie = request.cookies.get(CSRF_COOKIE, "")
    header = request.headers.get("X-CSRF", "")
    return bool(cookie) and secrets.compare_digest(cookie, header)


async def _guard(request: Request, user: dict, *, owner_only: bool = False,
                 write: bool = False) -> JSONResponse | None:
    if owner_only and user["role"] != "owner":
        return _fail("forbidden", "owner only", 403)
    if write and not _csrf_ok(request):
        return _fail("csrf", "missing or wrong CSRF token", 403)
    return None


def _ip(request: Request) -> str:
    return (request.headers.get("x-forwarded-for", "").split(",")[0].strip()
            or (request.client.host if request.client else ""))


async def _audit(request: Request, user: dict, action: str, target: str = "",
                 detail: Any = "") -> None:
    st = request.app.state
    out = repo.audit(st.db, user["user_id"], action, target, detail, _ip(request))
    if hasattr(out, "__await__"):
        await out
    await st.log_event(f"#{action.replace('.', ' ').title()}", {
        "actor": user["user_id"], "target": target, "detail": detail})


# ── csrf + session bootstrap ────────────────────────────────────────────────
@router.get("/admin/api/csrf")
async def csrf(request: Request, user=Depends(admin_user)):
    token = secrets.token_urlsafe(24)
    resp = _ok(token=token, role=user["role"])
    resp.set_cookie(CSRF_COOKIE, token, httponly=False, samesite="lax", path="/")
    return resp


@router.get("/admin/api/whoami")
async def whoami(user=Depends(admin_user)):
    """The panel header: who am I and what may I do."""
    return _ok(user={"id": user["user_id"], "name": user["name"],
                     "username": user.get("username", ""), "role": user["role"]})


@router.post("/admin/api/logout")
async def logout(request: Request, user=Depends(admin_user)):
    """Real session teardown — the cookie is HttpOnly, so JS cannot fake it."""
    blocked = await _guard(request, user, write=True)
    if blocked:
        return blocked
    st = request.app.state
    sid = request.cookies.get(SESSION_COOKIE, "")
    if sid:
        await repo.delete_session(st.db, sid)
    await _audit(request, user, "admin.logout", str(user["user_id"]))
    resp = _ok()
    clear_session_cookie(resp)
    return resp


# ── stats / dashboard ───────────────────────────────────────────────────────
@router.get("/admin/api/stats")
async def stats(request: Request, user=Depends(admin_user)):
    st = request.app.state
    stats = await repo.global_stats(st.db)
    days = await repo.daily_rows(st.db, 14)
    sys = {}
    try:
        import psutil
        vm = psutil.virtual_memory()
        sys = {"cpu": psutil.cpu_percent(interval=None), "ram": vm.percent,
               "ram_used": vm.used, "ram_total": vm.total,
               "disk": psutil.disk_usage("/").percent}
    except Exception:
        sys = {"cpu": 0, "ram": 0, "disk": 0}
    errors = await repo.recent_errors(st.db, 10)
    return _ok(stats=stats, days=list(reversed(days)), system=sys,
               uptime=round(time.time() - st.started_at), errors=errors,
               workers={"global": st.cfg.max_global_uploads,
                        "user": st.cfg.max_user_uploads})


# ── users ──────────────────────────────────────────────────────────────────
@router.get("/admin/api/users")
async def users(request: Request, user=Depends(admin_user), q: str = "",
                limit: int = 25, offset: int = 0):
    st = request.app.state
    rows = await repo.list_users(st.db, q, limit=min(200, limit), offset=max(0, offset))
    out = []
    for r in rows:
        lock = await repo.get_lock(st.db, r["user_id"])
        plan = await repo.effective_plan(st.db, r["user_id"])
        acc = await repo.get_drive_account(st.db, r["user_id"])
        out.append({**{k: r[k] for k in ("user_id", "name", "username", "first_seen",
                                         "last_seen", "role", "lang")},
                    "locked": bool(lock), "lock_reason": (lock or {}).get("reason", ""),
                    "lock_until": (lock or {}).get("expires_at", 0),
                    "plan": plan.get("id"), "plan_name": plan.get("name"),
                    "drive": (acc or {}).get("email", ""),
                    "needs_reconnect": bool((acc or {}).get("needs_reconnect"))})
    total = await repo.count_users(st.db)
    return _ok(users=out, total=total)


@router.get("/admin/api/users/{user_id}")
async def user_detail(request: Request, user_id: int, user=Depends(admin_user)):
    st = request.app.state
    row = await repo.get_user(st.db, user_id)
    if not row:
        return _fail("not_found", "no such user", 404)
    jobs = await repo.jobs_for_user(st.db, user_id, 20)
    usage = await repo.quota_used(st.db, user_id)
    plan = await repo.effective_plan(st.db, user_id)
    acc = await repo.get_drive_account(st.db, user_id)
    lock = await repo.get_lock(st.db, user_id)
    stats = await repo.user_stats(st.db, user_id)
    return _ok(user={k: row[k] for k in ("user_id", "name", "username", "first_seen",
                                         "last_seen", "role", "lang")},
               settings=row.get("settings", {}), plan=plan, quota=usage, stats=stats,
               drive={"email": (acc or {}).get("email", ""),
                      "needs_reconnect": bool((acc or {}).get("needs_reconnect")),
                      "connected_at": (acc or {}).get("connected_at", 0)},
               lock=lock,
               jobs=[{"id": j["id"], "status": j["status"], "created_at": j["created_at"],
                      "totals": j.get("totals") or {}} for j in jobs])


@router.post("/admin/api/users/{user_id}/lock")
async def lock(request: Request, user_id: int, body: dict = Body(...),
               user=Depends(admin_user)):
    st = request.app.state
    if user["role"] not in ("owner", "admin"):
        return _fail("forbidden", "your role cannot lock users", 403)
    if g := await _guard(request, user, write=True):
        return g
    target_role = await repo.role_of(st.db, user_id, st.cfg.owner_id)
    if target_role in ("owner", "admin"):
        return _fail("forbidden", "admins cannot be locked", 403)
    minutes = max(1, int(body.get("minutes") or st.cfg.lock_default_minutes))
    reason = str(body.get("reason") or "No reason given")[:200]
    scope = body.get("scope") if body.get("scope") in ("all", "upload", "web") else "all"
    row = await repo.lock_user(st.db, user_id, reason=reason, by_id=user["user_id"],
                               minutes=minutes, scope=scope)
    killed = await st.worker.cancel_user_jobs(user_id, why="locked")
    sessions = await repo.kill_user_sessions(st.db, user_id)
    await _audit(request, user, "user.lock", str(user_id),
                 {"reason": reason, "minutes": minutes, "scope": scope,
                  "jobs_cancelled": killed, "sessions_killed": sessions})
    await st.lock_card("locked", user_id, reason, row["expires_at"], user["user_id"],
                       minutes=minutes)
    return _ok(lock=row, jobs_cancelled=killed, sessions_killed=sessions)


@router.post("/admin/api/users/{user_id}/unlock")
async def unlock(request: Request, user_id: int, user=Depends(admin_user)):
    st = request.app.state
    if user["role"] not in ("owner", "admin", "support"):
        return _fail("forbidden", "not allowed", 403)
    if g := await _guard(request, user, write=True):
        return g
    existed = await repo.unlock_user(st.db, user_id)
    await _audit(request, user, "user.unlock", str(user_id), {"existed": existed})
    if existed:
        await st.lock_card("unlocked", user_id, "", 0, user["user_id"])
    return _ok(unlocked=existed)


@router.post("/admin/api/users/{user_id}/plan")
async def set_plan(request: Request, user_id: int, body: dict = Body(...),
                   user=Depends(admin_user)):
    st = request.app.state
    if user["role"] not in ("owner", "admin"):
        return _fail("forbidden", "your role cannot change plans", 403)
    if g := await _guard(request, user, write=True):
        return g
    days = max(1, min(3650, int(body.get("days") or 30)))
    if body.get("revoke"):
        await repo.revoke_plan(st.db, user_id)
        await _audit(request, user, "plan.revoke", str(user_id))
        return _ok(revoked=True)
    plan_id = str(body.get("plan_id") or "pro")[:40]
    row = await repo.grant_plan(st.db, user_id, plan_id, days)
    await _audit(request, user, "plan.grant", str(user_id), {"plan": plan_id, "days": days})
    return _ok(grant=row)


@router.post("/admin/api/users/{user_id}/quota")
async def reset_quota(request: Request, user_id: int, user=Depends(admin_user)):
    st = request.app.state
    if g := await _guard(request, user, write=True):
        return g
    day = time.strftime("%Y-%m-%d")
    await st.db.x("DELETE FROM quota_usage WHERE user_id=? AND day=?", (user_id, day))
    await _audit(request, user, "user.quota_reset", str(user_id))
    return _ok()


@router.post("/admin/api/users/{user_id}/disconnect")
async def force_disconnect(request: Request, user_id: int, user=Depends(admin_user)):
    st = request.app.state
    if g := await _guard(request, user, write=True):
        return g
    await st.accounts.disconnect(user_id)
    await _audit(request, user, "user.drive_disconnect", str(user_id))
    return _ok()


@router.post("/admin/api/users/{user_id}/message")
async def message_user(request: Request, user_id: int, body: dict = Body(...),
                       user=Depends(admin_user)):
    st = request.app.state
    if g := await _guard(request, user, write=True):
        return g
    text = str(body.get("text", ""))[:3800]
    if not text:
        return _fail("empty", "message is empty", 422)
    try:
        await st.bot.send_message(user_id, f"📩 <b>Message from support</b>\n\n{esc(text)}",
                                  parse_mode="HTML")
    except Exception as e:                                  # noqa: BLE001
        return _fail("send_failed", str(e)[:200], 502)
    await _audit(request, user, "user.message", str(user_id), {"chars": len(text)})
    return _ok()


@router.delete("/admin/api/users/{user_id}")
async def delete_user_data(request: Request, user_id: int, user=Depends(admin_user)):
    st = request.app.state
    if g := await _guard(request, user, owner_only=True, write=True):
        return g
    if user_id == st.cfg.owner_id:
        return _fail("forbidden", "cannot delete the owner", 403)
    await st.db.x("DELETE FROM job_items WHERE job_id IN (SELECT id FROM jobs WHERE user_id=?)",
                  (user_id,))
    for table in ("jobs", "drive_accounts", "user_locks", "quota_usage", "plan_grants",
                  "sessions", "presets", "favourites", "tg_files"):
        try:
            col = "user_id" if table != "tg_files" else "chat_id"
            await st.db.x(f"DELETE FROM {table} WHERE {col}=?", (user_id,))
        except Exception:                                   # noqa: BLE001
            pass
    await st.db.x("DELETE FROM users WHERE user_id=?", (user_id,))
    await _audit(request, user, "user.delete", str(user_id))
    return _ok(deleted=True)


# ── jobs monitor ───────────────────────────────────────────────────────────
@router.get("/admin/api/jobs")
async def jobs(request: Request, user=Depends(admin_user), status: str = "",
               limit: int = 50):
    st = request.app.state
    if status:
        rows = await st.db.q("SELECT * FROM jobs WHERE status=? ORDER BY created_at DESC LIMIT ?",
                             (status, min(200, limit)))
    else:
        rows = await st.db.q("SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?",
                             (min(200, limit),))
    out = []
    for r in rows:
        u = await repo.get_user(st.db, r["user_id"])
        out.append({"id": r["id"], "user_id": r["user_id"],
                    "user": (u or {}).get("name", ""), "status": r["status"],
                    "created_at": r["created_at"],
                    "totals": json.loads(r["totals"] or "{}")})
    return _ok(jobs=out, paused=st.worker.paused_global if hasattr(st.worker, "paused_global") else False)


@router.post("/admin/api/jobs/{job_id}/{action}")
async def job_action(request: Request, job_id: str, action: str, user=Depends(admin_user)):
    st = request.app.state
    if g := await _guard(request, user, write=True):
        return g
    if action == "pause":
        await st.worker.pause(job_id)
    elif action == "resume":
        await st.worker.resume(job_id)
    elif action == "cancel":
        await st.worker.cancel(job_id)
    elif action == "retry":
        await repo.retry_failed(st.db, job_id)
        await repo.update_job(st.db, job_id, status="queued")
        st.worker.spawn(job_id)
    else:
        return _fail("bad_action", "unknown action", 404)
    await _audit(request, user, f"job.{action}", job_id)
    return _ok()


@router.post("/admin/api/queue/{action}")
async def queue_control(request: Request, action: str, user=Depends(admin_user)):
    st = request.app.state
    if g := await _guard(request, user, write=True):
        return g
    if action in ("pause_all", "resume_all"):
        st.worker.paused_global = action == "pause_all"
        await _audit(request, user, f"queue.{action}")
        return _ok(paused=st.worker.paused_global)
    return _fail("bad_action", "unknown action", 404)


# ── locks ──────────────────────────────────────────────────────────────────
@router.get("/admin/api/locks")
async def locks(request: Request, user=Depends(admin_user), all_: int = Query(0, alias="all")):
    st = request.app.state
    rows = await repo.list_locks(st.db, include_expired=bool(all_))
    out = []
    for r in rows:
        u = await repo.get_user(st.db, r["user_id"])
        out.append({**r, "name": (u or {}).get("name", ""),
                    "username": (u or {}).get("username", "")})
    return _ok(locks=out)


@router.post("/admin/api/locks")
async def lock_raw(request: Request, body: dict = Body(...), user=Depends(admin_user)):
    return await lock(request, int(body.get("user_id") or 0), body, user)


# ── plans ──────────────────────────────────────────────────────────────────
@router.get("/admin/api/plans")
async def plans(request: Request, user=Depends(admin_user)):
    st = request.app.state
    return _ok(plans=await repo.all_plans(st.db),
               expiring=await repo.expiring_plans(st.db, 7),
               # the same ceiling the settings pane reports, so the plans table
               # can flag a per-file size that Telegram will never deliver
               cfg={"max_file_mb_reachable": effective_max_file_mb(
                        st.cfg, requested_mb=10_000),
                    "telegram_cloud_api": ((st.cfg.telegram_api_url or "").rstrip("/")
                                           in ("", "https://api.telegram.org"))})


@router.post("/admin/api/plans")
async def save_plan(request: Request, body: dict = Body(...), user=Depends(admin_user)):
    st = request.app.state
    if g := await _guard(request, user, owner_only=True, write=True):
        return g
    await repo.upsert_plan(
        st.db, str(body.get("id") or "pro")[:40], name=str(body.get("name") or "Pro")[:60],
        price_text=str(body.get("price_text") or "")[:60],
        duration_days=int(body.get("duration_days") or 30),
        max_file_mb=int(body.get("max_file_mb") or 2000),
        daily_gb=float(body.get("daily_gb") or 20),
        concurrent=int(body.get("concurrent") or 1),
        max_files_job=int(body.get("max_files_job") or 200),
        features=list(body.get("features") or []),
        sort=int(body.get("sort") or 0))
    await _audit(request, user, "plan.save", str(body.get("id")))
    return _ok(plans=await repo.all_plans(st.db))


# ── broadcast ──────────────────────────────────────────────────────────────
@router.post("/admin/api/broadcast")
async def broadcast(request: Request, body: dict = Body(...), user=Depends(admin_user)):
    st = request.app.state
    if user["role"] not in ("owner", "admin"):
        return _fail("forbidden", "your role cannot broadcast", 403)
    if g := await _guard(request, user, write=True):
        return g
    text = str(body.get("text", ""))[:4000]
    if not text:
        return _fail("empty", "message is empty", 422)
    audience = body.get("audience", "all")
    test_only = bool(body.get("test"))
    rows = await st.db.q("SELECT user_id FROM users")
    if audience == "plan":
        rows = [r for r in rows if await repo.effective_plan(st.db, r["user_id"])
                and (await repo.effective_plan(st.db, r["user_id"])).get("id") != "free"]
    if audience == "active":
        rows = await st.db.q("SELECT user_id FROM users WHERE last_seen>=?",
                             (time.time() - 7 * 86400,))
    targets = [user["user_id"]] if test_only else [r["user_id"] for r in rows]
    sent = failed = 0
    for uid in targets[:20000]:
        try:
            await st.bot.send_message(uid, text, parse_mode="HTML",
                                      disable_web_page_preview=True)
            sent += 1
        except Exception:                                   # noqa: BLE001
            failed += 1
        await _sleep(0.05)                                  # ~20/s, well under limits
    await _audit(request, user, "broadcast.send", audience,
                 {"sent": sent, "failed": failed, "test": test_only})
    return _ok(sent=sent, failed=failed)


async def _sleep(sec: float) -> None:
    import asyncio
    await asyncio.sleep(sec)


# ── audit / errors ─────────────────────────────────────────────────────────
@router.get("/admin/api/audit")
async def audit_list(request: Request, user=Depends(admin_user), q: str = "",
                     limit: int = 100):
    st = request.app.state
    if user["role"] not in ("owner", "admin"):
        return _fail("forbidden", "your role cannot read the audit log", 403)
    rows = await repo.list_audit(st.db, limit=min(500, limit), search=q)
    return _ok(audit=rows)


@router.get("/admin/api/errors")
async def errors(request: Request, user=Depends(admin_user)):
    st = request.app.state
    return _ok(errors=await repo.recent_errors(st.db, 50))


# ── settings ───────────────────────────────────────────────────────────────
@router.get("/admin/api/settings")
async def get_settings(request: Request, user=Depends(admin_user)):
    st = request.app.state
    g = await repo.get_global(st.db)
    safe = dict(g)
    from ..bot.ui import emoji_ids
    return _ok(settings=safe, cfg={
        "brand": st.cfg.brand if hasattr(st.cfg, "brand") else BRAND_NAME,
        "scope_mode": st.cfg.drive_scope_mode, "chunk_mb": st.cfg.chunk_bytes // 1024 // 1024,
        "upload_mode": st.cfg.upload_mode, "max_global": st.cfg.max_global_uploads,
        "public_url": st.cfg.public_url, "owner_id": st.cfg.owner_id,
        "secrets": st.cfg.secrets_summary(),
        # Animated emoji: show what ships, so the panel needs no docs.
        "emoji_catalogue_size": len(emoji_ids.CATALOGUE),
        "emoji_free_size": len(emoji_ids.FREE),
        "emoji_sample": emoji_ids.CARD_EMOJI[:8],
        "filename_tag_default": filename_tag_for(g),
        # Telegram is the real ceiling on file size — surface it, or an admin
        # sets a 2000 MB plan that can never be delivered.
        "telegram_cloud_api": ((st.cfg.telegram_api_url or "").rstrip("/")
                               in ("", "https://api.telegram.org")),
        "max_file_mb_reachable": effective_max_file_mb(
            st.cfg, requested_mb=g.get("default_max_file_mb", 2000)),
    })


def filename_tag_for(g: dict) -> str:
    """The exact tag appended under every upload, so admins can see it."""
    tag = g.get("filename_tag") or ""
    if not g.get("filename_tag_enabled") or not tag:
        return ""
    return tag if tag.startswith("@") else f"@{tag}"


@router.put("/admin/api/settings")
async def put_settings(request: Request, body: dict = Body(...), user=Depends(admin_user)):
    st = request.app.state
    if g := await _guard(request, user, owner_only=True, write=True):
        return g
    allowed = {"maintenance", "maintenance_msg", "registration", "welcome_text",
               "filename_tag", "filename_tag_enabled", "progress_style",
               "force_sub_channels", "log_channel_id", "dump_channel_id", "lock_channel_id",
               "support_contact", "brand_name", "plan_price_text",
               "default_max_file_mb", "allowed_types", "chunk_mb", "animated_emoji",
               "features"}
    patch = {k: v for k, v in body.items() if k in allowed}
    merged = await repo.set_global(st.db, patch)
    await _audit(request, user, "settings.write", "global", patch)
    if "maintenance" in patch and patch["maintenance"]:
        await st.log_event("#Maintenance ON", {"msg": merged.get("maintenance_msg", "")})
    return _ok(settings=merged)


@router.post("/admin/api/emoji/preview")
async def emoji_preview(body: dict = Body(...), user=Depends(admin_user)):
    """Render sample text with animated emoji ON and OFF, side by side.

    Lets an operator verify the ids render on their own device before turning
    the feature on for everybody — a stale id is otherwise invisible.
    """
    from ..bot.ui import emoji as E
    sample = str(body.get("text") or
                 "<pre>progress card stays plain</pre>\n🚀 ✅ 📤 ⚡ 🎉 Done")
    return _ok(on=E.decorate(sample, enabled=True),
               off=sample,
               deck=E.decorate("🚀 📁 📦 📊 ⭐ 🌐 🔄", enabled=True),
               catalogue=len(E.EMOJI_MAP))


@router.get("/admin/api/export/{what}.csv")
async def export_csv(request: Request, what: str, user=Depends(admin_user)):
    st = request.app.state
    if g := await _guard(request, user, owner_only=True):
        return g
    buf = io.StringIO()
    w = csv.writer(buf)
    if what == "users":
        w.writerow(["user_id", "name", "username", "role", "first_seen", "last_seen"])
        for r in await repo.list_users(st.db, limit=10000):
            w.writerow([r["user_id"], r["name"], r["username"], r["role"],
                        r["first_seen"], r["last_seen"]])
    elif what == "jobs":
        w.writerow(["job_id", "user_id", "status", "created_at", "totals"])
        for r in await st.db.q("SELECT * FROM jobs ORDER BY created_at DESC LIMIT 20000"):
            w.writerow([r["id"], r["user_id"], r["status"], r["created_at"], r["totals"]])
    else:
        return _fail("bad_export", "use users or jobs", 404)
    await _audit(request, user, "export", what)
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{what}.csv"'})


# ── danger zone ────────────────────────────────────────────────────────────
@router.post("/admin/api/danger/{action}")
async def danger(request: Request, action: str, body: dict = Body(default={}),
                 user=Depends(admin_user)):
    st = request.app.state
    if g := await _guard(request, user, owner_only=True, write=True):
        return g
    if action == "clear_errors":
        await st.db.x("DELETE FROM error_log")
    elif action == "purge_history":
        cutoff = time.time() - int(body.get("days", 90)) * 86400
        await st.db.x("DELETE FROM jobs WHERE finished_at>0 AND finished_at<?", (cutoff,))
    elif action == "backup":
        rows = await st.db.q("SELECT * FROM users")
        return _ok(backup={"taken_at": time.time(), "users": len(rows)})
    elif action == "restart_workers":
        await st.worker.stop()
        await st.worker.start()
    elif action == "sweep":
        return _ok(swept=await st.db.sweep())
    else:
        return _fail("bad_action", "unknown action", 404)
    await _audit(request, user, f"danger.{action}", "", body)
    return _ok()


# ── pages ──────────────────────────────────────────────────────────────────
@router.get("/admin/unlock", response_class=HTMLResponse)
async def admin_unlock_page(request: Request):
    user = await session_user(request)
    if user["role"] not in ("owner", "admin", "support"):
        return HTMLResponse("Not an administrator", status_code=403)
    return HTMLResponse('''<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1"><title>Admin security</title><body style="margin:0;background:#081014;color:#e8f6fa;font:16px system-ui;display:grid;place-items:center;min-height:100vh"><form method="post" action="/admin/unlock" style="width:min(360px,88vw);padding:24px;background:#0f1d22;border:1px solid #22d3ee55;border-radius:18px"><h1>Admin verification</h1><p>Enter your private admin password to continue. This unlock expires after 10 minutes.</p><label>Password<br><input name="password" type="password" required autocomplete="current-password" style="width:100%;padding:12px;margin:10px 0;background:#081014;color:white;border:1px solid #456;border-radius:8px"></label><button style="padding:12px 18px;border:0;border-radius:8px;background:#22d3ee;font-weight:bold">Unlock admin</button></form></body>''')


@router.post("/admin/unlock")
async def admin_unlock(request: Request):
    user = await session_user(request)
    if user["role"] not in ("owner", "admin", "support"):
        return HTMLResponse("Not an administrator", status_code=403)
    cfg = request.app.state.cfg
    if not cfg.admin_password:
        return HTMLResponse("Admin password is not configured", status_code=503)
    from urllib.parse import parse_qs
    form = parse_qs((await request.body()).decode("utf-8", "replace"))
    supplied = form.get("password", [""])[0]
    import hmac, time
    if not hmac.compare_digest(supplied.encode(), cfg.admin_password.encode()):
        return HTMLResponse("Incorrect password. Return to /admin/unlock and try again.", status_code=401)
    issued = int(time.time())
    token = admin_unlock_token(cfg.session_secret, user["sid"], issued)
    from fastapi.responses import RedirectResponse
    response = RedirectResponse("/admin", status_code=303)
    proto = request.headers.get("x-forwarded-proto", request.url.scheme).split(",")[0].strip()
    response.set_cookie("du_admin_unlock", token, max_age=600, httponly=True,
                        secure=proto == "https", samesite="strict", path="/admin")
    return response


@router.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    from .admin_page import render_admin
    cfg = request.app.state.cfg
    # Keep the public shell compatible; all data/actions remain protected by
    # admin_user. When a second-factor password is configured, require both
    # the authorized Telegram session and a fresh password unlock first.
    if cfg.admin_password:
        user = await session_user(request)
        if user["role"] not in ("owner", "admin", "support"):
            return HTMLResponse("Not an administrator", status_code=403)
        if not verify_admin_unlock(cfg.session_secret, user["sid"],
                                   request.cookies.get("du_admin_unlock", "")):
            return RedirectResponse("/admin/unlock", status_code=303)
    return HTMLResponse(render_admin(BRAND_NAME))
