import re
import os
import time
from urllib.parse import urlparse, parse_qs

def humanbytes(size):
    if size is None:
        return "0 B"
    try:
        size = float(size)
    except:
        return "0 B"
    if size == 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    i = 0
    while size >= 1024 and i < len(units)-1:
        size /= 1024
        i += 1
    return f"{size:.2f} {units[i]}"

def progress_bar(percentage, length=12):
    filled = int(length * percentage / 100)
    bar = "█" * filled + "░" * (length - filled)
    return f"{bar} {percentage:.1f}%"

def get_id_from_url(link: str):
    if not link:
        return "NotFound"
    link = link.strip()
    if re.match(r"^[-_\w]{25,}$", link) and "://" not in link:
        return link
    patterns = [
        r"https?://drive\.google\.com/(?:drive(?:/u/\d+)?/)?folders/([-_\w]+)",
        r"https?://drive\.google\.com/file/d/([-_\w]+)",
        r"https?://drive\.google\.com/open\?id=([-_\w]+)",
        r"https?://drive\.google\.com/uc\?id=([-_\w]+)",
        r"[?&]id=([-_\w]{25,})",
    ]
    for p in patterns:
        m = re.search(p, link)
        if m:
            return m.group(1)
    try:
        parsed = urlparse(link)
        qs = parse_qs(parsed.query)
        if "id" in qs:
            return qs["id"][0]
    except:
        pass
    return "NotFound"

def is_gdrive_link(url: str):
    return "drive.google.com" in url

def is_direct_link(url: str):
    return url.startswith("http://") or url.startswith("https://")

def extract_urls(text: str):
    if not text:
        return []
    urls = re.findall(r"https?://[^\s]+", text)
    return urls

def sanitize_filename(name: str):
    name = os.path.basename(name)
    name = re.sub(r'[<>:"/\\|?*]', "_", name)
    name = re.sub(r'\s+', ' ', name).strip()
    return name.strip() or "file"

def get_mime_type(file_path: str):
    from mimetypes import guess_type
    mime, _ = guess_type(file_path)
    return mime or "application/octet-stream"

def format_time(seconds):
    if seconds < 60:
        return f"{int(seconds)}s"
    elif seconds < 3600:
        return f"{int(seconds//60)}m {int(seconds%60)}s"
    else:
        return f"{int(seconds//3600)}h {int((seconds%3600)//60)}m"

def get_caption_filename(message, original_filename: str) -> str:
    """
    Use message caption as filename if present (e.g. 'Lecture 1' -> 'Lecture 1.mp4').
    Preserves original extension. Falls back to original_filename.
    Handles multi-line captions (uses first meaningful line).
    """
    caption = getattr(message, 'caption', None)
    if not caption:
        return original_filename
    caption = caption.strip()
    if not caption:
        return original_filename
    # Use first line if multi-line (common for course titles)
    first_line = caption.split('\n')[0].strip()
    # If first line is very short, use full caption (truncated)
    if len(first_line) < 3:
        first_line = caption.replace('\n', ' ').strip()
    # Truncate to 100 chars (Drive filename limit ~255, but keep safe)
    first_line = first_line[:100].strip()
    if not first_line:
        return original_filename
    # Preserve extension from original
    _, ext = os.path.splitext(original_filename)
    # If original has no ext, try to guess from mime
    if not ext:
        # Guess from message type
        if getattr(message, 'video', None):
            ext = '.mp4'
        elif getattr(message, 'photo', None):
            ext = '.jpg'
        elif getattr(message, 'audio', None):
            ext = '.mp3'
        elif getattr(message, 'document', None):
            # Try document mime
            mime = getattr(message.document, 'mime_type', '') or ''
            if 'pdf' in mime:
                ext = '.pdf'
            elif 'zip' in mime:
                ext = '.zip'
    # Sanitize caption part (keep spaces, remove dangerous chars)
    safe_caption = re.sub(r'[<>:"/\\|?*]', "_", first_line)
    safe_caption = re.sub(r'\s+', ' ', safe_caption).strip()
    if not safe_caption:
        return original_filename
    # Avoid double extension (e.g. "Lecture 1.mp4.mp4")
    if ext and safe_caption.lower().endswith(ext.lower()):
        return safe_caption
    return f"{safe_caption}{ext}"

def get_unique_filepath(directory: str, filename: str) -> str:
    """
    Return a unique filepath in directory (avoids overwrite + .temp clashes).
    Appends _1, _2, etc. if file exists. Also adds timestamp if needed for concurrent downloads.
    """
    os.makedirs(directory, exist_ok=True)
    filename = sanitize_filename(filename)
    dest = os.path.join(directory, filename)
    if not os.path.exists(dest):
        return dest
    base, ext = os.path.splitext(dest)
    counter = 1
    while os.path.exists(dest):
        dest = f"{base}_{counter}{ext}"
        counter += 1
        if counter > 100:
            # Fallback: add timestamp
            ts = int(time.time())
            dest = f"{base}_{ts}{ext}"
            break
    return dest

def get_unique_download_path(directory: str, filename: str, message_id: int = 0) -> str:
    """
    Unique path for concurrent downloads - includes message_id to prevent .temp clashes.
    Example: downloads/Lecture 1_12345.mp4 (12345 = message_id)
    """
    os.makedirs(directory, exist_ok=True)
    filename = sanitize_filename(filename)
    base, ext = os.path.splitext(filename)
    if message_id:
        unique_name = f"{base}_{message_id}{ext}"
    else:
        unique_name = filename
    dest = os.path.join(directory, unique_name)
    # If still exists (retry), add counter
    if os.path.exists(dest):
        counter = 1
        while os.path.exists(dest):
            dest = os.path.join(directory, f"{base}_{message_id}_{counter}{ext}" if message_id else f"{base}_{counter}{ext}")
            counter += 1
    return dest

async def robust_download(message, dest_path: str, max_retries: int = 2):
    """
    Robust Telegram download with retry + directory ensure + .temp handling.
    Fixes FileNotFoundError: '.../video.mp4.temp' -> '.../video.mp4'
    Root cause: concurrent downloads to same dest clash on .temp file.
    Solution: unique dest per message + ensure dir + retry.
    Returns dest_path on success, raises on failure.
    """
    import asyncio
    directory = os.path.dirname(dest_path)
    os.makedirs(directory, exist_ok=True)
    # Clean up stale .temp files from previous failed downloads
    temp_path = dest_path + ".temp"
    # Pyrogram uses .temp suffix internally - clean if exists from crashed download
    # (Don't delete if another download is actively using it - check mtime)
    if os.path.exists(temp_path):
        try:
            # If temp is older than 5 min, it's stale
            if time.time() - os.path.getmtime(temp_path) > 300:
                os.remove(temp_path)
        except:
            pass

    last_error = None
    for attempt in range(max_retries + 1):
        try:
            # Use client.download_media for more control (message.download wraps it)
            # Both use file_name param in Pyrogram 2.0.106
            result = await message.download(file_name=dest_path)
            # Pyrogram returns the downloaded path (may differ if file_name was dir)
            # Verify file exists
            if result and os.path.exists(result):
                # If Pyrogram returned different path (e.g. added suffix), use it
                if result != dest_path and os.path.exists(dest_path):
                    # Both exist? Use dest_path (our requested path)
                    pass
                elif result != dest_path:
                    # Pyrogram saved elsewhere, move to dest if needed
                    if os.path.exists(result) and not os.path.exists(dest_path):
                        import shutil
                        shutil.move(result, dest_path)
                return dest_path
            elif os.path.exists(dest_path):
                return dest_path
            else:
                raise FileNotFoundError(f"Download completed but file not found: {dest_path} (result={result})")
        except Exception as e:
            last_error = e
            # Clean up partial files before retry
            for p in [dest_path, temp_path]:
                try:
                    if os.path.exists(p):
                        os.remove(p)
                except:
                    pass
            if attempt < max_retries:
                await asyncio.sleep(2 * (attempt + 1))
                continue
            raise last_error
    raise last_error
