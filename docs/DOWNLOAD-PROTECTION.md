# Download protection

Every new file uploaded by the worker (Telegram, Studio/browser, or URL) is followed by a Drive API update setting `downloadRestrictions.itemDownloadRestriction.restrictedForWriters` and `restrictedForReaders` to true. The response is checked; the job item is not marked successful if the restriction could not be verified. Its Drive ID is saved before the update so a protection retry can use the existing file instead of uploading it again. Link sharing requested by the job is applied only after the protection update. Existing inherited folder sharing can apply as soon as the file is created, before protection succeeds; this is not an atomic privacy guarantee.

## Existing folder

In a private Telegram chat, connect your Google account and send `/protect FOLDER_LINK`, or open My Drive > Protect Folder Files for instructions. The bot scans files under that folder and subfolders, then asks for confirmation. No files are changed before confirmation. The preview is valid for ten minutes and contains a snapshot of up to 2,000 items including folders and shortcuts. Shortcuts are skipped rather than followed. Only files present in the snapshot are processed; future files are not automatically covered by this command. Each file is updated separately. The final message counts successes and failures, with up to ten failed file names and IDs. A restart during processing can leave a partial result; run the command again to reapply.

The command does not remove sharing or turn link sharing off. Google account permission, OAuth scope, administrator rules and Drive file type determine whether a file can be changed. Existing Drive files may require full Drive OAuth scope and reauthorization; full scope is sensitive/restricted and may require Google verification. A Drive folder link cannot grant access the connected account does not already have. Resource-key-only links, inaccessible items, Shared Drive policies and shortcuts may require separate handling.

Restrictions are not DRM. They don't stop screenshots, recording, previously downloaded copies, or actions by the owner or privileged users. The uploader cannot guarantee protection against all users or third-party applications. Review failures, and inspect Google Drive's sharing and restriction settings for important files.

`/copy` preserves the original file name by explicitly providing it to the Drive copy API; it does not remove an existing prefix already present in the source name. Copying does not itself apply this project's upload protection; use `/protect` on the destination folder if required.

Reference: https://developers.google.com/workspace/drive/api/guides/content-restrictions
