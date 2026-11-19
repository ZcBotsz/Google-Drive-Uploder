# ZC Drive Upload — UI Guide

## Product principles
- Mobile-first, English-language bot controls; each menu screen has a clear purpose and a route home.
- Existing Studio and protected Admin remain available. Admin actions require authorization in the handler layer, not merely a hidden button.
- Keep user file captions intact. UI decoration applies only to product-authored captions/text.
- Google quota is described only using fields reported by the connected Drive API; Gmail and Photos are not itemized.
- Max is ₹499/month with technical limits shown transparently. It is not unlimited storage.

## Telegram bot
`/start`, `/hello`, and `/menu` show the state-aware home menu. Disconnected accounts are offered Google Drive connect; connected accounts get upload/Studio, Drive, settings, plan, help and More tools. Each focused panel edits the menu message in place. Home/back controls are provided on submenus. Upload draft cards expose destination, naming and order controls; discard asks for confirmation. Google sign-in may open an external browser, then returns to the Drive-connected flow.

Button styling is chosen by action type: green/success for connecting or continuing, primary for normal navigation, and destructive for disconnect/discard/remove. Telegram clients may render button colors only when Bot API styles are supported. Animated emoji are separately feature-gated and only displayed where Telegram supports custom emoji; static emoji remain a fallback.

## Studio and admin
The public home page leads directly to Google sign-in for Studio. The Studio itself does not add a password prompt; keep the existing session/OAuth checks. Admin remains protected by the existing authorization mechanisms. Preserve upload processing features, preview, queue, and job controls while adjusting presentation.

## Accessibility and QA
Use readable labels, meaningful status text, keyboard focus visibility, responsive layout, and progress descriptions rather than box-drawing glyphs. Test narrow/mobile widths, keyboard navigation, screen-reader names, connected/disconnected states, invalid links, expired sessions, and unauthorized admin callbacks.

## Upload limits
Displayed limits must be sourced from configured plans and Telegram API/deployment configuration. Actual Drive quota, Google API limits, local Bot API availability, and deployment settings also apply. API ID/hash alone do not enable a local Bot API server or larger transfers.
