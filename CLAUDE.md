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

1. **NEVER wipe `data/app.db`.** It is the real local DB (1526 orders as of 2026-08-01; the count
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

## 5c. Users, privileges & records update (2026-07)

- **User display name:** `User.name` (nullable; additive migration `_add_user_name_column`).
  `User.display_name` = `name or username`. The header dropdown + sidebar show `display_name`;
  the top-right header is a Bootstrap dropdown (name + `@username` + **Logout**). **Username is for
  login only.** Create/edit user forms have a "Full Name" field.
- **Privilege save is exact:** `UserManagementService._resolved_permissions` returns exactly the
  submitted codes (no silent fall-back to role defaults — that used to re-grant the whole role when
  you unchecked everything). Non-admin with zero privileges → validation error. Routes pass
  `form_data.getlist("permission_codes")` (create still defaults to role perms as a new-user
  convenience). Admins always implicitly hold `ALL_PERMISSION_CODES`.
- **One-time approval backfill:** `_backfill_role_approval_permissions()` (own AppSetting marker
  `role_approval_perm_backfill`, NOT the `DATA_BACKFILL_VERSION` set — bumping that would re-run the
  destructive financial backfill) grants `orders/diesel/ledger.approve` to existing non-admin users
  whose role default now includes them. Additive only.
- **`records.edit_no_reapproval`** (new permission, "Edit Without Re-approval"): editing an already
  **approved order** keeps it approved and preserves the approver-set rates, re-syncing financials as
  a delta (`OrderService.update_order(keep_approval=…)` via `snapshot_order`+`sync_order_financials`).
  Without it, editing an approved order still reverses financials and returns it to pending (the old
  "back to approval with cleared rates" behaviour). Diesel entries and ledger transactions already
  edit **in place** while staying approved, so the flag is only wired to orders.
- **`audit.view`** (new permission, "View Records & Audit"): reveals the Entry & Approval Record +
  per-record **Change Log** (`services/audit.list_entity_audit(entity_type, id)`) on the order view,
  shows the orders-list Entered-by / Entry-date filters, and opens `/settings/audit`
  (`require_any_permission("settings.manage","audit.view")`). Edits are already audited via
  `record_audit(..., "update", ...)` for order / diesel_entry / transaction / bill.
- **Dependent filters:** Orders list — To-Site checkboxes filter by the selected contractor
  (`Site.contractor_id`, client-side JS on `#contractor_id`). Fuel Log — Vehicle dropdown filters by
  the selected Vehicle Owner (`Vehicle.owner_id`, client-side JS on `#owner_id`). Both restore the
  full list when the parent is cleared.
- **Pump payable in P&L:** diesel is deducted from the vehicle-owner payable and paid direct to the
  pump, so it is a **split of the vehicle payment, never subtracted again from profit**. Both P&Ls
  (`ReportService._profit_loss_context` and `OrderService.orders_pnl` →
  `_period_pump_payable` summing approved `DieselEntry.amount` over the date range / owner) surface
  `pump_payable` + `vehicle_owner_cash` as memo lines / a card; net profit is unchanged.
- **Diesel rate removed from Settings** (it lives per-pump). Report intro copy was de-jargonised
  (no release-note style descriptions shown to end users).

## 5d. Privileges, expenses & pickers update (2026-08)

- **Admin privileges are editable.** `User.permission_codes` / `can()` no longer grant everything to
  any admin — they honour the stored list. Only `User.PROTECTED_ADMIN_ID` (**user id 1**, the primary
  admin) implicitly holds `ALL_PERMISSION_CODES`, and `update_user_access` refuses to change its role
  or privileges at all (its edit page shows an explanatory panel instead of the form). Every other
  account — admins included — runs on exactly what is ticked, so `_validate_permissions` now rejects
  an empty list for **all** roles. One-time `_backfill_admin_explicit_permissions()` (marker
  `admin_explicit_perm_backfill`) writes the full code list onto every non-primary admin first, so
  nobody silently loses the access they already had.
- **Financial entities are ledger accounts.** `FinancialEntity.entity_kind` is `expense` | `loan`
  (`ENTITY_KINDS` in `models/financial_entity.py`). `Transaction.financial_entity_id` + entity type
  `financial_entity` on the transaction form post through the **main ledger** (approval + audit +
  P&L), via types `financial_entity_payment` / `financial_entity_receipt`. Balance convention matches
  contractors (positive = they owe us): paying an **expense** entity moves company cash only; paying
  a **loan** entity also raises their balance. The old `FinancialEntityTransaction` module remains as
  the entity master list.
- **Vehicle is NOT a top-level entity type.** The owner is the account we deal with. Choosing
  `vehicle_owner` reveals an **optional** vehicle picker, filtered client-side to that owner's
  vehicles. With a vehicle chosen the type becomes `vehicle_payment`/`vehicle_receipt` (booked
  against that vehicle, still rolling up to the owner); left blank it is an ordinary
  `vehicle_owner_payment`. Both `_transaction_input_from_form` copies (routes/ledger.py,
  routes/transactions.py) implement this — keep them in sync.
- **Per-vehicle payments show in statements.** `group_owner_activity_by_vehicle(..., advance_rows)`
  was already computed but rendered nowhere. Owner statement (view + print) and vehicle-owner bills
  (view + print) now show, per vehicle: trips → **Fuel-Log Diesel table → Payments Made table** → a
  calculation table ending in **Vehicle Payment Due** = gross − fuel-log diesel − payments.
  `BillingService._vehicle_payments_for_bill` supplies the bill side (period-bounded, approved only;
  owner-level payments are excluded there because the Account Summary already carries them).
- **Company-owned vehicles.** `VehicleOwner.is_company_expense` marks a holder record for our own
  vehicles (live: *Ahsan Petrol Expense*, id 39, vehicles 2693/778/265). Their fuel is a **P&L
  expense**, not an owner payable: excluded from `_accrual_rows`, from ledger owner payables, and
  `DieselService._adjust_vehicle_balance` no longer moves the holder's balance. Classification is
  marker-guarded (`company_expense_owner_classified`) **separately from the column add** — gating a
  data step on "did we just add the column?" makes it unrepeatable and it silently never runs if the
  column lands on its own.
- **P&L shows every expense.** `_profit_loss_context` now reads: Revenue → Vehicle/Plant payments →
  **Direct Trip Costs** → **Gross Profit** → operating expenses (company vehicle fuel + ledger
  overheads: `other_expense` and payments to Expense-kind entities, via
  `ReportRepository.company_vehicle_diesel_filtered` / `overhead_expense_transactions`) →
  **Operating Expenses** → Total Expenses → **Net Profit**. Loan-entity payments are excluded (a
  balance move, not a cost). Line `kind` is `positive`/`negative`/`memo`/`subtotal`; **memo lines
  explain a split and are never deducted again**. A "Where Every Rupee of Expense Went" panel
  itemises each expense with its basis. `OrderService.orders_pnl` subtracts the same operating
  expenses so the orders-list strip agrees with the report.
- **Bill summary** carries `summary_record_label`/`summary_record_count` and
  `summary_quantity_label`/`summary_quantity`(+`_unit`) from `_bill_summary_counts` — trips+quantity
  for contractor/owner bills, entries+litres for pump bills, loadings+quantity for plant bills. Shown
  on the bill view tiles, the print summary grid, and the Excel export.
- **Search-and-select everywhere.** `static/js/script.js` auto-enhances dropdowns into the
  type-to-search combobox, on forms **and list-page filter bars**, with no per-template tagging: a
  select is enhanced if its id/name ends in **`_id`** (every record picker is named after its FK;
  fixed lists like `kind`, `entity_type`, `billing_status`, `report_type`, `role`, `payment_method`
  never are) **or** it has ≥10 options. Override with `data-searchable="true"|"false"`. The binder
  skips options that are `hidden`/`disabled` (so dependent filters keep working), clears to whichever
  placeholder the select actually has (`""` on filters, `"0"` on WTForms), and re-syncs its visible
  text when something else changes the select — guarded by a `selfDispatching` flag so a
  partially-typed term is not wiped on each keystroke.

## 6. Testing

- Run: `PYTHONPATH=. .venv/Scripts/python.exe -m unittest al_rehman_goods_transport.tests.test_services al_rehman_goods_transport.tests.test_routes`
- Expect **one pre-existing failure** on Windows (a backup test) — that's known/acceptable.
- **Always** confirm `data/app.db` orders count is unchanged afterward (compare before/after).

## 7. Git / workflow conventions

- This repo pushes to GitHub `main`, which Render auto-deploys. **Commit/push only when the user asks.**
- End commit messages with the model that wrote them, e.g.:
  `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`
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
