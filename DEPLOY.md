# Deployment Guide — Al Rehman Goods Transport

Target: **Render** (web service) · **SQLite on a persistent disk** now · **Supabase (Postgres)** later.
The repo is **public**, so the live database is **never committed** — it is uploaded after deploy via
**Settings → Backup → Restore**.

---

## 1. Push the code to GitHub (public)

A local git repo with a first commit is already prepared (the database, uploads, and `.venv`
are excluded by `.gitignore`). Create an **empty public repo** on GitHub (no README), then:

```bash
git remote add origin https://github.com/<your-username>/<repo-name>.git
git branch -M main
git push -u origin main
```

Verify on GitHub that there is **no `data/` folder and no `.db` file** in the repo.

## 2. Create the Render service

1. Render dashboard → **New → Blueprint** → connect your GitHub repo.
2. Render reads `render.yaml` and proposes the `al-rehman-goods-transport` web service
   (Starter plan, 1 GB disk mounted at `/var/data`).
3. Before the first deploy, set the environment variable **`INITIAL_ADMIN_PASSWORD`**
   to a strong password (this is the one-time login used only until you restore your real data).
   `SECRET_KEY` is generated automatically.
4. Click **Apply / Deploy**.

> The Starter plan ($7/mo) is required because **persistent disks are not available on the free tier**.
> On the free tier the database would reset on every redeploy. If you prefer to stay free, the better
> option is to move to Supabase now (see section 5) and host the web service free.

## 3. Load your current data

1. Open `https://<your-app>.onrender.com/login` and sign in as **admin** with the
   `INITIAL_ADMIN_PASSWORD` you set.
2. Go to **Settings → Backup & Restore → Restore**, upload your local `data/app.db`.
3. The app reloads with all your real data and your real users (admin / ceo / rehmangoods).
   From now on use your real credentials; the seeded admin is gone.

## 4. Notes / limitations (SQLite phase)

- **Uploaded images** (delivery/loading/receipt photos) are stored on the app filesystem under
  `static/uploads/` and are **not** on the persistent disk, so they do not survive a redeploy.
  Tell me if you want uploads moved onto the disk too (small change).
- Daily automatic backups are written to `/var/data/backups` on the disk.

## 5. Later: switch to Supabase (Postgres)

1. In Supabase, create a project and copy the connection string (Session pooler / URI).
2. Add the Postgres driver to `requirements.txt`:  `psycopg[binary]>=3.1,<4.0`
3. In Render, change `DATABASE_URL` to:
   `postgresql+psycopg://<user>:<password>@<host>:<port>/<db>`
4. Remove the `disk:` block from `render.yaml` (no longer needed) and redeploy.
5. Recreate your data in Postgres (a one-time migration from SQLite → Postgres; I can script this).

> Note: a few schema-migration helpers in `core/database.py` are SQLite-specific and are skipped
> automatically on non-SQLite databases (`create_all` builds the full schema on Postgres). The
> one-time data migration is the main task for the switch — ask me when you're ready.
