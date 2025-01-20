import random
from datetime import datetime, timedelta
from faker import Faker
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from database_setup import Base, UserCharacteristic, Transaction


# Enable SQLAlchemy logging
import logging
logging.basicConfig()
logging.getLogger('sqlalchemy.engine').setLevel(logging.INFO)


# Initialize Faker and SQLAlchemy
fake = Faker()
engine = create_engine('postgresql://postgresql:4655Mvem0v3Quick@localhost/finance_app')  # Replace with your credentials
Session = sessionmaker(bind=engine)
session = Session()


print("Testing database connection...")
try:
    with engine.connect() as connection:
        print("Connection successful!")
except Exception as e:
    print(f"Connection failed: {e}")


# Categories and Subcategories
categories = {
    "Transporte": ["Bencina", "Transporte público", "Mantenimiento"],
    "Entretenimiento": ["Películas", "Subscripciones (Netflix)", "Conciertos", "Deportes"],
    "Alojamiento": ["Hoteles", "Arriendo"],
    "Servicios Personales": ["Peluquería/Barbería", "Farmacia", "Gimnasio", "Cuidado Personal"],
    "Shopping": ["Ropa", "Electrónicos", "Muebles", "Juguetes"],
    "Comida": ["Restaurantes", "Supermercado", "Café", "Delivery", "Otros"],
    "Hogar": ["Agua", "Gas", "Electricidad", "Internet", "Teléfono", "Mascotas", "Mantenimiento del Hogar"],
    "Salud": ["Doctor", "Seguro Médico", "Terapias", "Otros"],
    "Educación": ["Colegiatura", "Libros", "Cursos"],
    "Bancos y Finanzas": ["Comisiones", "Préstamos", "Inversiones"],
    "Otro": []
}

# Generate Fake Users
def create_fake_users(n_users=10):
    users = []
    print("Creating fake users...")
    for _ in range(n_users):
        dob = fake.date_of_birth(minimum_age=20, maximum_age=60)
        income = random.randint(500000, 5000000)  # CLP
        comuna = random.choice(["Providencia", "Las Condes", "Lo Barnechea"])
        degree = fake.random_element(["Ingeniero", "Profesor", "Doctor", "Contador", "Artista"])
        yoe = max(0, datetime.now().year - dob.year - 18)  # Years of experience
        user = UserCharacteristic(
            email=fake.unique.email(),
            dob=dob,
            income=income,
            region="Región Metropolitana de Santiago",
            city="Santiago",
            sector=comuna,
            degree=degree,
            yoe=yoe
        )
        print(f"Generated user: {user.email}, Income: {user.income}, DOB: {user.dob}")
        users.append(user)
    print(f"{len(users)} fake users created successfully.")
    return users


# Generate Fake Transactions
def create_fake_transactions(users, n_transactions_per_user=10):
    transactions = []
    print("Creating fake transactions...")
    for user in users:
        for _ in range(n_transactions_per_user):
            category = random.choice(list(categories.keys()))
            sub_category = random.choice(categories[category]) if categories[category] else None
            transaction = Transaction(
                user_id=user.user_id,
                merchant_id=random.randint(1, 100),  # Assuming merchant IDs exist
                amount=random.uniform(1000, 100000),  # Transaction amount in CLP
                date=fake.date_time_between(start_date='-1y', end_date='now'),
                category=category,
                sub_category=sub_category,
                merchant_fixed=fake.company()  # Fake merchant names
            )
            print(f"Generated transaction for user {user.email}: {transaction.amount} CLP in {transaction.category}")
            transactions.append(transaction)
    print(f"{len(transactions)} fake transactions created successfully.")
    return transactions

# Insert Data into the Database
def populate_database():
    print("Populating the database...")
    
    # Create fake users
    fake_users = create_fake_users()
    session.add_all(fake_users)
    print("Added users to session.")
    session.commit()  # Commit to assign user IDs
    print("Committed fake users to database.")
    
    # Create fake transactions
    fake_transactions = create_fake_transactions(fake_users)
    session.add_all(fake_transactions)
    print("Added transactions to session.")
    session.commit()  # Commit transactions
    print("Committed fake transactions to database.")


# Run the Population Script
if __name__ == '__main__':
    Base.metadata.create_all(engine)  # Ensure tables are created
    populate_database()
    print("Database populated with fake users and transactions!")
