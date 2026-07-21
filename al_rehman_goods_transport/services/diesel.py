from datetime import UTC, datetime
from datetime import date as date_type
from datetime import timedelta

from sqlalchemy import func

from ..extensions import db
from ..models import DieselEntry, Order, PetrolPump, Vehicle, VehicleOwner
from ..repositories import LookupRepository
from .exceptions import NotFoundError, ValidationError


def _safe(value):
    return float(value or 0.0)


def utc_now():
    return datetime.now(UTC).replace(tzinfo=None)


def receipt_sort_key(receipt_number):
    """Sort receipts numerically (768 before 1001); non-numeric last, blanks last."""
    text = (receipt_number or "").strip()
    if not text:
        return (2, 0, "")
    try:
        return (0, int(text), "")
    except ValueError:
        return (1, 0, text.lower())


class DieselService:
    def __init__(self, session=None):
        self.session = session or db.session

    def list_entries(self, vehicle_id=None, pump_id=None, owner_id=None, date_from=None, date_to=None, search=None, approval_status="approved"):
        q = DieselEntry.query
        # Only approved entries appear in the fuel log; pending ones live on the
        # Pending Approvals page until an approver clears them.
        if approval_status is not None:
            q = q.filter(DieselEntry.approval_status == approval_status)
        if vehicle_id:
            q = q.filter(DieselEntry.vehicle_id == vehicle_id)
        if pump_id:
            q = q.filter(DieselEntry.petrol_pump_id == pump_id)
        if owner_id:
            q = q.join(Vehicle, DieselEntry.vehicle_id == Vehicle.id).filter(Vehicle.owner_id == owner_id)
        if date_from:
            q = q.filter(DieselEntry.date >= date_from)
        if date_to:
            q = q.filter(DieselEntry.date <= date_to)
        if search:
            term = f"%{search.strip()}%"
            vehicle_ids = self.session.query(Vehicle.id).filter(Vehicle.vehicle_number.ilike(term))
            pump_ids = self.session.query(PetrolPump.id).filter(PetrolPump.name.ilike(term))
            q = q.filter(
                DieselEntry.receipt_number.ilike(term)
                | DieselEntry.notes.ilike(term)
                | DieselEntry.vehicle_id.in_(vehicle_ids)
                | DieselEntry.petrol_pump_id.in_(pump_ids)
            )
        # Sort by date, then receipt number numerically (768 before 1001, blanks last).
        entries = q.all()
        entries.sort(key=lambda e: (e.date or date_type.min, receipt_sort_key(e.receipt_number)))
        return entries

    def pump_payments_total(self, pump_id=None, date_from=None, date_to=None):
        """Total amount paid to petrol pump(s) from the ledger
        (petrol_pump_payment transactions) for the given filter scope."""
        from ..models import Transaction
        from sqlalchemy import and_, or_

        query = self.session.query(func.coalesce(func.sum(Transaction.amount), 0.0)).filter(
            Transaction.type == "petrol_pump_payment"
        )
        if pump_id:
            query = query.filter(
                or_(
                    Transaction.petrol_pump_id == pump_id,
                    and_(Transaction.entity_type == "petrol_pump", Transaction.entity_id == pump_id),
                )
            )
        if date_from:
            query = query.filter(Transaction.date >= date_from)
        if date_to:
            query = query.filter(Transaction.date < date_to + timedelta(days=1))
        return float(query.scalar() or 0.0)

    def last_entry_date(self):
        """Date of the most recently added diesel entry (for prefilling the form),
        falling back to today when there are no entries yet."""
        entry = DieselEntry.query.order_by(DieselEntry.id.desc()).first()
        return entry.date if entry and entry.date else date_type.today()

    def get_entry(self, entry_id):
        entry = DieselEntry.query.get(entry_id)
        if entry is None:
            raise NotFoundError("Diesel entry not found.")
        return entry

    def _assert_unique_receipt(self, receipt_number, exclude_id=None):
        """Reject a receipt number already used by another diesel entry
        (case-insensitive). Blank receipt numbers are allowed and not checked."""
        normalized = (receipt_number or "").strip()
        if not normalized:
            return
        query = self.session.query(DieselEntry.id).filter(
            func.lower(DieselEntry.receipt_number) == normalized.lower()
        )
        if exclude_id is not None:
            query = query.filter(DieselEntry.id != exclude_id)
        if self.session.query(query.exists()).scalar():
            raise ValidationError(f"Receipt number '{normalized}' is already used by another diesel entry.")

    def create_entry(self, data):
        self._assert_unique_receipt(data.get("receipt_number"))
        entry = DieselEntry(
            vehicle_id=data["vehicle_id"],
            petrol_pump_id=data.get("petrol_pump_id") or None,
            order_id=data.get("order_id") or None,
            date=data["date"],
            litres=data.get("litres") or None,
            amount=data["amount"],
            receipt_number=(data.get("receipt_number") or "").strip() or None,
            notes=(data.get("notes") or "").strip() or None,
            balance_applied=False,
            vehicle_balance_applied=False,
            # Held for approval: applies no pump/vehicle balance and is hidden from
            # the fuel log and statements until approved.
            approval_status="pending",
        )
        try:
            self.session.add(entry)
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        return entry

    def pending_count(self):
        return self.session.query(func.count(DieselEntry.id)).filter(DieselEntry.approval_status == "pending").scalar() or 0

    def list_pending(self):
        entries = (
            DieselEntry.query.filter(DieselEntry.approval_status == "pending").all()
        )
        # Sorted by date, then receipt number ascending (768 before 1001), blanks
        # last — same convention as the fuel log.
        entries.sort(key=lambda e: (e.date or date_type.min, receipt_sort_key(e.receipt_number)))
        return entries

    def approve_entry(self, entry_id, approver_id=None):
        """Approve a pending diesel entry: apply the pump and vehicle balances
        (the same posting create_entry used to do immediately)."""
        entry = self.get_entry(entry_id)
        if entry.approval_status == "approved":
            return entry
        try:
            if entry.petrol_pump_id and not entry.balance_applied:
                self._adjust_pump_balance(entry.petrol_pump_id, +_safe(entry.amount))
                entry.balance_applied = True
            if not entry.vehicle_balance_applied:
                self._adjust_vehicle_balance(entry.vehicle_id, -_safe(entry.amount))
                entry.vehicle_balance_applied = True
            entry.approval_status = "approved"
            entry.approved_by_id = approver_id
            entry.approved_at = utc_now()
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        return entry

    def reject_entry(self, entry_id):
        """Discard a pending diesel entry. Safe to delete because a pending entry
        has applied no balances."""
        entry = self.get_entry(entry_id)
        if entry.approval_status == "approved":
            raise ValidationError("This entry is already approved and cannot be rejected. Delete it instead.")
        try:
            self.session.delete(entry)
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise

    def update_entry(self, entry_id, data):
        entry = self.get_entry(entry_id)
        self._assert_unique_receipt(data.get("receipt_number"), exclude_id=entry_id)

        old_pump_id = entry.petrol_pump_id if entry.balance_applied else None
        old_pump_amount = _safe(entry.amount) if entry.balance_applied else 0.0
        old_vehicle_id = entry.vehicle_id if entry.vehicle_balance_applied else None
        old_vehicle_amount = _safe(entry.amount) if entry.vehicle_balance_applied else 0.0

        entry.vehicle_id = data["vehicle_id"]
        entry.petrol_pump_id = data.get("petrol_pump_id") or None
        entry.order_id = data.get("order_id") or None
        entry.date = data["date"]
        entry.litres = data.get("litres") or None
        entry.amount = data["amount"]
        entry.receipt_number = (data.get("receipt_number") or "").strip() or None
        entry.notes = (data.get("notes") or "").strip() or None

        # A pending entry has applied no balances — editing it must not post any.
        if entry.approval_status == "pending":
            try:
                self.session.commit()
            except Exception:
                self.session.rollback()
                raise
            return entry

        try:
            # reverse old pump effect, apply new
            self._adjust_pump_balance(old_pump_id, -old_pump_amount)
            if entry.petrol_pump_id:
                self._adjust_pump_balance(entry.petrol_pump_id, +_safe(entry.amount))
                entry.balance_applied = True
            else:
                entry.balance_applied = False

            # reverse old vehicle effect, apply new
            self._adjust_vehicle_balance(old_vehicle_id, +old_vehicle_amount)
            self._adjust_vehicle_balance(entry.vehicle_id, -_safe(entry.amount))
            entry.vehicle_balance_applied = True

            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        return entry

    def delete_entry(self, entry_id):
        entry = self.get_entry(entry_id)
        try:
            if entry.balance_applied:
                self._adjust_pump_balance(entry.petrol_pump_id, -_safe(entry.amount))
            if entry.vehicle_balance_applied:
                self._adjust_vehicle_balance(entry.vehicle_id, +_safe(entry.amount))
            self.session.delete(entry)
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise

    def build_form_choices(self, vehicle_id=None):
        vehicles = Vehicle.query.order_by(Vehicle.vehicle_number).all()
        pumps = PetrolPump.query.order_by(PetrolPump.name).all()
        vehicle_choices = [(0, "— Select Vehicle —")] + [
            (v.id, f"{v.vehicle_number} — {v.owner_display_name}") for v in vehicles
        ]
        pump_choices = [(0, "— Select Pump —")] + [(p.id, p.name) for p in pumps]
        order_choices = [(0, "— No Linked Order —")]
        if vehicle_id:
            orders = (
                Order.query
                .filter(Order.vehicle_id == vehicle_id, Order.approval_status == "approved")
                .order_by(Order.order_date.desc())
                .limit(100)
                .all()
            )
            order_choices += [(o.id, f"#{o.id} — {o.order_date.strftime('%Y-%m-%d')} — {o.material_name}") for o in orders]
        return {
            "vehicle_choices": vehicle_choices,
            "pump_choices": pump_choices,
            "order_choices": order_choices,
        }

    def summary_stats(self, entries):
        total_amount = sum(e.amount or 0 for e in entries)
        total_litres = sum(e.litres or 0 for e in entries if e.litres)
        vehicles = len({e.vehicle_id for e in entries})
        today = date_type.today()
        this_month = sum(
            e.litres or 0 for e in entries
            if e.litres and e.date.year == today.year and e.date.month == today.month
        )
        return {
            "total_entries": len(entries),
            "total_amount": total_amount,
            "total_litres": total_litres,
            "distinct_vehicles": vehicles,
            "this_month_litres": this_month,
        }

    # ── balance helpers ────────────────────────────────────────────────────────

    def _adjust_pump_balance(self, pump_id, delta):
        if not pump_id or delta == 0:
            return
        pump = self.session.get(PetrolPump, pump_id)
        if pump:
            pump.balance = _safe(pump.balance) + delta

    def _adjust_vehicle_balance(self, vehicle_id, delta):
        if not vehicle_id or delta == 0:
            return
        vehicle = self.session.get(Vehicle, vehicle_id)
        if vehicle:
            vehicle.balance = _safe(vehicle.balance) + delta
            if vehicle.owner_id:
                owner = self.session.get(VehicleOwner, vehicle.owner_id)
                if owner:
                    owner.balance = _safe(owner.balance) + delta
