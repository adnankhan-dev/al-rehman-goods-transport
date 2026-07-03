# CLAUDE.md — Al Rehman Goods Transport

Guidance for Claude Code when working in this repository. Read this first in any new conversation.

---

## 1. What this is

**Al Rehman Goods Transport** — a transport/logistics ERP for a Pakistani goods-transport
business. It tracks orders (deliveries), vehicles & vehicle owners, contractors, materials,
sites (to/from locations), plants, petrol pumps, diesel/fuel logs, a financial ledger
(transactions & balances), and printable bills/statements.

- **Business:** Al Rehman Goods Transport, Bahtr Mor Wah Cantt.
  Contacts: Ahsan Niazi 0307-2342827, Inam Khan 0301-5749086. (These appear on printed letterheads.)
- **Users:** internal staff + admin. Role/permission-gated (e.g. `diesel.view`, `diesel.create`,
  `bills.*`). Admin sees extra controls (e.g. "Entered by / Entry date" filters).

## 2. Stack & layout

- **Backend:** FastAPI (`APIRouter` per module, combined via `app.include_router`), served by **uvicorn**.
- **ORM:** SQLAlchemy 2.x. **Templates:** Jinja2. **Forms:** WTForms.
- **PDF:** xhtml2pdf. **Sessions:** itsdangerous.
- **DB:** local **SQLite** at `data/app.db`; production **Postgres** on **Supabase** (psycopg 3).
- **Hosting:** **Render** (see `render.yaml`), pulling from GitHub `main`.
- **Python entrypoint:** `run.py` (has bundled-venv relaunch logic). App factory: `create_app()`
  in `al_rehman_goods_transport/__init__.py` / `app.py`.

Package layout (`al_rehman_goods_transport/`): `api/`, `core/` (auth, config, database, flash,
templating), `models/`, `repositories/`, `routes/`, `services/`, `forms/`, `templates/`,
`static/`, `utils/`, `tests/`.

Config: `core/config.py` exposes `settings` (`database_url`, `host`, `port`, …). `DATABASE_URL`
env var overrides the DB.

## 3. ⚠️ Hard rules (read before editing/testing)

1. **NEVER wipe `data/app.db`.** It is the real local DB (163 orders as of 2026-07-03; the count
   grows with use — record it before any test run and verify it is unchanged after). Tests historically wiped it
   because importing the package builds the engine against the live `DATABASE_URL` before test
   overrides apply. Protections live in `tests/__init__.py` (it sets `os.environ["DATABASE_URL"]`
   to a throwaway `_test_app.db`, sets `settings.database_url`, and calls `configure_engine(...)`).
   - **When testing manually:** copy `app.db` to a `mktemp -d` temp file and point at the copy.
     After ANY test run, verify `SELECT COUNT(*) FROM "orders"` on `data/app.db` is unchanged.
2. **Pin FastAPI/Starlette.** `requirements.txt` pins `fastapi==0.138.0`, `starlette==1.3.1`.
   A silent rebuild once upgraded Starlette 0.x→1.x, which changed `include_router` internals and
   broke URL resolution (`NoMatchFound: bills.print_bill`) — every route on live 500'd. Do not
   loosen these pins without testing route name resolution on Starlette 1.x. The app builds a
   name→route index from each module's flat `router.routes` (via `api_route_objects` in
   `api/router.py`) to stay compatible with 1.x nesting.
3. **Rotate exposed secrets.** A real Supabase DB password was pasted into chat earlier. It MUST be
   rotated (Supabase → Project Settings → Database → Reset database password). Never commit secrets.
4. **Destructive admin action:** the in-app **"Restore Data Backup"** wipes and reloads the whole DB.
   It only runs on explicit admin upload + confirmation. Treat with care, especially in cloud mode.
5. **Additive migrations only.** SQLAlchemy `create_all` does NOT alter existing tables. New columns
   are added via cross-DB `ALTER TABLE ... ADD COLUMN` helpers in `core/database.py` (`init_db()`
   calls `_upgrade_schema()`, `_add_order_entry_audit_columns()`, `_add_petrol_pump_opening_balance()`,
   …). Add a new helper for each new column; keep it idempotent and SQLite+Postgres safe.

## 4. Architecture notes

- **Services** (`services/`) hold business logic; **repositories** wrap queries; **routes** are thin.
- **Balance/ledger engine:** `services/transactions.py`. `_apply_transaction_effect` mutates entity
  balances. `TransactionInput` supports a chosen `date` (combined to datetime for Postgres in
  `_transaction_kwargs`). Transaction types include `petrol_pump_payment`, `vehicle_owner_receipt`,
  `vehicle_payment`, `vehicle_advance`, etc. Entities: company, contractor, vehicle owner, petrol
  pump, plant.
- **Orders** (`models/order.py`) have `created_at`, `created_by_id` (FK user), `created_by`,
  `entered_by_name` — used by admin "Entered by / Entry date" filters. Advances now live in the
  **ledger**, not per-order (advance column removed from order form, statements, and bills).
- **Delivery receipt numbers** on orders are unique. **Diesel receipt numbers** are unique too.

## 5. Domain concepts (important for statements/bills)

- **Bills:** no edit — **delete-and-recreate** only. Grouping hierarchy in bills AND the orders
  "Print Statement": **To site → From site → material**, with multi-select site/material filters.
  Statements include contractor/vehicle rates, payment summaries, **net profit**, and plant costs.
- **Petrol pump statement** (`services/petrol_pumps.py::statement`): a **running/carry-forward**
  statement. `opening_balance = pump.opening_balance + prior_diesel − prior_payments` (activity
  before the period); `net_payable = opening_balance + period_diesel − payments_total`. Payments are
  matched robustly by `Transaction.petrol_pump_id == pump.id` OR (`entity_type=="petrol_pump"` AND
  `entity_id==pump.id`). Pumps have an editable **opening/previous balance**
  (`models/petrol_pump.py`, `opening_balance`), set via create/update forms; folded into running
  `balance`.
- **Fuel Log statement** (`routes/diesel.py`, `templates/diesel/*`) MUST reconcile to the pump
  statement: **Net Payable = previous/opening balance + diesel − payments** (the fuel log previously
  omitted the opening balance and disagreed with the pump statement). "Previous balance" on the fuel
  log = the selected pump's opening, or the SUM of all in-view pumps' openings when unfiltered.
- **Receipt sorting:** both the fuel log and the pump statement sort by receipt number **numerically
  ascending** (768 before 1001), blanks/non-numeric last. Helper: `services/diesel.py::receipt_sort_key`
  → returns `(0, int, "")` / `(1, 0, text)` / `(2, 0, "")`. `list_entries` fetches then Python-sorts.
- **Per-pump diesel prices:** date-range prices (`PetrolPumpPrice`) auto-fill the diesel amount by
  date (`price_for_date`, `prices_map`). Diesel **amount is authoritative** — litres never alter the
  amount. Duplicate diesel receipts are blocked. New diesel entry date defaults to the last entered date.
- **Vehicle owners:** payments/outstanding/standalone diesel appear in their statement + bill.
  "Receipt from Vehicle Owner" transaction (owners sometimes buy fuel from our pump and pay cash;
  balances can go negative). Ledger advances are reflected; per-order advance column removed.
- **Smart Save-Rates (order form):** saved rates (`ContractorRate`) are scoped by contractor +
  to-site + optional from-site/material/**vehicle owner** (owner chosen by the user — rates can
  differ per owner on the same route; NULL owner = any). After confirming an order, if the entered
  rates aren't already saved for that scope, a popup offers to save them with Valid From/Upto dates
  (`save_rate`/`rate_effective_from`/`rate_effective_to` hidden fields → `_maybe_save_rate_from_order`,
  gated by `rates.create`). `RateService.save_rate_from_order` end-dates any overlapping same-scope
  rate. Lookup tie-break scores: owner match +4, from-site +2, material +1; `/api/rates/lookup`
  accepts `vehicle_id` and resolves the owner server-side. The contractor rates print deliberately
  does NOT show the vehicle-owner column (customer-facing).
- **Plants:** related orders shown when a plant is opened; plants have printable statements like pumps.
- **Print/PDF layout:** letterhead + page-break handling (avoid breaking right after the header);
  don't print internal phrasing like "net of diesel and advances" or developer formula captions —
  keep captions **customer-facing** (this is production).
- **Settings:** manual monthly-entry form; collapsible filters with the search box outside the collapse.

## 6. Testing

- Run: `PYTHONPATH=. .venv/Scripts/python.exe -m unittest al_rehman_goods_transport.tests.test_services al_rehman_goods_transport.tests.test_routes`
- Expect **one pre-existing failure** on Windows (a backup test) — that's known/acceptable.
- **Always** confirm `data/app.db` orders count is unchanged afterward (compare before/after).

## 7. Git / workflow conventions

- This repo pushes to GitHub `main`, which Render auto-deploys. **Commit/push only when the user asks.**
- End commit messages with:
  `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`
- Windows line-ending warnings (LF→CRLF) on commit are harmless.
- Useful scripts at repo root: `backup_supabase.py` (read-only JSON backup via `MetaData().reflect`),
  `migrate_to_postgres.py`. Backups must use `settings.database_url` correctly (it previously pointed
  at the live DB during tests — fixed).

## 8. Standalone desktop app (planned, not built)

The user wants a standalone Windows desktop app. Decided approach:

- **Wrapper:** pywebview (native window over local uvicorn on a random `127.0.0.1` port) + PyInstaller
  to a single `.exe`; optional Inno Setup installer + `logo1.png`→`.ico`. Uses Edge WebView2 (present
  on Win 11). (Alternative considered: chromeless `msedge --app=` launcher; Electron/Tauri rejected as
  overkill; PWA suggested for the hosted Render site.)
- **Data source: BOTH / switchable** (user's choice). Launcher reads a config
  (`%APPDATA%\AlRehman\config.json`, `mode: local|cloud` + Supabase URL) and sets `DATABASE_URL`
  BEFORE `create_app()` — no app code changes needed. Store local `app.db` under `%APPDATA%\AlRehman\`
  (survives reinstalls). Disable the destructive "Restore Data Backup" in cloud mode unless confirmed.
- **Status:** user said "not yet, just advising." Do NOT scaffold until asked. When asked, need: a fresh
  (rotated) Supabase connection string for the cloud default.

---

_Keep this file current: when a decision, constraint, or domain rule is established in conversation,
add it here so future sessions start with the context._
