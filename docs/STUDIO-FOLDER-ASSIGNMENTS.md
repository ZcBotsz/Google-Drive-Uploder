# Studio folder assignments

In a transfer, open the Folder step and choose **Choose files for each folder**. Check any files, navigate to or create a Drive folder, and choose **Assign selected to this folder**. Repeat for each destination. You can filter file names, select all visible files, and reassign a file. Every file must have a destination before continuing. Review lists each file and its folder. Assignments are tied to file IDs, so changing the sequence does not move files between folders. Up to 100 distinct destination folders may be used per job; existing plan and file limits still apply.

The folder browser loads one folder at a time, with a Load more button when Drive returns additional pages. Search results are limited by the existing Drive search endpoint. With Google's `drive.file` scope, not every pre-existing folder is necessarily accessible to the app. The server checks each assigned folder with the signed-in user's Drive account when saving the job; later access changes or quota issues can still cause uploads to fail. Folders with the same name are separate destinations because their Drive IDs differ.

Selections are kept while navigating the wizard and saved with the job when the upload is started. If the page is reloaded before starting, unsaved selections must be made again. No changes to Google Cloud or Render are made by this package.
