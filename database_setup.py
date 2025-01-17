from sqlalchemy import create_engine, Column, Integer, String, Numeric, DateTime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

Base = declarative_base()

# Setup database connection
DATABASE_URI = 'postgresql+psycopg2://postgres:4655Mvem0v3Quick@localhost/finance_app'
engine = create_engine(DATABASE_URI)
Session = sessionmaker(bind=engine)
session = Session()

# Define database models
class Merchant(Base):
    __tablename__ = 'merchants'
    merchant_id = Column(Integer, primary_key=True)
    merchant_name = Column(String, unique=True)
    category = Column(String)
    sub_category = Column(String)

class Transaction(Base):
    __tablename__ = 'transactions'
    transaction_id = Column(Integer, primary_key=True)
    user_id = Column(Integer)
    merchant_id = Column(Integer)
    amount = Column(Numeric)
    date = Column(DateTime)
    category = Column(String)
    sub_category = Column(String)

Base.metadata.create_all(engine)
