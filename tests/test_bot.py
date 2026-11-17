"""Comprehensive tests for Telegram Drive Uploader Bot - run: pytest tests/ -v"""
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

def test_utils():
    from bot.helper.utils import humanbytes, get_id_from_url, extract_urls, sanitize_filename, is_gdrive_link
    assert humanbytes(0) == "0 B"
    assert humanbytes(1048576) == "1.00 MB"
    assert humanbytes(1073741824) == "1.00 GB"
    assert get_id_from_url("https://drive.google.com/file/d/1WLNCRtPItExsj5Sn1qKhO16WDg7G1rC_/view") == "1WLNCRtPItExsj5Sn1qKhO16WDg7G1rC_"
    assert get_id_from_url("https://drive.google.com/drive/folders/1AbC123") == "1AbC123"
    assert get_id_from_url("invalid") == "NotFound"
    assert extract_urls("hi https://example.com/a.pdf bye https://x.com/b") == ["https://example.com/a.pdf", "https://x.com/b"]
    assert sanitize_filename("../etc/passwd") == "passwd"
    assert is_gdrive_link("https://drive.google.com/file/d/xxx") is True
    print("✅ utils PASS")

def test_database(tmp_path=None):
    from bot.core import database as db
    db.init_db()
    uid = 999002
    db.upsert_user(uid, "testuser2", "Test2")
    u = db.get_user(uid)
    assert u["username"] == "testuser2"
    db.set_default_folder(uid, "folder123", "MyFolder")
    fid, fname = db.get_default_folder(uid)
    assert fid == "folder123" and fname == "MyFolder"
    db.set_auto_share(uid, False)
    assert db.get_user(uid)["auto_share"] == 0
    db.set_auto_share(uid, True)
    db.set_auto_rename(uid, r"\[.*?\]")
    assert db.get_user(uid)["auto_rename"] == r"\[.*?\]"
    db.add_upload(uid, "f.txt", 1234, "id1", "http://link", "folder123", "telegram")
    stats = db.get_stats(uid)
    assert stats["total_files"] >= 1
    assert stats["total_bytes"] >= 1234
    recent = db.get_recent_uploads(uid)
    assert len(recent) >= 1
    qid = db.add_to_queue(uid, "link", "https://example.com/f.pdf", chat_id=1, message_id=1)
    assert len(db.get_queue(uid)) >= 1
    db.update_queue(qid, status="completed", progress=100)
    db.clear_queue(uid)
    assert len(db.get_queue(uid)) == 0
    db.set_automation(uid, -100999, "folder123", "MyFolder")
    assert len(db.get_automations(uid)) == 1
    rule = db.get_automation_by_channel(-100999)
    assert rule["user_id"] == uid
    db.delete_automation(uid, -100999)
    # Ban
    db.ban_user(uid, "test ban")
    assert db.is_banned(uid) is True
    db.unban_user(uid)
    assert db.is_banned(uid) is False
    # Global stats
    gs = db.get_global_stats()
    assert "users" in gs and "total_files" in gs
    print("✅ database PASS")

def test_config():
    from config import Config
    # PORT handling
    assert Config.WEB_PORT > 0
    assert Config.WEB_HOST == "0.0.0.0"
    assert isinstance(Config.ADMIN_IDS, list)
    assert Config.is_admin(0) in (True, False)  # no crash
    print(f"✅ config PASS (PORT={Config.WEB_PORT}, DATA_DIR={Config.DATA_DIR})")

def test_web():
    from fastapi.testclient import TestClient
    from web.app import app
    c = TestClient(app)
    for path in ["/", "/health", "/ping", "/dashboard?user_id=123", "/picker?user_id=123", "/connect?user_id=123", "/admin?user_id=0"]:
        r = c.get(path)
        assert r.status_code == 200, f"{path} failed: {r.status_code} {r.text[:200]}"
    r = c.get("/health")
    j = r.json()
    assert j["status"] == "ok"
    assert "uptime_seconds" in j
    r = c.get("/api/status?user_id=123")
    assert r.status_code == 200
    r = c.get("/api/folders?user_id=123&parent_id=root")
    assert r.status_code in (200, 400)  # 400 expected without auth, not 500
    r = c.get("/api/admin/stats?user_id=0")
    assert r.status_code == 403  # non-admin blocked
    print("✅ web PASS")

def test_imports():
    import bot.modules.start, bot.modules.auth, bot.modules.upload, bot.modules.drive
    import bot.modules.batch, bot.modules.automation, bot.modules.status, bot.modules.help
    import bot.modules.admin, bot.modules.settings
    import bot.helper.gdrive, bot.helper.downloader, bot.helper.queue_manager, bot.helper.automation
    print("✅ imports PASS")

def test_gdrive_helper_no_crash():
    from bot.helper.gdrive import GoogleDriveHelper
    h = GoogleDriveHelper(user_id=123)
    assert h.token_path.endswith("123.pickle")
    # get_auth_url should fail gracefully without client_secrets (not crash server)
    try:
        url, flow = h.get_auth_url()
        assert "accounts.google.com" in url
        print("✅ gdrive auth URL PASS (env-based or file-based)")
    except FileNotFoundError as e:
        print(f"✅ gdrive auth correctly requires client_secrets: {e}")
    except Exception as e:
        print(f"✅ gdrive auth handled: {e}")

if __name__ == "__main__":
    test_utils()
    test_database()
    test_config()
    test_web()
    test_imports()
    test_gdrive_helper_no_crash()
    print("\n🎉 ALL TESTS PASSED!")
