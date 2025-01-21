import os
import pickle  # Add this for token management
import json
from flask import Flask, render_template, request, redirect, url_for, session as flask_session, jsonify
from sqlalchemy import func, extract, between
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from database_setup import session, Transaction, Merchant, LeanMerchant, UserCharacteristic
from datetime import datetime
from sqlalchemy.types import Integer  # Add this line


SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']
app = Flask(__name__)
app.secret_key = 'your_secret_key_here'  # Replace with a secure random key


# Helper function to manage OAuth token
def get_credentials():
    creds = None
    # Check if token.pickle exists
    if os.path.exists('token.pickle'):
        with open('token.pickle', 'rb') as token:
            creds = pickle.load(token)
    # Refresh or fetch new credentials if necessary
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
            creds = flow.run_local_server(port=5200)  # Use a non-conflicting port
        # Save the credentials for the next run
        with open('token.pickle', 'wb') as token:
            pickle.dump(creds, token)
    return creds

# Authentication Route
@app.route('/login')
def login():
    # Clear any existing session data
    flask_session.clear()

    creds = get_credentials()  # Reuse or fetch credentials
    flask_session['credentials'] = creds_to_dict(creds)

    # Get the user's email and store it in the session
    service = build('gmail', 'v1', credentials=creds)
    profile = service.users().getProfile(userId='me').execute()
    flask_session['email'] = profile['emailAddress']

    # Ensure the user exists in the database
    user = session.query(UserCharacteristic).filter_by(email=flask_session['email']).first()
    if not user:
        user = UserCharacteristic(email=flask_session['email'])
        session.add(user)
        session.commit()

    return redirect(url_for('home'))


# Helper function to convert credentials to dictionary
def creds_to_dict(creds):
    return {
        'token': creds.token,
        'refresh_token': creds.refresh_token,
        'token_uri': creds.token_uri,
        'client_id': creds.client_id,
        'client_secret': creds.client_secret,
        'scopes': creds.scopes
    }


@app.route('/logout')
def logout():
    flask_session.clear()
    return redirect(url_for('home'))



@app.route('/')
def home():
    if 'email' not in flask_session:
        return redirect(url_for('login'))

    # Get the user's email
    user_email = flask_session['email']
    user = session.query(UserCharacteristic).filter_by(email=user_email).first()

    # Check if user exists and has completed the questionnaire
    if not user or not (user.dob and user.income and user.region):  # Add more fields as needed
        return redirect(url_for('questionnaire'))

    return render_template('home.html')


@app.route('/transactions', methods=['GET', 'POST'])
def show_transactions():

    if 'email' not in flask_session:
        return redirect(url_for('login'))

    # Get the authenticated user
    user_email = flask_session['email']
    user = session.query(UserCharacteristic).filter_by(email=user_email).first()

    if not user:
        return "User not found", 403

    user_id = user.user_id

    if request.method == 'POST':
        print("Form Data Received:", request.form)

        # Get merchant_id and corresponding row data
        merchant_id = request.form.get('merchant_id')  # Extract the merchant_id submitted
        if not merchant_id:
            print("Merchant ID missing. Skipping update.")
            return redirect(url_for('show_transactions'))

        fixed_category = request.form.get(f'fixed_category_{merchant_id}', '').strip()
        fixed_sub_category = request.form.get(f'fixed_sub_category_{merchant_id}', '').strip()
        fixed_merchant_name = request.form.get(f'fixed_merchant_name_{merchant_id}', '').strip()

        print(f"Merchant ID: {merchant_id}, Category: {fixed_category}, Sub-Category: {fixed_sub_category}, Merchant Fixed: {fixed_merchant_name}")

        # Ensure category and fixed_merchant_name are provided
        if not fixed_category or not fixed_merchant_name:
            print("Category or Fixed Merchant Name missing. Skipping update.")
            return redirect(url_for('show_transactions'))


        # Check if there's already a row in LeanMerchant with the same merchant, category, and subcategory
        lean_merchant = session.query(LeanMerchant).filter(
            LeanMerchant.merchant_raw == str(merchant_id),  # Ensure comparison is between strings
            LeanMerchant.category == fixed_category,
            LeanMerchant.sub_category == fixed_sub_category
        ).first()

        if lean_merchant:
            # Update the existing LeanMerchant row
            lean_merchant.verification_count += 1
            print(f"Updated LeanMerchant: {lean_merchant}")
        else:
            # Insert a new row for genuinely different category-subcategory-merchant combinations
            lean_merchant = LeanMerchant(
                merchant_raw=merchant_id,
                category=fixed_category,
                sub_category=fixed_sub_category,
                merchant_fixed=fixed_merchant_name.capitalize(),
                verification_count=1
            )
            session.add(lean_merchant)
            session.flush()  # Ensure the new `lean_merchant.id` is available
            print(f"Inserted New LeanMerchant: {lean_merchant}")

        # Update the `transactions` table for this merchant_id and the authenticated user
        transactions_to_update = session.query(Transaction).filter(
            Transaction.merchant_id == merchant_id,
            Transaction.user_id == user_id  # Restrict updates to this user's transactions
        ).all()

        for transaction in transactions_to_update:
            transaction.merchant_fixed = fixed_merchant_name.capitalize()
            transaction.category = fixed_category
            transaction.sub_category = fixed_sub_category
            transaction.lean_merchant_id = lean_merchant.id  # Set the lean_merchant_id
            print(f"Updated Transaction ID {transaction.transaction_id} with merchant_fixed={transaction.merchant_fixed}, "
                  f"category={transaction.category}, sub_category={transaction.sub_category}, "
                  f"lean_merchant_id={transaction.lean_merchant_id}")

        session.commit()  # Ensure all changes are committed
        print("Session commit completed.")
        return redirect(url_for('show_transactions'))

    # Step 1: Check for transactions with missing `merchant_fixed` values
    transactions_with_missing = session.query(Transaction).filter(
        Transaction.merchant_fixed.is_(None),
        Transaction.user_id == user_id  # Filter for this user's transactions only
    ).all()

    # Step 2: Attempt to fill in missing data from LeanMerchant
    for transaction in transactions_with_missing:
        lean_merchant = session.query(LeanMerchant).filter(
            LeanMerchant.merchant_raw == str(transaction.merchant_id)
        ).first()

        if lean_merchant:
            transaction.merchant_fixed = lean_merchant.merchant_fixed
            transaction.category = lean_merchant.category
            transaction.sub_category = lean_merchant.sub_category
            transaction.lean_merchant_id = lean_merchant.id  # Set lean_merchant_id
            print(f"Filled Transaction ID {transaction.transaction_id} from LeanMerchant: "
                  f"merchant_fixed={transaction.merchant_fixed}, category={transaction.category}, sub_category={transaction.sub_category}, "
                  f"lean_merchant_id={transaction.lean_merchant_id}")
        else:
            print(f"No LeanMerchant match found for Transaction ID {transaction.transaction_id}.")

    session.commit()  # Commit updates to the transactions table

    # Fetch transactions and categories for display
    transactions = (
        session.query(Transaction, Merchant)
        .join(Merchant, Transaction.merchant_id == Merchant.merchant_id)
        .filter(Transaction.user_id == user_id)  # Restrict to this user's transactions
        .all()
    )

    categories = {
        "Transporte": ["Bencina", "Transporte público", "Mantenimiento","Peajes/Tag"],
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

    return render_template('transactions.html', transactions=transactions, categories=list(categories.keys()), category_map=categories)


@app.route('/visualization', methods=['GET'])
def spending_visualization():

    if 'email' not in flask_session:
        return redirect(url_for('login'))

    user_email = flask_session['email']
    user = session.query(UserCharacteristic).filter_by(email=user_email).first()

    if not user:
        return "User not found", 403

    user_id = user.user_id
    

    # Get the most recent month
    current_year = datetime.now().year
    current_month = datetime.now().month

    # Prepare hierarchical data for sunburst chart
    category_data = (
        session.query(Transaction.category, Transaction.sub_category, func.sum(Transaction.amount))
        .filter(
            Transaction.user_id == user_id,
            extract('year', Transaction.date) == current_year,
            extract('month', Transaction.date) == current_month
        )
        .group_by(Transaction.category, Transaction.sub_category)
        .all()
    )

    sunburst_data = {"name": "Spending", "children": []}
    category_map = {}
    total_spending = 0

    for category, sub_category, amount in category_data:
        total_spending += float(amount)  # Calculate the total spending
        if category not in category_map:
            category_map[category] = {"name": category, "children": []}
            sunburst_data["children"].append(category_map[category])
        category_map[category]["children"].append(
            {"name": sub_category or "Other", "value": float(amount)}
        )

    # Prepare bar chart data for monthly spending
    monthly_totals = (
        session.query(
            extract('year', Transaction.date).label('year'),
            extract('month', Transaction.date).label('month'),
            func.sum(Transaction.amount).label('total')
        )
        .filter(Transaction.user_id == user_id)
        .group_by('year', 'month')
        .order_by('year', 'month')
        .all()
    )
    bar_data = {
        'labels': [f"{int(item[0])}-{int(item[1]):02d}" for item in monthly_totals],
        'totals': [float(item[2]) for item in monthly_totals],
    }

    return render_template(
        'visualization.html',
        sunburstData=sunburst_data,
        barData=bar_data,
        totalSpending=total_spending  # Pass the total spending to the template
    )



@app.route('/questionnaire', methods=['GET', 'POST'])
def questionnaire():
    if request.method == 'POST':
        # Get form data
        dob = request.form.get('dob')
        income = request.form.get('income')
        region = request.form.get('region')
        provincia = request.form.get('provincia')
        comuna = request.form.get('comuna')
        degree = request.form.get('degree')
        yoe = request.form.get('yoe')

        # Fetch the logged-in user's email
        user_email = flask_session['email']

        # Find the user in the database and update their details
        user = session.query(UserCharacteristic).filter_by(email=user_email).first()
        if user:
            user.dob = dob
            user.income = income
            user.sector = comuna
            user.city = provincia
            user.region = region
            user.degree = degree
            user.yoe = yoe
            session.commit()  # Save changes to the database

        return redirect(url_for('home'))  # After submission, redirect to the home page

    # Load JSON data with regions, cities, and comunas
    with open('regions_data.json', 'r', encoding='utf-8') as f:
        regions_data = json.load(f)

    return render_template('questionnaire.html', regions_data=regions_data)


@app.route('/benchmark')
def benchmark():
    # Fetch user details
    user_email = flask_session['email']
    user = session.query(UserCharacteristic).filter_by(email=user_email).first()
    if not user:
        return redirect(url_for('questionnaire'))

    # Define user's age
    age = (datetime.now().year - user.dob.year) if user.dob else None
    print(f"User age: {age}")

    # Calculate user's decade start and end
    user_decade_start = (age // 10) * 10
    user_decade_end = user_decade_start + 9
    print(f"User's decade range: {user_decade_start}-{user_decade_end}")

    # Retrieve unique users in the same age decade
    similar_users_ids = session.query(UserCharacteristic.user_id).filter(
        between((datetime.now().year - func.date_part('year', UserCharacteristic.dob)).cast(Integer), user_decade_start, user_decade_end)
    ).all()
    similar_users_ids = [u[0] for u in similar_users_ids]
    print(f"Similar user IDs: {similar_users_ids}")

    # Function to calculate monthly averages for a set of users
    def calculate_monthly_averages(user_ids):
        monthly_totals = session.query(
            Transaction.user_id,
            Transaction.category,
            func.date_trunc('month', Transaction.date).label('month'),
            func.sum(Transaction.amount).label('monthly_total')
        ).filter(
            Transaction.user_id.in_(user_ids)
        ).group_by(
            Transaction.user_id, Transaction.category, func.date_trunc('month', Transaction.date)
        ).all()

        user_monthly_averages = {}
        for user_id, category, month, monthly_total in monthly_totals:
            user_monthly_averages.setdefault(user_id, {}).setdefault(category, []).append(monthly_total)

        category_averages = {}
        for user_id, categories in user_monthly_averages.items():
            for category, monthly_totals in categories.items():
                average = sum(monthly_totals) / len(monthly_totals)  # Monthly average for this user
                category_averages.setdefault(category, []).append(average)

        group_averages = {
            category: sum(averages) / len(averages) if averages else 0
            for category, averages in category_averages.items()
        }

        return group_averages, category_averages

    # Calculate averages for similar users
    group_averages, similar_category_averages = calculate_monthly_averages(similar_users_ids)
    print("\n=== Debug: Monthly Averages for Similar Users ===")
    for category, averages in similar_category_averages.items():
        print(f"Category: {category}, Averages: {averages}")
    print(f"Group averages (final): {group_averages}\n")

    # Calculate averages for all users
    all_users_ids = session.query(UserCharacteristic.user_id).distinct().all()
    all_users_ids = [u[0] for u in all_users_ids]
    all_averages, all_category_averages = calculate_monthly_averages(all_users_ids)
    print("\n=== Debug: Monthly Averages for All Users ===")
    for category, averages in all_category_averages.items():
        print(f"Category: {category}, Averages: {averages}")
    print(f"All users' averages (final): {all_averages}\n")

    # Calculate user-specific monthly averages
    user_monthly_averages, _ = calculate_monthly_averages([user.user_id])
    print("\n=== Debug: Monthly Averages for Current User ===")
    for category, average in user_monthly_averages.items():
        print(f"Category: {category}, Average: {average}")

    # Calculate differences for the bar chart
    similar_differences = {
        category: user_monthly_averages.get(category, 0) - group_averages.get(category, 0)
        for category in set(user_monthly_averages.keys()).union(group_averages.keys())
    }

    all_differences = {
        category: user_monthly_averages.get(category, 0) - all_averages.get(category, 0)
        for category in set(user_monthly_averages.keys()).union(all_averages.keys())
    }

    # Debug: Print differences
    print("\n=== Debug: Differences for Similar Group ===")
    for category, difference in similar_differences.items():
        print(f"Category: {category}, Difference: {difference}")

    print("\n=== Debug: Differences for All Users ===")
    for category, difference in all_differences.items():
        print(f"Category: {category}, Difference: {difference}")

    # Pass data to the template
    return render_template(
        'benchmark.html',
        user_spending=user_monthly_averages,
        group_averages=group_averages,
        all_users_averages=all_averages,
        age_range=f"{user_decade_start}-{user_decade_end}",
        similar_differences=similar_differences,
        all_differences=all_differences
    )



if __name__ == '__main__':
    print("Starting Flask server...")
    print("Available Routes:")
    print("  /             -> Home")
    print("  /transactions -> Transactions")
    print("  /visualization -> Spending Visualization")
    app.run(debug=True, port=5000)
