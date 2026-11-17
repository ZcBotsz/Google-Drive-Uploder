import os
import json
import pickle
import time
from fastapi import FastAPI, Request, Query, UploadFile, File, Form
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from config import Config
from bot.core import database as db
from bot.helper.gdrive import GoogleDriveHelper, get_drive_helper
from bot.helper.utils import humanbytes, get_id_from_url
from bot.core.logger import get_logger

LOGGER = get_logger(__name__)
START_TIME = time.time()

app = FastAPI(title="Telegram Drive Uploader - Web Dashboard", version="2.0.0-advanced")
app.mount("/static", StaticFiles(directory="web/static"), name="static")
templates = Jinja2Templates(directory="web/templates")

web_flows = {}

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(request, "index.html", {"config": Config})

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, user_id: int = Query(0)):
    user = db.get_user(user_id) if user_id else None
    stats = db.get_stats(user_id) if user_id else {"total_files": 0, "total_bytes": 0}
    recent = db.get_recent_uploads(user_id, limit=20) if user_id else []
    folder_id, folder_name = db.get_default_folder(user_id) if user_id else ("root", "My Drive")
    quota = {}
    is_connected = False
    if user_id:
        try:
            helper = get_drive_helper(user_id=user_id)
            quota = helper.get_storage_quota()
            is_connected = True
        except Exception as e:
            LOGGER.warning(f"Dashboard quota failed: {e}")
    return templates.TemplateResponse(request, "dashboard.html", {
        "user_id": user_id,
        "user": user,
        "stats": stats,
        "recent": recent,
        "folder_id": folder_id,
        "folder_name": folder_name,
        "quota": quota,
        "is_connected": is_connected,
        "humanbytes": humanbytes,
        "config": Config,
    })

@app.get("/picker", response_class=HTMLResponse)
async def picker(request: Request, user_id: int = Query(0)):
    folder_id, folder_name = db.get_default_folder(user_id) if user_id else ("root", "My Drive")
    return templates.TemplateResponse(request, "folder_picker.html", {
        "user_id": user_id,
        "folder_id": folder_id,
        "folder_name": folder_name,
        "config": Config,
    })

@app.get("/connect", response_class=HTMLResponse)
async def connect_page(request: Request, user_id: int = Query(0)):
    return templates.TemplateResponse(request, "connect.html", {
        "user_id": user_id,
        "config": Config,
    })

@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request, user_id: int = Query(0)):
    if not Config.is_admin(user_id):
        return templates.TemplateResponse(request, "admin.html", {"user_id": user_id, "is_admin": False, "config": Config})
    stats = db.get_global_stats()
    users = db.get_all_users(limit=50)
    return templates.TemplateResponse(request, "admin.html", {
        "user_id": user_id, "is_admin": True, "stats": stats, "users": users,
        "humanbytes": humanbytes, "config": Config,
    })

# --- APIs ---
@app.get("/api/folders")
async def api_folders(user_id: int, parent_id: str = "root"):
    try:
        helper = get_drive_helper(user_id=user_id)
        folders = helper.list_folders(parent_id)
        return {"success": True, "folders": folders, "parent_id": parent_id}
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=400)

@app.get("/api/files")
async def api_files(user_id: int, parent_id: str = "root"):
    try:
        helper = get_drive_helper(user_id=user_id)
        files = helper.list_files(parent_id, page_size=100)
        return {"success": True, "files": files}
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=400)

@app.get("/api/search")
async def api_search(user_id: int, q: str):
    try:
        helper = get_drive_helper(user_id=user_id)
        results = helper.search_files(q)
        return {"success": True, "results": results}
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=400)

@app.post("/api/set_folder")
async def api_set_folder(payload: dict):
    user_id = payload.get("user_id")
    folder_id = payload.get("folder_id")
    folder_name = payload.get("folder_name", "Folder")
    if not user_id or not folder_id:
        return JSONResponse({"success": False, "error": "Missing user_id/folder_id"}, status_code=400)
    try:
        helper = get_drive_helper(user_id=user_id)
        if folder_id != "root":
            meta = helper.get_file_metadata(folder_id)
            folder_name = meta.get("name", folder_name)
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=400)
    db.set_default_folder(user_id, folder_id, folder_name)
    return {"success": True, "folder_id": folder_id, "folder_name": folder_name}

@app.post("/api/create_folder")
async def api_create_folder(payload: dict):
    user_id = payload.get("user_id")
    name = payload.get("name")
    parent_id = payload.get("parent_id", "root")
    if not user_id or not name:
        return JSONResponse({"success": False, "error": "Missing name"}, status_code=400)
    try:
        helper = get_drive_helper(user_id=user_id)
        new_id = helper.create_directory(name, parent_id if parent_id != "root" else None)
        return {"success": True, "folder_id": new_id, "folder_name": name, "link": f"https://drive.google.com/drive/folders/{new_id}"}
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=400)

@app.post("/api/upload")
async def api_upload(user_id: int = Form(...), parent_id: str = Form("root"), file: UploadFile = File(...)):
    import shutil
    tmp_dir = os.path.join(Config.DOWNLOAD_DIR, "web_uploads")
    os.makedirs(tmp_dir, exist_ok=True)
    # Sanitize filename
    from bot.helper.utils import sanitize_filename
    safe_name = sanitize_filename(file.filename or "upload")
    dest = os.path.join(tmp_dir, safe_name)
    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)
    try:
        helper = get_drive_helper(user_id=user_id)
        result = helper.upload_file(dest, safe_name, parent_id)
        file_id = result.get("id")
        link = result.get("webViewLink") or f"https://drive.google.com/file/d/{file_id}/view"
        size = os.path.getsize(dest)
        db.add_upload(user_id, safe_name, size, file_id, link, parent_id, source="web")
        return {"success": True, "file_id": file_id, "link": link, "name": safe_name}
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=400)
    finally:
        try:
            os.remove(dest)
        except:
            pass

@app.get("/api/connect_url")
async def api_connect_url(user_id: int):
    try:
        helper = GoogleDriveHelper(user_id=user_id)
        auth_url, flow = helper.get_auth_url()
        web_flows[user_id] = flow
        return {"success": True, "auth_url": auth_url}
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=400)

@app.post("/api/connect_code")
async def api_connect_code(payload: dict):
    user_id = payload.get("user_id")
    code = payload.get("code")
    if not user_id or not code:
        return JSONResponse({"success": False, "error": "Missing code"}, status_code=400)
    flow = web_flows.pop(user_id, None)
    if not flow:
        return JSONResponse({"success": False, "error": "No pending auth flow. Click 'Get Auth Link' first."}, status_code=400)
    try:
        helper = GoogleDriveHelper(user_id=user_id)
        helper.exchange_code(flow, code)
        db.set_authorized(user_id, helper.token_path)
        return {"success": True, "message": "Connected!"}
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=400)

@app.get("/api/status")
async def api_status(user_id: int):
    user = db.get_user(user_id)
    stats = db.get_stats(user_id)
    folder_id, folder_name = db.get_default_folder(user_id)
    quota = {}
    try:
        helper = get_drive_helper(user_id=user_id)
        quota = helper.get_storage_quota()
    except:
        pass
    return {
        "user": user,
        "stats": stats,
        "folder_id": folder_id,
        "folder_name": folder_name,
        "quota": quota,
        "is_connected": bool(user and user.get("is_authorized")),
    }

@app.get("/api/recent")
async def api_recent(user_id: int):
    return {"success": True, "uploads": db.get_recent_uploads(user_id, limit=50)}

@app.get("/api/admin/stats")
async def api_admin_stats(user_id: int):
    if not Config.is_admin(user_id):
        return JSONResponse({"success": False, "error": "Admin only"}, status_code=403)
    return {"success": True, "stats": db.get_global_stats(), "users": db.get_all_users(limit=100)}

@app.post("/api/admin/ban")
async def api_admin_ban(payload: dict):
    admin_id = payload.get("admin_id")
    target = payload.get("target_id")
    reason = payload.get("reason", "")
    if not Config.is_admin(admin_id):
        return JSONResponse({"success": False, "error": "Admin only"}, status_code=403)
    db.ban_user(target, reason)
    return {"success": True}

@app.post("/api/admin/unban")
async def api_admin_unban(payload: dict):
    admin_id = payload.get("admin_id")
    target = payload.get("target_id")
    if not Config.is_admin(admin_id):
        return JSONResponse({"success": False, "error": "Admin only"}, status_code=403)
    db.unban_user(target)
    return {"success": True}

@app.get("/health")
async def health():
    uptime = int(time.time() - START_TIME)
    stats = db.get_global_stats()
    return {"status": "ok", "bot": "telegram-drive-uploader", "version": "2.0.0-advanced", "uptime_seconds": uptime, "users": stats["users"], "files": stats["total_files"]}

@app.get("/ping")
async def ping():
    return {"pong": True}

def run_web():
    import uvicorn
    # Render: bind 0.0.0.0:$PORT (Config.WEB_PORT already reads PORT)
    uvicorn.run(app, host=Config.WEB_HOST, port=Config.WEB_PORT, log_level="info")
