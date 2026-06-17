from .core.database import init_db
from .extensions import db
from .models import Company, Contractor, Material, Order, OrderDieselEntry, OrderLoading, PetrolPump, Plant, Site, Transaction, User, Vehicle, VehicleOwner


def _get_or_create_vehicle_owner(name):
    owner = VehicleOwner.query.filter_by(name=name).first()
    if owner is None:
        owner = VehicleOwner(name=name)
        db.session.add(owner)
        db.session.flush()
    return owner


def main():
    init_db()

    if not User.query.filter_by(username="admin").first():
        admin_user = User(username="admin", email="admin@example.com", is_admin=True)
        admin_user.set_role("admin")
        admin_user.set_permissions(None)
        admin_user.set_password("admin123")
        db.session.add(admin_user)

    company = Company.query.first()
    if company is None:
        company = Company(name="Al Rehman Goods Transport", balance=100000.0)
        db.session.add(company)

    if not Contractor.query.first():
        contractor1 = Contractor(name="ABC Contractors", contact_person="John Doe", phone="1234567890", email="john@abccontractors.com", address="123 Main Street, City, State", payment_terms="Monthly")
        contractor2 = Contractor(name="XYZ Builders", contact_person="Jane Smith", phone="0987654321", email="jane@xyzbuilders.com", address="456 Oak Avenue, City, State", payment_terms="Weekly")
        db.session.add_all([contractor1, contractor2])
        db.session.flush()

        site1 = Site(name="Construction Site A", address="789 Construction Road, City, State", contact_person="Site Manager A", phone="1112223333", contractor_id=contractor1.id)
        site2 = Site(name="Construction Site B", address="321 Building Lane, City, State", contact_person="Site Manager B", phone="4445556666", contractor_id=contractor2.id)
        db.session.add_all([site1, site2])

    if not Vehicle.query.first():
        owner_a = _get_or_create_vehicle_owner("Vehicle Owner A")
        owner_b = _get_or_create_vehicle_owner("Vehicle Owner B")
        db.session.add_all(
            [
                Vehicle(vehicle_number="ABC123", owner_id=owner_a.id, owner_name=owner_a.name, vehicle_type="Truck", capacity=100.0, insurance_details="Comprehensive insurance", fitness_certificate="FC12345"),
                Vehicle(vehicle_number="XYZ789", owner_id=owner_b.id, owner_name=owner_b.name, vehicle_type="Trailer", capacity=150.0, insurance_details="Third party insurance", fitness_certificate="FC67890"),
            ]
        )

    if not Plant.query.first():
        db.session.add_all(
            [
                Plant(name="Main Plant", address="101 Plant Road, Industrial Area, City", contact_person="Plant Manager", phone="7778889999", payment_terms="Monthly"),
                Plant(name="Secondary Plant", address="202 Factory Street, Industrial Zone, City", contact_person="Assistant Manager", phone="0001112222", payment_terms="Weekly"),
            ]
        )

    if not Material.query.first():
        db.session.add_all([Material(name="Sand"), Material(name="Cement")])

    if not PetrolPump.query.first():
        db.session.add(PetrolPump(name="Main Diesel Station"))

    db.session.flush()

    if not Order.query.first():
        first_vehicle = Vehicle.query.first()
        first_contractor = Contractor.query.first()
        first_site = Site.query.first()
        second_vehicle = Vehicle.query.offset(1).first()
        second_contractor = Contractor.query.offset(1).first()
        second_site = Site.query.offset(1).first()
        main_plant = Plant.query.first()
        main_pump = PetrolPump.query.first()
        sand = Material.query.filter_by(name="Sand").first()
        cement = Material.query.filter_by(name="Cement").first()

        first_order = Order(
            vehicle_id=first_vehicle.id,
            driver_name="Driver A",
            contractor_id=first_contractor.id,
            site_id=first_site.id,
            material_id=sand.id if sand else None,
            material_type="Sand",
            quantity=100.0,
            delivered_quantity=100.0,
            unit="cft",
            advance_amount=500.0,
            driver_contact="1112223333",
            diesel_amount=200.0,
            builty_number="B12345",
            receipt_number="R-1001",
            contractor_rate=16.0,
            vehicle_rate=11.0,
            plant_id=main_plant.id if main_plant else None,
            plant_amount=90.0,
            commission=20.0,
            status="Completed",
            company_advance_applied=True,
        )
        second_order = Order(
            vehicle_id=second_vehicle.id,
            driver_name="Driver B",
            contractor_id=second_contractor.id,
            site_id=second_site.id,
            material_id=cement.id if cement else None,
            material_type="Cement",
            quantity=50.0,
            delivered_quantity=50.0,
            unit="cft",
            advance_amount=300.0,
            driver_contact="4445556666",
            diesel_amount=150.0,
            builty_number="B67890",
            receipt_number="R-1002",
            contractor_rate=18.0,
            vehicle_rate=12.0,
            plant_id=main_plant.id if main_plant else None,
            plant_amount=110.0,
            commission=25.0,
            status="Completed",
            company_advance_applied=True,
        )
        db.session.add_all([first_order, second_order])
        db.session.flush()
        db.session.add_all(
            [
                OrderLoading(order_id=first_order.id, load_quantity=100.0, plant_id=main_plant.id if main_plant else None, plant_amount=90.0),
                OrderLoading(order_id=second_order.id, load_quantity=50.0, plant_id=main_plant.id if main_plant else None, plant_amount=110.0),
                OrderDieselEntry(order_id=first_order.id, amount=200.0, litres=22.0, receipt_number="DS-1001", petrol_pump_id=main_pump.id if main_pump else None),
                OrderDieselEntry(order_id=second_order.id, amount=150.0, litres=18.0, receipt_number="DS-1002", petrol_pump_id=main_pump.id if main_pump else None),
            ]
        )

    if not Transaction.query.first():
        first_vehicle = Vehicle.query.first()
        first_contractor = Contractor.query.first()
        db.session.add_all(
            [
                Transaction(type="initial_balance", amount=100000.0, description="Initial company balance", payment_method="cash"),
                Transaction(type="vehicle_payment", amount=2000.0, description="Payment for completed orders", payment_method="account", reference="TRX001", vehicle_id=first_vehicle.id if first_vehicle else None),
                Transaction(type="contractor_receipt", amount=5000.0, description="Payment received for completed work", payment_method="cheque", reference="CHQ12345", contractor_id=first_contractor.id if first_contractor else None),
            ]
        )

    db.session.commit()
    print("Database initialized successfully with sample data!")


if __name__ == "__main__":
    main()
