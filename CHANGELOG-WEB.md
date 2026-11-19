# Web experience update — 2026-11-18

- Kept the public homepage at `/` and private Studio at `/studio`, preserving the existing visual identity and same-domain route structure.
- Repointed homepage CTAs to the Studio and corrected the Studio boot state to describe browser/session startup rather than Telegram connection.
- Added session-scoped browser file intake at `POST /api/uploads`, with current-user job ownership checks, configured per-file/job/daily limits, staged files, and handoff to the existing Drive worker.
- Added a private `/api/jobs` history endpoint and a Studio "Past uploads" dialog; users only receive their own job summaries. Cross-user tests cover read, patch, cancel, export, and attempt to append browser files to another user's draft.
- Added progress context distinguishing Browser → Server from Telegram → Server before the common Server → Drive stage.
- Added web UI screenshots in `screenshots/`. They use a clearly synthetic demo account/state in a local mock harness; no user data or supplied photos were available.
- Updated `requirements.txt` with `python-multipart`, required for browser file intake, and documented the `DOWNLOAD_DIR/studio` staging/persistent disk requirement.
- Verified with `PYTHONPATH=. pytest -q`: **318 passed**. Web/API tests: **45 passed**. JavaScript syntax checks and Python compile checks passed.
- Captured desktop screenshots using a local mock API and synthetic demo state. No customer data was used. No supplied photos/assets were present in the repository.
- Telegram bot UI was not modified in this web pass.
