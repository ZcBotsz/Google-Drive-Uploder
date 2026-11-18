# Render: enable Telegram’s local Bot API for large files

The bot currently reports Telegram cloud and a 20 MB limit. That means the running Render service is not using the local API yet. API ID/hash alone do not change the transport. Update the Render service configuration and redeploy, then verify with `/system`.

## What the limits mean

- Telegram cloud Bot API downloads are limited to 20 MB.
- A self-hosted local Bot API can support files up to 2000 MB, subject to your plan limit, service resources, and configured `MAX_FILE_MB`.
- The 10 GB Render disks are persistent storage for database/cache; they do not set the maximum file size. In `UPLOAD_MODE=auto`, larger files stream rather than being fully staged on disk.

## Blueprint setup

This project’s `render.yaml` defines a private `driveup-tg-api` service and the `driveup` web/bot service. Keep both in the same Render region.

1. In Render, open **New → Blueprint**, select the repository containing this project’s `render.yaml`, and apply it.
2. Set `TELEGRAM_API_ID` and `TELEGRAM_API_HASH` on the `driveup-tg-api` service using values from <https://my.telegram.org> → API development tools.
3. Set `API_ID` and `API_HASH` on the `driveup` service to the same values.
4. Keep local API options `TELEGRAM_LOCAL=1`, `FILE_SERVER=1`, and `PORT=8081` as defined in the blueprint.
5. Ensure the bot service has `TELEGRAM_API_URL` and `TELEGRAM_FILES_URL` linked to the private API service using the blueprint’s `fromService` references. They must not point to `https://api.telegram.org`.
6. Deploy both services and wait for them to become healthy. If switching an already-running bot from cloud to a local API, follow Telegram’s current local Bot API migration guidance about logging the bot out of the cloud API before starting it locally; do not publish your bot token in a URL, log, or chat.

The blueprint’s shared `hostport` value uses API port 8081; this project maps file downloads to the local file-server port 8080. For manual environment settings on the bot Web Service, use the **matching private API service** and set `TELEGRAM_API_URL=http://PRIVATE-HOST:8081` and `TELEGRAM_FILES_URL=http://PRIVATE-HOST:8080`. Never point `TELEGRAM_API_URL` at 8080: that is nginx/file-server, not the Bot API. The file-download code strips the API server's work-directory prefix before requesting the file-server path.

## Manual / renamed service configuration

If the API service was created manually or renamed, open its Render **Connect** panel and use its private hostname. On the `driveup` service, set both variables to the private API endpoint with port 8081, for example:

```text
TELEGRAM_API_URL=http://PRIVATE-HOST:8081
TELEGRAM_FILES_URL=http://PRIVATE-HOST:8081
```

The application routes file-server requests to port 8080. Ensure both services are in the same region, the API service is running, and `FILE_SERVER=1` is enabled. If using `render.yaml`, update both `fromService.name` values to the exact local API service name.

## Verify after redeploy

Send `/system` to the bot as the owner. Success should show **Bot API: self-hosted (local)** and **per-file cap: 2000 MB** (wording may vary slightly by version). If it still says `Telegram cloud`, inspect the deployed `TELEGRAM_API_URL` and Render service references: the API ID/hash do not switch transport.

If `/system` reports local but a specific user is still limited to 20 MB, the user’s plan may retain a 20 MB `max_file_mb`. Open website Admin → Plans and raise that plan’s file limit (up to 2000 MB). Check `MAX_FILE_MB` as well. Do not advertise a 2 GB cap until `/system` confirms local mode and the effective plan/config allows it.

## Troubleshooting

- **Still cloud / 20 MB:** fix `TELEGRAM_API_URL`; ensure the API service is running and the correct private host and port are referenced.
- **Bot cannot connect:** check same-region placement, Render private networking, API service logs, and whether `http://` is included in manually entered URLs.
- **Download fails although API calls work:** confirm `FILE_SERVER=1`, port 8080 is available internally, and `TELEGRAM_FILES_URL` points at the API service. This project handles Render’s shared 8081/8080 port mapping.
- **Plan-specific 20 MB error:** increase the affected plan’s `max_file_mb` in Admin → Plans; transport cap and plan cap are separate.

Keep API credentials and bot token in Render secret environment variables. Never commit them to the repository or paste them into support chats.
