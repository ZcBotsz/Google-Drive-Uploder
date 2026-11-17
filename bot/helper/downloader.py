import os
import re
import aiohttp
from urllib.parse import urlparse, unquote
from config import Config
from bot.core.logger import get_logger
from bot.helper.utils import sanitize_filename

LOGGER = get_logger(__name__)

async def download_direct_link(url: str, dest_dir: str, progress_callback=None, filename_hint=None):
    """Download direct link with aiohttp, streaming + progress"""
    os.makedirs(dest_dir, exist_ok=True)
    # Guess filename
    filename = filename_hint
    if not filename:
        parsed = urlparse(url)
        filename = os.path.basename(unquote(parsed.path)) or "download"
        if not filename or filename == "/":
            filename = "download"
    filename = sanitize_filename(filename)
    dest_path = os.path.join(dest_dir, filename)
    # Avoid overwrite
    counter = 1
    base, ext = os.path.splitext(dest_path)
    while os.path.exists(dest_path):
        dest_path = f"{base}_{counter}{ext}"
        counter += 1

    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    timeout = aiohttp.ClientTimeout(total=3600)
    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
        async with session.get(url, allow_redirects=True) as resp:
            if resp.status != 200:
                raise ValueError(f"Download failed: HTTP {resp.status}")
            # Try Content-Disposition
            cd = resp.headers.get("Content-Disposition", "")
            if cd and not filename_hint:
                m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";\n]+)"?', cd)
                if m:
                    filename = sanitize_filename(unquote(m.group(1)))
                    dest_path = os.path.join(dest_dir, filename)
            total = int(resp.headers.get("Content-Length", 0) or 0)
            downloaded = 0
            with open(dest_path, "wb") as f:
                async for chunk in resp.content.iter_chunked(1024 * 1024):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if progress_callback and total:
                            progress_callback((downloaded / total) * 100, downloaded, total)
                        elif progress_callback:
                            progress_callback(0, downloaded, 0)
    LOGGER.info(f"Downloaded {url} -> {dest_path} ({downloaded} bytes)")
    return dest_path

async def download_with_ytdlp(url: str, dest_dir: str, progress_callback=None):
    """Download via yt-dlp for YouTube / 1000+ sites"""
    import yt_dlp
    os.makedirs(dest_dir, exist_ok=True)
    def hook(d):
        if progress_callback:
            if d["status"] == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                downloaded = d.get("downloaded_bytes", 0)
                pct = (downloaded / total * 100) if total else 0
                progress_callback(pct, downloaded, total)
    ydl_opts = {
        "outtmpl": os.path.join(dest_dir, "%(title)s.%(ext)s"),
        "progress_hooks": [hook],
        "no_warnings": True,
        "quiet": True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        filename = ydl.prepare_filename(info)
        return filename

async def download_file(url: str, dest_dir: str, progress_callback=None, filename_hint=None):
    """Smart downloader: yt-dlp for known sites, direct otherwise"""
    yt_sites = ["youtube.com", "youtu.be", "instagram.com", "twitter.com", "x.com", "tiktok.com", "facebook.com", "vimeo.com"]
    if any(s in url for s in yt_sites):
        try:
            return await download_with_ytdlp(url, dest_dir, progress_callback)
        except Exception as e:
            LOGGER.warning(f"yt-dlp failed, falling back to direct: {e}")
    return await download_direct_link(url, dest_dir, progress_callback, filename_hint)
