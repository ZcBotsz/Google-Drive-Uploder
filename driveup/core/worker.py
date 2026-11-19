"""Job worker — the state machine that moves bytes.

One `Worker` owns an in-process asyncio pool and a DB-backed queue:

    draft → queued → running → done
                        ↑ ↓
                      paused / failed / cancelled

Nothing important lives only in RAM: every transition is an UPDATE, the Drive
session URI is persisted per item, so `recover_interrupted()` after a crash
re-queues the job and the item resumes from whatever Google already committed.

Byte path (UPLOAD_MODE=auto → stream):
    bot.download (stream_content, 1 MiB)
        → bounded asyncio.Queue
            → ResumableUploader.put_chunk (8 MiB, 256 KiB aligned)

In disk mode the same bytes land in DOWNLOAD_DIR first (restart-resumable
download half), then the uploader reads the file.
"""
from __future__ import annotations

import asyncio
import contextlib
import io
import logging
import os
import time
from pathlib import Path
from typing import Optional

from ..bot.ui.cards import (failure_block, progress_card, summary_card, links_block,
                            human_secs)
from ..bot.ui import emoji as _emoji
from . import groups
from ..config import Settings
from ..db import repo
from ..db.sql import Database, dumps
from ..drive.accounts import AccountManager
from ..drive.upload import _fire
from ..tg import download as tg_download
from .pause_reason import pause_message
from . import failures
from ..config import effective_max_file_mb
from .url_upload import upload_from_url
from .thumbs import apply_thumb
from ..drive.errors import (AuthExpired, DailyLimit, DriveError, NetworkError,
                            RateLimited, SessionGone, StorageQuota)
from ..strings import esc
from .naming import NamingOptions, build_name, dedupe_names, human_size
from .progress import ProgressHub, ProgressState

log = logging.getLogger("driveup.worker")


class Cancelled(Exception):
    pass



async def _put(queue: asyncio.Queue, item) -> None:
    """Put that cannot wedge when the consumer has already given up."""
    try:
        await queue.put(item)
    except asyncio.CancelledError:
        raise


class Worker:
    def __init__(self, *, db: Database, cfg: Settings, accounts: AccountManager,
                 bot, hub: Optional[ProgressHub] = None, gate=None) -> None:
        self.db = db
        self.cfg = cfg
        self.accounts = accounts
        self.bot = bot
        self.hub = hub or ProgressHub()
        self.fg = gate
        self._jobs: dict[str, asyncio.Task] = {}
        self._queues: dict[int, asyncio.Semaphore] = {}
        self._global = asyncio.Semaphore(max(1, cfg.max_global_uploads))
        self._stopping = False
        self._cancel_flags: dict[str, asyncio.Event] = {}
        self._pause_flags: dict[str, asyncio.Event] = {}
        self._watcher: Optional[asyncio.Task] = None
        # per-job cache of split-folder ids: one Drive round-trip per folder name,
        # never one per file (a 500-file job would otherwise create 500 dupes)
        self._folders: dict[str, dict] = {}
        self._resolvers: dict[str, object] = {}

    # ── lifecycle ────────────────────────────────────────────────────────
    async def start(self) -> None:
        n = await repo.recover_interrupted(self.db)
        if n:
            log.warning("recovered %s interrupted job(s) — queued", n)
        for job in await repo.running_jobs(self.db):
            self.spawn(job["id"])
        self._watcher = asyncio.create_task(self._watch(), name="worker-watch")

    async def stop(self, timeout: float = 5.0) -> None:
        self._stopping = True
        for ev in self._cancel_flags.values():
            ev.set()
        tasks = list(self._jobs.values())
        for t in tasks:
            t.cancel()
        if tasks:
            done, pending = await asyncio.wait(tasks, timeout=timeout)
            for t in pending:                       # stuck in a socket read
                log.debug("worker task did not cancel cleanly: %s", t.get_name())
        self._jobs.clear()
        if self._watcher:
            self._watcher.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._watcher
            self._watcher = None

    async def _watch(self) -> None:
        """Watchdog: an item with no progress for STALL_SECONDS is re-queued."""
        while not self._stopping:
            try:
                await asyncio.sleep(20)
                for job_id, st in list(self.hub.current.items()):
                    if st.stage in ("uploading", "downloading") and not st.paused:
                        last = max((t for t, _ in st.meter.samples), default=st.started)
                        if time.monotonic() - last > self.cfg.stall_seconds:
                            log.warning("job %s stalled — cancelling the task", job_id)
                            ev = self._cancel_flags.get(job_id)
                            if ev:
                                ev.set()
            except asyncio.CancelledError:
                return
            except Exception as e:                       # noqa: BLE001
                log.debug("watchdog tick failed: %s", e)

    # ── control ──────────────────────────────────────────────────────────
    def sem_for(self, user_id: int) -> asyncio.Semaphore:
        n = max(1, self.cfg.max_user_uploads)
        return self._queues.setdefault(user_id, asyncio.Semaphore(n))

    def spawn(self, job_id: str) -> None:
        if job_id in self._jobs and not self._jobs[job_id].done():
            return
        self._cancel_flags.setdefault(job_id, asyncio.Event())
        self._pause_flags.setdefault(job_id, asyncio.Event())
        self._jobs[job_id] = asyncio.create_task(self._run_job(job_id), name=f"job-{job_id}")

    async def cancel(self, job_id: str) -> None:
        ev = self._cancel_flags.get(job_id)
        if ev:
            ev.set()
        await repo.update_job(self.db, job_id, status="cancelled", finished_at=time.time())
        for it in await repo.items_of(self.db, job_id):
            if it["state"] not in ("done", "skipped"):
                await repo.set_item(self.db, it["id"], state="failed", error="cancelled")

    # A job in one of these states is over: it must never be moved back to
    # "running" by a pause/resume tap that raced the finish. Doing so used to
    # leave a finished job showing as running for ever (the task had exited).
    TERMINAL = ("done", "failed", "cancelled")

    async def pause(self, job_id: str) -> None:
        job = await repo.get_job(self.db, job_id)
        if not job or job["status"] in self.TERMINAL:
            return                                  # nothing to pause any more
        ev = self._pause_flags.setdefault(job_id, asyncio.Event())
        ev.set()
        await repo.update_job(self.db, job_id, status="paused")
        st = self.hub.get(job_id)
        if st:
            st.paused = True
            self.hub.put(st)

    async def resume(self, job_id: str) -> None:
        """Un-pause a job. A no-op unless the job is *actually* paused.

        Writing "running" unconditionally was a real bug: a resume tap landing
        at the same moment the job finished flipped a finished job back to
        "running" with no task behind it — the card froze and the queue never
        drained. Only a paused row may transition to running here.
        """
        ev = self._pause_flags.get(job_id)
        job = await repo.get_job(self.db, job_id)
        if not job or job["status"] in self.TERMINAL or job["status"] != "paused":
            if ev:
                ev.clear()               # nothing to resume (finished / running)
            return
        if ev:
            ev.clear()
        await repo.update_job(self.db, job_id, status="running")
        st = self.hub.get(job_id)
        if st:
            st.paused = False
            self.hub.put(st)
        if job_id not in self._jobs or self._jobs[job_id].done():
            self.spawn(job_id)

    async def cancel_user_jobs(self, user_id: int, why: str = "locked") -> int:
        n = 0
        for job in await repo.running_jobs(self.db):
            if job["user_id"] == user_id:
                await self.cancel(job["id"])
                n += 1
                log.info("cancelled job %s of user %s (%s)", job["id"], user_id, why)
        return n

    async def _renumber(self, job: dict, items: list) -> None:
        opt = NamingOptions.from_dict(job.get("naming") or {})
        total = len(items)
        names = []
        for i, it in enumerate(items, 1):
            tg = it["tg"] or {}
            names.append(build_name(original=tg.get("name", "file"), mime=tg.get("mime", ""),
                                    seq=opt.start + (i - 1) * opt.step, index=i, total=total,
                                    opt=opt, size=tg.get("size", 0),
                                    caption=tg.get("caption", ""),
                                    message_id=tg.get("msg_id", 0)))
        resolved = dedupe_names(names, job.get("conflict_policy") or "rename")
        for i, it in enumerate(items):
            # '' means "skip" (conflict_policy=skip) — it must NOT fall back to
            # the duplicate name, or the skipped file gets uploaded anyway.
            new = resolved[i] if i in resolved else names[i]
            if new != it["final_name"]:
                await repo.set_item(self.db, it["id"], final_name=new)

    # ── the job ──────────────────────────────────────────────────────────
    async def _run_job(self, job_id: str) -> None:
        async with self._global:
            try:
                await self._run_job_inner(job_id)
            except asyncio.CancelledError:
                raise
            except Exception as e:                       # noqa: BLE001
                log.exception("job %s died", job_id)
                await repo.update_job(self.db, job_id, status="failed", finished_at=time.time())
                await repo.log_error(self.db, f"job:{job_id}", failures.service_line(e))
                # Say WHY, in the chat. A summary that only counts failures
                # ("0 done · 1 failed") is what made a working bot look broken.
                await self._tell_failure(job_id, e)
                self.hub.finish(job_id)
            finally:
                await self._reconcile(job_id)

    async def _tell_failure(self, job_id: str, err: Exception) -> None:
        """Put the cause in the chat, not only in the log."""
        try:
            job = await repo.get_job(self.db, job_id)
            if not job:
                return
            sm = job.get("status_message") or {}
            chat_id = sm.get("chat_id") or job.get("user_id")
            items = await repo.items_of(self.db, job_id)
            stuck = next((i for i in items if i.get("state") != "done"), None)
            name = (stuck or {}).get("final_name") or ((stuck or {}).get("tg") or {}).get("name", "")
            head = f"❌ <b>{failures.esc(name)}</b>\n" if name else "❌ <b>Upload failed</b>\n"
            body = failures.explain(err)
            tail = ""
            if job.get("destination", {}).get("path"):
                tail = f"\n\n📁 Target folder: <i>{failures.esc(job['destination']['path'])}</i>"
            await self._say(chat_id, head + body + tail)
        except Exception as e:                       # noqa: BLE001
            log.debug("could not report failure for %s: %s", job_id, e)

    async def _reconcile(self, job_id: str) -> None:
        """Never leave a job 'running' when no task is behind it.

        Every path that ends a run is supposed to write a terminal status, but a
        pause/resume tap landing at the wrong microsecond could still strand the
        row as "running": the user then watches a progress card that never moves
        and a queue that never drains. This is the backstop that makes that
        impossible — on shutdown the row is left queued so a restart resumes it.
        """
        try:
            job = await repo.get_job(self.db, job_id)
            # Only "running" is a stranded state. "paused" is a deliberate,
            # user-visible resting place (e.g. Google revoked → wait for
            # /connect), so it must survive the task exiting.
            if not job or job["status"] != "running":
                return
            items = await repo.items_of(self.db, job_id)
            pending = [i for i in items if i["state"] in ("pending", "uploading")]
            if self._stopping:
                await repo.update_job(self.db, job_id, status="queued")
                return
            if not pending:
                # Every file is done: the run ended without writing its final
                # status. Report the truth — a false "failed" would be a lie.
                await repo.update_job(self.db, job_id, status="done", finished_at=time.time())
                self.hub.finish(job_id)
                log.info("job %s reconciled to done (all items finished)", job_id)
                return
            await repo.update_job(self.db, job_id, status="failed", finished_at=time.time())
            await repo.log_error(self.db, f"job:{job_id}", "worker exited while job still running")
            log.warning("job %s reconciled to failed (task exited mid-run)", job_id)
            self.hub.finish(job_id)
        except Exception as e:                            # noqa: BLE001
            log.debug("reconcile failed for %s: %s", job_id, e)

    async def _run_job_inner(self, job_id: str) -> None:
        job = await repo.get_job(self.db, job_id)
        if not job:
            return
        if job["status"] in ("cancelled", "done"):
            return
        user_id = job["user_id"]
        items_all = await repo.items_of(self.db, job_id)
        if not items_all:
            await repo.update_job(self.db, job_id, status="failed", finished_at=time.time())
            return

        await repo.update_job(self.db, job_id, status="running", started_at=job["started_at"] or time.time())
        # Renumber at the moment of upload: whatever reordered the items (the
        # bot, the Studio, an API client), the {seq} prefix must follow.
        await self._renumber(job, items_all)
        totals = await repo.recompute_totals(self.db, job_id)
        total_bytes = totals["bytes"]
        base_done = totals["bytes_done"]
        status_msg = job.get("status_message") or {}
        chat_id = status_msg.get("chat_id") or user_id
        msg_id = status_msg.get("message_id")
        started = time.time()
        # Why each file failed, kept in run order so the summary can explain the
        # count instead of only reporting it.
        reasons: list[tuple[str, str]] = []

        async with self.sem_for(user_id):
            while True:
                if self._stopping:
                    return
                cancel = self._cancel_flags.setdefault(job_id, asyncio.Event())
                if cancel.is_set():
                    return
                pause = self._pause_flags.setdefault(job_id, asyncio.Event())
                while pause.is_set():
                    await asyncio.sleep(0.25)
                    # A pause flag can be re-armed by a tap that races the resume
                    # (lost wakeup). The row is the source of truth, so re-read it
                    # and heal instead of sleeping for ever with a "running" row.
                    row = await repo.get_job(self.db, job_id)
                    if not row or row["status"] != "paused":
                        pause.clear()
                        break
                item = await repo.next_pending_item(self.db, job_id)
                if not item:
                    break
                if not (item["final_name"] or "").strip():
                    log.info("item %s skipped by conflict policy", item["id"])
                    await repo.set_item(self.db, item["id"], state="skipped",
                                        error="duplicate name — skipped by policy")
                    continue
                fresh = await repo.get_job(self.db, job_id)
                if fresh and fresh["status"] == "cancelled":
                    return
                try:
                    await self._process_item(job, item, totals, base_done,
                                             chat_id=chat_id, msg_id=msg_id)
                except Cancelled:
                    await repo.set_item(self.db, item["id"], state="failed", error="cancelled")
                    return
                except (AuthExpired, DailyLimit, StorageQuota) as e:
                    # All three mean "this run cannot continue". Retrying the next
                    # file only produces an identical failure, so pause once and
                    # explain — the job resumes from this item later.
                    await self._pause_with_reason(job_id, item, chat_id, e, totals["done"])
                    return
                except Exception as e:                   # noqa: BLE001
                    log.exception("item failed: %s", item.get("id"))
                    # Store the cause in words, not the raw repr: this field is
                    # what the summary card shows the user. `log_error` keeps the
                    # technical form for /audit and the admin panel.
                    reason = failures.explain(e)
                    await repo.set_item(self.db, item["id"], state="failed",
                                        error=reason[:400])
                    await repo.log_error(self.db, f"item:{item.get('id')}", repr(e))
                    await self._record_job_event("❌ Upload failed", job, item,
                                                 f"{reason[:250]}")
                    reasons.append((item.get("final_name")
                                    or (item.get("tg") or {}).get("name", "file"), reason))
                    totals = await repo.recompute_totals(self.db, job_id)

        totals = await repo.recompute_totals(self.db, job_id)
        state = "done" if totals["failed"] == 0 else "done"
        await repo.update_job(self.db, job_id, status=state, finished_at=time.time())
        items = await repo.items_of(self.db, job_id)
        text = summary_card(done=totals["done"], skipped=totals["skipped"], failed=totals["failed"],
                            nbytes=totals["bytes_done"], elapsed=time.time() - started)
        if not reasons:                       # resumed job: read the stored causes
            reasons = [((i.get("final_name") or (i.get("tg") or {}).get("name", "file")),
                        i.get("error") or "")
                       for i in items if i.get("state") == "failed"]
        block = failure_block(reasons)
        if block:
            text += "\n\n" + block
        links = links_block(i for i in items if i["state"] == "done")
        if links:
            text += "\n\n" + links[:3500]
        text = _emoji.apply(text, self.cfg, await repo.get_global(self.db))
        await self._say(chat_id, text)
        if msg_id:
            from ..tg.ratelimit import safe_edit
            await safe_edit(self.bot, chat_id, msg_id, text, gate=self.fg, force=True)
        self.hub.finish(job_id)

    async def _record_job_event(self, title: str, job: dict, item: dict, detail: str) -> None:
        try:
            global_settings = await repo.get_global(self.db)
            channel = global_settings.get("log_channel_id") or self.cfg.log_channel_id
            if not channel:
                return
            tg = item.get("tg") or {}
            await self.bot.send_message(
                channel,
                f"{title}\n👤 User: <code>{int(job.get('user_id') or 0)}</code>\n"
                f"📄 File: {esc(item.get('final_name') or tg.get('name') or 'file')}\n"
                f"🗂 Job: <code>{esc(job.get('id',''))}</code>\n"
                f"📝 {esc(detail)}", parse_mode="HTML", disable_web_page_preview=True)
        except Exception as e:  # noqa: BLE001
            log.debug("log channel event failed: %s", e)

    async def _record_uploaded(self, job: dict, item: dict, drive_info: dict, size: int) -> None:
        """Audit a successful Drive write and mirror the source message to DUMP_CHANNEL_ID.

        This is best-effort by design: channel permission/configuration problems
        must never turn a successful Drive upload into a failed job.
        """
        try:
            global_settings = await repo.get_global(self.db)
            log_channel = global_settings.get("log_channel_id") or self.cfg.log_channel_id
            dump_channel = global_settings.get("dump_channel_id") or self.cfg.dump_channel_id
            tg = item.get("tg") or {}
            user_id = int(job.get("user_id") or 0)
            name = item.get("final_name") or tg.get("name") or "file"
            drive_id = (drive_info or {}).get("id", "")
            link = (drive_info or {}).get("webViewLink", "")
            details = (f"✅ <b>Drive upload complete</b>\n"
                       f"👤 User: <code>{user_id}</code>\n"
                       f"📄 File: {esc(name)}\n"
                       f"📦 Size: {human_size(size)}\n"
                       f"🗂 Job: <code>{esc(job['id'])}</code>")
            if drive_id:
                details += f"\nDrive ID: <code>{esc(drive_id)}</code>"
            if link and link.startswith("https://"):
                details += f'\n<a href="{esc(link)}">Open in Google Drive</a>'
            if log_channel:
                try:
                    await self.bot.send_message(log_channel, details, parse_mode="HTML",
                                                disable_web_page_preview=True)
                except Exception as log_error:  # noqa: BLE001
                    log.warning("upload log channel send failed: %s", log_error)
            if dump_channel:
                source_chat = int(tg.get("chat_id") or 0)
                source_msg = int(tg.get("msg_id") or 0)
                copied = False
                if source_chat and source_msg:
                    try:
                        await self.bot.copy_message(chat_id=dump_channel,
                                                    from_chat_id=source_chat,
                                                    message_id=source_msg)
                        copied = True
                    except Exception as copy_error:  # noqa: BLE001
                        log.warning("dump copy failed for item %s: %s", item.get("id"), copy_error)
                note = details if copied else "📎 <b>Uploaded file record</b>\n" + details
                try:
                    await self.bot.send_message(dump_channel, note, parse_mode="HTML",
                                                disable_web_page_preview=True)
                except Exception as dump_error:  # noqa: BLE001
                    log.warning("dump channel record failed for item %s: %s", item.get("id"), dump_error)
        except Exception as e:  # noqa: BLE001
            log.warning("upload audit/dump channel failed (upload remains successful): %s", e)

    async def _process_item(self, job: dict, item: dict, totals: dict, base_done: int,
                            chat_id: int, msg_id: Optional[int]) -> None:
        job_id = job["id"]
        user_id = job["user_id"]
        tg = item["tg"]
        size = int(tg.get("size") or 0)
        items = await repo.items_of(self.db, job_id)
        count = len(items)
        index = item["seq_index"]

        state = ProgressState(job_id=job_id, item_id=item["id"],
                              name=item["final_name"] or tg.get("name", ""),
                              folder=(job.get("destination") or {}).get("path", "My Drive"),
                              index=index, count=count, total=size,
                              overall_bytes_done=base_done, overall_bytes_total=totals["bytes"],
                              cancel=self._cancel_flags.setdefault(job_id, asyncio.Event()))
        self.hub.put(state)
        if tg.get("source_path"):
            state.on_tg(size, size)
            self.hub.put(state)
        # The card used to change only at the start and end of a file, so on a
        # small file the user saw one frozen frame. This redraws it while the
        # bytes move — see core/animate.py.
        anim = None
        if msg_id:
            from .animate import start_for
            anim = start_for(self, state, chat_id, msg_id)
        await repo.set_item(self.db, item["id"], state="downloading", started_at=time.time())

        cfg = self.cfg
        mode = cfg.upload_mode
        use_disk = bool(tg.get("source_path")) or mode == "disk" or (mode == "auto" and size > 0 and size < 32 * 1024 ** 2)

        session = item.get("drive") or {}
        drive_info: dict = {}

        # A link job carries its source in the item instead of a Telegram file.
        if (tg.get("url") or "").strip():
            try:
                drive_info = await upload_from_url(self, job, item, state)
            finally:
                if anim:
                    state.finished = True
                    await anim.stop()
            await repo.set_item(self.db, item["id"], state="done", finished_at=time.time(),
                                bytes_done=state.total, drive=drive_info, error="")
            await repo.add_quota(self.db, user_id, state.total, 1)
            await self._record_uploaded(job, item, drive_info, state.total)
            return

        for attempt in range(1, 4):
            try:
                if use_disk:
                    path = await self._download_to_disk(item, state)
                    drive_info = await self._upload_from_disk(job, item, path, state)
                else:
                    drive_info = await self._upload_streamed(job, item, state)
                break
            except SessionGone:
                if attempt >= 3:
                    raise
                log.info("session gone for %s — new session", item["id"])
                await repo.set_item(self.db, item["id"], drive={})
            except NetworkError:
                if attempt >= 3:
                    raise
                state.note_retry()
                if self.fg:
                    pass
                await asyncio.sleep(min(20, 2 ** attempt))
                st = await repo.get_item(self.db, item["id"])
                session = (st or {}).get("drive") or {}
                log.info("retrying %s (attempt %s) from %s", item["id"], attempt, session.get("committed"))
            except StorageQuota:
                raise
            except BaseException:
                if anim:
                    state.finished = True
                    await anim.stop()
                raise
        else:
            raise DriveError("upload did not complete")

        if anim:
            state.stage = "done"
            state.finished = True
            await anim.final()
            await anim.stop()
        await repo.set_item(self.db, item["id"], state="done", finished_at=time.time(),
                            bytes_done=size, drive=drive_info, error="")
        await repo.add_quota(self.db, user_id, size, 1)
        await self._record_uploaded(job, item, drive_info, size)
        source_path = (item.get("tg") or {}).get("source_path")
        if source_path:
            Path(source_path).unlink(missing_ok=True)
        fresh = await repo.recompute_totals(self.db, job_id)
        if msg_id:
            from ..tg.ratelimit import safe_edit
            text = progress_card(index=index, count=count, name=item["final_name"],
                                 folder=state.folder, tg_pct=100, drive_pct=100,
                                 done=size, total=size, speed=state.speed,
                                 elapsed=state.elapsed, eta=0, retries=state.retries,
                                 chunk_mb=cfg.chunk_bytes // 1024 // 1024,
                                 overall_pct=min(100, fresh["bytes_done"] * 100.0 / max(1, fresh["bytes"])),
                                 style=cfg.progress_style)
            await safe_edit(self.bot, chat_id, msg_id, text, gate=self.fg, pct=100, force=True)

    def _checkpoint(self, item_id: str, raw, holder: dict, state: ProgressState | None = None):
        """Persist the resumable session URI + committed offset as it moves.

        Without this a restart has nothing to resume from — which is precisely
        the acceptance test "kill at 50 %, restart, it continues".
        """
        async def cb(done: int, total: int) -> None:
            if raw is not None:
                out = raw(done, total)
                if asyncio.iscoroutine(out):
                    await out
            holder["committed"] = int(done)
            try:
                await repo.set_item(self.db, item_id, bytes_done=int(done),
                                    drive={"session_uri": holder.get("uri", ""),
                                           "committed": int(done)})
            except Exception as e:                        # noqa: BLE001
                log.debug("checkpoint failed: %s", e)
            if state is not None:
                self.hub.put(state)
        return cb

    # ── byte paths ───────────────────────────────────────────────────────
    async def _download_to_disk(self, item: dict, state: ProgressState) -> Path:
        tg = item["tg"]
        source_path = tg.get("source_path")
        if source_path:
            path = Path(source_path).resolve()
            expected = int(tg.get("size") or 0)
            if not path.is_file() or path.stat().st_size != expected:
                raise DriveError("The staged browser upload is missing or incomplete. Remove it and upload again.")
            return path
        os.makedirs(self.cfg.download_dir, exist_ok=True)
        safe = "".join(c for c in (tg.get("name") or "file") if c.isalnum() or c in "._- ")[:80]
        path = Path(self.cfg.download_dir) / f"{item['id']}_{safe}"
        if path.exists() and path.stat().st_size == int(tg.get("size") or 0):
            return path
        # Publish every byte checkpoint to both Telegram animation and Studio SSE.
        state.stage = "downloading"
        async def report(done: int, total: int) -> None:
            state.on_tg(done, total)
            self.hub.put(state)
        await tg_download.download_to_file(
            self.bot, tg["file_id"], path,
            progress=report, should_stop=state.want_stop, timeout=3600)
        if state.want_stop():
            path.unlink(missing_ok=True)
            raise Cancelled()
        return path

    async def _folder_for(self, job: dict, item: dict, client) -> dict:
        """Where this item goes — the job folder, or a per-group sub-folder."""
        dst = (job.get("destination") or {})
        root = dst.get("folder_id") or "root"
        split = dst.get("split") or {}
        if not split or (split.get("mode") or "none") in ("none", "off", ""):
            return {"folder_id": root, "path": ""}
        key = job["id"]
        cache = self._folders.get(key)
        if cache is None:
            cache = self._folders[key] = {}
        chain = (split.get("plan") or {}).get(item["id"]) or []
        if not chain:
            # the job was created before the split was saved, or the Studio
            # changed it — compute on the fly (cheap, and deterministic)
            chain = groups.plan(dst, [item]).get(item["id"]) or []
        ck = "/".join(chain)
        if ck in cache:
            return cache[ck]
        resolver = self._resolvers.get(key)
        if resolver is None:
            resolver = self._resolvers[key] = groups.FolderResolver(client, root)
        resolved = await resolver.resolve(chain)
        cache[ck] = resolved
        return resolved

    async def _upload_from_disk(self, job: dict, item: dict, path: Path,
                                state: ProgressState) -> dict:
        naming = NamingOptions.from_dict(job.get("naming") or {})
        client = await self.accounts.client(job["user_id"])
        dst = await self._folder_for(job, item, client)
        await repo.set_item(self.db, item["id"], state="uploading")
        session = (item.get("drive") or {})
        holder = {"uri": session.get("session_uri", ""), "committed": 0}
        up = client.uploader(chunk=self.cfg.chunk_bytes,
                             on_progress=self._checkpoint(item["id"], state.on_drive, holder, state),
                             should_cancel=state.want_stop)
        if holder["uri"]:
            up.session_uri = holder["uri"]
            up.committed = int(session.get("committed", 0))
        meta = {"name": item["final_name"],
                "mimeType": item["tg"].get("mime") or "application/octet-stream",
                "appProperties": {"tg_file_unique_id": item["tg"].get("file_unique_id", ""),
                                  "job_id": job["id"]}}
        if dst.get("folder_id") not in (None, "", "root"):
            meta["parents"] = [dst["folder_id"]]
        await apply_thumb(self, meta, job["user_id"])

        async def _remember_uri() -> None:
            holder["uri"] = up.session_uri
        try:
            await up.initiate(meta, path.stat().st_size)
            await _remember_uri()
            info = await up.upload(path, meta, path.stat().st_size)
        finally:
            if not (item.get("tg") or {}).get("source_path"):
                path.unlink(missing_ok=True)
        drive = {"file_id": info.get("id", ""), "link": info.get("webViewLink", ""),
                 "session_uri": "", "committed": info.get("size", 0)}
        if not drive["link"] and drive["file_id"]:
            meta = await client.get(drive["file_id"])
            drive["link"] = meta.get("webViewLink", "")
        if job.get("share_mode") == "anyone" and drive["file_id"]:
            try:
                await client.share_anyone(drive["file_id"])
            except DriveError:
                pass
        return drive

    async def _upload_streamed(self, job: dict, item: dict, state: ProgressState) -> dict:
        """Telegram → bounded queue → Drive resumable, no full-file disk copy."""
        tg = item["tg"]
        size = int(tg.get("size") or 0)
        chunk = self.cfg.chunk_bytes
        queue: asyncio.Queue = asyncio.Queue(maxsize=2 * max(1, chunk // (1024 * 1024)))

        done = 0
        error: list[BaseException] = []

        async def pump() -> None:
            nonlocal done
            try:
                async for piece in tg_download.stream_file_id(
                        self.bot, tg["file_id"], should_stop=state.want_stop,
                        timeout=3600):
                    done += len(piece)
                    await _fire(state.on_tg, min(done, size or done), size or done)
                    self.hub.put(state)
                    await _put(queue, piece)
                await _put(queue, None)
            except BaseException as e:               # noqa: BLE001
                error.append(e)
                await _put(queue, None)

        async def gen():
            while True:
                piece = await queue.get()
                if piece is None:
                    return
                yield piece

        pump_task = asyncio.create_task(pump())
        client = await self.accounts.client(job["user_id"])
        dst = await self._folder_for(job, item, client)
        await repo.set_item(self.db, item["id"], state="uploading")
        session = (item.get("drive") or {})
        holder = {"uri": session.get("session_uri", ""), "committed": 0}
        up = client.uploader(chunk=chunk,
                             on_progress=self._checkpoint(item["id"], state.on_drive, holder, state),
                             should_cancel=state.want_stop)
        if holder["uri"]:
            up.session_uri = holder["uri"]
            up.committed = int(session.get("committed", 0))
        meta = {"name": item["final_name"],
                "mimeType": tg.get("mime") or "application/octet-stream",
                "appProperties": {"tg_file_unique_id": tg.get("file_unique_id", ""),
                                  "job_id": job["id"]}}
        if dst.get("folder_id") not in (None, "", "root"):
            meta["parents"] = [dst["folder_id"]]
        await apply_thumb(self, meta, job["user_id"])
        try:
            if not up.session_uri:
                await up.initiate(meta, size)
                holder["uri"] = up.session_uri
            if not size:
                raise NetworkError("telegram reported a zero-byte file")
            info = await up.upload(gen(), meta, size)
        finally:
            pump_task.cancel()
            try:
                await pump_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        if error:
            raise NetworkError(f"telegram download failed: {error[0]}")
        if state.want_stop():
            raise Cancelled()
        drive = {"file_id": info.get("id", ""), "link": info.get("webViewLink", ""),
                 "session_uri": "", "committed": info.get("size", size)}
        if job.get("share_mode") == "anyone" and drive["file_id"]:
            try:
                await client.share_anyone(drive["file_id"])
            except DriveError:
                pass
        return drive

    # ── notifications ────────────────────────────────────────────────────
    async def _pause_with_reason(self, job_id: str, item: dict, chat_id,
                                 err: Exception, done: int) -> None:
        """Park the job and explain — see `pause_reason.py` for the words."""
        await repo.set_item(self.db, item["id"], state="pending", error=str(err))
        await repo.update_job(self.db, job_id, status="paused")
        await self._say(chat_id, pause_message(err, done))
        self.hub.finish(job_id)

    async def _say(self, chat_id: int, text: str) -> None:
        if not chat_id:
            return
        try:
            await self.bot.send_message(chat_id, text, parse_mode="HTML",
                                        disable_web_page_preview=True)
        except Exception as e:                            # noqa: BLE001
            log.debug("notify failed: %s", e)

    async def notify_job_card(self, job: dict) -> Optional[int]:
        """Post (or update) the live card for a job."""
        sm = job.get("status_message") or {}
        if sm.get("message_id") and sm.get("chat_id"):
            return sm["message_id"]
        return None
