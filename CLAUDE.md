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
- **Ledger update (transactions, bills, opening balances, letterhead):**
  - **Generic transactions:** the transaction form is Direction (Payment To / Receipt From) → Entity
    Type → Entity → Date → Amount. The internal type is `f"{entity_type}_{direction}"`; every entity
    (contractor, vehicle owner, plant, petrol pump, vehicle) supports both directions. Added inverse
    effects `contractor_payment`, `plant_receipt`, `petrol_pump_receipt`, `vehicle_receipt` in
    `services/transactions.py::_apply_transaction_effect`. Two `_transaction_input_from_form` copies
    exist (routes/ledger.py — the live one the form posts to — and routes/transactions.py); keep both in sync.
  - **Transaction + bill approval** (mirrors orders/diesel): new manual transactions and new bills are
    created **pending** via `approval_status` (default `approved`; migrations additive/idempotent).
    Pending transactions post no balances and are hidden from the ledger/statements; pending bills are
    hidden and cannot be settled. Settlement/system transactions stay auto-approved (`create_transaction(..., approval_status="pending")` only from the manual form). New `ledger.approve` permission (admin + accounts).
    Shared **Ledger Approvals** page `/ledger/pending` (route defined in routes/transactions.py) lists
    pending transactions + bills; approve/reject in routes/transactions.py and routes/bills.py. Sidebar
    badge via `nav_pending_ledger`.
  - **Order edit ⇒ re-approval:** editing an approved (unbilled) order reverses its financials and sets it back to pending (`OrderService.update_order`).
  - **Opening balances:** contractor/vehicle_owner/plant/vehicle gained `opening_balance` (pump already had it), settable on create/edit forms; `effective_balance = balance + opening_balance`. Shown as "Previous Balance".
  - **Bill numbering:** `BILL-<FIRSTWORD>-NNN` (uppercase first word of entity name, per-prefix sequence, collision-safe) — `repositories/billing.py::next_bill_number(entity_name=…)`.
  - **Bill display:** the bill view top box shows only **Net Payable**; every bill (view + print) has a
    **Financial Summary** = previous balance + this bill − payments/receipts in the bill tenure → net
    payable (`bill_snapshot["financial_summary"]`). Pending bills show an approve/reject banner and hide the settle form.
  - **Editable letterhead:** business name/address/two contact lines are stored in `AppSetting`
    (`letterhead_*`), edited on the Settings page (`update_letterhead` action). Injected into every
    template via `core/templating._base_context` as `letterhead`; the two Python-generated documents
    (orders statement, manual entry form) fetch `SettingsService().get_letterhead()`.
- **Session timeout:** `SessionMiddleware` runs with `max_age=settings.session_max_age` (env
  `SESSION_MAX_AGE`, default **3 hours**) — a sliding **idle** timeout (activity re-issues the signed
  cookie; away longer than this ⇒ forced re-login). Before this it had no max_age, so Starlette's
  14-day default kept sliding on every request (flash writes) and users effectively never logged out.
- **Printing:** every print document forces **high-contrast black ink** under `@media print` (black
  text, black 1px borders, `border-collapse: collapse` so first/last-row borders match the interior,
  white backgrounds so dark header/total bands print clean). The block is marked `AK-BLACK-PRINT` in
  each standalone print template's `<style>`, the two Python-generated docs (orders statement, manual
  form — braces doubled inside the f-strings), and `static/css/style.css` (for base-template pages).
- **Editable print columns:** optional columns on Bill (contractor trip table: vehicle, receipt),
  Vehicle Owner statement, Contractor statement, and Fuel log can be shown/hidden from **Settings →
  Print Columns** (`SettingsService.TEMPLATE_COLUMNS`, stored as JSON in AppSetting
  `template_column_prefs`; `get/update_template_columns`). Hiding works by CSS: each optional column's
  cells carry a `col-<key>` class (key matches the catalog, e.g. `col-from_site`), and each template
  emits `.col-<key>{display:none}` for its doc's hidden list — the whole column (incl. colspan totals)
  collapses. `template_columns` (dict of doc→hidden list) is injected via `core/templating._base_context`.
  A hidden column simply no-ops in any table that doesn't use its class.
- **Vehicle-delivered quantity (order):** `Order.vehicle_delivered_quantity` (nullable) captures the
  vehicle's own measured quantity when it is *lower* than the contractor's. When set, the vehicle
  payable is computed on it (`Order.effective_vehicle_quantity` → used by `gross_vehicle_amount`);
  contractor amount always uses `delivered_quantity`. So profit absorbs the difference. It is NOT on
  the add-order form — only the **edit** form, and only for users with the new `orders.adjust_vehicle`
  permission (admin by default); unauthorised edits preserve the stored value. Reflected as the
  "Delivered" quantity in all vehicle-owner statements/bills (view, print, excel) and in
  `group_owner_activity_by_vehicle`. Additive migration `_add_order_vehicle_delivered_quantity`.
- **Fuel log sort:** the fuel log (`DieselService.list_entries`) and the pending diesel list
  (`list_pending`) sort by **date, then receipt number** (numeric; blanks last). (Earlier it was
  receipt-number only.)
- **Order & Diesel approval workflow:** every new order and every new fuel-log diesel entry is
  created **pending** and posts **no financials** (no ledger, no vehicle-owner/pump/vehicle balance,
  no P&L, not billable) and is **hidden** from all lists/reports/statements until an approver clears
  it. Gate field: `Order.approval_status` / `DieselEntry.approval_status` (`'pending'`/`'approved'`,
  default `'approved'` so existing rows are grandfathered; migrations `_add_order_approval_columns`
  / `_add_diesel_approval_columns` are additive + idempotent). Pending orders also carry
  `status='Pending Approval'` (approved → `'Completed'`) so the many existing `status=="Completed"`
  queries exclude them automatically; the query sites **without** a status filter were gated
  explicitly (`repositories/orders.py` list/count, `services/dashboard.py` counts, diesel fuel-log
  list, billing/reports/petrol-pump/vehicle-owner diesel queries, `plant_loadings_all`). New
  permissions `orders.approve` / `diesel.approve` (admin + accounts by default). **Rates are NOT
  entered at order creation** — the add-order form has no rate fields; the approver sets contractor
  & vehicle rates on the **Pending Approvals** screen (`/orders/pending`, grouped contractor → to-site
  → material → vehicle owner, rate inputs pre-filled from saved `ContractorRate` suggestions) and can
  save the applied rate (same save-rate popup, now on approval). Approve is per-row / per-group /
  approve-all; reject deletes the pending record (safe — no financials posted). `OrderService`:
  `create_order` (pending, no sync), `approve_order` (sets rates + `sync_order_financials`),
  `reject_order`, `list_pending_approvals`; `DieselService`: `create_entry` (pending, no balance),
  `approve_entry` (applies balances), `reject_entry`. Sidebar badges via `nav_pending_orders` /
  `nav_pending_diesel` injected in `core/templating._base_context`.
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

## 5b. Financial engine rules (2026-07 restructure)

- **Previous balance is COMPUTED, never stored.** `services/financials.py::entity_period_financials`
  derives it from history (trips/diesel/loadings + signed ledger transactions before the period
  start). Manual `opening_balance` columns were retired; a migration converted saved values into
  backdated `previous_balance` transactions (type `previous_balance`: moves only the entity
  balance, never company cash; amount may be negative; posted via the ledger form).
- **Bill settlement is retired.** No settle route/UI/settled-outstanding columns. Bills are pure
  period documents; money moves only as ledger receipts/payments, which every bill/statement
  reflects live by date (backdated transactions automatically land in the right bill period).
- **Bill timeframe is mandatory** (start+end); start defaults to the day after the entity's last
  bill end (`BillingService.suggested_start_date`), editable. The timeframe drives the FINANCIAL
  section only — pick lists and candidate validation are date-unbounded.
- **Every bill/statement ends with the shared "Account Summary"** — macro `financial_section` in
  `templates/_financial_summary.html`: Previous Balance, + period activity, signed receipt/payment
  lines (never the words "Less"/"Add"/"carried forward"), `= Current Balance`, plus detail tables
  "Received from <name>" / "Payment paid to <name>" (`Transaction.type_label`).
- **Statements have date filters** (owner + contractor view pages; pump/plant prints accept
  date_from/date_to): activity before the range becomes the Previous Balance.
- **Admin bill editing** (`ledger.admin`, admin-only): add/remove trips or a whole vehicle on an
  existing bill (`BillingService.remove_order_from_bill` — pass kind='diesel' for owner-bill fuel
  rows — `remove_vehicle_from_bill`, `add_orders_to_bill`, totals recalc), and edit billed orders
  (`update_order(allow_billed=True)` recomputes attached bill totals).
- **Order-linked diesel and order advances are RETIRED** (no data existed): fuel lives only in the
  Fuel Log (DieselEntry), advances only in the ledger (vehicle_payment). Order form/view, bills,
  statements, and pump accruals no longer reference them.
- **Order view** shows Entered By / Approved By (+ timestamps) to users with `orders.approve`.

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
