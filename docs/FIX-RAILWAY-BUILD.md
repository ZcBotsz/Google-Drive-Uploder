# Fixing "Railpack could not determine how to build the app"

You hit this on Railway:

```
⚠ Script start.sh not found
✖ Railpack could not determine how to build the app.
   The following languages are supported: … Python …
   The app contents that Railpack analyzed contains:
   ./
   └── driveup/
```

Read the last three lines again — that is the whole diagnosis.

---

## What actually happened

Railway looked at the **root of your repository** and found a single folder:
`driveup/`. Your real files — `Dockerfile`, `main.py`, `requirements.txt`,
`railway.json` — are one level **inside** it.

So Railpack had three problems at once:

1. **It never saw `railway.json`.** Railway reads config from the repo root. Your
   `railway.json` is inside `driveup/`, so Railway fell back to its default
   builder (Railpack).
2. **Railpack found no language marker at the root** — no `requirements.txt`, no
   `main.py`, nothing. Hence "could not determine how to build the app."
3. **It never saw the `Dockerfile` either**, which is the thing you actually
   wanted it to use.

This is not a bug in the app. The code is fine; the folder is one level too deep.

---

## Fix A — two clicks, no re-upload (recommended)

Keep your repo exactly as it is.

1. Open your project → click the service box (`Psjd`).
2. **Settings** tab.
3. Find **Source** → **Root Directory**.
4. Type: `driveup`  (no leading slash, no trailing slash)
5. Click **Deploy** (or press `⇧ Enter`).

Railway now clones only `driveup/`, sees `railway.json` and `Dockerfile`, and
builds with Docker instead of Railpack.

> ⚠️ **After you set the Root Directory**, the volume mount path stays
> `/app/data` — that is *inside the container*, not the repo, so it does not
> change. Nothing else needs updating.

### If the build starts but fails later

Check it is really using Docker: the build log should show
`using builder DOCKERFILE` or Docker layer steps (`FROM python:3.12-slim`,
`RUN pip install …`). If it still says *Railpack*, the Root Directory did not
save — re-open Settings and confirm the field is not empty.

---

## Fix B — flatten the repo (works on Railway *and* Render, no settings)

Use `DriveUp-deploy-flat.zip`. Unlike the earlier zip, its files sit at the
**archive root** — there is no `driveup/driveup/` nesting.

### B1 · With git (safest)

```bash
# start from a clean folder
mkdir driveup-flat && cd driveup-flat
unzip ~/Downloads/DriveUp-deploy-flat.zip     # files land here, not in a subfolder
ls              # Dockerfile  main.py  railway.json  driveup/  …
```

Then push **this** folder as the repo:

```bash
git init
git add .
git commit -m "DriveUp flat layout"
git branch -M main
git remote add origin https://github.com/YOUR-NAME/driveup.git
git push -u origin main --force
```

`--force` overwrites the old nested layout. That is intentional here — the old
history only contained the wrong structure. If you would rather keep history,
skip `--force`, pull first, and `git mv driveup/* .` instead.

### B2 · With the GitHub website (no git installed)

The upload page **preserves folders**, so `driveup/` still ends up as a
subfolder. To get a real flat upload:

1. Open your repo on github.com.
2. Select **everything inside** the `driveup` folder (the `driveup` app folder,
   `Dockerfile`, `main.py`, …) and **Delete** them.
3. **Add file → Upload files** → drag in the **contents** of the unzipped
   `DriveUp-deploy-flat.zip` (select all files *and* the `driveup/` package
   folder, then drag them in one go).
4. Commit.

After this, the repo root must show:

```
Dockerfile   docker-entrypoint.sh   main.py   railway.json
render.yaml  requirements.txt       README.md  driveup/  docs/
```

If you see a single `driveup/` folder at the root and nothing else, you are
back to the current state — repeat step 2 or just use **Fix A**.

### B3 · Verify before deploying

On github.com, the repo home page should list files, not folders. Quick check
from a terminal:

```bash
git clone https://github.com/YOUR-NAME/driveup.git check
ls check
# expect: Dockerfile  docker-entrypoint.sh  driveup  docs  main.py
#         railway.json  render.yaml  requirements.txt  scripts
```

---

## After either fix — finish the deploy

1. **Variables** tab → Raw Editor → paste (replace `<…>`):

   ```
   BOT_TOKEN=<BotFather token>
   OWNER_ID=<your numeric id>
   BOT_USERNAME=<bot username, no @>
   GOOGLE_CLIENT_ID=<…apps.googleusercontent.com>
   GOOGLE_CLIENT_SECRET=<GOCSPX-…>
   TOKEN_ENC_KEY=<44-char Fernet key>
   SESSION_SECRET=<48-char random string>
   PUBLIC_URL=https://<domain from step 3 below>
   GOOGLE_REDIRECT_URI=https://<domain from step 3 below>/auth/google/callback
   DRIVE_SCOPE_MODE=file
   UPLOAD_MODE=auto
   UPLOAD_CHUNK_MB=8
   MAX_GLOBAL_UPLOADS=4
   MAX_USER_UPLOADS=2
   ```

2. **Attach a volume**: right-click the canvas → **Attach Volume** →
   mount path exactly `/app/data`. Without it, every redeploy wipes the database.

3. **Settings → Networking → Generate Domain** → copy
   `https://…up.railway.app` → put it in `PUBLIC_URL` and
   `GOOGLE_REDIRECT_URI` → also add
   `https://…up.railway.app/auth/google/callback` to your Google OAuth client.

4. **Settings → Replicas = 1.** Two replicas on one SQLite file means
   `database is locked` and double uploads.

5. Open `https://…up.railway.app/health`. You want
   `{"ok":true,"status":"ok","db":true,"version":"1.0.0"}`.

6. Then BotFather → `/newapp` → **Web App URL** = that same domain.

---

## What a correct build looks like

```
✅ using builder DOCKERFILE
✅ FROM python:3.12-slim
✅ RUN pip install --no-cache-dir -r requirements.txt
✅ COPY driveup ./driveup
✅ Successfully built
→ config OK — bot_token=1234…TEST  fernet=set
→ database ready at /app/data/driveup.sqlite3
→ web listening on 0.0.0.0:8080
→ bot @YourBot online — /start to begin
```

If you see `Script start.sh not found` again, the Root Directory is still wrong —
that message only ever appears when Railpack is used instead of Docker.

---

## Also possible: Render

The same nesting breaks Render the same way. Two options there:

* **Fix A for Render**: Settings → **Root Directory** → `driveup`
* **Fix B**: upload the flat zip; `render.yaml` is found at the root and the
  blueprint works as documented.

---

## One more thing I changed while fixing this

`railway.json` no longer sets `startCommand`. Setting it can override the
Dockerfile's `ENTRYPOINT`, which is the script that fixes the volume's
permissions (`unable to open database file` otherwise). With the entrypoint in
charge, the container starts as root, chowns `/app/data`, and drops to the
unprivileged `driveup` user.
