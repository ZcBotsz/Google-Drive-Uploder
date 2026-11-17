import re
import os
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
    # Direct ID
    if re.match(r"^[-_\w]{25,}$", link) and "://" not in link:
        return link
    # Drive URL patterns
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
    # Fallback: last path segment
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
    # Find all URLs
    urls = re.findall(r"https?://[^\s]+", text)
    return urls

def sanitize_filename(name: str):
    # Remove path traversal
    name = os.path.basename(name)
    # Remove dangerous chars
    name = re.sub(r'[<>:"/\\|?*]', "_", name)
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
