# Live transfer progress

A Telegram file typically has two hops: Telegram → the server, then the server → Google Drive. In disk mode the first hop completes before the second begins. In streaming mode both hops can overlap; the server buffers pieces and Google Drive confirms uploads in chunks. With the default `auto` mode, Telegram files smaller than 32 MiB use a temporary disk file, while larger files stream. Browser files are already staged on the server before the Drive transfer.

The bot and Studio show each hop separately. Overall progress counts bytes uploaded/committed to Drive, not bytes merely downloaded from Telegram. ETA and speed are estimates for the active stage, and become unavailable when there are no recent samples. Telegram messages are throttled to avoid rate limits; Studio receives progress events and periodic snapshots. A stalled percentage can mean the next chunk is still transferring or waiting for confirmation, not necessarily that it has completed.

Manual tree folders are prepared before file transfer. Pause is requested between files: the current file may finish first. At completion the bot edits the existing status message where possible; if it cannot edit, it sends one fallback summary. The existing channel audit and dump messages are separate and remain enabled when configured.

Actual transfer time depends on Telegram and Google responses, file size, server resources, network speed, upload mode and chunk size. These display changes do not guarantee a faster transfer. Telegram's cloud Bot API and plan/deployment file limits still apply; a local Bot API server may be required for larger files.
