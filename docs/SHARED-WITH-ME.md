# Shared with me folders in Studio

In a transfer's Folder step, tap **Shared with me**. Studio lists the connected Google account's visible shared folders. Tap a folder to select it, or **Open** to browse its children. View-only folders are marked when Google reports that the account cannot add children. Choose the shared folder as the parent, then use one-folder upload or **Build a manual folder tree** as usual. The selected destination is shown separately from the folder you are browsing. Load more fetches additional results.

This uses the Google Drive `sharedWithMe` query with the signed-in user's existing credentials. It does not bypass Google account permissions, grant Editor access, or automatically expose every shared folder. It only lists folders directly in Google's Shared with me collection that the app's granted scope can see. To navigate nested folders, open an accessible shared parent. A folder shared only by link, a shortcut, or one not in that collection might not appear in the list.

If a shared folder is missing:
1. Confirm the uploader is connected to the exact Google account to which the owner granted access, and that the account can create files in the folder in Google Drive. Ask the owner for Editor access if needed.
2. The default `DRIVE_SCOPE_MODE=file` uses Google's narrower `drive.file` scope; being an Editor alone does not give the app access to every existing shared folder. This version does **not** implement Google Picker.
3. An operator can consider `DRIVE_SCOPE_MODE=full` on their deployment, restart/redeploy, then ask each affected user to reconnect using `/connect` and consent again. This requests broad access to Drive files accessible to that user. It applies to the deployment's future authorizations, not a single folder, and Google OAuth configuration, consent, publishing status and restricted-scope verification requirements may apply. Review the security and privacy implications before enabling it. A wider scope does not override the folder owner's permissions.
4. Reopen the batch and try **Shared with me** again. Search can also find folders accessible to the app. If still missing, check Workspace restrictions and the exact Google error; do not share account credentials or tokens.

No environment variable, Google Cloud setting, or deployed service is changed by this project archive.
