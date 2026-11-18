# Railway par local Bot API — bilkul shuru se

## Pehle wahi baat, Railway ke liye

`TELEGRAM_API_URL` **kahin milta nahi — banta hai.** Telegram app me aisa koi
setting nahi hai. Aap ek chhota server chalate ho, aur uska address hi woh URL hai.

```
Pehle:   bot  ──────────────►  api.telegram.org   (20 MB, kisi ka control nahi)
Ab:      bot  ──►  aapka server  ──►  Telegram     (2000 MB, aapka control)
                     ↑
              yahi TELEGRAM_API_URL hai
```

Aur **nahi chahiye agar 20 MB tak kaafi hai** — kuch bhi set na karo, bot
`api.telegram.org` par chalega, 20 MB tak ki files jayengi. Bas.

Railway par aapke paas **do raaste** hain. Dono neeche hain.

---

# Raasta A — 20 MB tak kaafi (aasan)

Kuch bhi extra nahi karna:

1. Railway par sirf **ek** service: aapka DriveUp repo
2. `TELEGRAM_API_URL` **mat** daalo
3. Bas

`/system` par dikhega: `🔌 Bot API: Telegram cloud`, `📦 per-file cap: 20 MB`.

**Note:** `API_ID` / `API_HASH` akela dalne se kuch nahi hota — 20 MB hi rahega.
Woh values sirf tab kaam aati hain jab URL bhi ho.

---

# Raasta B — 2000 MB chahiye

## Part 1 · api_id + api_hash (5 minute)

| # | Kahan | Kya karo |
|---|---|---|
| 1 | Browser | `https://my.telegram.org` kholo |
| 2 | Wahan | phone number **+91** ke saath, jaise `+919876543210` |
| 3 | **Next** | |
| 4 | Telegram app | ek **code** aayega (SMS nahi) — woh daalo |
| 5 | Login ke baad | **API development tools** par click |
| 6 | Form | **App title:** `DriveUp` · **Short name:** `driveup` · **Platform:** `Other` · URL khaali |
| 7 | **Create application** | |
| 8 | Screen par | **`api_id`** (number, jaise `12345678`) aur **`api_hash`** (lamba text) |

> ⚠️ Yeh **bot token nahi hai** — alag cheez hai. Dono chahiye.
> `api_hash` password jaisa hai, kisi ko mat do. Ye reset nahi hota.

---

## Part 2 · Bot API server banao

Aap **project canvas** par ho (jahan aapki bot service dikh rahi hai).

| # | Kya karo |
|---|---|
| 1 | **+ New** / **Create** dabao |
| 2 | **Docker Image** chuno |
| 3 | Image name mein daalo: `ghcr.io/gramiojs/telegram-bot-api:latest` |
| 4 | **Deploy** dabao (Enter ke baad) |
| 5 | Service ban jayegi — usko kholo |

### 2.1 Naam badlo — **zaroori nahi ab**

Purani guide kehti thi naam bilkul `tg-bot-api` rakhna padega. **Ab aisa nahi hai.**

Neeche wale steps mein hum `${{...}}` reference variables use karenge — usme
service ka **actual naam** automatically bhar jaata hai. Naam kuch bhi ho, chalega.

> Phir bhi naam `tg-api` rakh dena aasan rehta hai padhne mein.

### 2.2 Variables daalo

Us service par → **Variables** tab → **+ New Variable**:

| Variable | Value |
|---|---|
| `TELEGRAM_API_ID` | Part 1 wala number |
| `TELEGRAM_API_HASH` | Part 1 wala text |
| `TELEGRAM_LOCAL` | `1` |
| `FILE_SERVER` | `1` |
| `PORT` | `8081` |

`TELEGRAM_LOCAL=1` = 20 MB limit hat jayegi.
`FILE_SERVER=1` = file server (port 8080) chalu ho jayega, jisse bot files padh sakega.

### 2.3 Volume (disk) lagao

**Settings** → **Volumes** → **+ Add Volume**:

```
Mount path:  /var/lib/telegram-bot-api
Size:        10 GB
```

Bina volume ke: server chalega, par har redeploy par files chali jayengi.

> 📌 Railway par **ek volume = ek service**. Aapka bot is volume ko share **nahi**
> kar sakta. Isliye `FILE_SERVER=1` chahiye — bot files HTTP se padhega. Yeh
> design yahin tay hua hai, aur sahi hai.

### 2.4 Public domain **mat** banao

Ye service private rahegi. Public domain mat generate karo — zaroorat nahi,
aur bina zaroorat internet par kholna theek nahi.

---

## Part 3 · Bot ko is server se jodo

Apni **DriveUp bot** service kholo → **Variables**.

### 3.1 Telegram API ka address

Do variables add karo. **Type karne ki zaroorat nahi — reference use karo:**

```
TELEGRAM_API_URL    =  http://${{tg-api.RAILWAY_PRIVATE_DOMAIN}}:8081
TELEGRAM_FILES_URL  =  http://${{tg-api.RAILWAY_PRIVATE_DOMAIN}}:8080
```

`tg-api` ki jagah **apni** service ka naam daalo (jo aapne Part 2 me rakha).

### ⚠️ Railway ka sabse bada trap

Log `${{tg-api.PORT}}` likhte hain — **yeh kaam nahi karta.** Railway ka
auto-generated `PORT` reference resolve nahi hota, aur reference khaali aata hai.

Isliye **port haath se likhna padta hai**: `8081` aur `8080`.

Yahi wajah hai ki maine Port ko Part 2 me `PORT=8081` set karwaya — fixed rehta hai.

### 3.2 API credentials

```
API_ID      <Part 1 wala number>
API_HASH    <Part 1 wala text>
```

### 3.3 Jo variables **nahi** daalni

| Variable | Kyun |
|---|---|
| `TELEGRAM_WORK_DIR` | default `/var/lib/telegram-bot-api` hi sahi hai |
| koi public URL | private network use kar rahe hain |

Ab **bot service ko redeploy** karo.

---

## Part 4 · Ek baar logOut (zaroori)

Ek bot token ek waqt me **ya** cloud par chalta hai **ya** aapke server par.
Dono par nahi.

Browser me kholo (apna token daal kar):

```
https://api.telegram.org/bot<APNA_TOKEN>/logOut
```

`{"ok":true,"result":true}` dikhega. Bas.

> **10 minute** tak cloud locked rehta hai. Uske baad wapas bhi ja sakte ho —
> pakka nahi hai.

**Kab:** deploy poora hone ke **baad**, ek baar. Har deploy par nahi.

---

## Part 5 · Check karo

| # | Kya karo |
|---|---|
| 1 | Bot ko `/start` bhejo |
| 2 | Phir `/system` bhejo |

Dikhna chahiye:

```
🔌 Bot API: self-hosted (local)
🌐 http://tg-api.railway.internal:8081
📥 files: http://tg-api.railway.internal:8080
📦 per-file cap: 2000 MB
```

`Telegram cloud` likha hai? → `TELEGRAM_API_URL` set nahi hua / reference resolve nahi hua.

---

# Railway vs Render — kya alag hai

| | Railway | Render |
|---|---|---|
| Private hostname | `naam.railway.internal` — **naam fix, aap chunte ho** | `naam-<random>:port` — hash badalta hai |
| Bot ko kaise bhejein | `${{service.RAILWAY_PRIVATE_DOMAIN}}` | `fromService: hostport` |
| Port reference | ❌ **kaam nahi karta** — haath se likho | ✅ `hostport` me aata hai |
| Volume | ek volume = ek service, share nahi | per service |
| Redeploy par downtime | volume ke saath hota hai | hota hai |
| Config file | `railway.json` (build/deploy) | `render.yaml` (poora blueprint) |

**Sabse bada farq:** Railway me port haath se likhna padta hai, Render me nahi.
Aur Railway me poori service ka naam fix rehta hai, isliye `${{}}` reference
saaf kaam karta hai.

---

# Part 6 · Kuch galat ho to

| Jo dikhe | Matlab | Kya karo |
|---|---|---|
| `Bot API: Telegram cloud` | URL set nahi / resolve nahi hua | Service name `${{}}` me sahi hai? Redeploy |
| `API_ID/API_HASH missing` | dono jagah daalne hain | server service **aur** bot service, dono me |
| `409 Conflict` | bot do jagah chal raha hai | Part 4 logOut, purani deployment band |
| `401 Unauthorized` | api_hash galat | my.telegram.org se dobara copy |
| `http://tg-api.railway.internal:8081` par timeout | services alag project/environment me | dono **ek hi project** me hone chahiye |
| File upload fail, chhoti file bhi | file server se bytes nahi aa rahe | `FILE_SERVER=1` laga hai? `TELEGRAM_FILES_URL` 8080 par hai? |
| Volume ke baad redeploy bahut slow | Railway volumes par downtime | normal hai (Railway volumes par replica nahi chalte) |

---

# Sabse chhota summary

**20 MB tak:** kuch nahi karna.

**2000 MB chahiye:**
```
1. my.telegram.org → API development tools → api_id + api_hash
2. Railway → + New → Docker Image → ghcr.io/gramiojs/telegram-bot-api:latest
3. Us service me: TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_LOCAL=1,
   FILE_SERVER=1, PORT=8081  +  volume /var/lib/telegram-bot-api (10 GB)
4. Bot service me:
   TELEGRAM_API_URL   = http://<service>.RAILWAY_PRIVATE_DOMAIN:8081
   TELEGRAM_FILES_URL = http://<service>.RAILWAY_PRIVATE_DOMAIN:8080
   API_ID / API_HASH
5. Ek baar logOut
6. /system → "self-hosted (local)" dikhe = ho gaya
```

Port **8081** (API) aur **8080** (files) — `${{...PORT}}` se nahi, haath se.
