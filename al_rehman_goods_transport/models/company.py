from ..extensions import db


class Company(db.Model):
    __tablename__ = "company"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), default="Al Rehman Goods Transport", nullable=False)
    balance = db.Column(db.Float, default=0.0, nullable=False)

    def __repr__(self):
        return f"<Company {self.name}>"
