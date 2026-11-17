"""
Archive extraction (from WZML-X / MLTB): Extract zip/tar/7z and upload contents to Drive.
Supports: .zip, .tar, .tar.gz, .tgz, .7z (via py7zr if available)
"""
import os
import zipfile
import tarfile
import shutil
from bot.core.logger import get_logger

LOGGER = get_logger(__name__)

def is_archive(filename: str) -> bool:
    lower = filename.lower()
    return lower.endswith(('.zip', '.tar', '.tar.gz', '.tgz', '.tar.bz2', '.7z'))

def extract_archive(archive_path: str, dest_dir: str) -> str:
    """
    Extract archive to dest_dir. Returns extracted directory path.
    Raises on failure.
    """
    os.makedirs(dest_dir, exist_ok=True)
    base = os.path.splitext(os.path.basename(archive_path))[0]
    # Handle .tar.gz double extension
    if base.endswith('.tar'):
        base = base[:-4]
    extract_dir = os.path.join(dest_dir, base)
    os.makedirs(extract_dir, exist_ok=True)

    lower = archive_path.lower()
    if lower.endswith('.zip'):
        with zipfile.ZipFile(archive_path, 'r') as z:
            z.extractall(extract_dir)
    elif lower.endswith(('.tar', '.tar.gz', '.tgz', '.tar.bz2')):
        with tarfile.open(archive_path, 'r:*') as t:
            t.extractall(extract_dir)
    elif lower.endswith('.7z'):
        try:
            import py7zr
            with py7zr.SevenZipFile(archive_path, mode='r') as z:
                z.extractall(path=extract_dir)
        except ImportError:
            raise ValueError("7z support requires 'py7zr' package. Install: pip install py7zr")
    else:
        raise ValueError(f"Unsupported archive format: {archive_path}")

    LOGGER.info(f"Extracted {archive_path} -> {extract_dir}")
    return extract_dir

def count_files(directory: str) -> int:
    count = 0
    for _, _, files in os.walk(directory):
        count += len(files)
    return count
