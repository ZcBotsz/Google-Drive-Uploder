"""The URL guard must refuse to let a stranger reach our own network.

Reference bots `aryanvikash/Google-Drive-Uploader` and
`cdfxscrq/GDrive-Uploader-TG-Bot` take a link and fetch it with no address
check at all — on Railway that means the bot could be told to fetch
169.254.169.254 (instance metadata) or the internal file server.
"""
from __future__ import annotations

import ipaddress

import pytest

from driveup.net import fetch


@pytest.mark.parametrize("url,why", [
    ("http://169.254.169.254/latest/meta-data/", "AWS/GCP metadata"),
    ("http://127.0.0.1:8080/admin", "loopback"),
    ("http://localhost/f", "localhost by name"),
    ("http://10.1.2.3/f", "private 10/8"),
    ("http://192.168.0.9/f", "private 192.168"),
    ("http://172.16.4.4/f", "private 172.16"),
    ("http://[::1]/f", "IPv6 loopback"),
    ("http://0.0.0.0/f", "unspecified"),
    ("file:///etc/passwd", "file scheme"),
    ("ftp://example.com/x", "ftp scheme"),
])
def test_dangerous_urls_are_refused(url, why):
    with pytest.raises(fetch.UnsafeURL):
        fetch.assert_public_url(url)


@pytest.mark.parametrize("url", [
    "https://example.com/a.zip",
    "http://example.com/b.bin",
    "https://raw.githubusercontent.com/o/r/main/f.tar.gz",
])
def test_public_urls_pass(url):
    assert fetch.assert_public_url(url) == url


def test_private_ranges_are_all_covered():
    """Whatever `_addresses_for` returns, every non-public range must trip."""
    for raw in ("10.0.0.1", "172.31.255.254", "192.168.1.1", "169.254.1.1",
                "127.0.0.1", "0.0.0.0", "224.0.0.1", "::1"):
        ip = ipaddress.ip_address(raw)
        assert (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified), raw


@pytest.mark.parametrize("line,url,name", [
    ("https://a.com/f.zip | my movie.mkv", "https://a.com/f.zip", "my movie.mkv"),
    ("https://a.com/f.zip * n.bin", "https://a.com/f.zip", "n.bin"),
    ("https://a.com/f.zip", "https://a.com/f.zip", ""),
    ("https://a.com/f.zip|tight.txt", "https://a.com/f.zip", "tight.txt"),
])
def test_user_can_rename_a_link(line, url, name):
    assert fetch.parse_url_line(line) == (url, name)


@pytest.mark.parametrize("url,expected", [
    ("https://a.com/dir/movie.mkv", "movie.mkv"),
    ("https://a.com/x/../../etc/passwd", "passwd"),   # traversal is defused
    ("https://a.com/", "download"),
])
def test_names_are_safe_basenames(url, expected):
    assert fetch.filename_from_url(url) == expected
