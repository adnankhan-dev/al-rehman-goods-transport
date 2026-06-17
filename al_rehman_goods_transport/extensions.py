from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import backref, relationship

from .core.database import Base, SessionLocal


class SQLAlchemyCompat:
    Model = Base
    Column = Column
    Integer = Integer
    String = String
    Float = Float
    Text = Text
    Boolean = Boolean
    DateTime = DateTime
    ForeignKey = ForeignKey
    relationship = staticmethod(relationship)
    backref = staticmethod(backref)
    func = func
    session = SessionLocal


db = SQLAlchemyCompat()
