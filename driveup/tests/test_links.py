"""Signed Studio/Admin links, and the pages they unlock.

These are the tests that would have caught the "website and admin panel are not
working" report: a Telegram *url* button opens the system browser, where
`window.Telegram.WebApp.initData` does not exist. Without a signed link the
Studio (and /admin) can only ever answer 401, so every button looked broken.

The fixture is imported from test_web so there is exactly one definition.
"""
from __future__ import annotations

import pytest

from driveup.core.links import LINK_TTL, mint_token, parse_token
from driveup.tests.test_web import make_job, web          # noqa: F401  (fixture)

# ══════════════════════════════════════════════════════════════════════════
# Signed links — the reason the Studio and /admin were unreachable in a
# phone browser. A Telegram *url* button opens the system browser, where
# initData does not exist; without these routes every page 401s forever.
# ══════════════════════════════════════════════════════════════════════════
from driveup.core.links import LINK_TTL, mint_token, parse_token    # noqa: E402


def test_token_roundtrip_and_tamper_rejection():
    tok = mint_token("secret", 55)
    assert parse_token("secret", tok)["user_id"] == 55
    assert parse_token("other-secret", tok) is None            # wrong key
    assert parse_token("secret", tok[:-1] + "x") is None       # broken signature
    assert parse_token("secret", "") is None
    assert parse_token("secret", "not-a-token") is None
    assert parse_token("secret", "55-9999999999--" + "a" * 40) is None  # forged


def test_token_expires():
    tok = mint_token("secret", 55, ttl=60, now=1_000_000)
    assert parse_token("secret", tok, now=1_000_000 + 30) is not None
    assert parse_token("secret", tok, now=1_000_000 + 61) is None


def test_admin_token_is_distinguishable():
    normal = mint_token("secret", 55)
    admin = mint_token("secret", 55, admin=True)
    assert normal != admin
    assert parse_token("secret", normal)["admin"] is False
    assert parse_token("secret", admin)["admin"] is True
    # the 'a' suffix is what marks admin; dropping it must not still verify
    assert parse_token("secret", admin[:-1]) is None


async def test_start_link_mints_a_session_and_redirects(web):
    """A signed /start link is how a real browser gets in — no initData needed."""
    tok = mint_token(web["cfg"].session_secret, 55)
    r = await web["client"].get(f"/start?t={tok}", follow_redirects=False)
    assert r.status_code == 303, r.text
    assert r.headers["location"] == "/"
    assert r.cookies.get("du_session"), "no session cookie was set"
    # and that cookie really authenticates
    me = await web["client"].get("/api/me")
    assert me.status_code == 200 and me.json()["userId"] == 55


async def test_start_link_carries_the_job_id(web):
    job = await make_job(web)
    tok = mint_token(web["cfg"].session_secret, 55, job_id=job)
    r = await web["client"].get(f"/start?t={tok}&job={job}", follow_redirects=False)
    assert r.headers["location"] == f"/?job={job}"
    body = (await web["client"].get(f"/api/me?job={job}")).json()
    assert body["job"]["id"] == job


async def test_start_link_goes_to_admin_when_asked(web):
    tok = mint_token(web["cfg"].session_secret, 1, admin=True)   # 1 == owner
    r = await web["client"].get(f"/start?t={tok}&to=admin", follow_redirects=False)
    assert r.headers["location"] == "/admin"
    assert r.cookies.get("du_session")


async def test_admin_link_is_refused_for_a_normal_user(web):
    """Forge-proof: even a *valid* signature cannot escalate a non-admin role."""
    tok = mint_token(web["cfg"].session_secret, 55, admin=True)  # 55 is not an admin
    r = await web["client"].get(f"/start?t={tok}", follow_redirects=False)
    assert r.status_code == 403
    assert "du_session" not in r.cookies


async def test_expired_start_link_explains_itself(web):
    tok = mint_token(web["cfg"].session_secret, 55, ttl=-10)
    r = await web["client"].get(f"/start?t={tok}")
    assert r.status_code == 400
    assert "expired" in r.text.lower()
    assert "du_session" not in r.cookies


async def test_connect_without_session_explains_how_to_get_one(web):
    r = await web["client"].get("/connect")
    assert r.status_code == 200                    # never a bare 404/401
    assert "/connect" in r.text


async def test_studio_page_serves_the_app_when_signed_in(web):
    tok = mint_token(web["cfg"].session_secret, 55)
    await web["client"].get(f"/start?t={tok}", follow_redirects=False)
    r = await web["client"].get("/")
    assert r.status_code == 200
    assert len(r.text) > 1500                      # the real app, not the blocker
    assert "blocker" not in r.text


# ── animated-emoji preview endpoint (admin panel button) ────────────────────
async def test_emoji_preview_renders_both_states(web):
    tok = mint_token(web["cfg"].session_secret, 1, admin=True)   # 1 == owner
    await web["client"].get(f"/start?t={tok}", follow_redirects=False)
    r = await web["client"].post("/admin/api/emoji/preview", json={})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["on"] != body["off"]                  # something changed
    assert "<tg-emoji emoji-id=" in body["on"]
    assert "<tg-emoji" not in body["off"]
    assert body["catalogue"] >= 30
    # the <pre> guard survives the round trip
    assert "<pre>progress card stays plain</pre>" in body["on"]


async def test_emoji_preview_needs_admin(web):
    r = await web["client"].post("/admin/api/emoji/preview", json={})
    assert r.status_code == 401


async def test_settings_expose_the_emoji_catalogue(web):
    tok = mint_token(web["cfg"].session_secret, 1, admin=True)
    await web["client"].get(f"/start?t={tok}", follow_redirects=False)
    body = (await web["client"].get("/admin/api/settings")).json()
    assert body["cfg"]["emoji_catalogue_size"] >= 30
    assert body["cfg"]["emoji_free_size"] >= 5


# ── admin panel must be usable from a phone (the reported complaint) ───────
def test_admin_page_ships_the_mobile_layout():
    """"Admin se kaam nahi kar raha" was a layout bug, not an API bug:
    a 9-link sidebar ate the first screen and wide tables forced sideways
    scrolling, so rows looked missing. These rules are what fixed it."""
    from driveup.web.admin_page import render_admin
    html = render_admin("DriveUp")
    assert "@media (max-width: 820px)" in html          # a real mobile breakpoint
    assert "overflow-x:auto" in html                    # tab bar scrolls, page doesn't
    assert "table.atable thead { display:none; }" in html   # tables become cards
    assert 'content:attr(data-l)' in html               # every cell gets a label
    assert "min-height:44px" in html                    # 44 px tap targets


def test_admin_page_css_is_served_inline():
    """No external stylesheet: the panel must look right even on a bad link."""
    from driveup.web.admin_page import render_admin
    html = render_admin("DriveUp")
    assert "<style>" in html and "</style>" in html
    assert "__ADMIN_CSS__" not in html


async def test_settings_report_the_reachable_file_limit(web):
    """The panel must never show a plan limit Telegram cannot deliver."""
    tok = mint_token(web["cfg"].session_secret, 1, admin=True)
    await web["client"].get(f"/start?t={tok}", follow_redirects=False)
    body = (await web["client"].get("/admin/api/settings")).json()
    assert body["cfg"]["max_file_mb_reachable"] >= 1
    assert "telegram_cloud_api" in body["cfg"]


def test_admin_plans_pane_can_actually_edit_plans():
    """The Plans pane used to be read-only: the POST route existed but no UI
    reached it, so an admin could not change a price or a limit from the panel.
    """
    from driveup.web.admin_page import render_admin
    html = render_admin("DriveUp")
    for field in ("eName", "ePrice", "eDays", "eMaxFile", "eDailyGb",
                  "eConc", "eFiles", "eSave"):
        assert f'id="{field}"' in html, f"{field} missing — plan editor incomplete"
    assert "loadPlans" in html and "fillPlan" in html


def test_plan_editor_warns_about_an_undeliverable_size():
    from driveup.web.admin_page import render_admin
    html = render_admin("DriveUp")
    assert "can only download" in html      # the live ceiling warning
    assert "TELEGRAM_API_URL" in html       # and how to lift it


def test_closed_drawer_cannot_widen_the_page():
    """The off-screen drawer used to push the document 8 px wide on Settings,
    which is a horizontal scrollbar on every phone. It must be contained."""
    from driveup.web.admin_page import render_admin
    html = render_admin("DriveUp")
    assert "contain:layout paint" in html
    assert "overflow-x:hidden" in html


def test_local_bot_api_requires_the_raw_credentials():
    """TELEGRAM_API_URL points at telegram-bot-api, which refuses to start
    without API_ID/API_HASH. Setting one and forgetting the other used to fail
    silently at runtime; now it fails at boot with a sentence."""
    from driveup.config import Settings
    cfg = Settings()
    cfg.telegram_api_url = "http://tg-bot-api.railway.internal:8081"
    cfg.api_id, cfg.api_hash = 0, ""
    problems = " ".join(cfg.validate())
    assert "API_ID" in problems and "API_HASH" in problems
    cfg.api_id = 12345678
    cfg.api_hash = "0123456789abcdef0123456789abcdef"
    # credentials are no longer the complaint — but this container still cannot
    # reach the server's files, which is the *other* half-configured state
    assert not [p for p in cfg.validate() if "API_ID" in p or "API_HASH" in p]
    cfg.telegram_files_url = "http://tg-bot-api.railway.internal:8080"
    assert not [p for p in cfg.validate() if "TELEGRAM" in p]


def test_uses_local_api_is_the_single_switch():
    from driveup.config import Settings
    cfg = Settings()
    cfg.telegram_api_url = "https://api.telegram.org"
    assert cfg.uses_local_api is False
    cfg.telegram_api_url = "http://127.0.0.1:8081"
    assert cfg.uses_local_api is True


def test_a_signature_ending_in_the_letter_a_is_still_valid():
    """Regression: the admin marker used to be a bare trailing `a`.

    The signature is hex, so one token in sixteen ended in `a`; parse_token
    stripped that digit, re-hashed a different string and rejected a link that
    had just been minted. It surfaced as *intermittent* 401s — the Studio or
    /admin "sometimes just would not open" — which is why it survived so long.
    """
    found = 0
    for uid in range(55, 655):                # vary the user: same uid in the
        tok = mint_token("secret", uid)       # same second mints the same sig
        if tok.endswith("a"):
            found += 1
            assert parse_token("secret", tok) is not None, tok
    assert found, "no token ending in 'a' was generated — the test proved nothing"


def test_admin_marker_cannot_be_stripped_to_forge_a_user_token():
    admin = mint_token("secret", 55, admin=True)
    assert parse_token("secret", admin)["admin"] is True
    # remove the -a marker: must not verify as a normal token
    assert parse_token("secret", admin[:-2]) is None
