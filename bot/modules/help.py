from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, Message

HELP_PAGES = [
    ("🏠 Welcome", "👋 **Telegram → Drive Uploader**\n\nUpload any file/video/link to Google Drive.\n\n• Send file → auto upload\n• Send link → download & upload\n• Send Drive link → clone\n\nTap → for more."),
    ("🔗 Connect Drive", "🔗 **Connect Google Drive (OAuth)**\n\n1. Run `/connect`\n2. Open auth link, sign in with Gmail\n3. Paste code here\n\nYour token is saved securely per-user.\nOwner can also use `token.pickle` or Service Accounts in `accounts/` folder."),
    ("📂 Folders & Web UI", "📂 **Choose Where to Upload**\n\n• `/folders` — open Web Folder Picker (best!)\n• `/setfolder <url>` — set by link\n• `/newfolder <name>` — create folder\n• `/list` — list current folder\n• Web: `/picker?user_id=YOUR_ID` — visual tree, search, create\n\nCurrent folder is shown in `/start`."),
    ("📦 Batch & Automation", "📦 **Batch Upload**\n\n• `/batch` — turn ON batch mode\n• Send many files/links\n• `/done` — start uploading all\n• `/cancel` — stop\n\n🤖 **Automation**\n\n• `/auto <channel_id> <folder_url>`\n• Bot auto-uploads new channel files to Drive\n• Add bot as admin in channel first"),
    ("⚙️ All Commands", "⚙️ **Commands**\n\n/start — home\n/connect — connect Drive\n/revoke — disconnect\n/folders — folder picker\n/setfolder — set folder\n/newfolder — create folder\n/list — list Drive folder\n/search — search Drive\n/clone — clone Drive file/folder\n/batch — batch mode\n/done — start batch\n/cancel — cancel uploads\n/auto — automation\n/status — storage & stats\n/web — web dashboard\n/delete — delete Drive file\n/help — this help"),
]

@Client.on_message(filters.private & filters.command(["help"]))
async def help_cmd(client: Client, message: Message):
    title, body = HELP_PAGES[0]
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Next →", callback_data="help_1")],
    ])
    await message.reply_text(f"{title}\n\n{body}", reply_markup=keyboard)

@Client.on_callback_query(filters.create(lambda _, __, q: q.data.startswith("help_")))
async def help_cb(client: Client, query):
    try:
        idx = int(query.data.split("_")[1])
    except:
        idx = 0
    if idx < 0 or idx >= len(HELP_PAGES):
        idx = 0
    title, body = HELP_PAGES[idx]
    buttons = []
    if idx > 0:
        buttons.append(InlineKeyboardButton("← Back", callback_data=f"help_{idx-1}"))
    if idx < len(HELP_PAGES)-1:
        buttons.append(InlineKeyboardButton("Next →", callback_data=f"help_{idx+1}"))
    if not buttons:
        buttons.append(InlineKeyboardButton("🏠 Home", callback_data="back_home"))
    await query.message.edit_text(f"{title}\n\n{body}", reply_markup=InlineKeyboardMarkup([buttons]))
    await query.answer()
