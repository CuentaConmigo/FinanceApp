from sqlalchemy import create_engine, Column, Integer, String, Numeric, DateTime,ForeignKey, func, Text
from sqlalchemy.orm import declarative_base
from sqlalchemy.orm import sessionmaker,relationship
from datetime import datetime


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
    merchant = relationship("Merchant", backref="transactions")


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
    last_synced = Column(DateTime, default=None)




class Budget(Base):
    __tablename__ = 'budgets'
    budget_id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('user_characteristics.user_id'), nullable=False)
    category = Column(String, nullable=False)
    sub_category = Column(String)
    budget_set = Column(Numeric, nullable=False)

    user = relationship("UserCharacteristic", backref="budgets")

class OAuthToken(Base):
    __tablename__ = 'oauth_tokens'
    email = Column(String, primary_key=True)
    token = Column(Text)
    refresh_token = Column(Text)
    token_uri = Column(Text)
    client_id = Column(Text)
    client_secret = Column(Text)
    scopes = Column(Text)


class Insight(Base):
    __tablename__ = 'insights'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('user_characteristics.user_id'))
    month = Column(Integer, nullable=False)
    year = Column(Integer, nullable=False)
    version = Column(Integer, default=1)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    tokens_used = Column(Integer)



# Create all tables in the database
Base.metadata.create_all(engine)
