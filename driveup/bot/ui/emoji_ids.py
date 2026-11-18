"""Bundled catalogue of Telegram animated custom-emoji ids.

Telegram renders a `<tg-emoji emoji-id="…">🚀</tg-emoji>` inline in HTML text as
an *animated* emoji — but only if the id belongs to a sticker set the bot may
use. There are two classes:

  * **free** — any bot may use them; every viewer sees the animation.
  * **premium** — render for Telegram Premium viewers; everyone else still sees
    the plain character because we keep it as the tag's inner text.

So a wrong/withdrawn id degrades to the ordinary emoji, never to a broken box.
That is why shipping a catalogue is safe, while *monkey-patching the client*
(the reference zip's approach) is not: that broke on every library upgrade.

The operator can override or extend this at runtime from the admin panel
(`EMOJI_MAP` / Settings → animated emoji), and can disable the whole feature
with `ENABLE_ANIMATED_EMOJI=0`.
"""
from __future__ import annotations

from typing import Dict, Tuple

# ── ids verified against the reference sticker catalogue ────────────────────
# char → custom_emoji_id
FREE: Dict[str, str] = {
    "🚀": "5296361615669619005",
    "📁": "5357315181649076022",
    "📦": "5258134813302332906",
    "📊": "6093382540784046658",
    "⭐": "6145627678318793779",
    "🌐": "4940645878684713890",
    "🔄": "5292226786229236118",
}

PREMIUM: Dict[str, str] = {
    "📤": "5445355530111437729",
    "📥": "5443127283898405358",
    "✅": "5262832270573582269",
    "❌": "5210952531676504517",
    "⚠️": "5420323339723881652",
    "⚙️": "5267334530171169409",
    "🔒": "5206432422194849059",
    "🔥": "4949596062248600256",
    "⚡": "5249381781622247862",
    "⏱": "5350438526691326210",
    "⏳": "5217697679030637222",
    "📉": "5246762912428603768",
    "🔗": "5258177608356481410",
    "🎉": "5193018401810822951",
    "🏁": "5348445120700102867",
    "💎": "5197350061012436657",
    "📱": "5318911503938634641",
    "⛔": "5296728826783482422",
    "🛠": "5462921117423384478",
    "📢": "5370599459661045441",
    "🗑": "5372825386591732174",
    "✏️": "5361905569680605959",
    "🔍": "5231012545799666522",
    "💡": "5193127592764394874",
    "🎯": "5256131095094652290",
    "📌": "5397782960512444700",
    "🆕": "5382357040008021292",
    "⬇️": "5406745015365943482",
    "⬆️": "5435891415055878798",
}

# Which emoji the progress / job / status cards actually use — kept small so a
# card never grows by more than a few dozen bytes of markup.
CARD_EMOJI: Tuple[str, ...] = ("📤", "📥", "📁", "📦", "⚡", "⏱", "⏳", "📉",
                               "✅", "❌", "⚠️", "🚀", "🔥", "⛔", "🏁", "🎉")

#: Newest entry wins when an operator pastes their own mapping.
CATALOGUE: Dict[str, str] = {**PREMIUM, **FREE}


def default_map(enabled_for_everyone: bool = False) -> Dict[str, str]:
    """The catalogue to ship: free-only when free viewers must see animation."""
    return dict(FREE) if enabled_for_everyone else dict(CATALOGUE)
