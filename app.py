import os
import json
from flask import Flask, render_template, request, redirect, url_for, session as flask_session, jsonify
from sqlalchemy import func, extract, between
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from database_setup import session, Transaction, Merchant, LeanMerchant, UserCharacteristic, Budget, OAuthToken
from datetime import datetime, date
from sqlalchemy.types import Integer  # Add this line
from calendar import monthrange
from services.email_sync import sync_user_transactions
from services.auth_helpers import get_credentials




SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']
app = Flask(__name__)
app.secret_key = 'your_secret_key_here'  # Replace with a secure random key


def dot_thousands(value):
    """
    Convert a numeric value (int/float/Decimal) to a string
    with '.' as the thousands separator (no decimal places).
    E.g.: 507489 -> '507.489'
    """
    if value is None:
        return ''
    # Convert to float if it’s Decimal
    val = float(value)
    # Format with comma separators for thousands
    # E.g., 507,489 for English/US style
    formatted = f"{val:,.0f}"
    # Now replace commas with dots
    return formatted.replace(',', '.')

# Register the filter with Flask/Jinja
app.jinja_env.filters['dot_thousands'] = dot_thousands



# Authentication Route
@app.route('/login')
def login():
    # Clear any existing session data
    flask_session.clear()

    # Start the OAuth flow
    flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
    creds = flow.run_local_server(port=5200)

    # Get Gmail user profile
    service = build('gmail', 'v1', credentials=creds)
    profile = service.users().getProfile(userId='me').execute()
    email = profile['emailAddress']
    flask_session['email'] = email

    # Store token in DB
    token_record = session.query(OAuthToken).filter_by(email=email).first()
    if not token_record:
        token_record = OAuthToken(email=email)

    token_record.token = creds.token
    token_record.refresh_token = creds.refresh_token
    token_record.token_uri = creds.token_uri
    token_record.client_id = creds.client_id
    token_record.client_secret = creds.client_secret
    token_record.scopes = ','.join(creds.scopes)

    session.merge(token_record)
    session.commit()

    # Check if user exists
    user = session.query(UserCharacteristic).filter_by(email=email).first()
    if not user:
        # New user → create row, redirect to questionnaire
        user = UserCharacteristic(email=email)
        session.add(user)
        session.commit()
        return redirect(url_for('questionnaire'))

    # Existing user → check if missing fields
    if not (user.dob and user.income and user.region and user.name):
        return redirect(url_for('questionnaire'))

    # ✅ Existing + complete profile → go sync transactions
    return render_template("syncing.html")



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
    if not user or not (user.dob and user.income and user.region and user.name):  # Add more fields as needed
        return redirect(url_for('questionnaire'))


    # Count how many user IDs exist in the table
    # Note: func.count() returns a numeric count of rows
    num_users = session.query(func.count(UserCharacteristic.user_id)).scalar()

    return render_template('home.html', num_users=num_users, user=user)


@app.route('/sync')
def sync():
    if 'email' not in flask_session:
        return redirect(url_for('login'))

    full = request.args.get('full', 'false').lower() == 'true'
    sync_user_transactions(flask_session['email'], full_sync=full)
    return redirect(url_for('show_transactions'))


@app.route('/sync_status')
def sync_status():
    email = flask_session.get("email")
    from services.email_sync import sync_progress
    count = sync_progress.get(email, 0)
    return jsonify({"count": count})

@app.route('/syncing')
def syncing():
    return render_template('syncing.html', request=request)





@app.route('/transactions', methods=['GET', 'POST'])
def show_transactions():

    if 'email' not in flask_session:
        return redirect(url_for('login'))

    # Get the authenticated user
    user_email = flask_session['email']
    user = session.query(UserCharacteristic).filter_by(email=user_email).first()

    if not user:
        return redirect(url_for('questionnaire'))

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
        # Preserve filter options on redirect
        filter_option = request.form.get('filter_option', 'all')
        selected_year = request.form.get('selected_year', '')
        selected_month = request.form.get('selected_month', '')

        return redirect(url_for(
            'show_transactions',
            filter=filter_option,
            year=selected_year,
            month=selected_month
        ))

    # Step 1: Get all transactions for this user missing merchant_fixed
    transactions_with_missing = session.query(Transaction).filter(
        Transaction.merchant_fixed.is_(None),
        Transaction.user_id == user_id
    ).all()

    # Step 2: Get distinct merchant_ids from those transactions
    merchant_ids = list({t.merchant_id for t in transactions_with_missing})

    # Step 3: Fetch all LeanMerchant matches in a single query
    lean_merchants = session.query(LeanMerchant).filter(
        LeanMerchant.merchant_raw.in_(merchant_ids)
    ).all()

    # Step 4: Create a fast lookup dictionary {merchant_id: LeanMerchant}
    lean_dict = {lm.merchant_raw: lm for lm in lean_merchants}

    # Step 5: Fill transactions using the lookup (in-memory, fast)
    for transaction in transactions_with_missing:
        lean_merchant = lean_dict.get(transaction.merchant_id)

        if lean_merchant:
            transaction.merchant_fixed = lean_merchant.merchant_fixed
            transaction.category = lean_merchant.category
            transaction.sub_category = lean_merchant.sub_category
            transaction.lean_merchant_id = lean_merchant.id
            print(f"✅ Filled Transaction ID {transaction.transaction_id} from LeanMerchant")
        else:
            print(f"❌ No LeanMerchant match found for Transaction ID {transaction.transaction_id}")

    # Step 6: Commit all updates in one DB call

    session.commit()  # Commit updates to the transactions table

       # 1) Read the "filter" parameter from query string, default = 'all'
    filter_option = request.args.get('filter', 'all')
    selected_year = request.args.get('year', type=int)
    selected_month = request.args.get('month', type=int)

    print(f"Filter chosen by user: {filter_option}")

    # 2) Build a base query for the user's transactions, joined with Merchant
    transactions_query = (
        session.query(Transaction, Merchant)
        .join(Merchant, Transaction.merchant_id == Merchant.merchant_id)
        .filter(Transaction.user_id == user_id)
    )

    # Apply year/month filters if selected
    if selected_year:
        transactions_query = transactions_query.filter(extract('year', Transaction.date) == selected_year)
    if selected_month:
        transactions_query = transactions_query.filter(extract('month', Transaction.date) == selected_month)


    # 3) If the user wants unverified only, add a filter
    if filter_option == 'unverified':
        transactions_query = transactions_query.filter(Transaction.merchant_fixed.is_(None))

    # 4) Sort by Transaction.date descending so newest come first
    transactions_query = transactions_query.order_by(Transaction.date.desc())

    # 5) Retrieve final results
    transactions = transactions_query.all()

    # Build categories mapping (unchanged)
    categories = {
        "Transporte": ["Bencina", "Transporte público", "Mantenimiento", "Peajes/Tag","Estacionamiento"],
        "Entretenimiento": ["Películas", "Subscripciones (Netflix)", "Conciertos", "Deportes"],
        "Alojamiento": ["Hoteles", "Arriendo"],
        "Servicios Personales": ["Peluquería/Barbería", "Farmacia", "Gimnasio", "Cuidado Personal"],
        "Shopping": ["Ropa", "Electrónicos", "Muebles", "Juguetes"],
        "Comida": ["Restaurantes", "Supermercado", "Café", "Delivery", "Otros"],
        "Hogar": ["Agua", "Gas", "Electricidad", "Internet", "Teléfono", "Mascotas", "Mantenimiento del Hogar"],
        "Salud": ["Doctor", "Seguro Médico", "Terapias", "Otros"],
        "Educación": ["Colegiatura", "Libros", "Cursos"],
        "Bancos y Finanzas": ["Comisiones", "Préstamos", "Inversiones"],
        "Otro": ["Viajes", "Regalos", "Otros"]
    }

    # Get available years and months for dropdowns
    year_months = (
        session.query(
            extract('year', Transaction.date).label('year'),
            extract('month', Transaction.date).label('month')
        )
        .filter(Transaction.user_id == user_id)
        .group_by('year', 'month')
        .order_by('year', 'month')
        .all()
    )


    return render_template(
        'transactions.html',
        transactions=transactions,
        categories=list(categories.keys()),
        category_map=categories,
        filter_option=filter_option,  # pass to template so we can highlight selected filter
        year_months=year_months,
        selected_year=selected_year,
        selected_month=selected_month,
        user=user

    )

@app.route('/visualization', methods=['GET'])
def spending_visualization():
    if 'email' not in flask_session:
        return redirect(url_for('login'))

    user_email = flask_session['email']
    user = session.query(UserCharacteristic).filter_by(email=user_email).first()
    if not user:
        return redirect(url_for('questionnaire'))

    user_id = user.user_id

    # Distinct year-month pairs
    distinct_months = (
        session.query(
            extract('year', Transaction.date).label('year'),
            extract('month', Transaction.date).label('month'),
        )
        .filter(Transaction.user_id == user_id)
        .group_by('year', 'month')
        .order_by('year', 'month')
        .all()
    )

    selected_year = request.args.get('year', type=int)
    selected_month = request.args.get('month', type=int)
    selected_category = request.args.get('category', 'all')


    if distinct_months:
        if not selected_year or not selected_month:
            # Default to last
            selected_year, selected_month = distinct_months[-1]
    else:
        # No transactions exist
        selected_year = None
        selected_month = None

    # Filter data for the chosen month/year if available
    if selected_year and selected_month:
        category_data = (
            session.query(Transaction.category, Transaction.sub_category, func.sum(Transaction.amount))
            .filter(
                Transaction.user_id == user_id,
                extract('year', Transaction.date) == selected_year,
                extract('month', Transaction.date) == selected_month
            )
            .group_by(Transaction.category, Transaction.sub_category)
            .all()
        )
    else:
        category_data = []

    # Build sunburst_data
    sunburst_data = {"name": "Spending", "children": []}
    category_map = {}
    total_spending = 0
    for category, sub_category, amount in category_data:
        total_spending += float(amount)
        if category not in category_map:
            category_map[category] = {"name": category, "children": []}
            sunburst_data["children"].append(category_map[category])
        category_map[category]["children"].append(
            {"name": sub_category or "Otro", "value": float(amount)}
        )

    # Monthly bar chart over all time
    monthly_totals = (
    session.query(
        extract('year', Transaction.date).label('year'),
        extract('month', Transaction.date).label('month'),
        func.sum(Transaction.amount).label('total')
    )
    .filter(Transaction.user_id == user_id)
    )

    if selected_category != 'all':
        monthly_totals = monthly_totals.filter(Transaction.category == selected_category)

    monthly_totals = (
        monthly_totals
        .group_by('year', 'month')
        .order_by('year', 'month')
        .all()
    )

    from collections import defaultdict
    from dateutil.relativedelta import relativedelta

    # Step 1: Get the raw monthly totals (as before)
    raw_totals = {
        f"{int(year)}-{int(month):02d}": float(total)
        for year, month, total in monthly_totals
    }

    # Step 2: Determine start and end dates
    if raw_totals:
        sorted_keys = sorted(raw_totals.keys())
        start = datetime.strptime(sorted_keys[0], "%Y-%m")
        end = datetime.strptime(sorted_keys[-1], "%Y-%m")

        # Step 3: Fill in all months with 0 if missing
        all_months = []
        totals = []
        current = start
        while current <= end:
            key = current.strftime("%Y-%m")
            all_months.append(key)
            totals.append(raw_totals.get(key, 0))
            current += relativedelta(months=1)

        bar_data = {
            'labels': all_months,
            'totals': totals,
            'category': selected_category
        }
    else:
        bar_data = {'labels': [], 'totals': [],'category': selected_category}

    # Only if we have at least 4 months
    rolling_summary = None
    if len(bar_data['totals']) >= 4:
        recent = bar_data['totals'][-1]
        prev_3 = bar_data['totals'][-4:-1]
        avg_prev_3 = sum(prev_3) / 3

        change = recent - avg_prev_3
        pct_change = (change / avg_prev_3) * 100 if avg_prev_3 != 0 else 0

        rolling_summary = {
            'recent': round(recent, 0),
            'avg_prev': round(avg_prev_3, 0),
            'pct_change': round(pct_change, 1),
            'trend': "down" if change < 0 else "up" if change > 0 else "flat"
    }


    #print("🔍 Rolling Summary:", rolling_summary)
    #print("🟢 Bar Data Totals:", bar_data['totals'])


    user_categories = session.query(Transaction.category).filter(
        Transaction.user_id == user_id,
        Transaction.category.isnot(None)
    ).distinct().all()
    categories = sorted([c[0] for c in user_categories if c[0]])  # Clean list


    return render_template(
        'visualization.html',
        sunburstData=sunburst_data,
        barData=bar_data,
        totalSpending=total_spending,
        distinct_months=distinct_months,
        selected_year=selected_year,
        selected_month=selected_month,
        rolling_summary=rolling_summary,
        selected_category=selected_category,
        categories=categories,
  

    )

@app.route('/insights')
def insights():
    return render_template("insights.html")



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

        name = request.form.get('name')


        # Fetch the logged-in user's email
        user_email = flask_session['email']

        # Try to find user, else create new
        user = session.query(UserCharacteristic).filter_by(email=user_email).first()
        if not user:
            user = UserCharacteristic(email=user_email)

        # Update all user fields
        user.dob = dob
        user.income = income
        user.sector = comuna
        user.city = provincia
        user.region = region
        user.degree = degree
        user.yoe = yoe
        user.name = name

        session.merge(user)
        session.commit()

        return render_template("syncing.html")
        

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


@app.route("/debug")
def debug():
    email = flask_session.get("email")
    if not email:
        return "No user logged in.", 401

    user = session.query(UserCharacteristic).filter_by(email=email).first()
    if not user:
        return f"No user found in DB for email {email}", 404

    transactions = (
        session.query(Transaction)
        .filter_by(user_id=user.user_id)
        .order_by(Transaction.date.desc())
        .limit(5)
        .all()
    )

    return render_template("debug.html", email=email, user=user, transactions=transactions)



@app.route('/api/visualization_data')
def get_visualization_data():
    if 'email' not in flask_session:
        return jsonify({"error": "Unauthorized"}), 401

    user_email = flask_session['email']
    user = session.query(UserCharacteristic).filter_by(email=user_email).first()
    if not user:
        return jsonify({"error": "User not found"}), 404

    user_id = user.user_id
    selected_category = request.args.get('category', 'all')
    selected_year = request.args.get('year', type=int)
    selected_month = request.args.get('month', type=int)

    from dateutil.relativedelta import relativedelta
    from datetime import datetime

    monthly_totals = (
        session.query(
            extract('year', Transaction.date).label('year'),
            extract('month', Transaction.date).label('month'),
            func.sum(Transaction.amount).label('total')
        )
        .filter(Transaction.user_id == user_id)
    )

    if selected_category != 'all':
        monthly_totals = monthly_totals.filter(Transaction.category == selected_category)

    monthly_totals = (
        monthly_totals
        .group_by('year', 'month')
        .order_by('year', 'month')
        .all()
    )

    raw_totals = {
        f"{int(year)}-{int(month):02d}": float(total)
        for year, month, total in monthly_totals
    }

    if raw_totals:
        sorted_keys = sorted(raw_totals.keys())
        start = datetime.strptime(sorted_keys[0], "%Y-%m")
        end = datetime.strptime(sorted_keys[-1], "%Y-%m")
        all_months = []
        totals = []
        current = start
        while current <= end:
            key = current.strftime("%Y-%m")
            all_months.append(key)
            totals.append(raw_totals.get(key, 0))
            current += relativedelta(months=1)
    else:
        all_months = []
        totals = []

    return jsonify({
        "labels": all_months,
        "totals": totals,
        "category": selected_category
    })

@app.route('/api/sunburst_data')
def get_sunburst_data():
    if 'email' not in flask_session:
        return jsonify({"error": "Unauthorized"}), 401

    user_email = flask_session['email']
    user = session.query(UserCharacteristic).filter_by(email=user_email).first()
    if not user:
        return jsonify({"error": "User not found"}), 404

    user_id = user.user_id
    selected_year = request.args.get('year', type=int)
    selected_month = request.args.get('month', type=int)

    category_data = []
    if selected_year and selected_month:
        category_data = (
            session.query(Transaction.category, Transaction.sub_category, func.sum(Transaction.amount))
            .filter(
                Transaction.user_id == user_id,
                extract('year', Transaction.date) == selected_year,
                extract('month', Transaction.date) == selected_month
            )
            .group_by(Transaction.category, Transaction.sub_category)
            .all()
        )

    sunburst_data = {"name": "Spending", "children": []}
    category_map = {}
    for category, sub_category, amount in category_data:
        if category not in category_map:
            category_map[category] = {"name": category, "children": []}
            sunburst_data["children"].append(category_map[category])
        category_map[category]["children"].append(
            {"name": sub_category or "Otro", "value": float(amount)}
        )

    total_spending = sum(float(amount) for _, _, amount in category_data)

    return jsonify({
        "sunburstData": sunburst_data,
        "totalSpending": total_spending
    })


@app.route('/presupuesto', methods=['GET', 'POST'])

def presupuesto():
    # 1) Check user session
    if 'email' not in flask_session:
        return redirect(url_for('login'))

    # 2) Fetch user from DB
    user_email = flask_session['email']
    user = session.query(UserCharacteristic).filter_by(email=user_email).first()
    if not user:
        return redirect(url_for('questionnaire'))

    user_id = user.user_id

    # ==============
    #  POST Logic
    # ==============
    if request.method == 'POST':
        category = request.form.get('category')
        sub_category = request.form.get('sub_category')  # may be empty
        new_budget_value = request.form.get('budget_set', '0')  # default to 0 if missing

        # Convert to float
        try:
            new_budget_value = float(new_budget_value)
        except ValueError:
            new_budget_value = 0.0

        # Fetch or create the Budget row
        budget_row = session.query(Budget).filter_by(
            user_id=user_id,
            category=category,
            sub_category=sub_category
        ).first()

        if budget_row:
            # Update existing budget
            budget_row.budget_set = new_budget_value
        else:
            # Create new budget
            budget_row = Budget(
                user_id=user_id,
                category=category,
                sub_category=sub_category,
                budget_set=new_budget_value
            )
            session.add(budget_row)

        session.commit()
        return redirect(url_for('presupuesto'))

    # ==============
    #  GET Logic
    # ==============
    # Use last transaction date (if exists) instead of today
    latest_tx = session.query(func.max(Transaction.date)).filter_by(user_id=user_id).scalar()

    if latest_tx:
        current_year = latest_tx.year
        current_month = latest_tx.month
    else:
        current_year = datetime.now().year
        current_month = datetime.now().month

    # For fraction of the month
    days_in_month = monthrange(current_year, current_month)[1]
    day_of_month = datetime.now().day
    fraction_of_month = day_of_month / days_in_month if days_in_month else 1

    # (A) Sum of all budgets: "Con tu configuración actual gastarás X al mes"
    total_budget = session.query(func.sum(Budget.budget_set))\
                          .filter_by(user_id=user_id)\
                          .scalar() or 0

    # (B) This month’s spending: "Este mes has gastado un total de X"
    this_month_spending = session.query(func.sum(Transaction.amount))\
        .filter(
            Transaction.user_id == user_id,
            extract('year', Transaction.date) == current_year,
            extract('month', Transaction.date) == current_month
        )\
        .scalar() or 0

    # (C) 3-month average spending: "En promedio gastas X al mes"
    # For the last 3 calendar months (including current)
    # Build a list of (year, month) for [this month, previous month, 2 months ago]
    months_list = []
    y = current_year
    m = current_month
    for i in range(3):
        # Calculate year/month going backwards
        adj_month = m - i
        adj_year = y
        if adj_month < 1:
            adj_month += 12
            adj_year -= 1
        months_list.append((adj_year, adj_month))

    three_month_total = 0
    for (year_m, month_m) in months_list:
        amt = session.query(func.sum(Transaction.amount))\
            .filter(
                Transaction.user_id == user_id,
                extract('year', Transaction.date) == year_m,
                extract('month', Transaction.date) == month_m
            )\
            .scalar() or 0
        three_month_total += amt

    three_month_total = three_month_total or 0  # ensure it's not None
    from decimal import Decimal
    avg_monthly_spending = three_month_total / Decimal('3.0')

    # 1) Retrieve user budgets
    user_budgets = session.query(Budget).filter_by(user_id=user_id).all()

    print(f"[DEBUG] {len(user_budgets)} existing budgets for user {user_id}")
    # 2) Calculate how much user has spent in each (cat, subcat) this month
    spending_data = (
        session.query(
            Transaction.category,
            func.coalesce(Transaction.sub_category, 'Otros').label('sub_category'),
            func.sum(Transaction.amount)
        )
        .filter(
            Transaction.user_id == user_id,
            extract('year', Transaction.date) == current_year,
            extract('month', Transaction.date) == current_month
        )
        .group_by(Transaction.category, func.coalesce(Transaction.sub_category, 'Otros'))
        .all()
    )
    print(f"[DEBUG] Spending data for user {user_id} in {current_year}-{current_month:02d}:")
    for row in spending_data:
        print(row)

    # Auto-fill missing budget rows with 0 for new (cat, subcat) pairs
    existing_budget_keys = set((b.category, b.sub_category) for b in user_budgets)
    new_budgets = []

    for (cat, subcat, _) in spending_data:
        key = (cat, subcat)
        if key not in existing_budget_keys:
            new_budget = Budget(
                user_id=user_id,
                category=cat,
                sub_category=subcat,
                budget_set=0.0
            )
            session.add(new_budget)
            new_budgets.append(new_budget)

    if new_budgets:
        session.commit()
        print(f"[DEBUG] Added {len(new_budgets)} new budget rows with 0 value.")
        user_budgets = session.query(Budget).filter_by(user_id=user_id).all()  # 🔁 Re-fetch with new data



    # 3) Merge budgets + spending into budget_view_data
    spent_map = {}
    for (cat, subcat, total_amount) in spending_data:
        spent_map[(cat, subcat)] = float(total_amount)

    budget_view_data = []
    for b in user_budgets:
        usage = spent_map.get((b.category, b.sub_category), 0.0)
        budget_limit = float(b.budget_set)
        over_under = budget_limit - usage
        budget_view_data.append({
            "category": b.category,
            "sub_category": b.sub_category,
            "budget_set": budget_limit,
            "usage": usage,
            "over_under": over_under
        })

    # If you want to show categories that have spending but no budget, do so here
    for (cat, subcat), spent_amount in spent_map.items():
        exists = any(
            x for x in budget_view_data
            if x["category"] == cat and x["sub_category"] == subcat
        )
        if not exists:
            budget_view_data.append({
                "category": cat,
                "sub_category": subcat,
                "budget_set": 0.0,
                "usage": spent_amount,
                "over_under": -spent_amount
            })

    # optional: total_savings or leftover across categories
    # (not used if we're switching to your new text, but you can keep it)
    total_savings = sum(
        item["over_under"] for item in budget_view_data if item["over_under"] > 0
    )

    print("[DEBUG] Budget View Data:")
    for item in budget_view_data:
        print(item)


    # Render template, passing the new summary stats
    return render_template(
        "presupuesto.html",
        budget_data=budget_view_data,
        fraction_of_month=fraction_of_month,
        total_savings=total_savings,

        total_budget=total_budget,
        this_month_spending=this_month_spending,
        avg_monthly_spending=avg_monthly_spending
    )




if __name__ == '__main__':
    print("Starting Flask server...")
    print("Available Routes:")
    print("  /             -> Home")
    print("  /transactions -> Transactions")
    print("  /visualization -> Spending Visualization")
    app.run(debug=True, port=5000)
