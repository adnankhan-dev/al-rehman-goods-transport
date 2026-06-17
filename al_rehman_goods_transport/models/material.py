from ..extensions import db


class Material(db.Model):
    __tablename__ = "material"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    unit = db.Column(db.String(10), default="cft", nullable=False)

    orders = db.relationship("Order", back_populates="material")

    def __repr__(self):
        return f"<Material {self.name}>"
