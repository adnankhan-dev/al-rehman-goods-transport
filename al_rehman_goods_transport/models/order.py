from datetime import UTC, datetime

from sqlalchemy import Index, text

from ..extensions import db


def utc_now():
    return datetime.now(UTC).replace(tzinfo=None)


class Order(db.Model):
    __tablename__ = 'orders'
    __table_args__ = (
        Index(
            "uq_orders_builty_number",
            "builty_number",
            unique=True,
            sqlite_where=text("builty_number IS NOT NULL AND builty_number != ''"),
        ),
    )
    
    id = db.Column(db.Integer, primary_key=True)
    order_date = db.Column(db.DateTime, default=utc_now)
    vehicle_id = db.Column(db.Integer, db.ForeignKey('vehicle.id'), nullable=False)
    driver_name = db.Column(db.String(100), nullable=False)
    contractor_id = db.Column(db.Integer, db.ForeignKey('contractor.id'), nullable=False)
    site_id = db.Column(db.Integer, db.ForeignKey('site.id'), nullable=False)
    from_site_id = db.Column(db.Integer, db.ForeignKey('site.id'), nullable=True)
    material_id = db.Column(db.Integer, db.ForeignKey("material.id"), nullable=True)
    material_type = db.Column(db.String(100), nullable=False)
    quantity = db.Column(db.Float, nullable=False)
    unit = db.Column(db.String(10), default='cft')  # New field for unit (cft or ton)
    advance_amount = db.Column(db.Float, default=0)
    driver_contact = db.Column(db.String(15))
    diesel_amount = db.Column(db.Float, default=0)
    builty_number = db.Column(db.String(50), unique=True, nullable=True)
    status = db.Column(db.String(20), default='Completed')
    bill_id = db.Column(db.Integer, db.ForeignKey("bill.id"), nullable=True)
    billed_at = db.Column(db.DateTime, nullable=True)
    vehicle_owner_bill_id = db.Column(db.Integer, db.ForeignKey("bill.id"), nullable=True)
    
    # Fields to be filled at completion
    plant_id = db.Column(db.Integer, db.ForeignKey('plant.id'), nullable=True)
    contractor_rate = db.Column(db.Float, nullable=True)
    vehicle_rate = db.Column(db.Float, nullable=True)
    delivered_quantity = db.Column(db.Float, nullable=True)
    # Optional vehicle-side delivered quantity: set only when the vehicle's own
    # measurement is lower than the contractor's. When present, the vehicle's
    # payable is computed on THIS quantity; when NULL, delivered_quantity is used.
    # Set only via the edit form by an authorised user, never on order entry.
    vehicle_delivered_quantity = db.Column(db.Float, nullable=True)
    plant_amount = db.Column(db.Float, default=0)
    commission = db.Column(db.Float, default=0)  # New field for commission
    completion_date = db.Column(db.DateTime, nullable=True)
    receipt_number = db.Column(db.String(50), nullable=True)
    remarks = db.Column(db.Text, nullable=True)
    delivery_receipt_image = db.Column(db.String(255), nullable=True)
    company_advance_applied = db.Column(db.Boolean, default=False, nullable=False)

    # Audit: who entered this order into the system and when.
    created_at = db.Column(db.DateTime, default=utc_now, nullable=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)

    # Approval workflow: a new order is 'pending' and posts NO financials and is
    # hidden from lists/reports/billing until an approver sets its rates and
    # approves it (status flips to 'Completed'). Default 'approved' so existing
    # rows and any legacy path stay visible.
    approval_status = db.Column(db.String(20), default="approved", server_default="approved", nullable=False)
    approved_by_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    approved_at = db.Column(db.DateTime, nullable=True)

    # Relationships
    vehicle = db.relationship('Vehicle', back_populates='orders')
    contractor = db.relationship('Contractor', back_populates='orders')
    site = db.relationship('Site', foreign_keys=[site_id], back_populates='orders')
    from_site = db.relationship('Site', foreign_keys=[from_site_id])
    plant = db.relationship('Plant', back_populates='orders')
    material = db.relationship("Material", back_populates="orders")
    created_by = db.relationship("User", foreign_keys=[created_by_id], lazy="joined")
    approved_by = db.relationship("User", foreign_keys=[approved_by_id], lazy="joined")
    bill = db.relationship("Bill", foreign_keys=[bill_id], back_populates="orders")
    vehicle_owner_bill = db.relationship("Bill", foreign_keys=[vehicle_owner_bill_id], backref=db.backref("vehicle_owner_orders", lazy="dynamic"))
    loadings = db.relationship(
        "OrderLoading",
        back_populates="order",
        cascade="all, delete-orphan",
        order_by="OrderLoading.id.asc()",
    )
    diesel_entries = db.relationship(
        "OrderDieselEntry",
        back_populates="order",
        cascade="all, delete-orphan",
        order_by="OrderDieselEntry.id.asc()",
    )
    
    def __repr__(self):
        return f'<Order {self.id}>'

    @property
    def entered_by_name(self):
        return self.created_by.username if self.created_by else None

    @property
    def is_pending_approval(self):
        return self.approval_status == "pending"

    @property
    def approved_by_name(self):
        return self.approved_by.username if self.approved_by else None

    @property
    def material_name(self):
        if self.material:
            return self.material.name
        return self.material_type

    @property
    def primary_loading(self):
        return self.loadings[0] if self.loadings else None

    @property
    def primary_diesel_entry(self):
        return self.diesel_entries[0] if self.diesel_entries else None
    
    @property
    def effective_vehicle_quantity(self):
        """Quantity the vehicle is paid on: the explicit vehicle-delivered
        quantity when set, otherwise the (contractor) delivered quantity."""
        if self.vehicle_delivered_quantity is not None:
            return self.vehicle_delivered_quantity
        return self.delivered_quantity or self.quantity or 0

    def total_contractor_amount(self):
        quantity = self.delivered_quantity or self.quantity or 0
        if self.contractor_rate:
            return quantity * self.contractor_rate
        return 0

    def gross_vehicle_amount(self):
        quantity = self.effective_vehicle_quantity
        if self.vehicle_rate:
            return quantity * self.vehicle_rate
        return 0

    def total_vehicle_amount(self):
        return self.gross_vehicle_amount() - (self.commission or 0)

    def remaining_vehicle_payment(self):
        return self.total_vehicle_amount() - self.total_advance_amount() - self.total_diesel_amount()

    def profit_amount(self):
        return self.total_contractor_amount() - self.total_vehicle_amount() - (self.plant_amount or 0)
    
    def total_diesel_amount(self):
        if self.diesel_entries:
            return sum((entry.amount or 0) for entry in self.diesel_entries)
        return self.diesel_amount or 0
    
    def total_advance_amount(self):
        return self.advance_amount or 0

    @property
    def is_billed(self):
        return self.bill_id is not None

    @property
    def is_plant_billed(self):
        return any(l.bill_id is not None for l in self.loadings)

    @property
    def is_vehicle_owner_billed(self):
        return self.vehicle_owner_bill_id is not None

    @property
    def billable_amount(self):
        return self.total_contractor_amount()
