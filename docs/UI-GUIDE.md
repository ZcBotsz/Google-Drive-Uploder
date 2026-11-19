# ZC Drive Upload — UI Guide

## Product principles
- Mobile-first, English-language bot controls; each menu screen has a clear purpose and a route home.
- Existing Studio and protected Admin remain available. Admin actions require authorization in the handler layer, not merely a hidden button.
- Keep user file captions intact. UI decoration applies only to product-authored captions/text.
- Google quota is described only using fields reported by the connected Drive API; Gmail and Photos are not itemized.
- Max is ₹499/month with technical limits shown transparently. It is not unlimited storage.

## Telegram bot
`/start`, `/hello`, and `/menu` show the state-aware home menu. Disconnected accounts are offered Google Drive connect; connected accounts get upload/Studio, Drive, settings, plan, help and More tools. Each focused panel edits the menu message in place. Home/back controls are provided on submenus. Upload draft cards expose destination, naming and order controls; discard asks for confirmation. Google sign-in may open an external browser, then returns to the Drive-connected flow.

Button styling is chosen by action type: green/success for connecting or continuing, primary for normal navigation, and destructive for disconnect/discard/remove. Telegram clients may render button colors only when Bot API styles are supported. Animated emoji are separately feature-gated and only displayed where Telegram supports custom emoji; static emoji remain a fallback.

## Studio and admin
The primary `/` route opens `/studio`; signed-out visitors see the existing Google sign-in action. After sign-in, `/studio` without a job opens the private dashboard (recent jobs, past uploads, and a separate **Start an upload** action). Selecting a job opens its existing review/progress flow. Public product/plan information remains available at `/home`. The Studio itself does not add a password prompt. After sign-in, users can choose files from their browser, review folder/naming/order options, start the existing Drive worker, follow live progress, and open their own past uploads. Large selections are sent in sequential 20-file requests and combined into one job; the configured plan still controls the total files/job and per-file size. Browser files are staged under `DOWNLOAD_DIR/studio`; persistent disk is required for safe retries/recovery across restarts. For manual folder routing, choose “Assign consecutive file batches to folders,” enter unique child folder names and file counts, and make the counts add up to the entire job. The backend stores an explicit item-ID-to-folder plan; the worker creates or reuses those folders under the selected parent. The Studio sends the verified-session user identity to server-side ownership checks; browser-provided user IDs are not used. Admin remains protected by the existing authorization mechanisms.

## Accessibility and QA
Use readable labels, meaningful status text, keyboard focus visibility, responsive layout, and progress descriptions rather than box-drawing glyphs. Studio progress receives worker chunk checkpoints over SSE, with fresh database totals as a polling fallback; file intake has its own accessible transfer progress bar. Studio history is fetched from a session-scoped API and contains only the signed-in user's jobs. Test narrow/mobile widths, keyboard navigation, connected/disconnected states, invalid links, expired sessions, empty history, upload limits, staged-file cleanup, and unauthorized/cross-user job access.

## Upload limits
Displayed limits must be sourced from configured plans and Telegram API/deployment configuration. Actual Drive quota, Google API limits, local Bot API availability, and deployment settings also apply. API ID/hash alone do not enable a local Bot API server or larger transfers.
