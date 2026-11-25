# Place channel files into existing Drive folders

1. Send files to your configured automation channel, finish the batch explicitly in the bot, and open the job in Studio.
2. On the Folder step, select **Build a manual folder tree**.
3. In the Drive browser above the tree, tap **My Drive** or **Shared with me**, and tap the existing folder you want. Open folders to browse inside them.
4. Tap **Add selected Drive folder to tree**. The folder appears as an existing destination. Select it in the tree, check the channel files below, and tap **Move selected here**. This is a draft assignment; it does not move or rename the existing folder.
5. Repeat for as many existing folders as needed, then save the tree or tap Next. Review each file's destination before starting. You can reassign files. New planned subfolders under the original tree parent are still optional. To create a real subfolder inside an existing Drive folder, select it in the browser and use **New folder**, then add the created folder to the tree.

The tree parent selected before entering tree mode remains fixed when browsing other folders. The existing folder identities use Drive IDs, not just names. Up to 100 existing destinations and 200 planned folders are supported; existing plan and quota limits still apply. A folder can be removed from the organizer after moving its assigned files. This only removes the reference, never the actual Google folder. Telegram file captions are not altered.

The server validates each existing destination using the signed-in account's Drive credentials and checks Google's canAddChildren capability when provided. Permissions can change after saving. Shared with me folders require Editor permission and visibility within the app's Google OAuth grant. `drive.file` may not expose a previously shared folder. See SHARED-WITH-ME.md for deployment options and restricted-scope cautions. This feature routes files already collected by the uploader; it does not copy existing Drive files.
