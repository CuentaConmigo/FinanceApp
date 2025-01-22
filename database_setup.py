from sqlalchemy import create_engine, Column, Integer, String, Numeric, DateTime,ForeignKey, func
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
    user_id = Column(Integer, ForeignKey('user_characteristics.user_id'))  # Updated to reference user_characteristics
    merchant_id = Column(Integer, ForeignKey('merchants.merchant_id'))  # Existing reference
    lean_merchant_id = Column(Integer, ForeignKey('lean_merchants.id'), nullable=True)  # New reference
    amount = Column(Numeric)
    date = Column(DateTime)
    category = Column(String)
    sub_category = Column(String)
    merchant_fixed = Column(String)

class LeanMerchant(Base):
    __tablename__ = 'lean_merchants'
    id = Column(Integer, primary_key=True)
    merchant_raw = Column(Integer, ForeignKey('merchants.merchant_id'), nullable=False)  # Foreign key from Merchant
    category = Column(String, nullable=False)
    sub_category = Column(String, nullable=True)
    merchant_fixed = Column(String, nullable=False)
    verification_count = Column(Integer, default=0) 


class UserCharacteristic(Base):
    __tablename__ = 'user_characteristics'
    user_id = Column(Integer, primary_key=True)
    email = Column(String, unique=True, nullable=False)  # Email for OAuth integration
    dob = Column(DateTime)
    income = Column(Numeric)
    sector = Column(String)
    city = Column(String)
    region = Column(String)
    degree = Column(String)
    yoe = Column(Integer)
    name = Column(String)  # matches the "name" column in your PostgreSQL table





# Create all tables in the database
Base.metadata.create_all(engine)
