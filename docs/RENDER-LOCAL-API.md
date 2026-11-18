# Render par 10 GB storage + badi files — exactly kya karna hai

Do alag sawaal hain, aur inka jawab alag hai. Pehle woh clear karte hain.

## 1. 10 GB disk ka asli matlab

`render.yaml` mein dono services par **10 GB disk** hai. Lekin ye "10 GB ki file"
nahi hai — aur ye jaan lena zaroori hai:

| Cheez | Kitni badi ho sakti hai |
|---|---|
| **Per-file limit (Telegram)** | **2000 MB**, sirf local Bot API ke saath |
| Cloud Bot API (aaj) | **20 MB** — isse zyada kabhi nahi |
| Disk par ek waqt mein kya hota hai | **sirf 32 MB se choti files** |

`UPLOAD_MODE=auto` par **32 MB se badi har file disk par likhi hi nahi jaati** —
Telegram se seedha Drive ki resumable session mein stream hoti hai. Isliye 10 GB
disk file-size ki limit nahi hai; woh database, logs aur local API ke cache ke
liye hai.

**Asli limit jo aapko chahiye:** `MAX_FILE_MB` (default 2000) aur plan ka
`max_file_mb`. 10 GB disk par aaram se 500 MB–2 GB ki files chalti hain, kyunki
woh disk par aati hi nahi.

> ⚠️ 10 GB se badi **ek** file bhi upload karni hai to `UPLOAD_MODE=disk` mat
> kijiyega — woh poori file disk par likhega. `auto` hi rakhein.

## 2. 2000 MB file ke liye local Bot API — Render par

Telegram ka cloud API 20 MB se aage kuch nahi deta. Uske liye ek self-hosted Bot
API server chahiye. `render.yaml` mein woh pehle se likha hua hai:

```
driveup-tg-api   (private service, disk 10 GB, port 8081 API + 8080 files)
driveup          (web service, disk 10 GB, port 8080)
```

Dono ek hi **region** mein hone chahiye — warna private network kaam nahi karega.
Blueprint mein dono `singapore` par hain.

### Step 1 — API_ID aur API_HASH

1. https://my.telegram.org → **API development tools**
2. App banaiye (naam kuch bhi) → `api_id` aur `api_hash` milenge
3. Dono values sambhal ke rakhein — ye **do jagah** jaati hain:
   - `driveup-tg-api` service par: `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`
   - `driveup` service par: `API_ID`, `API_HASH`

### Step 2 — Blueprint deploy

1. Render → **New → Blueprint**
2. Repo chuniye (jisme `render.yaml` ho)
3. `sync: false` wale saare fields dashboard poochhega — wahan values daaliye

**Jo cheezein aapko type karni hain:**

| Variable | Kahan se |
|---|---|
| `BOT_TOKEN` | BotFather |
| `OWNER_ID` | aapka Telegram user id |
| `BOT_USERNAME` | bot ka username, `@` ke bina |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | Google Cloud Console |
| `TOKEN_ENC_KEY` | `python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `SESSION_SECRET` | koi bhi lamba random string |
| `PUBLIC_URL` | `https://<aapka-service>.onrender.com` |
| `GOOGLE_REDIRECT_URI` | `https://<aapka-service>.onrender.com/auth/google/callback` |
| `API_ID` / `API_HASH` | Step 1 |
| `TELEGRAM_API_ID` / `TELEGRAM_API_HASH` | Step 1 (wahi values) |

**Jo aapko type nahi karna:** `TELEGRAM_API_URL` aur `TELEGRAM_FILES_URL` —
Render khud `fromService: hostport` se bhar deta hai.

### Step 3 — ek baar `logOut`

Local API par shift karne se pehle, **ek baar** ye URL browser mein kholiye
(apna token daal kar):

```
https://api.telegram.org/bot<TOKEN>/logOut
```

Ye batata hai ki bot ab cloud se nahi, aapke server se chalega. Iske baad cloud
~10 minute tak locked rehta hai — normal hai.

### Step 4 — check kariye

Bot ko `/start` bhejiye. Phir `/status` (admin) ya `/system` — wahan dikhega:

```
TELEGRAM_API_URL: http://driveup-tg-api-xxxx:8081
uses local API : True
per-file cap   : 2000 MB
```

Agar `uses local API: False` hai to `TELEGRAM_API_URL` set nahi hua.

## 3. `hostport` ka ek trap — jo pehle se fix hai

Render `fromService: hostport` **scheme ke bina** deta hai:

```
driveup-tg-api-abc123:8081        ← no http://
```

aur aiogram ka `from_base()` ise chup-chaap accept kar leta hai — phir **har API
call fail** hoti hai. User ko woh "bot kaam nahi karta" jaisa dikhta hai.

Code mein ab do jagah sambhala hua hai:

- `Settings.api_base` scheme add karta hai → `http://driveup-tg-api-abc123:8081`
- `_with_file_port()` file server ko 8080 par bhejta hai (API 8081 par hai),
  kyunki `hostport` dono ke liye ek hi value deta hai

Dono ke tests hain (`tests/test_local_api.py`).

## 4. Render par jo cheezein alag hain (Railway se)

| | Railway | Render |
|---|---|---|
| Private hostname | `service.railway.internal` (fixed) | `service-<hash>:port` (hash random) |
| Isliye variable | haath se likhna | **`fromService` se reference** karna |
| Port | aap chunte hain | `hostport` property |
| Disk | per service | per service, `sizeGB` 1 se shuru |
| Private service | n/a | `type: pserv`, koi public URL nahi |

## 5. Agar sirf 20 MB tak kaafi hai

Tab local API ki zaroorat nahi. `render.yaml` se `driveup-tg-api` service aur
`TELEGRAM_API_URL` / `TELEGRAM_FILES_URL` / `API_ID` / `API_HASH` hata dijiye.
Bot cloud API par chalega aur 20 MB tak ki files jayengi — aur `MAX_FILE_MB`
khud 20 par clamp ho jayega, taaki koi aisa wada na ho jo poora na ho sake.
