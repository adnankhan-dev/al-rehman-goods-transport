from ..extensions import db
from ..models import Company


class CompanyRepository:
    def __init__(self, session=None):
        self.session = session or db.session

    def get_singleton(self):
        companies = self.session.query(Company).order_by(Company.id.asc()).all()
        if len(companies) > 1:
            raise ValueError("Multiple company balance records exist. Consolidation is required before continuing.")
        if companies:
            company = companies[0]
            if company.balance is None:
                company.balance = 0.0
            return company

        company = Company(balance=0.0)
        self.session.add(company)
        self.session.flush()
        return company
