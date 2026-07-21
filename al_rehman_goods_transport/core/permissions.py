from __future__ import annotations

from typing import Iterable


LEGACY_PERMISSION_CODES = {
    "orders.manage",
    "ledger.manage",
    "contractors.manage",
    "sites.manage",
    "materials.manage",
    "vehicle_owners.manage",
    "vehicles.manage",
    "plants.manage",
    "petrol_pumps.manage",
}

PERMISSION_GROUPS = [
    {
        "key": "workspace",
        "label": "Workspace",
        "description": "High-level access to the main workspace areas.",
        "permissions": [
            {"code": "dashboard.view", "label": "Dashboard", "description": "View the main dashboard and summary metrics."},
            {"code": "reports.view", "label": "Reports", "description": "Open operational, financial, and time reports."},
            {"code": "settings.view", "label": "Settings", "description": "Open the settings workspace."},
            {"code": "settings.manage", "label": "Manage Settings", "description": "Update ERP-wide settings such as diesel rate and backups."},
            {"code": "users.manage", "label": "Manage Users", "description": "Create users, reset passwords, and control access."},
        ],
    },
    {
        "key": "orders",
        "label": "Orders",
        "description": "Trip intake, dispatch, and delivery records.",
        "permissions": [
            {"code": "orders.view", "label": "View Orders", "description": "Open order lists, detail pages, and print views."},
            {"code": "orders.create", "label": "Create Orders", "description": "Create new orders."},
            {"code": "orders.edit", "label": "Edit Orders", "description": "Update or complete existing orders."},
            {"code": "orders.delete", "label": "Delete Orders", "description": "Delete existing orders."},
            {"code": "orders.approve", "label": "Approve Orders", "description": "Review pending orders, set contractor/vehicle rates, and approve or reject them."},
            {"code": "orders.adjust_vehicle", "label": "Adjust Vehicle Quantity", "description": "Set a separate vehicle-delivered quantity on an order (when the vehicle measurement is lower than the contractor's)."},
        ],
    },
    {
        "key": "ledger",
        "label": "Ledger",
        "description": "Billing, settlements, and transaction posting.",
        "permissions": [
            {"code": "ledger.view", "label": "View Ledger", "description": "View ledger pages, bills, and transaction details."},
            {"code": "ledger.create", "label": "Create Ledger Entries", "description": "Post transactions and create bills."},
            {"code": "ledger.edit", "label": "Edit Ledger Entries", "description": "Update settlements and editable transaction records."},
            {"code": "ledger.delete", "label": "Delete Ledger Entries", "description": "Delete editable transaction records."},
            {"code": "ledger.approve", "label": "Approve Ledger Entries", "description": "Review pending transactions and bills and approve or reject them."},
            {"code": "ledger.admin", "label": "Administer Bills", "description": "Add or remove trips/vehicles on an existing bill and edit billed orders. Reserved for administrators."},
        ],
    },
    {
        "key": "diesel",
        "label": "Diesel",
        "description": "Vehicle fuel entries logged per trip or date.",
        "permissions": [
            {"code": "diesel.view", "label": "View Diesel Entries", "description": "Open diesel entry lists and detail pages."},
            {"code": "diesel.create", "label": "Create Diesel Entries", "description": "Log new diesel entries for vehicles."},
            {"code": "diesel.edit", "label": "Edit Diesel Entries", "description": "Update existing diesel entries."},
            {"code": "diesel.delete", "label": "Delete Diesel Entries", "description": "Delete diesel entry records."},
            {"code": "diesel.approve", "label": "Approve Diesel Entries", "description": "Review pending diesel entries and approve or reject them."},
        ],
    },
    {
        "key": "rates",
        "label": "Contractor Rates",
        "description": "Fixed rate schedules per contractor, route, and date range.",
        "permissions": [
            {"code": "rates.view", "label": "View Rates", "description": "Open contractor rate lists and detail pages."},
            {"code": "rates.create", "label": "Create Rates", "description": "Create new contractor rate schedules."},
            {"code": "rates.edit", "label": "Edit Rates", "description": "Update existing contractor rate schedules."},
            {"code": "rates.delete", "label": "Delete Rates", "description": "Delete contractor rate records."},
        ],
    },
    {
        "key": "masters",
        "label": "Master Records",
        "description": "Contractors, sites, fleet, plants, and supply masters.",
        "permissions": [
            {"code": "contractors.view", "label": "View Contractors", "description": "Open contractor lists and profiles."},
            {"code": "contractors.create", "label": "Create Contractors", "description": "Create contractor records."},
            {"code": "contractors.edit", "label": "Edit Contractors", "description": "Update contractor records."},
            {"code": "contractors.delete", "label": "Delete Contractors", "description": "Delete contractor records."},
            {"code": "sites.view", "label": "View Sites", "description": "Open site records."},
            {"code": "sites.create", "label": "Create Sites", "description": "Create site records."},
            {"code": "sites.edit", "label": "Edit Sites", "description": "Update site records."},
            {"code": "sites.delete", "label": "Delete Sites", "description": "Delete site records."},
            {"code": "materials.view", "label": "View Materials", "description": "Open material records."},
            {"code": "materials.create", "label": "Create Materials", "description": "Create material records."},
            {"code": "materials.edit", "label": "Edit Materials", "description": "Update material records."},
            {"code": "materials.delete", "label": "Delete Materials", "description": "Delete material records."},
            {"code": "vehicle_owners.view", "label": "View Vehicle Owners", "description": "Open vehicle owner records."},
            {"code": "vehicle_owners.create", "label": "Create Vehicle Owners", "description": "Create vehicle owner records."},
            {"code": "vehicle_owners.edit", "label": "Edit Vehicle Owners", "description": "Update vehicle owner records."},
            {"code": "vehicle_owners.delete", "label": "Delete Vehicle Owners", "description": "Delete vehicle owner records."},
            {"code": "vehicles.view", "label": "View Vehicles", "description": "Open vehicle records."},
            {"code": "vehicles.create", "label": "Create Vehicles", "description": "Create vehicle records."},
            {"code": "vehicles.edit", "label": "Edit Vehicles", "description": "Update vehicle records."},
            {"code": "vehicles.delete", "label": "Delete Vehicles", "description": "Delete vehicle records."},
            {"code": "plants.view", "label": "View Plants", "description": "Open plant records."},
            {"code": "plants.create", "label": "Create Plants", "description": "Create plant records."},
            {"code": "plants.edit", "label": "Edit Plants", "description": "Update plant records."},
            {"code": "plants.delete", "label": "Delete Plants", "description": "Delete plant records."},
            {"code": "petrol_pumps.view", "label": "View Petrol Pumps", "description": "Open petrol pump records."},
            {"code": "petrol_pumps.create", "label": "Create Petrol Pumps", "description": "Create petrol pump records."},
            {"code": "petrol_pumps.edit", "label": "Edit Petrol Pumps", "description": "Update petrol pump records."},
            {"code": "petrol_pumps.delete", "label": "Delete Petrol Pumps", "description": "Delete petrol pump records."},
        ],
    },
]


PERMISSION_METADATA = {
    permission["code"]: permission
    for group in PERMISSION_GROUPS
    for permission in group["permissions"]
}
ALL_PERMISSION_CODES = tuple(PERMISSION_METADATA.keys())
VALID_PERMISSION_CODES = set(ALL_PERMISSION_CODES) | LEGACY_PERMISSION_CODES

ROLE_DEFINITIONS = {
    "admin": {
        "label": "Administrator",
        "description": "Full access across the ERP, including user management.",
        "permissions": ALL_PERMISSION_CODES,
    },
    "operations": {
        "label": "Operations Manager",
        "description": "Operations access with new-record entry by default, without edit/delete, finance, or user administration.",
        "permissions": (
            "dashboard.view",
            "reports.view",
            "orders.view",
            "orders.create",
            "diesel.view",
            "diesel.create",
            "rates.view",
            "rates.create",
            "contractors.view",
            "contractors.create",
            "sites.view",
            "sites.create",
            "materials.view",
            "materials.create",
            "vehicle_owners.view",
            "vehicle_owners.create",
            "vehicles.view",
            "vehicles.create",
            "plants.view",
            "plants.create",
            "petrol_pumps.view",
            "petrol_pumps.create",
        ),
    },
    "data_entry": {
        "label": "Data Entry Operator",
        "description": "Daily operational data entry without edit/delete, finance, reporting, settings, or user administration.",
        "permissions": (
            "dashboard.view",
            "orders.view",
            "orders.create",
            "diesel.view",
            "diesel.create",
            "rates.view",
            "contractors.view",
            "contractors.create",
            "sites.view",
            "sites.create",
            "materials.view",
            "materials.create",
            "vehicle_owners.view",
            "vehicle_owners.create",
            "vehicles.view",
            "vehicles.create",
            "plants.view",
            "plants.create",
            "petrol_pumps.view",
            "petrol_pumps.create",
        ),
    },
    "accounts": {
        "label": "Accounts Manager",
        "description": "Billing, settlements, reporting, and settings with create access by default, without edit/delete or user administration.",
        "permissions": (
            "dashboard.view",
            "reports.view",
            "settings.view",
            "settings.manage",
            "orders.view",
            "orders.approve",
            "diesel.view",
            "diesel.approve",
            "ledger.view",
            "ledger.create",
            "ledger.approve",
            "rates.view",
            "contractors.view",
            "sites.view",
            "materials.view",
            "vehicle_owners.view",
            "vehicles.view",
            "plants.view",
            "petrol_pumps.view",
        ),
    },
    "viewer": {
        "label": "Viewer",
        "description": "Read-only access to daily records and reports.",
        "permissions": (
            "dashboard.view",
            "reports.view",
            "orders.view",
            "diesel.view",
            "ledger.view",
            "rates.view",
            "contractors.view",
            "sites.view",
            "materials.view",
            "vehicle_owners.view",
            "vehicles.view",
            "plants.view",
            "petrol_pumps.view",
        ),
    },
}


def normalize_role(role: str | None) -> str:
    role_value = (role or "").strip().lower()
    return role_value if role_value in ROLE_DEFINITIONS else "viewer"


def normalize_permission_codes(codes: Iterable[str] | None) -> list[str]:
    selected = {str(code).strip() for code in (codes or []) if str(code).strip() in VALID_PERMISSION_CODES}
    ordered_current = [code for code in ALL_PERMISSION_CODES if code in selected]
    ordered_legacy = [code for code in sorted(LEGACY_PERMISSION_CODES) if code in selected]
    return ordered_current + ordered_legacy


def role_permissions(role: str | None) -> list[str]:
    normalized_role = normalize_role(role)
    return list(ROLE_DEFINITIONS[normalized_role]["permissions"])


def role_label(role: str | None) -> str:
    return ROLE_DEFINITIONS[normalize_role(role)]["label"]


def permission_label(code: str) -> str:
    return PERMISSION_METADATA.get(code, {}).get("label", code)


def permission_description(code: str) -> str:
    return PERMISSION_METADATA.get(code, {}).get("description", "")


def grants_permission(granted_codes: Iterable[str] | None, requested_code: str) -> bool:
    granted = set(normalize_permission_codes(granted_codes))
    if requested_code in granted:
        return True

    resource, _, action = requested_code.rpartition(".")
    if not resource or not action:
        return False

    legacy_manage_code = f"{resource}.manage"
    if legacy_manage_code in granted:
        return True

    if action == "view":
        if any(f"{resource}.{permission_action}" in granted for permission_action in ("create", "edit", "delete")):
            return True

    return False
