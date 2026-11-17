import os
import pickle
import json
import random
from os import path as ospath, listdir
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError
from google_auth_oauthlib.flow import InstalledAppFlow, Flow
from config import Config
from bot.core.logger import get_logger
from bot.helper.utils import get_id_from_url, get_mime_type
from tenacity import retry, wait_exponential, stop_after_attempt, retry_if_exception_type

LOGGER = get_logger(__name__)

G_DRIVE_DIR_MIME = "application/vnd.google-apps.folder"
G_DRIVE_FILE_LINK = "https://drive.google.com/file/d/{}/view?usp=sharing"
G_DRIVE_FOLDER_LINK = "https://drive.google.com/drive/folders/{}"
G_DRIVE_DOWNLOAD_LINK = "https://drive.google.com/uc?id={}&export=download"

class GoogleDriveHelper:
    def __init__(self, user_id=None, token_path=None, use_sa=None):
        self.user_id = user_id
        self.token_path = token_path or (f"{Config.TOKEN_DIR}/{user_id}.pickle" if user_id else "token.pickle")
        self.use_sa = use_sa if use_sa is not None else Config.USE_SERVICE_ACCOUNTS
        self.service = None
        self.sa_index = 0
        self.sa_count = 1
        self.sa_number = 0
        self._OAUTH_SCOPE = Config.OAUTH_SCOPES

    def authorize(self):
        """Authorize with SA, user token, or owner token - like WZML-X"""
        credentials = None
        if self.use_sa:
            sa_dir = Config.ACCOUNTS_DIR
            if not ospath.exists(sa_dir):
                raise ValueError(f"Service accounts dir not found: {sa_dir}")
            json_files = sorted(f for f in listdir(sa_dir) if f.endswith(".json"))
            self.sa_number = len(json_files)
            if self.sa_number == 0:
                raise ValueError("No service account files in accounts/")
            if self.service is None:
                self.sa_index = random.randrange(self.sa_number)
            elif self.sa_index >= self.sa_number:
                self.sa_index = 0
            LOGGER.info(f"Authorizing with SA {json_files[self.sa_index]}")
            credentials = service_account.Credentials.from_service_account_file(
                f"{sa_dir}/{json_files[self.sa_index]}", scopes=self._OAUTH_SCOPE
            )
        elif ospath.exists(self.token_path):
            LOGGER.info(f"Authorize with {self.token_path}")
            with open(self.token_path, "rb") as f:
                credentials = pickle.load(f)
            # Refresh if expired
            if credentials and credentials.expired and credentials.refresh_token:
                from google.auth.transport.requests import Request
                credentials.refresh(Request())
                with open(self.token_path, "wb") as f:
                    pickle.dump(credentials, f)
        elif ospath.exists("token.pickle"):
            LOGGER.info("Authorize with owner token.pickle")
            with open("token.pickle", "rb") as f:
                credentials = pickle.load(f)
        else:
            raise ValueError("No valid credentials. Run /connect to authorize your Google Drive.")

        if credentials is None:
            raise ValueError("No valid credentials found.")
        self.service = build("drive", "v3", credentials=credentials, cache_discovery=False)
        return self.service

    def switch_service_account(self):
        if self.sa_number == 0:
            return
        self.sa_index = (self.sa_index + 1) % self.sa_number
        self.sa_count += 1
        LOGGER.info(f"Switching SA to index {self.sa_index}")
        self.service = self.authorize()

    def _execute(self, build_request):
        while True:
            try:
                return build_request().execute()
            except HttpError as err:
                if not self._sa_quota_switch(err):
                    raise

    def _sa_quota_switch(self, err):
        if not self.use_sa or self.sa_count >= self.sa_number:
            return False
        try:
            content = err.content
            if isinstance(content, bytes):
                content = content.decode()
            reason = json.loads(content)["error"]["errors"][0]["reason"]
        except:
            return False
        if reason not in ("userRateLimitExceeded", "dailyLimitExceeded", "rateLimitExceeded"):
            return False
        self.switch_service_account()
        LOGGER.info(f"SA quota hit ({reason}), switched account")
        return True

    # --- OAuth Flow (per-user) ---
    def get_auth_url(self):
        """Generate OAuth URL for user to authorize"""
        if not ospath.exists(Config.CLIENT_SECRETS_FILE):
            # Try to build from env
            if Config.GOOGLE_CLIENT_ID and Config.GOOGLE_CLIENT_SECRET:
                client_config = {
                    "installed": {
                        "client_id": Config.GOOGLE_CLIENT_ID,
                        "client_secret": Config.GOOGLE_CLIENT_SECRET,
                        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                        "token_uri": "https://oauth2.googleapis.com/token",
                        "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
                        "redirect_uris": ["http://localhost"],
                    }
                }
                flow = Flow.from_client_config(client_config, scopes=self._OAUTH_SCOPE, redirect_uri="http://localhost")
            else:
                raise FileNotFoundError(f"client_secrets.json not found. Create it from Google Cloud Console or set GOOGLE_CLIENT_ID/SECRET in .env. See README.")
        else:
            flow = InstalledAppFlow.from_client_secrets_file(Config.CLIENT_SECRETS_FILE, scopes=self._OAUTH_SCOPE, redirect_uri="http://localhost")
        auth_url, _ = flow.authorization_url(access_type="offline", prompt="consent", include_granted_scopes="true")
        return auth_url, flow

    def exchange_code(self, flow, code: str):
        """Exchange auth code for credentials and save"""
        code = code.strip()
        # Handle full URL pasted
        if "code=" in code:
            try:
                from urllib.parse import urlparse, parse_qs
                qs = parse_qs(urlparse(code).query)
                if "code" in qs:
                    code = qs["code"][0]
            except:
                pass
        flow.fetch_token(code=code)
        creds = flow.credentials
        os.makedirs(os.path.dirname(self.token_path) or ".", exist_ok=True)
        with open(self.token_path, "wb") as f:
            pickle.dump(creds, f)
        return creds

    # --- Drive Operations ---
    @retry(wait=wait_exponential(multiplier=1, min=2, max=6), stop=stop_after_attempt(3), retry=retry_if_exception_type(Exception))
    def create_directory(self, name: str, parent_id: str = None):
        if parent_id in ("root", "", None):
            parent_id = None
        body = {"name": name, "mimeType": G_DRIVE_DIR_MIME, "description": Config.GD_DESCRIPTION}
        if parent_id:
            body["parents"] = [parent_id]
        result = self._execute(lambda: self.service.files().create(body=body, fields="id", supportsAllDrives=True))
        return result.get("id")

    @retry(wait=wait_exponential(multiplier=1, min=2, max=6), stop=stop_after_attempt(3), retry=retry_if_exception_type(Exception))
    def get_file_metadata(self, file_id: str):
        return self._execute(lambda: self.service.files().get(fileId=file_id, supportsAllDrives=True, fields="id,name,mimeType,size,parents,webViewLink,createdTime,modifiedTime"))

    @retry(wait=wait_exponential(multiplier=1, min=2, max=6), stop=stop_after_attempt(3), retry=retry_if_exception_type(Exception))
    def list_folders(self, parent_id: str = "root", query_extra=""):
        if parent_id in ("root", "", None):
            q = f"'root' in parents and mimeType = '{G_DRIVE_DIR_MIME}' and trashed = false {query_extra}"
        else:
            q = f"'{parent_id}' in parents and mimeType = '{G_DRIVE_DIR_MIME}' and trashed = false {query_extra}"
        page_token = None
        folders = []
        while True:
            resp = self._execute(lambda: self.service.files().list(
                supportsAllDrives=True, includeItemsFromAllDrives=True, q=q, spaces="drive",
                pageSize=100, fields="nextPageToken, files(id,name,mimeType,modifiedTime)", orderBy="name", pageToken=page_token
            ))
            folders.extend(resp.get("files", []))
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        return folders

    @retry(wait=wait_exponential(multiplier=1, min=2, max=6), stop=stop_after_attempt(3), retry=retry_if_exception_type(Exception))
    def list_files(self, parent_id: str = "root", page_size=50):
        if parent_id in ("root", "", None):
            q = "'root' in parents and trashed = false"
        else:
            q = f"'{parent_id}' in parents and trashed = false"
        resp = self._execute(lambda: self.service.files().list(
            supportsAllDrives=True, includeItemsFromAllDrives=True, q=q, spaces="drive",
            pageSize=page_size, fields="files(id,name,mimeType,size,modifiedTime,webViewLink)", orderBy="folder,name"
        ))
        return resp.get("files", [])

    @retry(wait=wait_exponential(multiplier=1, min=2, max=6), stop=stop_after_attempt(3), retry=retry_if_exception_type(Exception))
    def search_files(self, query: str):
        q = f"name contains '{query}' and trashed = false"
        resp = self._execute(lambda: self.service.files().list(
            supportsAllDrives=True, includeItemsFromAllDrives=True, q=q, spaces="drive",
            pageSize=50, fields="files(id,name,mimeType,size,modifiedTime,webViewLink)"
        ))
        return resp.get("files", [])

    @retry(wait=wait_exponential(multiplier=1, min=2, max=6), stop=stop_after_attempt(3), retry=retry_if_exception_type(Exception))
    def set_permission(self, file_id: str):
        if not Config.AUTO_SHARE:
            return
        body = {"role": "reader", "type": "anyone", "withLink": True}
        try:
            self._execute(lambda: self.service.permissions().create(fileId=file_id, body=body, supportsAllDrives=True))
        except Exception as e:
            LOGGER.warning(f"Set permission failed: {e}")

    def upload_file(self, file_path: str, file_name: str = None, parent_id: str = "root", progress_callback=None, is_cancelled=None):
        """Resumable upload with progress - core from WZML-X / MLTB"""
        if not ospath.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")
        file_name = file_name or ospath.basename(file_path)
        mime_type = get_mime_type(file_path)
        file_size = ospath.getsize(file_path)

        body = {"name": file_name, "description": Config.GD_DESCRIPTION, "mimeType": mime_type}
        if parent_id and parent_id != "root":
            body["parents"] = [parent_id]

        if file_size == 0:
            media_body = MediaFileUpload(file_path, mimetype=mime_type, resumable=False)
            resp = self._execute(lambda: self.service.files().create(body=body, media_body=media_body, fields="id,name,webViewLink", supportsAllDrives=True))
            self.set_permission(resp["id"])
            return resp

        media_body = MediaFileUpload(file_path, mimetype=mime_type, resumable=True, chunksize=100 * 1024 * 1024)
        request = self.service.files().create(body=body, media_body=media_body, fields="id,name,webViewLink", supportsAllDrives=True)
        response = None
        retries = 0
        while response is None:
            if is_cancelled and is_cancelled():
                raise ValueError("Upload cancelled by user")
            try:
                status, response = request.next_chunk()
                if status:
                    progress = (status.resumable_progress / status.total_size) * 100 if status.total_size else 0
                    if progress_callback:
                        progress_callback(progress, status.resumable_progress, status.total_size)
            except HttpError as err:
                if err.resp.status in [500, 502, 503, 504, 429] and retries < 10:
                    retries += 1
                    continue
                if self._sa_quota_switch(err):
                    # Retry with new SA - restart upload
                    return self.upload_file(file_path, file_name, parent_id, progress_callback, is_cancelled)
                raise
        self.set_permission(response["id"])
        return response

    def upload_dir(self, dir_path: str, parent_id: str = "root", progress_callback=None):
        """Upload entire folder recursively"""
        dir_name = ospath.basename(ospath.abspath(dir_path))
        new_folder_id = self.create_directory(dir_name, parent_id if parent_id != "root" else None)
        total_files = 0
        total_folders = 0
        for root, dirs, files in os.walk(dir_path):
            rel = ospath.relpath(root, dir_path)
            if rel == ".":
                current_parent = new_folder_id
            else:
                parts = rel.split(os.sep)
                current_parent = new_folder_id
                for part in parts:
                    # Find or create subfolder
                    existing = [f for f in self.list_folders(current_parent) if f["name"] == part]
                    if existing:
                        current_parent = existing[0]["id"]
                    else:
                        current_parent = self.create_directory(part, current_parent)
                        total_folders += 1
            for fname in files:
                fpath = ospath.join(root, fname)
                self.upload_file(fpath, fname, current_parent, progress_callback)
                total_files += 1
                try:
                    os.remove(fpath)
                except:
                    pass
        return new_folder_id, total_files, total_folders

    def copy_file(self, file_id: str, dest_parent: str = "root"):
        body = {"parents": [dest_parent]} if dest_parent and dest_parent != "root" else {}
        # For root, need to handle differently - copy without parents goes to root
        if dest_parent == "root":
            body = {}
        resp = self._execute(lambda: self.service.files().copy(supportsAllDrives=True, fileId=file_id, body=body, fields="id,name,webViewLink"))
        self.set_permission(resp["id"])
        return resp

    def clone_folder(self, folder_id: str, dest_parent: str = "root", progress_callback=None):
        """Clone entire folder recursively"""
        meta = self.get_file_metadata(folder_id)
        new_folder_id = self.create_directory(meta["name"], dest_parent if dest_parent != "root" else None)
        files = self.list_files(folder_id, page_size=200)
        total = 0
        for f in files:
            if f["mimeType"] == G_DRIVE_DIR_MIME:
                self.clone_folder(f["id"], new_folder_id, progress_callback)
            else:
                self.copy_file(f["id"], new_folder_id)
                total += 1
                if progress_callback:
                    progress_callback(total)
        return new_folder_id, total

    def delete_file(self, file_id: str):
        return self._execute(lambda: self.service.files().delete(fileId=file_id, supportsAllDrives=True))

    def get_storage_quota(self):
        resp = self._execute(lambda: self.service.about().get(fields="storageQuota"))
        return resp.get("storageQuota", {})

    def get_id_from_url(self, link: str):
        fid = get_id_from_url(link)
        if fid == "NotFound":
            raise ValueError("Invalid Google Drive URL")
        return fid

def get_drive_helper(user_id=None, token_path=None, use_sa=None):
    helper = GoogleDriveHelper(user_id=user_id, token_path=token_path, use_sa=use_sa)
    helper.authorize()
    return helper
