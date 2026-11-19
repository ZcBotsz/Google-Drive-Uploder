# Web experience update — 2026-11-18

- Made `/` open the Studio at `/studio`, as requested; kept the public product/plan page at `/home`. The workspace remains same-domain and account-protected.
- Repointed homepage CTAs to the Studio and corrected the Studio boot state to describe browser/session startup rather than Telegram connection.
- Added session-scoped browser file intake at `POST /api/uploads`, with current-user job ownership checks, configured per-file/job/daily limits, staged files, and handoff to the existing Drive worker.
- Added a private `/api/jobs` history endpoint and a Studio "Past uploads" dialog; users only receive their own job summaries. Cross-user tests cover read, patch, cancel, export, and attempt to append browser files to another user's draft.
- Browser selections are chunked into 20-file requests and combined into one draft job. Verified a 600-file synthetic browser selection produced 30 sequential requests and 600 review rows.
- Added manual folder batching: define child folder names and counts (e.g. 1 / 9 / 5 / 5), validate the exact total, preview file ranges, and persist explicit per-file routes into those folders.
- Fixed Studio progress by publishing download/upload chunk checkpoints to ProgressHub/SSE, recomputing fresh totals for polling fallback, and resuming the live panel when reopening a job.
- Added progress context distinguishing Browser → Server from Telegram → Server before the common Server → Drive stage.
- Added web UI screenshots in `screenshots/`. They use a clearly synthetic demo account/state in a local mock harness; no user data or supplied photos were available.
- Updated `requirements.txt` with `python-multipart`, required for browser file intake, and documented the `DOWNLOAD_DIR/studio` staging/persistent disk requirement.
- Verified with `PYTHONPATH=. pytest -q`: **321 passed**. Web/API tests: **48 passed**. JavaScript syntax checks and Python compile checks passed.
- Captured desktop screenshots using a local mock API and synthetic demo state. No customer data was used. No supplied photos/assets were present in the repository.
- Telegram bot UI was not modified in this web pass.
