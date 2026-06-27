from datetime import date as date_type

from sqlalchemy import func

from ..extensions import db
from ..models import DieselEntry, Order, PetrolPump, Vehicle, VehicleOwner
from ..repositories import LookupRepository
from .exceptions import NotFoundError, ValidationError


def _safe(value):
    return float(value or 0.0)


class DieselService:
    def __init__(self, session=None):
        self.session = session or db.session

    def list_entries(self, vehicle_id=None, pump_id=None, owner_id=None, date_from=None, date_to=None):
        q = DieselEntry.query.order_by(DieselEntry.date.desc(), DieselEntry.id.desc())
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
        return q.all()

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
        )
        try:
            self.session.add(entry)
            if entry.petrol_pump_id:
                self._adjust_pump_balance(entry.petrol_pump_id, +_safe(entry.amount))
                entry.balance_applied = True
            self._adjust_vehicle_balance(entry.vehicle_id, -_safe(entry.amount))
            entry.vehicle_balance_applied = True
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        return entry

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
                .filter(Order.vehicle_id == vehicle_id)
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
