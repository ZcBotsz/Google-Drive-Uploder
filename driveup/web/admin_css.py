"""Admin panel stylesheet.

Kept out of `admin_page.py` so the page module stays about structure. The mobile
block at the bottom is the important half: this panel is operated from a phone,
and the desktop sidebar + wide tables made it unusable there.
"""
from __future__ import annotations

ADMIN_CSS = "<style>" + r"""
.admin-shell { display:grid; grid-template-columns: 208px 1fr; gap:16px; }
  @media (max-width: 820px) { .admin-shell { grid-template-columns: 1fr; } }
  .side { position:sticky; top:70px; align-self:start; }
  .side a { display:flex; align-items:center; gap:9px; padding:10px 12px; border-radius:10px;
    color:var(--dim); font-size:.88rem; font-weight:600; }
  .side a:hover { background:rgba(255,255,255,.04); text-decoration:none; color:var(--txt); }
  .side a.active { background:rgba(34,211,238,.13); color:var(--accent); }
  .pane { display:none; } .pane.active { display:block; animation: slide-in 240ms var(--ease); }
  .tblwrap { overflow-x:auto; }
  .mini { font-size:.74rem; }
  .statrow { display:grid; grid-template-columns:repeat(auto-fit,minmax(120px,1fr)); gap:10px; }
  .drawer { position:fixed; inset:0 0 0 auto; width:min(460px,94vw); z-index:120;
    background:var(--surface); border-left:1px solid var(--line-2); padding:18px;
    overflow:auto; transform:translateX(100%); transition:transform 260ms var(--ease);
    /* the closed drawer sits off-screen; contain it so its children can never
       widen the document and add a horizontal scrollbar to every pane */
    contain:layout paint; }
  .drawer.open { transform:none; }
  html, body { max-width:100%; overflow-x:hidden; }
  .drawer .close { position:absolute; top:12px; right:12px; }

  /* ── MOBILE (the way this panel is actually used) ──────────────────────
     On a phone the 9-link sidebar ate the whole first screen and pushed the
     data below the fold; wide tables then forced a sideways scroll, so rows
     were clipped and buttons looked missing. Replaced by a horizontal tab bar
     and card rows — nothing scrolls sideways, every row is tappable. */
  @media (max-width: 820px) {
    .admin-shell { display:block; }
    .side {
      position:sticky; top:0; z-index:60; display:flex; gap:6px;
      overflow-x:auto; overflow-y:hidden; padding:8px; margin:0 0 10px;
      border-radius:12px; scrollbar-width:none; -webkit-overflow-scrolling:touch;
    }
    .side::-webkit-scrollbar { display:none; }
    .side a {
      flex:0 0 auto; padding:10px 13px; font-size:.82rem; white-space:nowrap;
      border:1px solid var(--line-2); border-radius:999px; background:rgba(255,255,255,.02);
    }
    .side a.active { border-color:var(--accent); }
    .row { flex-wrap:wrap; }
    .row > input, .row > select { min-width:0; flex:1 1 100%; }
    .tblwrap { overflow-x:visible; }
    /* tables become stacked cards: no horizontal scroll, no clipped columns */
    table.atable thead { display:none; }
    table.atable, table.atable tbody, table.atable tr, table.atable td { display:block; width:100%; }
    table.atable tr {
      border:1px solid var(--line-2); border-radius:12px; padding:10px 12px;
      margin-bottom:8px; background:rgba(255,255,255,.02);
    }
    table.atable td { border:0; padding:3px 0; text-align:left; }
    table.atable td::before {
      content:attr(data-l) " "; color:var(--dim); font-size:.72rem;
      text-transform:uppercase; letter-spacing:.04em;
    }
    table.atable td[data-l=""]::before, table.atable td.act::before { content:""; }
    table.atable td.act { display:flex; gap:8px; padding-top:8px; }
    /* 44 px minimum tap target (Apple/Google guidance), was 24–36 px */
    .btn, .btn.sm, button, select, input[type=search], input[type=text],
    input[type=number] { min-height:44px; }
    .switch input { width:44px; height:26px; }
    .statrow { grid-template-columns:repeat(2,1fr); }
    .grid.two { grid-template-columns:1fr; }
  }
""" + "</style>"
