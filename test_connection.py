from sqlalchemy import create_engine

# Make sure to replace 'yourpassword' with your actual password
DATABASE_URI = r'postgresql+psycopg2://postgres:4655Mvem0v3Quick@localhost/finance_app'
engine = create_engine(DATABASE_URI)
try:
    conn = engine.connect()
    print("Connected successfully!")
    conn.close()
except Exception as e:
    print("Error connecting to the database: ", e)