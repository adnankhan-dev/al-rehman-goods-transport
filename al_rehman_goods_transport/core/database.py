import json

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, scoped_session, sessionmaker

from .config import settings
from .permissions import ALL_PERMISSION_CODES


class Base(DeclarativeBase):
    pass


def _build_engine(database_url):
    engine_kwargs = {"future": True}
    if database_url.startswith("sqlite"):
        engine_kwargs["connect_args"] = {"check_same_thread": False}
    new_engine = create_engine(database_url, **engine_kwargs)
    if database_url.startswith("sqlite"):
        # SQLite does not enforce foreign keys unless asked per-connection.
        @event.listens_for(new_engine, "connect")
        def _enable_sqlite_fks(dbapi_connection, _record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return new_engine


engine = _build_engine(settings.database_url)
SessionLocal = scoped_session(sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session))
Base.query = SessionLocal.query_property()


def configure_engine(database_url):
    """Rebind the module-level engine/session to a different database.

    Used by the test suite to guarantee it never operates on the live
    database, regardless of when DATABASE_URL is set relative to package
    import order.
    """
    global engine
    SessionLocal.remove()
    engine = _build_engine(database_url)
    SessionLocal.configure(bind=engine)
    Base.query = SessionLocal.query_property()
    return engine


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
        SessionLocal.remove()


DATA_BACKFILL_VERSION = "1"


def init_db():
    Base.metadata.create_all(bind=engine)
    _upgrade_schema()
    _add_order_entry_audit_columns()
    _add_petrol_pump_opening_balance()
    _add_contractor_rate_vehicle_owner()
    _add_order_approval_columns()
    _add_diesel_approval_columns()
    _add_order_vehicle_delivered_quantity()
    _add_entity_opening_balances()
    _add_transaction_approval_columns()
    _add_bill_approval_columns()
    _convert_opening_balances_to_transactions()
    _run_one_time_backfills()


def _add_petrol_pump_opening_balance():
    """Add PetrolPump.opening_balance to existing databases (SQLite + Postgres)."""
    inspector = inspect(engine)
    if "petrol_pump" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("petrol_pump")}
    if "opening_balance" not in columns:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE petrol_pump ADD COLUMN opening_balance FLOAT DEFAULT 0"))


def _add_contractor_rate_vehicle_owner():
    """Add ContractorRate.vehicle_owner_id to existing databases (SQLite + Postgres).

    Saved rates can be scoped to a specific vehicle owner; NULL means the rate
    applies to any owner on the route. INTEGER is portable across both DBs."""
    inspector = inspect(engine)
    if "contractor_rate" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("contractor_rate")}
    if "vehicle_owner_id" not in columns:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE contractor_rate ADD COLUMN vehicle_owner_id INTEGER"))


def _add_entity_opening_balances():
    """Add a pre-ERP `opening_balance` to contractor / vehicle_owner / plant /
    vehicle (petrol_pump already has one). Additive, idempotent, SQLite+Postgres."""
    inspector = inspect(engine)
    for table in ("contractor", "vehicle_owner", "plant", "vehicle"):
        if table not in inspector.get_table_names():
            continue
        columns = {column["name"] for column in inspector.get_columns(table)}
        if "opening_balance" not in columns:
            with engine.begin() as connection:
                connection.execute(text(f"ALTER TABLE {table} ADD COLUMN opening_balance FLOAT DEFAULT 0"))


def _convert_opening_balances_to_transactions():
    """Retire manually-set opening balances: convert each saved value into a
    backdated 'previous_balance' ledger transaction so statements and bills
    compute the previous balance purely from history.

    Idempotent — only non-zero opening_balance rows are converted, and the
    column is zeroed afterwards. For petrol pumps the running balance already
    includes the opening (it was folded in on create/update), so only the
    marker transaction is inserted; for the other entities the opening was a
    separate display add-on (effective_balance), so it is folded into the
    stored balance here to keep totals identical."""
    inspector = inspect(engine)
    tables = {
        "contractor": ("contractor_id", True),
        "vehicle_owner": ("vehicle_owner_id", True),
        "plant": ("plant_id", True),
        "vehicle": ("vehicle_id", True),
        "petrol_pump": ("petrol_pump_id", False),
    }
    existing_tables = set(inspector.get_table_names())
    if "transaction" not in existing_tables:
        return

    with engine.begin() as connection:
        for table, (fk_column, fold_into_balance) in tables.items():
            if table not in existing_tables:
                continue
            columns = {column["name"] for column in inspector.get_columns(table)}
            if "opening_balance" not in columns:
                continue
            rows = connection.execute(
                text(f"SELECT id, opening_balance FROM {table} WHERE COALESCE(opening_balance, 0) <> 0")
            ).fetchall()
            for row_id, opening in rows:
                connection.execute(
                    text(
                        f"""
                        INSERT INTO "transaction"
                            (date, type, entity_type, entity_id, amount, description,
                             is_system_generated, approval_status, {fk_column})
                        VALUES
                            (:date, 'previous_balance', :entity_type, :entity_id, :amount,
                             :description, :system, 'approved', :fk_id)
                        """
                    ),
                    {
                        "date": "2020-01-01 00:00:00",
                        "entity_type": table,
                        "entity_id": row_id,
                        "amount": float(opening),
                        "description": "Previous balance before ERP",
                        "system": True,
                        "fk_id": row_id,
                    },
                )
                if fold_into_balance:
                    connection.execute(
                        text(f"UPDATE {table} SET balance = COALESCE(balance, 0) + :amount, opening_balance = 0 WHERE id = :id"),
                        {"amount": float(opening), "id": row_id},
                    )
                else:
                    connection.execute(
                        text(f"UPDATE {table} SET opening_balance = 0 WHERE id = :id"),
                        {"id": row_id},
                    )


def _add_transaction_approval_columns():
    """Add approval-workflow columns to the transaction table. Existing rows
    default to 'approved' so posted/system transactions are unaffected."""
    inspector = inspect(engine)
    if "transaction" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("transaction")}
    with engine.begin() as connection:
        if "approval_status" not in columns:
            connection.execute(text("ALTER TABLE \"transaction\" ADD COLUMN approval_status VARCHAR(20) DEFAULT 'approved'"))
            connection.execute(text("UPDATE \"transaction\" SET approval_status = 'approved' WHERE approval_status IS NULL"))
        if "approved_by_id" not in columns:
            connection.execute(text("ALTER TABLE \"transaction\" ADD COLUMN approved_by_id INTEGER"))
        if "approved_at" not in columns:
            connection.execute(text("ALTER TABLE \"transaction\" ADD COLUMN approved_at TIMESTAMP"))


def _add_bill_approval_columns():
    """Add approval-workflow columns to the bill table. Existing bills default
    to 'approved' so they stay visible and settleable exactly as before."""
    inspector = inspect(engine)
    if "bill" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("bill")}
    with engine.begin() as connection:
        if "approval_status" not in columns:
            connection.execute(text("ALTER TABLE bill ADD COLUMN approval_status VARCHAR(20) DEFAULT 'approved'"))
            connection.execute(text("UPDATE bill SET approval_status = 'approved' WHERE approval_status IS NULL"))
        if "approved_by_id" not in columns:
            connection.execute(text("ALTER TABLE bill ADD COLUMN approved_by_id INTEGER"))
        if "approved_at" not in columns:
            connection.execute(text("ALTER TABLE bill ADD COLUMN approved_at TIMESTAMP"))


def _add_order_vehicle_delivered_quantity():
    """Add Order.vehicle_delivered_quantity to existing databases (SQLite +
    Postgres). NULL means the vehicle is paid on the contractor delivered
    quantity, exactly as before — so existing orders are unchanged."""
    inspector = inspect(engine)
    if "orders" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("orders")}
    if "vehicle_delivered_quantity" not in columns:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE orders ADD COLUMN vehicle_delivered_quantity FLOAT"))


def _add_order_approval_columns():
    """Add the order approval-workflow columns to existing databases.

    Runs on SQLite and Postgres. Existing orders default to 'approved' so they
    stay visible and financially posted exactly as before — only newly created
    orders start 'pending'. Additive, idempotent, and never rewrites data."""
    inspector = inspect(engine)
    if "orders" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("orders")}
    with engine.begin() as connection:
        if "approval_status" not in columns:
            connection.execute(text("ALTER TABLE orders ADD COLUMN approval_status VARCHAR(20) DEFAULT 'approved'"))
            # Belt-and-suspenders: make sure every existing row is explicitly approved.
            connection.execute(text("UPDATE orders SET approval_status = 'approved' WHERE approval_status IS NULL"))
        if "approved_by_id" not in columns:
            connection.execute(text("ALTER TABLE orders ADD COLUMN approved_by_id INTEGER"))
        if "approved_at" not in columns:
            connection.execute(text("ALTER TABLE orders ADD COLUMN approved_at TIMESTAMP"))


def _add_diesel_approval_columns():
    """Add the diesel-entry approval-workflow columns to existing databases.

    Existing diesel entries default to 'approved' so pump/vehicle balances and
    statements are unchanged; only new entries start 'pending'. Additive and
    idempotent (SQLite + Postgres)."""
    inspector = inspect(engine)
    if "diesel_entry" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("diesel_entry")}
    with engine.begin() as connection:
        if "approval_status" not in columns:
            connection.execute(text("ALTER TABLE diesel_entry ADD COLUMN approval_status VARCHAR(20) DEFAULT 'approved'"))
            connection.execute(text("UPDATE diesel_entry SET approval_status = 'approved' WHERE approval_status IS NULL"))
        if "approved_by_id" not in columns:
            connection.execute(text("ALTER TABLE diesel_entry ADD COLUMN approved_by_id INTEGER"))
        if "approved_at" not in columns:
            connection.execute(text("ALTER TABLE diesel_entry ADD COLUMN approved_at TIMESTAMP"))


def _add_order_entry_audit_columns():
    """Add Order.created_at / created_by_id to existing databases.

    Unlike _upgrade_schema (SQLite-only), this runs on Postgres too: create_all
    does not ALTER an existing 'orders' table, so a live Supabase DB would
    otherwise be missing these columns. TIMESTAMP/INTEGER are portable across
    SQLite and Postgres."""
    inspector = inspect(engine)
    if "orders" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("orders")}
    with engine.begin() as connection:
        if "created_at" not in columns:
            connection.execute(text("ALTER TABLE orders ADD COLUMN created_at TIMESTAMP"))
        if "created_by_id" not in columns:
            connection.execute(text("ALTER TABLE orders ADD COLUMN created_by_id INTEGER"))


def _get_backfill_version():
    session = SessionLocal()
    try:
        from ..models import AppSetting

        setting = session.query(AppSetting).filter(AppSetting.key == "data_backfill_version").first()
        return setting.value if setting else None
    finally:
        session.close()
        SessionLocal.remove()


def _mark_backfills_done():
    session = SessionLocal()
    try:
        from ..models import AppSetting

        setting = session.query(AppSetting).filter(AppSetting.key == "data_backfill_version").first()
        if setting is None:
            setting = AppSetting(key="data_backfill_version")
            session.add(setting)
        setting.value = DATA_BACKFILL_VERSION
        session.commit()
    finally:
        session.close()
        SessionLocal.remove()


def _run_one_time_backfills():
    """Run legacy data migrations exactly once per database.

    These must not run on every boot: _backfill_financial_entities rewrites
    owner balances from vehicle sums, which would silently undo any owner
    payment recorded since the previous restart.
    """
    if _get_backfill_version() == DATA_BACKFILL_VERSION:
        return

    _backfill_vehicle_owners()
    _backfill_materials_and_order_details()
    _backfill_financial_entities()
    _apply_pending_diesel_balances()
    _mark_backfills_done()


def _upgrade_schema():
    # These are in-place migrations for legacy SQLite databases (raw ALTER/UPDATE
    # with SQLite syntax and unquoted identifiers). On Postgres the full, current
    # schema is built by create_all(), so this must not run there.
    if not settings.database_url.startswith("sqlite"):
        return

    inspector = inspect(engine)
    tables = set(inspector.get_table_names())

    if "vehicle" in tables:
        vehicle_columns = {column["name"] for column in inspector.get_columns("vehicle")}
        if "owner_id" not in vehicle_columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE vehicle ADD COLUMN owner_id INTEGER"))

    if "orders" in tables:
        order_columns = {column["name"] for column in inspector.get_columns("orders")}
        with engine.begin() as connection:
            if "bill_id" not in order_columns:
                connection.execute(text("ALTER TABLE orders ADD COLUMN bill_id INTEGER"))
            if "billed_at" not in order_columns:
                connection.execute(text("ALTER TABLE orders ADD COLUMN billed_at DATETIME"))
            if "vehicle_owner_bill_id" not in order_columns:
                connection.execute(text("ALTER TABLE orders ADD COLUMN vehicle_owner_bill_id INTEGER"))
            if "material_id" not in order_columns:
                connection.execute(text("ALTER TABLE orders ADD COLUMN material_id INTEGER"))
            if "from_site_id" not in order_columns:
                connection.execute(text("ALTER TABLE orders ADD COLUMN from_site_id INTEGER"))
            if "delivery_receipt_image" not in order_columns:
                connection.execute(text("ALTER TABLE orders ADD COLUMN delivery_receipt_image VARCHAR(255)"))
            if "company_advance_applied" not in order_columns:
                connection.execute(text("ALTER TABLE orders ADD COLUMN company_advance_applied BOOLEAN DEFAULT 0"))
                connection.execute(text("UPDATE orders SET company_advance_applied = CASE WHEN COALESCE(advance_amount, 0) > 0 THEN 1 ELSE 0 END"))
            if "remarks" not in order_columns:
                connection.execute(text("ALTER TABLE orders ADD COLUMN remarks TEXT"))

        _try_create_builty_index()

    if "material" in tables:
        material_columns = {column["name"] for column in inspector.get_columns("material")}
        with engine.begin() as connection:
            if "unit" not in material_columns:
                connection.execute(text("ALTER TABLE material ADD COLUMN unit VARCHAR(10) DEFAULT 'cft' NOT NULL"))
        if "name" in material_columns:
            with engine.begin() as connection:
                connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_material_name ON material(name)"))

    if "petrol_pump" in tables:
        pump_columns = {column["name"] for column in inspector.get_columns("petrol_pump")}
        with engine.begin() as connection:
            if "balance" not in pump_columns:
                connection.execute(text("ALTER TABLE petrol_pump ADD COLUMN balance FLOAT DEFAULT 0"))
        if "name" in pump_columns:
            with engine.begin() as connection:
                connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_petrol_pump_name ON petrol_pump(name)"))

    if "vehicle_owner" in tables:
        owner_columns = {column["name"] for column in inspector.get_columns("vehicle_owner")}
        with engine.begin() as connection:
            if "balance" not in owner_columns:
                connection.execute(text("ALTER TABLE vehicle_owner ADD COLUMN balance FLOAT DEFAULT 0"))

    if "site" in tables:
        _upgrade_site_schema(inspector)

    if "order_loading" in tables:
        _upgrade_order_loading_schema(inspector)

    if "transaction" in tables:
        transaction_columns = {column["name"] for column in inspector.get_columns("transaction")}
        with engine.begin() as connection:
            if "entity_type" not in transaction_columns:
                connection.execute(text('ALTER TABLE "transaction" ADD COLUMN entity_type VARCHAR(50)'))
            if "entity_id" not in transaction_columns:
                connection.execute(text('ALTER TABLE "transaction" ADD COLUMN entity_id INTEGER'))
            if "reference_id" not in transaction_columns:
                connection.execute(text('ALTER TABLE "transaction" ADD COLUMN reference_id INTEGER'))
            if "is_system_generated" not in transaction_columns:
                connection.execute(text('ALTER TABLE "transaction" ADD COLUMN is_system_generated BOOLEAN DEFAULT 0'))
            if "vehicle_owner_id" not in transaction_columns:
                connection.execute(text('ALTER TABLE "transaction" ADD COLUMN vehicle_owner_id INTEGER'))
            if "petrol_pump_id" not in transaction_columns:
                connection.execute(text('ALTER TABLE "transaction" ADD COLUMN petrol_pump_id INTEGER'))

    if "bill" in tables:
        bill_columns = {column["name"] for column in inspector.get_columns("bill")}
        with engine.begin() as connection:
            if "plant_id" not in bill_columns:
                connection.execute(text("ALTER TABLE bill ADD COLUMN plant_id INTEGER"))
            if "petrol_pump_id" not in bill_columns:
                connection.execute(text("ALTER TABLE bill ADD COLUMN petrol_pump_id INTEGER"))
            if "vehicle_owner_id" not in bill_columns:
                connection.execute(text("ALTER TABLE bill ADD COLUMN vehicle_owner_id INTEGER"))
            if "settled_amount" not in bill_columns:
                connection.execute(text("ALTER TABLE bill ADD COLUMN settled_amount FLOAT DEFAULT 0"))

    if "user" in tables:
        user_columns = {column["name"] for column in inspector.get_columns("user")}
        full_permissions = json.dumps(list(ALL_PERMISSION_CODES))
        with engine.begin() as connection:
            if "role" not in user_columns:
                connection.execute(text("ALTER TABLE user ADD COLUMN role VARCHAR(50) DEFAULT 'admin'"))
            if "permissions" not in user_columns:
                connection.execute(text("ALTER TABLE user ADD COLUMN permissions TEXT DEFAULT '[]'"))
            if "is_active" not in user_columns:
                connection.execute(text("ALTER TABLE user ADD COLUMN is_active BOOLEAN DEFAULT 1 NOT NULL"))
            if "last_login_at" not in user_columns:
                connection.execute(text("ALTER TABLE user ADD COLUMN last_login_at DATETIME"))

            connection.execute(text("UPDATE user SET role = 'admin' WHERE role IS NULL OR TRIM(role) = ''"))
            connection.execute(text("UPDATE user SET permissions = :permissions WHERE permissions IS NULL OR TRIM(permissions) = ''"), {"permissions": full_permissions})
            connection.execute(text("UPDATE user SET is_admin = CASE WHEN LOWER(role) = 'admin' THEN 1 ELSE is_admin END"))
            connection.execute(text("UPDATE user SET is_active = 1 WHERE is_active IS NULL"))

    if "diesel_entry" in tables:
        diesel_entry_columns = {column["name"] for column in inspector.get_columns("diesel_entry")}
        with engine.begin() as connection:
            if "balance_applied" not in diesel_entry_columns:
                connection.execute(text("ALTER TABLE diesel_entry ADD COLUMN balance_applied BOOLEAN DEFAULT 0 NOT NULL"))
            if "bill_id" not in diesel_entry_columns:
                connection.execute(text("ALTER TABLE diesel_entry ADD COLUMN bill_id INTEGER"))
            if "vehicle_balance_applied" not in diesel_entry_columns:
                connection.execute(text("ALTER TABLE diesel_entry ADD COLUMN vehicle_balance_applied BOOLEAN DEFAULT 0 NOT NULL"))
            if "vehicle_owner_bill_id" not in diesel_entry_columns:
                connection.execute(text("ALTER TABLE diesel_entry ADD COLUMN vehicle_owner_bill_id INTEGER"))

    if "order_loading" in tables:
        loading_columns = {column["name"] for column in inspector.get_columns("order_loading")}
        with engine.begin() as connection:
            if "bill_id" not in loading_columns:
                connection.execute(text("ALTER TABLE order_loading ADD COLUMN bill_id INTEGER"))

    if "contractor_rate" in tables:
        rate_columns = {column["name"] for column in inspector.get_columns("contractor_rate")}
        with engine.begin() as connection:
            if "vehicle_rate" not in rate_columns:
                connection.execute(text("ALTER TABLE contractor_rate ADD COLUMN vehicle_rate FLOAT"))


def _upgrade_site_schema(inspector):
    site_columns = {column["name"]: column for column in inspector.get_columns("site")}
    with engine.begin() as connection:
        if "is_business_site" not in site_columns:
            connection.execute(text("ALTER TABLE site ADD COLUMN is_business_site BOOLEAN DEFAULT 0"))
        if "is_archived" not in site_columns:
            connection.execute(text("ALTER TABLE site ADD COLUMN is_archived BOOLEAN DEFAULT 0 NOT NULL"))

    if not settings.database_url.startswith("sqlite"):
        return

    contractor_column = site_columns.get("contractor_id")
    if contractor_column is None or contractor_column.get("nullable", True):
        return

    with engine.begin() as connection:
        connection.execute(text("PRAGMA foreign_keys=OFF"))
        connection.execute(text("ALTER TABLE site RENAME TO site_legacy"))
        connection.execute(
            text(
                """
                CREATE TABLE site (
                    id INTEGER NOT NULL PRIMARY KEY,
                    name VARCHAR(100) NOT NULL,
                    address TEXT,
                    contact_person VARCHAR(100),
                    phone VARCHAR(50),
                    contractor_id INTEGER,
                    is_business_site BOOLEAN DEFAULT 0 NOT NULL,
                    FOREIGN KEY(contractor_id) REFERENCES contractor (id)
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO site (id, name, address, contact_person, phone, contractor_id, is_business_site)
                SELECT id, name, address, contact_person, phone, contractor_id, COALESCE(is_business_site, 0)
                FROM site_legacy
                """
            )
        )
        connection.execute(text("DROP TABLE site_legacy"))
        connection.execute(text("PRAGMA foreign_keys=ON"))


def _upgrade_order_loading_schema(inspector):
    loading_columns = {column["name"]: column for column in inspector.get_columns("order_loading")}
    plant_column = loading_columns.get("plant_id")
    if plant_column is None or plant_column.get("nullable", True):
        return

    if not settings.database_url.startswith("sqlite"):
        return

    with engine.begin() as connection:
        connection.execute(text("PRAGMA foreign_keys=OFF"))
        connection.execute(text("ALTER TABLE order_loading RENAME TO order_loading_legacy"))
        connection.execute(
            text(
                """
                CREATE TABLE order_loading (
                    id INTEGER NOT NULL PRIMARY KEY,
                    order_id INTEGER NOT NULL,
                    load_quantity FLOAT NOT NULL DEFAULT 0.0,
                    plant_id INTEGER,
                    plant_amount FLOAT NOT NULL DEFAULT 0.0,
                    loading_image VARCHAR(255),
                    FOREIGN KEY(order_id) REFERENCES orders (id),
                    FOREIGN KEY(plant_id) REFERENCES plant (id)
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO order_loading (id, order_id, load_quantity, plant_id, plant_amount, loading_image)
                SELECT id, order_id, load_quantity, plant_id, plant_amount, loading_image
                FROM order_loading_legacy
                """
            )
        )
        connection.execute(text("DROP TABLE order_loading_legacy"))
        connection.execute(text("PRAGMA foreign_keys=ON"))


def _try_create_builty_index():
    with engine.begin() as connection:
        duplicate_result = connection.execute(
            text(
                """
                SELECT builty_number
                FROM orders
                WHERE builty_number IS NOT NULL AND builty_number != ''
                GROUP BY builty_number
                HAVING COUNT(*) > 1
                """
            )
        ).fetchone()
        if duplicate_result:
            return

        connection.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_orders_builty_number ON orders(builty_number) WHERE builty_number IS NOT NULL AND builty_number != ''"
            )
        )


def _backfill_vehicle_owners():
    from ..models import Vehicle, VehicleOwner

    session = SessionLocal()
    try:
        vehicles = (
            session.query(Vehicle)
            .filter(Vehicle.owner_id.is_(None), Vehicle.owner_name.is_not(None))
            .all()
        )
        if not vehicles:
            return

        owner_names = sorted({(vehicle.owner_name or "").strip() for vehicle in vehicles if (vehicle.owner_name or "").strip()})
        existing_owners = {
            owner.name: owner
            for owner in session.query(VehicleOwner).filter(VehicleOwner.name.in_(owner_names)).all()
        }

        missing_names = [name for name in owner_names if name not in existing_owners]
        if missing_names:
            new_owners = [VehicleOwner(name=name) for name in missing_names]
            session.add_all(new_owners)
            session.flush()
            existing_owners.update({owner.name: owner for owner in new_owners})

        for vehicle in vehicles:
            owner_name = (vehicle.owner_name or "").strip()
            if not owner_name:
                continue

            owner = existing_owners.get(owner_name)
            if owner:
                vehicle.owner_id = owner.id
                vehicle.sync_owner_name()

        session.commit()
    finally:
        session.close()
        SessionLocal.remove()


def _backfill_materials_and_order_details():
    from ..models import Material, Order, OrderDieselEntry, OrderLoading

    session = SessionLocal()
    try:
        orders = session.query(Order).all()
        if not orders:
            return

        material_lookup = {
            material.name.strip().lower(): material
            for material in session.query(Material).all()
            if (material.name or "").strip()
        }

        for order in orders:
            if order.material_id is None and (order.material_type or "").strip():
                key = order.material_type.strip().lower()
                material = material_lookup.get(key)
                if material is None:
                    material = Material(name=order.material_type.strip())
                    session.add(material)
                    session.flush()
                    material_lookup[key] = material
                order.material_id = material.id

            if not order.loadings and order.plant_id and order.quantity is not None:
                session.add(
                    OrderLoading(
                        order_id=order.id,
                        load_quantity=order.quantity or 0,
                        plant_id=order.plant_id,
                        plant_amount=order.plant_amount or 0,
                    )
                )

            if not order.diesel_entries and (order.diesel_amount or 0) > 0:
                session.add(OrderDieselEntry(order_id=order.id, amount=order.diesel_amount or 0))

            if (order.advance_amount or 0) > 0 and not order.company_advance_applied:
                order.company_advance_applied = True

        session.commit()
    finally:
        session.close()
        SessionLocal.remove()


def _backfill_financial_entities():
    from ..models import DieselEntry, Order, PetrolPump, Transaction, Vehicle, VehicleOwner

    session = SessionLocal()
    try:
        for owner in session.query(VehicleOwner).all():
            owner.balance = sum((vehicle.balance or 0) for vehicle in owner.vehicles)

        pump_payment_exists = session.query(Transaction).filter(Transaction.type == "petrol_pump_payment").first() is not None
        standalone_diesel_exists = session.query(DieselEntry).first() is not None
        if not pump_payment_exists and not standalone_diesel_exists:
            # Keep any pre-ERP 'previous_balance' postings in the recomputed figure.
            for pump in session.query(PetrolPump).all():
                previous = sum(
                    (txn.amount or 0)
                    for txn in session.query(Transaction).filter(
                        Transaction.type == "previous_balance",
                        Transaction.petrol_pump_id == pump.id,
                    ).all()
                )
                pump.balance = previous + sum((entry.amount or 0) for entry in pump.diesel_entries)

        for transaction in session.query(Transaction).all():
            if transaction.entity_type:
                continue

            if transaction.vehicle_id:
                transaction.entity_type = "vehicle"
                transaction.entity_id = transaction.vehicle_id
            elif transaction.vehicle_owner_id:
                transaction.entity_type = "vehicle_owner"
                transaction.entity_id = transaction.vehicle_owner_id
            elif transaction.contractor_id:
                transaction.entity_type = "contractor"
                transaction.entity_id = transaction.contractor_id
            elif transaction.plant_id:
                transaction.entity_type = "plant"
                transaction.entity_id = transaction.plant_id
            elif transaction.petrol_pump_id:
                transaction.entity_type = "petrol_pump"
                transaction.entity_id = transaction.petrol_pump_id

        advance_transactions = {
            transaction.reference_id
            for transaction in session.query(Transaction).filter(Transaction.type == "vehicle_advance").all()
            if transaction.reference_id
        }
        for order in session.query(Order).filter(Order.advance_amount > 0, Order.company_advance_applied.is_(True)).all():
            if order.id in advance_transactions:
                continue
            vehicle = session.get(Vehicle, order.vehicle_id)
            owner_id = vehicle.owner_id if vehicle and vehicle.owner_id else None
            session.add(
                Transaction(
                    type="vehicle_advance",
                    entity_type="vehicle",
                    entity_id=order.vehicle_id,
                    reference_id=order.id,
                    amount=order.advance_amount,
                    description=f"Advance for order #{order.id}",
                    vehicle_id=order.vehicle_id,
                    vehicle_owner_id=owner_id,
                    is_system_generated=True,
                )
            )

        session.commit()
    finally:
        session.close()
        SessionLocal.remove()


def _apply_pending_diesel_balances():
    """Apply pump and vehicle/owner balances for DieselEntry records that predate balance tracking."""
    from ..models import DieselEntry, PetrolPump, Vehicle, VehicleOwner

    session = SessionLocal()
    try:
        # Apply pump balances for entries not yet applied
        pump_pending = (
            session.query(DieselEntry)
            .filter(
                DieselEntry.balance_applied.is_(False),
                DieselEntry.petrol_pump_id.isnot(None),
            )
            .all()
        )
        for entry in pump_pending:
            pump = session.get(PetrolPump, entry.petrol_pump_id)
            if pump:
                pump.balance = float(pump.balance or 0.0) + float(entry.amount or 0.0)
            entry.balance_applied = True

        # Apply vehicle/owner balance deductions for entries not yet applied
        vehicle_pending = (
            session.query(DieselEntry)
            .filter(
                DieselEntry.vehicle_balance_applied.is_(False),
                DieselEntry.vehicle_id.isnot(None),
            )
            .all()
        )
        for entry in vehicle_pending:
            vehicle = session.get(Vehicle, entry.vehicle_id)
            if vehicle:
                vehicle.balance = float(vehicle.balance or 0.0) - float(entry.amount or 0.0)
                if vehicle.owner_id:
                    owner = session.get(VehicleOwner, vehicle.owner_id)
                    if owner:
                        owner.balance = float(owner.balance or 0.0) - float(entry.amount or 0.0)
            entry.vehicle_balance_applied = True

        if pump_pending or vehicle_pending:
            session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
        SessionLocal.remove()
