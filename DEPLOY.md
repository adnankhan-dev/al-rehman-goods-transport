# Deployment Guide — Al Rehman Goods Transport

Target: **Render (free web service)** + **Supabase (Postgres)**.
Code is on GitHub (public); no database or secrets are committed.

---

## 1. Create the Supabase database

1. supabase.com → **New project**. Choose a region near you, set a database password.
2. After it provisions, click **Connect** (top bar) → **Session pooler** → copy the URI.
   It looks like:
   `postgresql://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres`
3. Convert it to the SQLAlchemy + psycopg form (used by both the migration and the app):
   - change the scheme `postgresql://` → `postgresql+psycopg://`
   - append `?sslmode=require`

   Final form:
   `postgresql+psycopg://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres?sslmode=require`

> Use the **Session pooler** string (IPv4) — the plain "Direct connection" is IPv6-only and won't work from Render.

## 2. Copy your current data into Supabase (run locally, once)

```bash
cd "d:/Al Rehman Goods/Al Rehman Goods Transport"
.venv/Scripts/python -m pip install -r requirements.txt
.venv/Scripts/python migrate_to_postgres.py "postgresql+psycopg://postgres.<ref>:<password>@<host>:5432/postgres?sslmode=require"
```

It creates the schema and copies every table from `data/app.db` into Supabase, then fixes id
sequences. Run it **once** into a fresh database.

## 3. Deploy on Render (free)

1. Render → **New → Blueprint** → connect GitHub → select the repo. It reads `render.yaml`
   (free web service, no disk).
2. Set environment variables in the dashboard:
   - **`DATABASE_URL`** = the `postgresql+psycopg://...` string from step 1.
   - `SECRET_KEY` is generated automatically.
   - `INITIAL_ADMIN_PASSWORD` can be left unset (your migrated data already has your users).
3. **Apply / Deploy**, then open `https://<your-app>.onrender.com/health` → `{"status":"OK"}`
   and log in with your existing credentials.

## Notes

- Free Render web services **sleep after ~15 min idle** and take a few seconds to wake on the next
  request. Fine for internal use; upgrade to a paid instance later if you want it always-on.
- The SQLite-only features (file Backup/Restore in Settings, daily file backups) automatically
  disable on Postgres. With Supabase you get its own backups; take periodic dumps from the
  Supabase dashboard.
- Uploaded images still save to the app filesystem (`static/uploads/`) and won't persist across
  redeploys on the free tier. Ask me to move uploads to Supabase Storage if you need them durable.
