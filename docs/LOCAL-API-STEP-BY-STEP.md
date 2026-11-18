# Local Bot API — ekdum shuru se, click by click

## Pehle 30 second mein samajh lo (yahi aapka confusion hai)

Aap pooch rahe ho: *"yeh URL kahan milega?"*

**Jawab: kahin nahi milega. Woh aapko khud banani hai.**

`api.telegram.org` Telegram ki apni service hai. Woh 20 MB se badi file deti hi
nahi. Uske badle aap **apna khud ka chhota server** chala dete ho jo wahi kaam
karta hai, par bina limit ke. Us server ka address hi `TELEGRAM_API_URL` hai.

```
Pehle:   aapka bot  ──────────────►  api.telegram.org   (20 MB limit, kisi ka control nahi)

Ab:      aapka bot  ──►  aapka server  ──►  Telegram     (2000 MB, aapka control)
                          ↑
                    yahi "TELEGRAM_API_URL" hai
```

**Telegram app se yeh nahi hota.** Telegram me aisa koi button/setting nahi hai.
Telegram sirf do cheezein deta hai:
- **Bot token** — BotFather se (aapke paas already hai)
- **api_id + api_hash** — my.telegram.org se

Uske baad **Render** par woh server chalta hai. Bas.

---

# PART 1 — api_id aur api_hash laao (5 minute)

Ye do cheezein my.telegram.org se aati hain. **Apne phone wale Telegram account
se**, bot se nahi.

| # | Kahan | Kya karo |
|---|---|---|
| 1 | Browser me kholo | `https://my.telegram.org` |
| 2 | Wahan phone number daalo | **+91** ke saath aapka pura number, jaise `+919876543210` |
| 3 | **Next** dabao | |
| 4 | Telegram app kholo | Aapko Telegram ke andar ek **code** aayega (SMS nahi) |
| 5 | Woh code website me daalo | |
| 6 | Login hone ke baad | **API development tools** par click karo |
| 7 | Form bharo | **App title:** `DriveUp` <br> **Short name:** `driveup` <br> **Platform:** `Other` <br> **URL:** khaali chhod do |
| 8 | **Create application** dabao | |
| 9 | Ab screen par do cheezein dikhengi | **`api_id`** — ek number, jaise `12345678` <br> **`api_hash`** — lamba text, jaise `0123456789abcdef0123456789abcdef` |

**In dono ko kahin likh lo.** Baad me phir dekh sakte ho (`my.telegram.org/apps`),
lekin abhi likh lena aasan hai.

> ⚠️ `api_hash` password jaisa hai. Kisi ko mat do, kisi screenshot me mat daalo.
> Ye kabhi reset nahi hota — leak ho gaya to naya application banana padta hai.

---

# PART 2 — Render par server chalao

## Aapka repo taiyar karo (ek baar)

`render.yaml` me server pehle se likha hua hai. Bas file GitHub par honi chahiye.

1. GitHub par apna repo kholo (jisme DriveUp ka code hai)
2. `render.yaml` uske **root** me hona chahiye — zip me se `render.yaml` utha kar
   repo ke top level par daal do
3. Commit karo

## Blueprint deploy (sabse aasan — dono services ek saath ban jaayengi)

| # | Kahan | Kya karo |
|---|---|---|
| 1 | Browser | `https://dashboard.render.com` kholo, login karo |
| 2 | Upar right | **New** button → **Blueprint** chuno |
| 3 | GitHub connect karo | Apna DriveUp repo select karo → **Connect** |
| 4 | Render padhega `render.yaml` | Ab **do** services dikhengi:<br>• `driveup` (aapka bot)<br>• `driveup-tg-api` (naya server) |
| 5 | **Apply** / **Create** dabao | |
| 6 | Render poochhega values | Neeche wali list bharo |

### Kaun kaun si value daalni hai

Dashboard ek-ek karke poochhega. Ye daalo:

| Variable | Kya daalna hai | Kahan se |
|---|---|---|
| `BOT_TOKEN` | bot ka token | BotFather |
| `OWNER_ID` | aapka Telegram user id | `@userinfobot` se |
| `BOT_USERNAME` | bot ka username | BotFather (`@` ke bina) |
| `GOOGLE_CLIENT_ID` | Google OAuth id | Google Cloud Console |
| `GOOGLE_CLIENT_SECRET` | Google OAuth secret | Google Cloud Console |
| `TOKEN_ENC_KEY` | 44 akshar ka random | neeche command hai |
| `SESSION_SECRET` | koi lamba random text | apne mann se, 32+ akshar |
| `PUBLIC_URL` | `https://driveup-xxxx.onrender.com` | Render service banne ke baad dikhega |
| `GOOGLE_REDIRECT_URI` | `https://driveup-xxxx.onrender.com/auth/google/callback` | wahi URL + `/auth/google/callback` |
| `API_ID` | **Part 1 wala api_id** | my.telegram.org |
| `API_HASH` | **Part 1 wala api_hash** | my.telegram.org |
| `TELEGRAM_API_ID` | **wahi api_id** | my.telegram.org |
| `TELEGRAM_API_HASH` | **wahi api_hash** | my.telegram.org |

`TOKEN_ENC_KEY` banane ke liye laptop/terminal me:

```bash
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

### Jo aapko **nahi** daalni

| Variable | Kyun nahi |
|---|---|
| `TELEGRAM_API_URL` | **Render khud bharta hai** — yehi aap dhoondh rahe the |
| `TELEGRAM_FILES_URL` | wahi, Render khud bharta hai |
| `TELEGRAM_WORK_DIR` | already set hai |

Bas yahi aapka jawab hai — **us URL ko aap nahi likhte, Render likhta hai.**

---

# PART 3 — woh URL aakhir kahan hai? (dekhna ho to)

Agar aap use **dekhna** chahte ho:

| # | Kahan | Kya karo |
|---|---|---|
| 1 | Render dashboard | `driveup-tg-api` service kholo |
| 2 | Upar | **Connect** button dabao |
| 3 | **Internal** tab chuno | Yahan likha hoga kuch aisa:<br>`driveup-tg-api-a1b2:8081` |
| 4 | **Yehi woh URL hai** | Bot isi se baat karta hai |

Do baatein yaad rakho:

- **`a1b2` wala hissa random hai** aur har deploy par badal sakta hai. Isliye
  `render.yaml` me haath se likha nahi hai — `fromService: hostport` se Render
  khud bhar deta hai. Agar aap haath se likhoge aur woh badal gaya, bot band ho jayega.
- **8081** API hai, **8080** files ke liye. Code dono sambhaal leta hai.

---

# PART 4 — ek baar bot ko "logout" karao (zaroori!)

Ek bot token ek waqt me **ya** Telegram cloud par chalta hai **ya** aapke server
par. Dono par nahi. Isliye switch karne se pehle ek baar logout karna padta hai.

Browser me ye link kholo (apna token daal kar):

```
https://api.telegram.org/bot<APNA_TOKEN_YAHAN>/logOut
```

Screen par `{"ok":true,"result":true}` dikhega. Bas ho gaya.

> Iske baad Telegram cloud ~10 minute tak locked rehta hai. Normal hai, ghabrana nahi.

**Kab karna hai:** Render par deploy **poora hone ke baad**, ek baar. Har deploy par nahi.

---

# PART 5 — check karo ki chal gaya

| # | Kahan | Kya karo |
|---|---|---|
| 1 | Telegram | apne bot ko `/start` bhejo |
| 2 | Phir | `/system` bhejo (aap owner ho to chalega) |
| 3 | Screen par dekho | Ye dikhna chahiye:<br>`🔌 Bot API: self-hosted (local)`<br>`🌐 http://driveup-tg-api-xxxx:8081`<br>`📦 per-file cap: 2000 MB` |

Agar `Telegram cloud` likha hai → `TELEGRAM_API_URL` set nahi hua. Deploy dobara karo.

Ab **2000 MB tak ki file** jaayegi.

---

# PART 6 — kuch galat ho gaya to

| Jo dikhe | Matlab | Kya karo |
|---|---|---|
| `Bot API: Telegram cloud` | URL set nahi hua | `render.yaml` me `TELEGRAM_API_URL` ka `fromService` block check karo, redeploy |
| `API_ID/API_HASH missing` | Part 1 ke values nahi daale | Dono services me daalo (do jagah jaate hain!) |
| `409 Conflict` | Bot do jagah chal raha hai | Part 4 wala logout karo, purani service band karo |
| `401 Unauthorized` | api_hash galat | my.telegram.org se dobara copy karo |
| Deploy fail: "image not found" | GHCR image pull nahi hui | `ghcr.io/gramiojs/telegram-bot-api:latest` — public hai, config check karo |
| File phir bhi nahi ja rahi | Local server chal raha, par files tak nahi pahunch raha | `TELEGRAM_FILES_URL` set hona chahiye (Blueprint khud karta hai) |

---

# Sabse chhota summary

```
1. my.telegram.org → API development tools → api_id + api_hash copy karo
2. GitHub par render.yaml daalo
3. Render → New → Blueprint → repo → Apply
4. 4 values daalo: API_ID, API_HASH, TELEGRAM_API_ID, TELEGRAM_API_HASH
   (baaki Google/Bot wale pehle se hai)
5. Telegram me ek baar logOut karo
6. Bot ko /system bhejo → "self-hosted (local)" dikhe to kaam ho gaya
```

`TELEGRAM_API_URL` kabhi type nahi karna — woh Render banata hai.
