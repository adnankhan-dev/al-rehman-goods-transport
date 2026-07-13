from collections import defaultdict
from datetime import datetime

from ..repositories import ReportRepository


REPORT_TYPES = (
    {
        "key": "profit_loss",
        "label": "Profit & Loss",
        "description": "Completed trips with revenue, cost, and profit visibility.",
    },
    {
        "key": "diesel",
        "label": "Diesel Report",
        "description": "Diesel issue records with only pump, litres, amount, and linked trip detail.",
    },
    {
        "key": "plants",
        "label": "Plant Report",
        "description": "Crush plant activity with loading quantity and plant amount only.",
    },
    {
        "key": "performance",
        "label": "Performance",
        "description": "Monthly profit trend with contractor and vehicle rankings.",
    },
    {
        "key": "finances",
        "label": "Finances",
        "description": "Who owes you and whom you owe — detailed receivables and payables.",
    },
    {
        "key": "ageing",
        "label": "Ageing",
        "description": "How old each outstanding balance is — 0–30, 31–60, 61–90, and 90+ days.",
    },
    {
        "key": "vehicles",
        "label": "Vehicle Profitability",
        "description": "Revenue, cost, fuel, and profit per vehicle for the filtered period.",
    },
)


class ReportService:
    def __init__(self, repository=None):
        self.repository = repository or ReportRepository()

    def financial_report(self, start_date, end_date):
        # Order-linked diesel and order advances are retired: a trip's cost is
        # the full vehicle amount plus plant charges. Fuel lives in the Fuel
        # Log against the pump/owner accounts, advances in the ledger.
        completed_orders = self.repository.completed_orders_between(start_date, end_date)
        total_revenue = sum(order.total_contractor_amount() for order in completed_orders)
        vehicle_payables = sum(order.total_vehicle_amount() for order in completed_orders)
        plant_payments = sum(order.plant_amount or 0 for order in completed_orders)
        total_expenses = vehicle_payables + plant_payments
        total_profit = total_revenue - total_expenses

        revenue_by_contractor = {}
        for order in completed_orders:
            contractor_name = order.contractor.name
            revenue_by_contractor[contractor_name] = revenue_by_contractor.get(contractor_name, 0) + order.total_contractor_amount()

        return {
            "total_revenue": total_revenue,
            "total_expenses": total_expenses,
            "total_profit": total_profit,
            "revenue_by_contractor": revenue_by_contractor,
            "expenses_by_category": {
                "Vehicle Payments": vehicle_payables,
                "Plant Payments": plant_payments,
            },
            "cash_flow": {"Inflow": total_revenue, "Outflow": total_expenses, "Net Cash Flow": total_profit},
        }

    def report_types(self):
        return REPORT_TYPES

    def normalize_report_type(self, report_type):
        allowed = {item["key"] for item in REPORT_TYPES}
        return report_type if report_type in allowed else "profit_loss"

    def workspace_context(self, filters):
        report_type = self.normalize_report_type(filters.get("report_type"))
        filter_state = dict(filters)
        filter_state["report_type"] = report_type
        filter_options = self.repository.filter_options()

        context = {
            "report_type": report_type,
            "report_types": self.report_types(),
            "filter_state": filter_state,
            "filter_options": filter_options,
            "filter_chips": self._filter_chips(filter_state, filter_options),
        }

        if report_type == "diesel":
            context.update(self._diesel_context(filter_state))
        elif report_type == "plants":
            context.update(self._plants_context(filter_state))
        elif report_type == "performance":
            context.update(self._performance_context(filter_state))
        elif report_type == "finances":
            context.update(self._finances_context(filter_state))
        elif report_type == "ageing":
            context.update(self._ageing_context(filter_state))
        elif report_type == "vehicles":
            context.update(self._vehicles_context(filter_state))
        else:
            context.update(self._profit_loss_context(filter_state))
        return context

    def _profit_loss_context(self, filters):
        orders = self.repository.completed_orders_filtered(filters)
        # Order-linked diesel and advances are retired — a trip's expense side
        # is the vehicle amount plus plant charges only.
        total_revenue = sum(order.total_contractor_amount() for order in orders)
        vehicle_payables = sum(order.total_vehicle_amount() for order in orders)
        plant_payments = sum(order.plant_amount or 0 for order in orders)
        total_expenses = vehicle_payables + plant_payments
        total_profit = total_revenue - total_expenses
        total_quantity = sum(order.delivered_quantity or order.quantity or 0 for order in orders)

        profit_by_contractor = defaultdict(float)
        for order in orders:
            profit_by_contractor[order.contractor.name if order.contractor else "Unknown"] += order.profit_amount()

        sorted_contractors = sorted(profit_by_contractor.items(), key=lambda item: item[1], reverse=True)[:8]
        expense_breakdown = {
            "Vehicle Payments": vehicle_payables,
            "Plant Payments": plant_payments,
        }

        # Compare against the equal-length period immediately before, so the
        # P&L answers "is this better or worse than last time?" at a glance.
        comparison = None
        if filters.get("date_from") and filters.get("date_to"):
            from datetime import timedelta as _td

            length = filters["date_to"] - filters["date_from"]
            prev_to = filters["date_from"] - _td(days=1)
            prev_from = prev_to - length
            prev_filters = dict(filters)
            prev_filters["date_from"] = prev_from
            prev_filters["date_to"] = prev_to
            prev_orders = self.repository.completed_orders_filtered(prev_filters)
            prev_revenue = sum(o.total_contractor_amount() for o in prev_orders)
            prev_vehicle = sum(o.total_vehicle_amount() for o in prev_orders)
            prev_plant = sum(o.plant_amount or 0 for o in prev_orders)
            prev_profit = prev_revenue - prev_vehicle - prev_plant

            def pct(current, previous):
                if abs(previous) < 0.005:
                    return None
                return (current - previous) / abs(previous) * 100

            comparison = {
                "prev_from": prev_from,
                "prev_to": prev_to,
                "rows": [
                    {"label": "Revenue", "current": total_revenue, "previous": prev_revenue, "pct": pct(total_revenue, prev_revenue)},
                    {"label": "Vehicle Payments", "current": vehicle_payables, "previous": prev_vehicle, "pct": pct(vehicle_payables, prev_vehicle)},
                    {"label": "Plant Payments", "current": plant_payments, "previous": prev_plant, "pct": pct(plant_payments, prev_plant)},
                    {"label": "Net Profit", "current": total_profit, "previous": prev_profit, "pct": pct(total_profit, prev_profit)},
                ],
                "prev_trips": len(prev_orders),
            }

        return {
            "report_heading": "Profit & Loss Report",
            "comparison": comparison,
            "report_intro_title": "Operational orders with a built-in profit and loss statement.",
            "report_intro_copy": "Use the same familiar order-style workspace, but now with revenue, expense, and profitability visibility for every filtered trip.",
            "summary_cards": [
                {"label": "Trips in Report", "value": len(orders), "hint": "Completed orders after filters", "tone": "primary"},
                {"label": "Revenue", "value": f"Rs. {total_revenue:,.0f}", "hint": "Contractor-side billed value", "tone": "accent"},
                {"label": "Expenses", "value": f"Rs. {total_expenses:,.0f}", "hint": "Vehicle and plant costs", "tone": "ocean"},
                {"label": "Net Profit", "value": f"Rs. {total_profit:,.0f}", "hint": f"Delivered quantity {total_quantity:,.2f}", "tone": "slate"},
            ],
            "statement_lines": [
                {"label": "Revenue", "amount": total_revenue, "kind": "positive"},
                {"label": "Vehicle Payments", "amount": vehicle_payables, "kind": "negative"},
                {"label": "Plant Payments", "amount": plant_payments, "kind": "negative"},
            ],
            "statement_totals": {
                "revenue": total_revenue,
                "expenses": total_expenses,
                "profit": total_profit,
            },
            "orders": orders,
            "profit_chart": {
                "labels": [item[0] for item in sorted_contractors],
                "values": [round(item[1], 2) for item in sorted_contractors],
            },
            "expense_chart": {
                "labels": list(expense_breakdown.keys()),
                "values": [round(value, 2) for value in expense_breakdown.values()],
            },
            "print_title": "Profit & Loss Report",
        }

    def _diesel_context(self, filters):
        entries = self._unified_diesel_rows(filters)
        total_amount = sum(entry["amount"] for entry in entries)
        total_litres = sum(entry["litres"] for entry in entries)

        by_pump = defaultdict(float)
        by_vehicle = defaultdict(float)
        for entry in entries:
            by_pump[entry["pump_name"]] += entry["amount"]
            by_vehicle[entry["vehicle_number"]] += entry["amount"]

        top_pumps = sorted(by_pump.items(), key=lambda item: item[1], reverse=True)[:8]
        top_vehicles = sorted(by_vehicle.items(), key=lambda item: item[1], reverse=True)[:8]

        return {
            "report_heading": "Diesel Report",
            "report_intro_title": "Diesel-only reporting without unrelated operational noise.",
            "report_intro_copy": "This view keeps only the pump, litres, amount, receipt, and linked trip identity so fuel review stays concise and practical.",
            "summary_cards": [
                {"label": "Diesel Entries", "value": len(entries), "hint": "Filtered fuel issue records", "tone": "primary"},
                {"label": "Total Litres", "value": f"{total_litres:,.2f}", "hint": "Calculated litres", "tone": "accent"},
                {"label": "Total Amount", "value": f"Rs. {total_amount:,.0f}", "hint": "Fuel value in report", "tone": "ocean"},
                {"label": "Pumps Used", "value": len({entry["pump_name"] for entry in entries if entry["pump_name"] != "Unassigned Pump"}), "hint": "Distinct pumps in filtered data", "tone": "slate"},
            ],
            "diesel_entries": entries,
            "diesel_chart": {
                "labels": [item[0] for item in top_pumps],
                "values": [round(item[1], 2) for item in top_pumps],
            },
            "vehicle_diesel_chart": {
                "labels": [item[0] for item in top_vehicles],
                "values": [round(item[1], 2) for item in top_vehicles],
            },
            "print_title": "Diesel Report",
        }

    def _unified_diesel_rows(self, filters):
        """Merge live DieselEntry records with legacy order-linked diesel rows
        into one display shape, newest first."""
        rows = []

        for entry in self.repository.standalone_diesel_entries_filtered(filters):
            rows.append(
                {
                    "date": entry.date,
                    "order_id": entry.order_id,
                    "vehicle_number": entry.vehicle.vehicle_number if entry.vehicle else "-",
                    "pump_name": entry.petrol_pump.name if entry.petrol_pump else "Unassigned Pump",
                    "receipt_number": entry.receipt_number,
                    "litres": float(entry.litres or 0),
                    "amount": float(entry.amount or 0),
                    "source": "diesel_module",
                }
            )

        for entry in self.repository.diesel_entries_filtered(filters):
            order = entry.order
            order_moment = (order.completion_date or order.order_date) if order else None
            rows.append(
                {
                    "date": order_moment.date() if order_moment else None,
                    "order_id": entry.order_id,
                    "vehicle_number": order.vehicle.vehicle_number if order and order.vehicle else "-",
                    "pump_name": entry.petrol_pump.name if entry.petrol_pump else "Unassigned Pump",
                    "receipt_number": entry.receipt_number,
                    "litres": float(entry.litres or 0),
                    "amount": float(entry.amount or 0),
                    "source": "order_legacy",
                }
            )

        rows.sort(key=lambda row: row["date"] or datetime.min.date(), reverse=True)
        return rows

    def _plants_context(self, filters):
        loadings = self.repository.plant_loadings_filtered(filters)
        total_amount = sum(loading.plant_amount or 0 for loading in loadings)
        total_quantity = sum(loading.load_quantity or 0 for loading in loadings)

        by_plant = defaultdict(float)
        by_material = defaultdict(float)
        for loading in loadings:
            plant_name = loading.plant.name if loading.plant else "Unknown Plant"
            material_name = loading.order.material_name if loading.order else "Unknown Material"
            by_plant[plant_name] += loading.plant_amount or 0
            by_material[material_name] += loading.load_quantity or 0

        top_plants = sorted(by_plant.items(), key=lambda item: item[1], reverse=True)[:8]
        top_materials = sorted(by_material.items(), key=lambda item: item[1], reverse=True)[:8]

        return {
            "report_heading": "Plant Report",
            "report_intro_title": "Crush plant activity with only plant-facing details.",
            "report_intro_copy": "This report keeps the focus on plant name, loading quantity, material, trip reference, and payable amount so plant reconciliation stays clear.",
            "summary_cards": [
                {"label": "Plant Trips", "value": len(loadings), "hint": "Filtered loading records", "tone": "primary"},
                {"label": "Loaded Quantity", "value": f"{total_quantity:,.2f}", "hint": "Total loading quantity", "tone": "accent"},
                {"label": "Plant Amount", "value": f"Rs. {total_amount:,.0f}", "hint": "Total payable to plants", "tone": "ocean"},
                {"label": "Plants Used", "value": len({loading.plant_id for loading in loadings if loading.plant_id}), "hint": "Distinct plants in report", "tone": "slate"},
            ],
            "plant_loadings": loadings,
            "plant_chart": {
                "labels": [item[0] for item in top_plants],
                "values": [round(item[1], 2) for item in top_plants],
            },
            "material_chart": {
                "labels": [item[0] for item in top_materials],
                "values": [round(item[1], 2) for item in top_materials],
            },
            "print_title": "Plant Report",
        }

    def _performance_context(self, filters):
        orders = self.repository.completed_orders_filtered(filters)

        monthly = {}
        contractors = {}
        vehicles = {}
        for order in orders:
            profit = order.profit_amount()
            revenue = order.total_contractor_amount()
            quantity = float(order.delivered_quantity or order.quantity or 0)
            moment = order.completion_date or order.order_date

            month_key = moment.strftime("%Y-%m")
            month = monthly.setdefault(month_key, {"label": moment.strftime("%b %Y"), "trips": 0, "quantity": 0.0, "revenue": 0.0, "profit": 0.0})
            month["trips"] += 1
            month["quantity"] += quantity
            month["revenue"] += revenue
            month["profit"] += profit

            contractor_name = order.contractor.name if order.contractor else "Unknown"
            contractor = contractors.setdefault(contractor_name, {"name": contractor_name, "trips": 0, "quantity": 0.0, "revenue": 0.0, "profit": 0.0})
            contractor["trips"] += 1
            contractor["quantity"] += quantity
            contractor["revenue"] += revenue
            contractor["profit"] += profit

            vehicle_label = order.vehicle.vehicle_number if order.vehicle else "Unknown"
            vehicle = vehicles.setdefault(vehicle_label, {
                "vehicle_number": vehicle_label,
                "owner_name": order.vehicle.owner_display_name if order.vehicle else "-",
                "trips": 0,
                "quantity": 0.0,
                "earnings": 0.0,
                "profit": 0.0,
            })
            vehicle["trips"] += 1
            vehicle["quantity"] += quantity
            vehicle["earnings"] += order.total_vehicle_amount()
            vehicle["profit"] += profit

        monthly_rows = [monthly[key] for key in sorted(monthly.keys())]
        contractor_rows = sorted(contractors.values(), key=lambda row: row["profit"], reverse=True)
        vehicle_rows = sorted(vehicles.values(), key=lambda row: row["quantity"], reverse=True)

        total_profit = sum(row["profit"] for row in monthly_rows)
        best_month = max(monthly_rows, key=lambda row: row["profit"], default=None)
        top_contractor = contractor_rows[0] if contractor_rows else None

        return {
            "report_heading": "Performance Report",
            "report_intro_title": "Trend and rankings built from the filtered completed trips.",
            "report_intro_copy": "Month-by-month profit, plus which contractors and vehicles drive the result — using the same filters as the other reports.",
            "summary_cards": [
                {"label": "Trips in Report", "value": len(orders), "hint": "Completed orders after filters", "tone": "primary"},
                {"label": "Total Profit", "value": f"Rs. {total_profit:,.0f}", "hint": "Across the filtered period", "tone": "accent"},
                {"label": "Best Month", "value": best_month["label"] if best_month else "—", "hint": f"Rs. {best_month['profit']:,.0f} profit" if best_month else "No data", "tone": "ocean"},
                {"label": "Top Contractor", "value": top_contractor["name"] if top_contractor else "—", "hint": f"Rs. {top_contractor['profit']:,.0f} profit" if top_contractor else "No data", "tone": "slate"},
            ],
            "monthly_rows": monthly_rows,
            "contractor_rows": contractor_rows,
            "vehicle_rows": vehicle_rows,
            "trend_chart": {
                "labels": [row["label"] for row in monthly_rows],
                "values": [round(row["profit"], 2) for row in monthly_rows],
            },
            "contractor_chart": {
                "labels": [row["name"] for row in contractor_rows[:8]],
                "values": [round(row["profit"], 2) for row in contractor_rows[:8]],
            },
            "print_title": "Performance Report",
        }

    def _finances_context(self, filters):
        """Point-in-time receivables (owed to us) and payables (we owe), per account.

        Sign conventions: contractor +balance = receivable; vehicle owner / plant /
        petrol pump +balance = payable; financial entity +balance = receivable.
        A negative balance flips the side (an advance held the other way)."""
        receivables = []
        payables = []

        def add(side_positive_is_receivable, label, name, balance):
            value = float(balance or 0.0)
            if abs(value) <= 0.005:
                return
            is_receivable = (value > 0) == side_positive_is_receivable
            target = receivables if is_receivable else payables
            target.append({"type": label, "name": name, "amount": abs(value)})

        for c in self.repository.list_contractors():
            add(True, "Contractor", c.name, c.balance)
        for o in self.repository.list_vehicle_owners():
            add(False, "Vehicle Owner", o.name, o.balance)
        for p in self.repository.list_plants():
            add(False, "Plant", p.name, p.balance)
        for p in self.repository.list_petrol_pumps():
            add(False, "Petrol Pump", p.name, p.balance)
        for fe in self.repository.list_financial_entities():
            add(True, "Financial Entity", fe.name, fe.balance)

        receivables.sort(key=lambda r: r["amount"], reverse=True)
        payables.sort(key=lambda r: r["amount"], reverse=True)
        total_receivable = sum(r["amount"] for r in receivables)
        total_payable = sum(r["amount"] for r in payables)
        net = total_receivable - total_payable

        return {
            "report_heading": "Finances Report",
            "report_intro_title": "Everything you are owed and everything you owe, in one place.",
            "report_intro_copy": "Live balances per account — receivables (money coming to you) and payables (money you must pay), with a net position.",
            "summary_cards": [
                {"label": "Total Receivable", "value": f"Rs. {total_receivable:,.0f}", "hint": "Money owed to you", "tone": "accent"},
                {"label": "Total Payable", "value": f"Rs. {total_payable:,.0f}", "hint": "Money you owe others", "tone": "ocean"},
                {"label": "Net Position", "value": f"Rs. {net:,.0f}", "hint": "Receivable minus payable", "tone": "primary" if net >= 0 else "slate"},
                {"label": "Open Accounts", "value": len(receivables) + len(payables), "hint": "Accounts with a balance", "tone": "slate"},
            ],
            "receivables": receivables,
            "payables": payables,
            "total_receivable": total_receivable,
            "total_payable": total_payable,
            "net_position": net,
            "finance_chart": {
                "labels": ["Receivable", "Payable"],
                "values": [round(total_receivable, 2), round(total_payable, 2)],
            },
            "print_title": "Finances Report",
        }

    def _ageing_context(self, filters):
        from .financials import entity_ageing
        from ..extensions import db
        from ..models import Contractor, PetrolPump, Plant, VehicleOwner

        as_of = filters.get("date_to")
        rows = []

        def collect(model, entity_type, type_label, receivable):
            for entity in db.session.query(model).order_by(model.name.asc()).all():
                ageing = entity_ageing(entity_type, entity.id, as_of=as_of)
                if ageing["total"] <= 0.005 and ageing["credit"] <= 0.005:
                    continue
                rows.append({
                    "name": entity.name,
                    "type": type_label,
                    "receivable": receivable,
                    "buckets": ageing["buckets"],
                    "total": ageing["total"],
                    "credit": ageing["credit"],
                })

        collect(Contractor, "contractor", "Contractor", True)
        collect(VehicleOwner, "vehicle_owner", "Vehicle Owner", False)
        collect(PetrolPump, "petrol_pump", "Petrol Pump", False)
        collect(Plant, "plant", "Plant", False)
        rows.sort(key=lambda row: row["total"], reverse=True)

        bucket_totals = [sum(row["buckets"][i] for row in rows) for i in range(4)]
        total_outstanding = sum(bucket_totals)
        overdue_90 = bucket_totals[3]
        credit_total = sum(row["credit"] for row in rows)

        return {
            "report_heading": "Ageing Report",
            "report_intro_title": "How old every outstanding balance is.",
            "report_intro_copy": "Receipts and payments settle the OLDEST charges first; whatever remains is aged by the date it was earned. Chase the 90+ column first.",
            "summary_cards": [
                {"label": "Total Outstanding", "value": f"Rs. {total_outstanding:,.0f}", "hint": "Across all open accounts", "tone": "primary"},
                {"label": "90+ Days", "value": f"Rs. {overdue_90:,.0f}", "hint": "Oldest, highest-risk money", "tone": "ocean"},
                {"label": "Advance Positions", "value": f"Rs. {credit_total:,.0f}", "hint": "Paid/received beyond charges", "tone": "accent"},
                {"label": "Open Accounts", "value": len(rows), "hint": "Accounts with a balance", "tone": "slate"},
            ],
            "ageing_rows": rows,
            "ageing_bucket_totals": bucket_totals,
            "ageing_total": total_outstanding,
            "ageing_as_of": as_of,
            "print_title": "Ageing Report",
        }

    def _vehicles_context(self, filters):
        from ..extensions import db
        from ..models import DieselEntry

        orders = self.repository.completed_orders_filtered(filters)
        rows_by_vehicle = {}
        for order in orders:
            key = order.vehicle_id or 0
            row = rows_by_vehicle.setdefault(key, {
                "vehicle_number": order.vehicle.vehicle_number if order.vehicle else "Unknown",
                "owner_name": order.vehicle.owner_display_name if order.vehicle else "-",
                "trips": 0, "quantity": 0.0, "revenue": 0.0,
                "vehicle_cost": 0.0, "plant_cost": 0.0, "fuel": 0.0,
            })
            row["trips"] += 1
            row["quantity"] += float(order.delivered_quantity or order.quantity or 0)
            row["revenue"] += float(order.total_contractor_amount())
            row["vehicle_cost"] += float(order.total_vehicle_amount())
            row["plant_cost"] += float(order.plant_amount or 0)

        fuel_query = db.session.query(DieselEntry).filter(DieselEntry.approval_status == "approved")
        if filters.get("date_from"):
            fuel_query = fuel_query.filter(DieselEntry.date >= filters["date_from"].date() if hasattr(filters["date_from"], "date") else filters["date_from"])
        if filters.get("date_to"):
            fuel_query = fuel_query.filter(DieselEntry.date <= filters["date_to"].date() if hasattr(filters["date_to"], "date") else filters["date_to"])
        for entry in fuel_query.all():
            if entry.vehicle_id in rows_by_vehicle:
                rows_by_vehicle[entry.vehicle_id]["fuel"] += float(entry.amount or 0)

        vehicle_profit_rows = []
        for row in rows_by_vehicle.values():
            row["profit"] = row["revenue"] - row["vehicle_cost"] - row["plant_cost"]
            row["profit_per_trip"] = row["profit"] / row["trips"] if row["trips"] else 0.0
            vehicle_profit_rows.append(row)
        vehicle_profit_rows.sort(key=lambda row: row["profit"], reverse=True)

        total_profit = sum(row["profit"] for row in vehicle_profit_rows)
        best = vehicle_profit_rows[0] if vehicle_profit_rows else None
        worst = vehicle_profit_rows[-1] if vehicle_profit_rows else None

        return {
            "report_heading": "Vehicle Profitability Report",
            "report_intro_title": "What each vehicle earns the company.",
            "report_intro_copy": "Per vehicle: contractor revenue minus the owner payment and plant charges. Fuel is shown for context — it settles between the owner and pump accounts, not the trip P&L.",
            "summary_cards": [
                {"label": "Vehicles in Report", "value": len(vehicle_profit_rows), "hint": "With completed trips after filters", "tone": "primary"},
                {"label": "Total Profit", "value": f"Rs. {total_profit:,.0f}", "hint": "Across all vehicles", "tone": "accent"},
                {"label": "Best Vehicle", "value": best["vehicle_number"] if best else "—", "hint": f"Rs. {best['profit']:,.0f} profit" if best else "No data", "tone": "ocean"},
                {"label": "Lowest Vehicle", "value": worst["vehicle_number"] if worst else "—", "hint": f"Rs. {worst['profit']:,.0f} profit" if worst else "No data", "tone": "slate"},
            ],
            "vehicle_profit_rows": vehicle_profit_rows,
            "vehicle_profit_chart": {
                "labels": [row["vehicle_number"] for row in vehicle_profit_rows[:10]],
                "values": [round(row["profit"], 2) for row in vehicle_profit_rows[:10]],
            },
            "print_title": "Vehicle Profitability Report",
        }

    def export_report(self, filters):
        """Excel (.xls via HTML table) export of the active report's register."""
        from html import escape

        context = self.workspace_context(filters)
        report_type = context["report_type"]

        def money(value):
            return f"{float(value or 0):,.2f}"

        headers, rows = [], []
        if report_type == "diesel":
            headers = ["Date", "Order", "Vehicle", "Pump", "Receipt", "Litres", "Amount"]
            rows = [[
                entry["date"].strftime("%Y-%m-%d") if entry["date"] else "-",
                entry["order_id"] or "-", entry["vehicle_number"], entry["pump_name"],
                entry["receipt_number"] or "-", f"{entry['litres']:.2f}", money(entry["amount"]),
            ] for entry in context["diesel_entries"]]
        elif report_type == "plants":
            headers = ["Date", "Order", "Plant", "Vehicle", "Material", "Loaded Qty", "Plant Amount"]
            rows = [[
                (loading.order.completion_date or loading.order.order_date).strftime("%Y-%m-%d") if loading.order else "-",
                loading.order_id, loading.plant.name if loading.plant else "-",
                loading.order.vehicle.vehicle_number if loading.order and loading.order.vehicle else "-",
                loading.order.material_name if loading.order else "-",
                f"{float(loading.load_quantity or 0):.2f}", money(loading.plant_amount),
            ] for loading in context["plant_loadings"]]
        elif report_type == "performance":
            headers = ["Month", "Trips", "Quantity", "Revenue", "Profit"]
            rows = [[
                row["label"], row["trips"], f"{row['quantity']:.2f}", money(row["revenue"]), money(row["profit"]),
            ] for row in context["monthly_rows"]]
        elif report_type == "finances":
            headers = ["Side", "Account", "Type", "Amount"]
            rows = [["Receivable", row["name"], row["type"], money(row["amount"])] for row in context["receivables"]]
            rows += [["Payable", row["name"], row["type"], money(row["amount"])] for row in context["payables"]]
        elif report_type == "ageing":
            headers = ["Account", "Type", "0-30 Days", "31-60 Days", "61-90 Days", "90+ Days", "Total", "Advance"]
            rows = [[
                row["name"], row["type"],
                money(row["buckets"][0]), money(row["buckets"][1]), money(row["buckets"][2]), money(row["buckets"][3]),
                money(row["total"]), money(row["credit"]),
            ] for row in context["ageing_rows"]]
        elif report_type == "vehicles":
            headers = ["Vehicle", "Owner", "Trips", "Quantity", "Revenue", "Vehicle Cost", "Plant Cost", "Profit", "Profit/Trip", "Fuel (info)"]
            rows = [[
                row["vehicle_number"], row["owner_name"], row["trips"], f"{row['quantity']:.2f}",
                money(row["revenue"]), money(row["vehicle_cost"]), money(row["plant_cost"]),
                money(row["profit"]), money(row["profit_per_trip"]), money(row["fuel"]),
            ] for row in context["vehicle_profit_rows"]]
        else:  # profit_loss
            headers = ["ID", "Date", "Vehicle", "Contractor", "Material", "Delivered", "Revenue", "Vehicle Payable", "Plant", "Profit"]
            rows = [[
                order.id,
                (order.completion_date or order.order_date).strftime("%Y-%m-%d"),
                order.vehicle.vehicle_number if order.vehicle else "-",
                order.contractor.name if order.contractor else "-",
                order.material_name,
                f"{float(order.delivered_quantity or order.quantity or 0):.2f}",
                money(order.total_contractor_amount()), money(order.total_vehicle_amount()),
                money(order.plant_amount), money(order.profit_amount()),
            ] for order in context["orders"]]

        title = context.get("print_title") or "Report"
        period = ""
        if filters.get("date_from") or filters.get("date_to"):
            period = f" ({filters['date_from'].strftime('%Y-%m-%d') if filters.get('date_from') else 'start'} to {filters['date_to'].strftime('%Y-%m-%d') if filters.get('date_to') else 'date'})"
        header_html = "".join(f"<th style='background:#0b2742;color:#fff'>{escape(str(h))}</th>" for h in headers)
        body_html = "".join(
            "<tr>" + "".join(f"<td>{escape(str(cell))}</td>" for cell in row) + "</tr>"
            for row in rows
        )
        html = (
            "<html><head><meta charset='utf-8'></head><body>"
            f"<h3>Al Rehman Goods Transport — {escape(title)}{escape(period)}</h3>"
            f"<table border='1'><thead><tr>{header_html}</tr></thead><tbody>{body_html}</tbody></table>"
            "</body></html>"
        )
        filename = f"{report_type}_report.xls"
        return filename, html

    def _filter_chips(self, filters, options):
        lookups = {
            "contractor_id": {item.id: item.name for item in options["contractors"]},
            "site_id": {item.id: item.name for item in options["sites"]},
            "from_site_id": {item.id: item.name for item in options["from_sites"]},
            "material_id": {item.id: item.name for item in options["materials"]},
            "vehicle_id": {item.id: item.vehicle_number for item in options["vehicles"]},
            "plant_id": {item.id: item.name for item in options["plants"]},
            "petrol_pump_id": {item.id: item.name for item in options["petrol_pumps"]},
        }

        chips = []
        labels = {
            "contractor_id": "Contractor",
            "site_id": "To Site",
            "from_site_id": "From Site",
            "material_id": "Material",
            "vehicle_id": "Vehicle",
            "plant_id": "Plant",
            "petrol_pump_id": "Petrol Pump",
        }
        for key, label in labels.items():
            selected_id = filters.get(key)
            if selected_id:
                selected_name = lookups[key].get(selected_id)
                if selected_name:
                    chips.append(f"{label}: {selected_name}")

        if filters.get("billing_status"):
            chips.append(f"Billing: {filters['billing_status'].replace('_', ' ').title()}")
        if filters.get("date_from"):
            chips.append(f"From: {filters['date_from'].strftime('%Y-%m-%d')}")
        if filters.get("date_to"):
            chips.append(f"To: {filters['date_to'].strftime('%Y-%m-%d')}")
        if filters.get("search"):
            chips.append(f"Search: {filters['search']}")

        return chips
