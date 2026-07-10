from markupsafe import Markup
from wtforms import BooleanField, DateField, FileField, FloatField, SelectField, StringField, SubmitField, TextAreaField
from wtforms.form import Form
from wtforms.validators import DataRequired, Optional


class BaseForm(Form):
    def hidden_tag(self):
        return Markup("")


class OrderForm(BaseForm):
    order_date = DateField('Order Date', format='%Y-%m-%d', validators=[DataRequired()], render_kw={"type": "date"})
    vehicle_id = SelectField('Vehicle', coerce=int, validators=[DataRequired()])
    driver_name = StringField('Driver Name', validators=[DataRequired()])
    contractor_id = SelectField('Contractor', coerce=int, validators=[DataRequired()])
    site_id = SelectField('Site', coerce=int, validators=[DataRequired()])
    from_site_id = SelectField('From Site', coerce=int, validators=[Optional()])
    material_id = SelectField('Material', coerce=int, validators=[DataRequired()])
    load_quantity = FloatField('Loading Quantity', validators=[DataRequired()])
    builty_number = StringField('Builty Number', validators=[Optional()])
    receipt_number = StringField('Delivery Receipt Number', validators=[Optional()])
    delivered_quantity = FloatField('Delivered Quantity', validators=[DataRequired()])
    # Rates are no longer entered at order creation — they are set by an approver
    # on the Pending Approvals screen. Kept Optional so the field stays available
    # for legacy paths without blocking submission.
    vehicle_rate = FloatField('Vehicle Rate per Unit', validators=[Optional()])
    contractor_rate = FloatField('Contractor Rate per Unit', validators=[Optional()])
    plant_id = SelectField('Plant', coerce=int, validators=[Optional()])
    plant_amount = FloatField('Plant Amount', default=0, validators=[Optional()])
    commission = FloatField('Commission from Vehicle', default=0, validators=[Optional()])
    loading_image = FileField('Loading Image', validators=[Optional()])
    delivery_receipt_image = FileField('Delivery Receipt Image', validators=[Optional()])
    remarks = TextAreaField('Remarks', validators=[Optional()])
    submit = SubmitField('Save Order')


class VehicleOwnerForm(BaseForm):
    name = StringField('Owner Name', validators=[DataRequired()])
    phone = StringField('Phone', validators=[Optional()])
    address = TextAreaField('Address', validators=[Optional()])
    opening_balance = FloatField('Previous / Opening Balance (pre-ERP, Rs.)', default=0.0, validators=[Optional()])
    submit = SubmitField('Save Vehicle Owner')

class ContractorForm(BaseForm):
    name = StringField('Name', validators=[DataRequired()])
    contact_person = StringField('Contact Person', validators=[Optional()])
    phone = StringField('Phone', validators=[Optional()])
    email = StringField('Email', validators=[Optional()])
    address = TextAreaField('Address', validators=[Optional()])
    payment_terms = StringField('Payment Terms', validators=[Optional()])
    balance = FloatField('Initial Balance', default=0.0, validators=[Optional()])  # Add this field
    opening_balance = FloatField('Previous / Opening Balance (pre-ERP, Rs.)', default=0.0, validators=[Optional()])
    submit = SubmitField('Save Contractor')
class VehicleForm(BaseForm):
    vehicle_number = StringField('Vehicle Number', validators=[DataRequired()])
    owner_id = SelectField('Vehicle Owner', coerce=int, validators=[DataRequired()])
    vehicle_type = StringField('Vehicle Type', validators=[Optional()])
    capacity = FloatField('Capacity', validators=[Optional()])
    insurance_details = TextAreaField('Insurance Details', validators=[Optional()])
    fitness_certificate = StringField('Fitness Certificate', validators=[Optional()])
    balance = FloatField('Initial Balance', default=0.0, validators=[Optional()])  # Add this field
    opening_balance = FloatField('Previous / Opening Balance (pre-ERP, Rs.)', default=0.0, validators=[Optional()])
    submit = SubmitField('Save Vehicle')
class PlantForm(BaseForm):
    name = StringField('Name', validators=[DataRequired()])
    address = TextAreaField('Address', validators=[Optional()])
    contact_person = StringField('Contact Person', validators=[Optional()])
    phone = StringField('Phone', validators=[Optional()])
    payment_terms = StringField('Payment Terms', validators=[Optional()])
    balance = FloatField('Initial Balance', default=0.0, validators=[Optional()])  # Add this field
    opening_balance = FloatField('Previous / Opening Balance (pre-ERP, Rs.)', default=0.0, validators=[Optional()])
    submit = SubmitField('Save Plant')
class MaterialForm(BaseForm):
    name = StringField('Material Name', validators=[DataRequired()])
    unit = SelectField('Measuring Unit', choices=[('cft', 'CFT'), ('ton', 'Ton')], validators=[DataRequired()])
    submit = SubmitField('Save Material')
class PetrolPumpForm(BaseForm):
    name = StringField('Petrol Pump Name', validators=[DataRequired()])
    opening_balance = FloatField('Opening / Previous Balance (Rs.)', validators=[Optional()])
    submit = SubmitField('Save Petrol Pump')
class SiteForm(BaseForm):
    name = StringField('Name', validators=[DataRequired()])
    address = TextAreaField('Address', validators=[Optional()])
    contact_person = StringField('Contact Person', validators=[Optional()])
    phone = StringField('Phone', validators=[Optional()])
    contractor_id = SelectField('Contractor', coerce=int, validators=[Optional()])
    is_business_site = BooleanField('Associate With Our Business', validators=[Optional()])
    is_archived = BooleanField('Archive this site (hide from new order entry)', validators=[Optional()])
    submit = SubmitField('Save Site')
class TransactionForm(BaseForm):
    # Generic direction first, then the entity type, then the specific account.
    # The internal transaction type is derived as f"{entity_type}_{direction}"
    # (e.g. contractor + receipt -> contractor_receipt).
    type = SelectField('Transaction Type', choices=[
        ('payment', 'Payment To'),
        ('receipt', 'Receipt From'),
        ('other_expense', 'Other Expense'),
        ('initial_balance', 'Initial Balance'),
    ], validators=[DataRequired()])
    entity_type = SelectField('Entity Type', choices=[
        ('contractor', 'Contractor'),
        ('vehicle_owner', 'Vehicle Owner'),
        ('plant', 'Plant'),
        ('petrol_pump', 'Petrol Pump'),
        ('vehicle', 'Vehicle'),
    ], validators=[Optional()])
    date = DateField('Transaction Date', format='%Y-%m-%d', validators=[Optional()], render_kw={"type": "date"})
    amount = FloatField('Amount', validators=[DataRequired()])
    description = TextAreaField('Description', validators=[Optional()])
    payment_method = SelectField('Payment Method', choices=[
        ('cash', 'Cash'),
        ('cheque', 'Cheque'),
        ('account', 'Account Transfer')
    ], validators=[Optional()])
    reference = StringField('Reference Number', validators=[Optional()])
    vehicle_id = SelectField('Vehicle', coerce=int, validators=[Optional()])
    vehicle_owner_id = SelectField('Vehicle Owner', coerce=int, validators=[Optional()])
    contractor_id = SelectField('Contractor', coerce=int, validators=[Optional()])
    plant_id = SelectField('Plant', coerce=int, validators=[Optional()])
    petrol_pump_id = SelectField('Petrol Pump', coerce=int, validators=[Optional()])
    submit = SubmitField('Save Transaction')


class ContractorRateForm(BaseForm):
    contractor_id = SelectField('Contractor', coerce=int, validators=[DataRequired()])
    site_id = SelectField('To Site (Delivery)', coerce=int, validators=[DataRequired()])
    from_site_id = SelectField('From Site (Pickup)', coerce=int, validators=[Optional()])
    material_id = SelectField('Material (Optional)', coerce=int, validators=[Optional()])
    vehicle_owner_id = SelectField('Vehicle Owner (Optional)', coerce=int, validators=[Optional()])
    unit = SelectField('Unit', choices=[('cft', 'CFT'), ('ton', 'Ton')], validators=[DataRequired()])
    rate = FloatField('Contractor Rate per Unit (Rs.)', validators=[DataRequired()])
    vehicle_rate = FloatField('Vehicle Rate per Unit (Rs., optional)', validators=[Optional()])
    effective_from = DateField('Effective From', format='%Y-%m-%d', validators=[DataRequired()], render_kw={"type": "date"})
    effective_to = DateField('Effective To (leave blank for open-ended)', format='%Y-%m-%d', validators=[Optional()], render_kw={"type": "date"})
    notes = TextAreaField('Notes / Reason for Rate Change', validators=[Optional()])
    submit = SubmitField('Save Rate')


class DieselEntryForm(BaseForm):
    vehicle_id = SelectField('Vehicle', coerce=int, validators=[DataRequired()])
    petrol_pump_id = SelectField('Petrol Pump', coerce=int, validators=[DataRequired(message="Please select a petrol pump.")])
    order_id = SelectField('Linked Order', coerce=int, validators=[Optional()])
    date = DateField('Date', format='%Y-%m-%d', validators=[DataRequired()], render_kw={"type": "date"})
    litres = FloatField('Litres', validators=[Optional()])
    amount = FloatField('Amount (Rs.)', validators=[DataRequired()])
    receipt_number = StringField('Receipt Number', validators=[Optional()])
    notes = TextAreaField('Notes', validators=[Optional()])
    submit = SubmitField('Save Entry')


class SettingsForm(BaseForm):
    diesel_rate = FloatField('Diesel Rate Per Litre', validators=[DataRequired()])
    submit = SubmitField('Save Settings')


class EditOrderForm(OrderForm):
    submit = SubmitField('Update Order')

# Make sure to export all forms
__all__ = ['OrderForm', 'VehicleOwnerForm', 'ContractorForm', 'VehicleForm', 'PlantForm', 'MaterialForm', 'PetrolPumpForm', 'SiteForm', 'TransactionForm', 'SettingsForm', 'EditOrderForm', 'ContractorRateForm', 'DieselEntryForm']
