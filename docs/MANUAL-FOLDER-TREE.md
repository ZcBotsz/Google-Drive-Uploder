# Manual folder tree for channel batches

1. Connect your own automation channel in the uploader bot and send or forward files into it. The collector keeps the batch open until you explicitly finish it in the bot. Open its Studio review link.
2. In Files, check the collected files. On the Folder step, select the existing Drive folder that will contain your new tree. Select **Build a manual folder tree**.
3. Select **Parent folder** or a folder in the tree. Tap **+ Folder** to add a folder inside it. Repeat to add subfolders. You can rename a planned folder, move it (choose a destination in the dropdown), or delete an empty planned folder. Deleting does not delete anything in Google Drive.
4. Check one or more files and tap **Move selected here**. Filter or select visible files to work with larger batches. Each file displays its planned folder. All files initially belong to the selected parent folder; selecting a new parent changes where that entire tree will be built.
5. **Save tree draft** saves the plan for this job without starting it. Moving to the Naming step also saves the tree. Changes after saving are not saved until you save again or proceed. Review the tree and start when ready.

Folders, including empty folders, are created when the worker starts, not while planning. Existing matching folders under the chosen parent are reused. If Google Drive contains multiple folders with the same name in a parent, Drive may choose one; use a unique name if exact selection matters. This is a manual organizer: it does not automatically interpret extractor messages, and it cannot recover files not successfully collected. The original Telegram captions are not changed by the organizer.

The plan supports up to 200 folders and 12 nested levels. Existing plan, quota, channel, and Telegram limits still apply. The chosen parent must be writable and visible under the user's connected Drive permissions; `drive.file` does not automatically expose all existing folders. Upload failures can occur if permissions or quota change after review. The saved plan is per-job and tied to job item IDs, so changing upload order does not change file destinations.
