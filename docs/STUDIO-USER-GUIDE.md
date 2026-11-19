# ZC Drive Upload — Studio workspace and large-batch guide

This guide describes the current code in the final workspace ZIP. It does not mean the Render service has been deployed or configured.

## What Studio is

Studio opens to a private workspace dashboard after sign-in. It shows Drive connection, account plan/usage, recent transfers, and history. **Start an upload** opens the existing upload wizard; the wizard is an action inside the workspace, not the whole landing page. A signed job link can open that particular job's review/progress screen.

Google sign-in creates the website session. The OAuth return now redirects to `/studio?connected=1`; Studio confirms the connected account and shows the workspace. Users are authorized against their own session and job ownership. Do not share signed `/start?t=...` links; they are temporary login links.

## Browser batch upload: 500–600 files

1. Sign into Studio and ensure the intended Google Drive account is connected.
2. Choose **Start an upload** → **Choose files** and select the files in one selection (for example, 600).
3. The browser checks configured per-job, per-file, and remaining daily limits before intake. The server independently checks the authenticated user's plan and quota.
4. Studio sends the selected files in **sequential groups of 20**. Thus 500 files means 25 intake requests; 600 means 30. It combines them into one draft job, reports transfer progress, and keeps the partial draft if a later request fails. The user can then select only the missing files to continue; do not reselect ones already accepted.
5. Review the file list. In **Upload sequence**, choose Telegram/selection order, natural name order, size/date order, or manual drag order. Strict mode is the default; the worker processes one file after another in the stored sequence within the job.
6. Review destination, naming, folder assignment, and the final review, then start the job. Progress is available in Studio and in the existing bot status flow.

The HTTP intake endpoint caps each request at 100 files; the browser deliberately uses smaller chunks of 20 for safer retries and progress feedback. These are staging request sizes, **not** the Drive worker's file batch size and not a promise that every account can upload 600 files regardless of quota. Actual acceptance depends on the current plan, file sizes, daily allowance, Google Drive storage, Telegram/API limits, persistent storage, and service configuration. A synthetic test verified 600 files → 30 sequential requests → 600 review rows; it was not a live Drive upload.

## Route chosen files into folders by counts

In the wizard's destination/folder step:

1. Select the parent Drive folder.
2. Choose **By folder counts (manual)**.
3. Add one row per child folder. The folder names can be any names you choose; “Someshwar” is only an example.
4. Enter counts that add up to the full selection. Example for 20 selected files: `Someshwar = 5`, `Course = 9`, `Other = 6`. Example for 600: `Folder A = 200`, `Folder B = 150`, `Folder C = 150`, `Folder D = 100`.
5. Studio previews each group's file range in the current selected order. Counts must be positive, names unique, and the sum exactly equal the selected file count. Adjust sequence before final confirmation if specific files should go to a particular folder.
6. Review the assignments before starting. The worker creates/reuses the named child folders under the chosen parent and routes each selected item to its saved destination. Folder routing is per file, not merely a display label.

If you need a particular set of files in each folder, first arrange the upload sequence (manual drag or a suitable sort), then assign consecutive counts. Counts alone mean “the next N files in the current order”; they do not infer a folder from the file contents/name.

## Telegram bot uploads

The existing Telegram bot and its UI are preserved. Send files to the bot using its existing flow; it continues to create/queue jobs and use the existing worker. The bot's **Open Studio** link is for reviewing that job in the private web workspace. Browser intake's 20-file chunking applies to selecting files on the Studio website; it does not alter Telegram's album/message/file transport limits. The worker fetches and uploads pending items in sequence, one item at a time inside a job. It does not require users to press upload separately for every file.

## Limits, pauses, and recovery

- The server, not the browser, is authoritative for authentication, job ownership, plan count, per-file size, and daily quota.
- The shipped default Max plan is configured for up to 5,000 files/job and a 750 GB/day allowance; the effective per-file size cap depends on API/service configuration. Admin-assigned plans or service settings can change effective limits. Quotas do not create Google storage; Google account storage still applies.
- If a request fails mid-intake, the app reports how many files were accepted and leaves the draft available. Retry only unaccepted files.
- If Drive authorization expires or a quota/storage limit is reached during a job, the worker pauses with a reason; reconnect/resolve the limit and resume from the pending item rather than restarting the entire selection.
- Screenshots in `screenshots/` are synthetic mock data only.

## Deployment note

The ZIP is source code and docs. It does not change Render, Google OAuth redirect URIs/scopes, persistent disks, Telegram settings, or secrets. Deploy to the existing `Google-Drive-Uploader-1` service and verify the environment/Google callback settings before expecting the live site to change. Never put real secrets into the ZIP or screenshots.

## Optional owner-only Telegram channel batch collection

This is separate from sending files directly to the bot. Configure the **numeric**
`COLLECT_CHANNEL_ID` (for example `-100...`) and the correct numeric `OWNER_ID`
in the running service's environment, then restart/redeploy. Leave it empty to
disable collection. Add the bot as an administrator of that channel and start
it in a private chat with the owner account so it can send the owner messages.
Use a private channel where every poster is trusted: Telegram channel posts do
not reliably identify their human author, so **all supported posts to that ID
are assigned to the configured owner's account**, not to individual posters.
This is not a multi-user channel feature. Changing owners while a batch is open
is intentionally refused; finish it under the original owner first.

1. Post or forward supported media to the configured channel. Only new channel
   posts received while the bot is running can be collected; it cannot import
   historical posts automatically. Albums are collected as individual items.
2. The first accepted file creates a private draft and a bot card. It does not
   start uploading. Send `/channel_status` in your private bot chat for the
   current accepted count, recent rejected post IDs/reasons, and a fresh link.
   Verify the count and wait until all expected posts have arrived before
   finishing, especially after large albums or a period of bot downtime.
3. Tap **Finish batch** on the card or send `/finish` privately to the bot.
   Then open Studio, review order and folder assignments, and explicitly start.
   Files are processed one by one by the existing worker. The next new post
   starts a separate batch; there is no idle-timeout automatic finish.

The configured plan, remaining daily quota and effective Telegram per-file limit
still apply. Rejected posts are **not** uploaded; the owner is notified and can
see the latest 10 rejection details with `/channel_status`. Resolve the cause
and forward the file as a new post; if the batch is full, finish it first.
The Telegram cloud Bot API generally limits bot downloads to 20 MB; a properly
configured local Bot API is needed for larger files, subject to all other limits.
Redelivery of the same channel message is ignored, but intentionally posting the
same file twice in different messages preserves both. Captions are stored as
received. Existing upload logging and dump-channel behavior remain in effect.

Use a persistent SQLite disk and **one running app/poller instance per bot**.
Telegram can retain pending updates only for a limited time; downtime, Telegram
or network failures, deleted posts, and service/disk failures can still cause
missing files. Check `/channel_status` before finishing; this is not an archive
or a guarantee against missed posts. If the owner cannot be messaged, use the
command after starting the bot. No secrets or channel IDs should be committed.
