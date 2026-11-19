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

## Per-user channel automation

In a private chat with the bot, open **Settings → Channel automation → Set channel ID**.
Send the numeric channel ID (for example `-100...`). First add the bot as a
channel administrator. The requesting user must also be an administrator of that
channel. The bot verifies both using Telegram before linking; the channel must
be a Telegram channel, not a group. One channel can be linked to only one account
at a time, and each user can link one channel. An open batch must be finished
before switching or disconnecting. The ID alone does not grant access.

Use a private channel with trusted posters: all supported posts in your channel
are collected into **your** account, including posts made by other channel
administrators. Telegram does not reliably identify a channel post's human author.
The bot rechecks administrator status for each new post and fails closed if
Telegram cannot verify it. Verification failures are not queued; the bot warns
the user, and `/channel_status` lists recent affected post IDs. Restore access
and repost missing files. Transient Telegram outages can also cause verification
failures. Per-post verification can slow large collections or hit Telegram rate
limits; check counts before finishing. The failed-verification history is an
all-time diagnostic, not a live unresolved count.

1. Post or forward supported media to your connected channel. Only new channel
   posts the bot receives are collected. Albums are individual files. Nothing
   uploads automatically. The existing plan, quota, and effective Telegram
   per-file limit apply; rejected posts are reported. The cloud Bot API normally
   supports bot downloads only up to 20 MB; larger files require a configured
   local Bot API and remain subject to plan and infrastructure limits.
2. Open automation settings and tap **Batch status**, or send `/channel_status`.
   Check accepted count and rejected/verification-failure IDs. The current
   status has the latest 10 rejection details. Forward rejected files again as
   new posts after resolving the cause. If only rejected files created an empty
   batch, use **Discard empty batch** to reset it. If the batch is full, finish
   the accepted files and post remaining files in a new batch.
3. Tap **Finish batch** or send `/finish`. Wait until every expected post has
   arrived before finishing, especially for albums or after downtime. Then open
   the signed Studio link, review item order and folder-count assignments, and
   explicitly start the upload. Items are processed one by one by the existing
   worker. While collection is open, Studio editing and start are blocked to
   avoid changing the order while new posts arrive. The next post after finishing
   opens another batch; there is no idle-timeout finish.

Existing `COLLECT_CHANNEL_ID` installations remain supported as a fallback for
`OWNER_ID` when that owner has not linked a channel in the automation settings.
That fallback is trusted configuration, not a per-user binding, and can be
removed only in the service environment. Do not configure the same channel for
other users. A new per-user channel binding takes precedence for the owner;
removing that binding re-enables the fallback if the environment value remains.
Existing drafts remain with their original owner and cannot be claimed by a
new user. Uploaded-file logging and dump-channel behavior remain unchanged.

Run a single app/poller instance per bot with persistent SQLite storage. Telegram
retains pending updates only for a limited time; downtime, deleted posts, API
errors, storage failures, and channel permission changes can cause missing
posts. Channel collection is not an archive and does not import older posts.
Review counts and routing before starting. No credentials or real channel IDs
are included in this package. No deployment is performed by updating this file.

## Finding the Studio dashboard

Open `/studio` after signing in, or use a fresh signed Studio link from the bot.
The signed-in landing page is your private dashboard; use the **Dashboard** link
in the Studio header to return there from a particular job. A job-specific bot
link still opens that job directly so its review and progress are not lost.

The overview counts completed items and bytes in your own account. Today's usage
is tracked separately by the service, while Google storage is requested from
Google and may be unavailable if Google does not report it. The quick-upload
panel stages selected files using the existing browser batch flow; it does not
start the Drive transfer. Choose the destination and folder-count assignments,
check naming and order, then explicitly start. The dashboard's recent transfers
and history use the signed-in account's jobs. A disconnected account can still
see its dashboard and reconnect Google Drive from there.

The downloadable project does not change your live site. Deploy the updated
files to your existing service before expecting the new dashboard to appear.
