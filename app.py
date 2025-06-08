import os
import json
from flask import Flask, render_template, request, redirect, url_for, session as flask_session, jsonify,flash
from sqlalchemy import func, extract, between
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from database_setup import session, Transaction, Merchant, LeanMerchant, UserCharacteristic, Budget, OAuthToken, Insight, Feedback
from datetime import datetime, date, timedelta
from sqlalchemy.types import Integer  # Add this line
from calendar import monthrange
from services.email_sync import sync_user_transactions
from services.auth_helpers import create_oauth_flow, fernet,get_credentials
from collections import defaultdict, Counter
from openai import OpenAI
from dotenv import load_dotenv
import re
from cryptography.fernet import Fernet
load_dotenv()
ENCRYPTION_KEY = os.getenv('ENCRYPTION_KEY')
fernet = Fernet(ENCRYPTION_KEY)




SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']
app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY')


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



@app.route('/login')
def login():
    flask_session.clear()

    flow = create_oauth_flow()
    auth_url, state = flow.authorization_url(
        access_type='offline',
        include_granted_scopes='true',
        prompt='consent'
    )
    flask_session['state'] = state
    return redirect(auth_url)

@app.route('/oauth2callback')
def oauth2callback():
    state = flask_session.get('state')
    flow = create_oauth_flow()
    flow.fetch_token(authorization_response=request.url)

    creds = flow.credentials
    service = build("gmail", "v1", credentials=creds)
    profile = service.users().getProfile(userId="me").execute()
    email = profile["emailAddress"]
    flask_session["email"] = email

    # Save tokens to DB
    token_record = session.query(OAuthToken).filter_by(email=email).first()
    if not token_record:
        token_record = OAuthToken(email=email)

    token_record.token = fernet.encrypt(creds.token.encode()).decode()
    token_record.refresh_token = fernet.encrypt(creds.refresh_token.encode()).decode() if creds.refresh_token else None
    token_record.token_uri = creds.token_uri
    token_record.client_id = creds.client_id
    token_record.client_secret = creds.client_secret
    token_record.scopes = ",".join(creds.scopes)

    session.merge(token_record)
    session.commit()

    # User logic
    user = session.query(UserCharacteristic).filter_by(email=email).first()
    if not user:
        user = UserCharacteristic(email=email, onboarded=False)
        session.add(user)
        session.commit()
        return render_template("onboarding.html")

    elif not user.onboarded:
        return render_template("onboarding.html")

    if not (user.dob and user.income and user.region and user.name):
        return redirect(url_for("questionnaire"))

    return render_template("syncing.html", request=request)



@app.route('/start_sync', methods=['POST'])
def start_sync():
    if 'email' not in flask_session:
        return redirect(url_for('login'))

    email = flask_session['email']

    user = session.query(UserCharacteristic).filter_by(email=email).first()
    if not user:
        user = UserCharacteristic(email=email)
        session.add(user)

    user.onboarded = True
    session.commit()

    sync_user_transactions(email, full_sync=True)

    return redirect(url_for('questionnaire'))




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

    if not user or not user.onboarded:
     return render_template("onboarding.html")


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
        "Comida": ["Restaurantes", "Supermercado", "Café", "Delivery","Snacks", "Otros"],
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


@app.route('/add_transaction', methods=['GET', 'POST'])
def add_transaction():
    if 'email' not in flask_session:
        return redirect(url_for('login'))

    user_email = flask_session['email']
    user = session.query(UserCharacteristic).filter_by(email=user_email).first()
    if not user:
        return redirect(url_for('questionnaire'))

    categories = {
        "Transporte": ["Bencina", "Transporte público", "Mantenimiento", "Peajes/Tag","Estacionamiento"],
        "Entretenimiento": ["Películas", "Subscripciones (Netflix)", "Conciertos", "Deportes"],
        "Alojamiento": ["Hoteles", "Arriendo"],
        "Servicios Personales": ["Peluquería/Barbería", "Farmacia", "Gimnasio", "Cuidado Personal"],
        "Shopping": ["Ropa", "Electrónicos", "Muebles", "Juguetes"],
        "Comida": ["Restaurantes", "Supermercado", "Café", "Delivery","Snacks", "Otros"],
        "Hogar": ["Agua", "Gas", "Electricidad", "Internet", "Teléfono", "Mascotas", "Mantenimiento del Hogar"],
        "Salud": ["Doctor", "Seguro Médico", "Terapias", "Otros"],
        "Educación": ["Colegiatura", "Libros", "Cursos"],
        "Bancos y Finanzas": ["Comisiones", "Préstamos", "Inversiones"],
        "Otro": ["Viajes", "Regalos", "Otros"]
    }

    if request.method == 'POST':
        date_str = request.form.get('date', '').strip()
        amount_str = request.form.get('amount', '').strip()
        category = request.form.get('category', '').strip()
        sub_category = request.form.get('sub_category', '').strip()
        merchant_name = request.form.get('merchant_name', '').strip() or "Manual"

        # 🔐 Validate fields
        if not date_str or not re.match(r'\d{4}-\d{2}-\d{2}', date_str):
            flash("Fecha inválida.")
            return redirect(url_for('add_transaction'))

        if not re.match(r'^\d+(\.\d{1,2})?$', amount_str):
            flash("Monto inválido. Usa solo números.")
            return redirect(url_for('add_transaction'))

        if not category or category not in categories:
            flash("Categoría inválida.")
            return redirect(url_for('add_transaction'))

        if not sub_category or sub_category not in categories.get(category, []):
            flash("Subcategoría inválida.")
            return redirect(url_for('add_transaction'))

        if len(merchant_name) > 50:
            flash("Nombre del comercio muy largo (máx 50 caracteres).")
            return redirect(url_for('add_transaction'))

        # 🔄 Parse safely
        try:
            amount = float(amount_str)
            date_obj = datetime.strptime(date_str, '%Y-%m-%d')
        except Exception as e:
            flash("Error al procesar la fecha o el monto.")
            return redirect(url_for('add_transaction'))

        # 🔍 Use or create merchant
        merchant = session.query(Merchant).filter_by(merchant_name=merchant_name).first()
        if not merchant:
            merchant = Merchant(merchant_name=merchant_name, category=category, sub_category=sub_category)
            session.add(merchant)
            session.flush()

        # 💾 Store transaction
        new_tx = Transaction(
            user_id=user.user_id,
            merchant_id=merchant.merchant_id,
            amount=amount,
            date=date_obj,
            category=category,
            sub_category=sub_category,
            merchant_fixed=merchant_name
        )
        session.add(new_tx)
        session.commit()

        flash("Transacción agregada con éxito.")
        return redirect(url_for('show_transactions'))

    return render_template('add_transaction.html', categories=categories)





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
    raw_categories = [c[0] for c in user_categories if c[0]]
    categories = sorted([cat for cat in raw_categories if cat != "No Verificado"])
    if "No Verificado" in raw_categories:
        categories.append("No Verificado")



    return render_template(
        'visualization.html',
        user=user,
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



@app.route('/questionnaire', methods=['GET', 'POST'])
def questionnaire():
    if request.method == 'POST':
        # Get form data
        dob = request.form.get('dob', '').strip()
        income = request.form.get('income', '').strip()
        region = request.form.get('region', '').strip()
        provincia = request.form.get('provincia', '').strip()
        comuna = request.form.get('comuna', '').strip()
        degree = request.form.get('degree', '').strip()
        yoe = request.form.get('yoe', '').strip()
        name = request.form.get('name', '').strip()

        # Validate fields

        if not dob or not re.match(r'\d{4}-\d{2}-\d{2}', dob):
            flash("Fecha de nacimiento inválida.")
            return redirect(url_for('questionnaire'))

        try:
            income_val = int(income)
            if income_val <= 0:
                flash("Los ingresos deben ser un número positivo.")
                return redirect(url_for('questionnaire'))
        except ValueError:
            flash("Ingresos deben ser numéricos.")
            return redirect(url_for('questionnaire'))

        if len(name) > 50:
            flash("El nombre es demasiado largo.")
            return redirect(url_for('questionnaire'))

        if not region or not provincia or not comuna:
            flash("Por favor completa región, provincia y comuna.")
            return redirect(url_for('questionnaire'))

        try:
            yoe_val = int(yoe)
            if yoe_val < 0 or yoe_val > 80:
                flash("Años de experiencia inválido.")
                return redirect(url_for('questionnaire'))
        except ValueError:
            flash("Años de experiencia debe ser un número.")
            return redirect(url_for('questionnaire'))


        # Fetch user
        user_email = flask_session['email']
        user = session.query(UserCharacteristic).filter_by(email=user_email).first()
        if not user:
            user = UserCharacteristic(email=user_email)

        # Save data
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

    # Load dropdown data
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
        from collections import defaultdict

        # Step 1: Query transactions grouped by user, category, and month
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

        # Step 2: Organize monthly data per user/category
        user_months = defaultdict(lambda: defaultdict(list))
        for user_id, category, month, monthly_total in monthly_totals:
            user_months[user_id][category].append((month, monthly_total))

        category_averages = defaultdict(list)

        for user_id, categories in user_months.items():
            total_user_avg = 0
            for category, month_data in categories.items():
                # Sort months descending to get latest
                sorted_data = sorted(month_data, key=lambda x: x[0], reverse=True)
                last_3 = sorted_data[:3]
                if last_3:
                    avg = sum(v for _, v in last_3) / len(last_3)
                    category_averages[category].append(avg)
                    total_user_avg += avg
            category_averages["Total Gastos"].append(total_user_avg)

        # Step 3: Final average across users for each category
        group_averages = {
            category: sum(avgs) / len(avgs) if avgs else 0
            for category, avgs in category_averages.items()
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

    def sort_categories(d):
        keys = sorted([k for k in d.keys() if k not in ("Total Gastos", "No Verificado")])
        if "Total Gastos" in d:
            keys = ["Total Gastos"] + keys
        if "No Verificado" in d:
            keys.append("No Verificado")
        return {k: d[k] for k in keys}

    user_monthly_averages = sort_categories(user_monthly_averages)
    group_averages = sort_categories(group_averages)
    all_averages = sort_categories(all_averages)
    similar_differences = sort_categories(similar_differences)
    all_differences = sort_categories(all_differences)
    ordered_categories = list(user_monthly_averages.keys())

    # Extract month names used in user's average
    recent_months = []

    # Use bar_data keys to extract month names
    from calendar import month_name
    if user.user_id in similar_users_ids:
        user_tx_months = (
            session.query(func.date_trunc('month', Transaction.date))
            .filter(Transaction.user_id == user.user_id)
            .group_by(func.date_trunc('month', Transaction.date))
            .order_by(func.date_trunc('month', Transaction.date).desc())
            .limit(3)
            .all()
        )
        spanish_months = [
            "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
            "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"
        ]
        recent_months = [spanish_months[m.date().month - 1] for (m,) in reversed(user_tx_months)]

            

    # Pass data to the template
    return render_template(
        'benchmark.html',
        user=user,
        user_spending=user_monthly_averages,
        group_averages=group_averages,
        all_users_averages=all_averages,
        age_range=f"{user_decade_start}-{user_decade_end}",
        similar_differences=similar_differences,
        all_differences=all_differences,
        categories=ordered_categories,
        recent_months=recent_months

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

    # (C) Updated logic: compare latest month to average of prior 3 months, include 0s for empty months
    from collections import OrderedDict
    from dateutil.relativedelta import relativedelta
    import datetime as dt

    # Step 1: Build last 4 calendar months as keys like '2025-01'
    months_keys = []
    current = dt.datetime(current_year, current_month, 1)
    for i in range(4):
        dt_month = current - relativedelta(months=3 - i)
        months_keys.append(dt_month.strftime('%Y-%m'))


    # Step 2: Get actual spending per year/month
    monthly_totals_raw = session.query(
        extract('year', Transaction.date).label('year'),
        extract('month', Transaction.date).label('month'),
        func.sum(Transaction.amount).label('total')
    ).filter(Transaction.user_id == user_id)\
    .group_by('year', 'month')\
    .all()

    # Convert to dict: '2025-01' -> 123456.0
    monthly_map = {
        f"{int(y)}-{int(m):02d}": float(total)
        for y, m, total in monthly_totals_raw
    }

    # Step 3: Fill in any missing months with 0
    monthly_with_zeros = OrderedDict()
    for key in months_keys:
        monthly_with_zeros[key] = monthly_map.get(key, 0)

    # Step 4: Use last month vs average of previous 3
    values = list(monthly_with_zeros.values())
    if len(values) == 4:
        recent = values[-1]
        prev_3 = values[:3]
        avg_monthly_spending = sum(prev_3) / 3
    else:
        avg_monthly_spending = 0

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
        user=user,
        total_budget=total_budget,
        this_month_spending=this_month_spending,
        avg_monthly_spending=avg_monthly_spending
    )

@app.route('/insights')
def insights():
    # Fetch user details
    user_email = flask_session['email']
    user = session.query(UserCharacteristic).filter_by(email=user_email).first()
    if not user:
        return redirect(url_for('questionnaire'))
    user_id = user.user_id
    user_income = user.income

    # --- Step 1: Get last full month ---
    today = date.today()
    first_day_this_month = date(today.year, today.month, 1)
    last_month = first_day_this_month - timedelta(days=1)
    #target_month = last_month.month
    #target_year = last_month.year
    target_month = 1
    target_year = 2025
    spanish_months = [
        "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
        "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"
    ]


    # --- Step 2: Check if insight already exists ---
    existing = session.query(Insight).filter_by(
        user_id=user_id,
        month=target_month,
        year=target_year
    ).order_by(Insight.version.desc()).first()

    if existing:
        all_insights = session.query(Insight).filter_by(user_id=user_id).order_by(Insight.year.desc(), Insight.month.desc(), Insight.version.desc()).all()
        return render_template(
            "insights.html",
            insight=existing.content,
            current_month_name=last_month.strftime('%B'),
            current_year=target_year,
            all_insights=all_insights,
            insight_generated_this_month=True,
            spanish_months=spanish_months
        )

    # --- Step 3: Get verified transactions for last month ---
    transactions = session.query(Transaction).filter(
        Transaction.user_id == user_id,
        func.extract('month', Transaction.date) == target_month,
        func.extract('year', Transaction.date) == target_year,
        Transaction.category != "No Verificado"
    ).all()

    if not transactions:
        all_insights = session.query(Insight).filter_by(user_id=user_id).order_by(
            Insight.year.desc(), Insight.month.desc(), Insight.version.desc()
        ).all()
        return render_template(
            "insights.html",
            all_insights=all_insights,
            current_month_name=last_month.strftime('%B'),
            current_year=target_year,
            insight_generated_this_month=False,
            spanish_months=spanish_months
        )

    # --- Step 3.5: Abort if any unverified transactions exist ---
    unverified_count = session.query(Transaction).filter(
        Transaction.user_id == user_id,
        func.extract('month', Transaction.date) == target_month,
        func.extract('year', Transaction.date) == target_year,
        Transaction.category == "No Verificado"
    ).count()

    print(f"[DEBUG] Unverified transactions for target month: {unverified_count}")

    if unverified_count > 0:
        print("[DEBUG] Blocking insight generation due to unverified transactions.")
        all_insights = session.query(Insight).filter_by(user_id=user_id).order_by(
            Insight.year.desc(), Insight.month.desc(), Insight.version.desc()
        ).all()
        return render_template(
            "insights.html",
            all_insights=all_insights,
            current_month_name=last_month.strftime('%B'),
            current_year=target_year,
            insight_generated_this_month=False,
            spanish_months=spanish_months

        )


    # --- Step 4: Summarize data ---
    total_spent = sum(t.amount for t in transactions)
    spent_pct_income = (total_spent / user_income * 100) if user_income else None
    num_tx = len(transactions)
    unique_merchants = len(set(t.merchant_fixed for t in transactions))
    largest_tx = max(t.amount for t in transactions)

    from decimal import Decimal
    category_spending = defaultdict(lambda: Decimal('0.0'))
    for t in transactions:
        category_spending[t.category] += t.amount
    top_cat, top_cat_amt = max(category_spending.items(), key=lambda x: x[1])
    top_cat_pct = (top_cat_amt / total_spent) * 100
    estimated_savings = int(Decimal("0.2") * top_cat_amt)

    merchant_counter = Counter(t.merchant_fixed for t in transactions)
    top_merchant, merchant_count = merchant_counter.most_common(1)[0]
    # Estimate if top merchant is a small-ticket one (e.g. coffee)
    merchant_amounts = defaultdict(list)
    for t in transactions:
        merchant_amounts[t.merchant_fixed].append(t.amount)

    # Get average ticket size for most frequent merchant
    avg_ticket_top_merchant = sum(merchant_amounts[top_merchant]) / len(merchant_amounts[top_merchant])

    # If it's low (e.g. < $8,000), we can reference it in creative tip
    is_low_ticket = avg_ticket_top_merchant < 8000
    monthly_savings_if_skipped_2 = int(avg_ticket_top_merchant * 2)

        # --- Extra Insight: Top Merchants and Categories ---

    # Top 5 merchants by number of transactions
    merchant_tx_count = Counter(t.merchant_fixed for t in transactions)
    merchant_amounts = defaultdict(list)

    for t in transactions:
        merchant_amounts[t.merchant_fixed].append(t.amount)

    top_merchants_data = []
    for merchant, count in merchant_tx_count.most_common(5):
        amounts = merchant_amounts[merchant]
        avg_ticket = sum(amounts) / len(amounts) if amounts else 0
        top_merchants_data.append({
            "merchant": merchant,
            "transactions": count,
            "avg_ticket": float(avg_ticket)
        })

    # Format string for prompt
    top_merchants_str = "\n".join(
        f"- {m['merchant']}: {m['transactions']} transacciones, ticket promedio ${m['avg_ticket']:,.0f}"
        for m in top_merchants_data
    )

    # Top 3 categories by number of transactions
    category_tx_count = Counter(t.category for t in transactions)
    category_amounts = defaultdict(list)

    for t in transactions:
        category_amounts[t.category].append(t.amount)

    top_categories_data = []
    for category, count in category_tx_count.most_common(3):
        amounts = category_amounts[category]
        avg_ticket = sum(amounts) / len(amounts) if amounts else 0
        top_categories_data.append({
            "category": category,
            "transactions": count,
            "avg_ticket": float(avg_ticket)
        })

    top_categories_str = "\n".join(
        f"- {c['category']}: {c['transactions']} transacciones, ticket promedio ${c['avg_ticket']:,.0f}"
        for c in top_categories_data
    )


    # --- Step 6: Peer Benchmark (Age-based) ---
    age = (datetime.now().year - user.dob.year) if user.dob else None
    decade_start = (age // 10) * 10
    decade_end = decade_start + 9

    similar_user_ids = session.query(UserCharacteristic.user_id).filter(
        between((datetime.now().year - func.date_part('year', UserCharacteristic.dob)).cast(Integer), decade_start, decade_end)
    ).all()
    similar_user_ids = [u[0] for u in similar_user_ids]

    def calculate_monthly_averages(user_ids):
        from collections import defaultdict

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

        user_months = defaultdict(lambda: defaultdict(list))
        for user_id, category, month, monthly_total in monthly_totals:
            user_months[user_id][category].append((month, monthly_total))

        category_averages = defaultdict(list)

        for user_id, categories in user_months.items():
            total_user_avg = 0
            for category, month_data in categories.items():
                sorted_data = sorted(month_data, key=lambda x: x[0], reverse=True)
                last_3 = sorted_data[:3]
                if last_3:
                    avg = sum(v for _, v in last_3) / len(last_3)
                    category_averages[category].append(avg)
                    total_user_avg += avg
            category_averages["Total Gastos"].append(total_user_avg)

        group_averages = {
            category: sum(avgs) / len(avgs) if avgs else 0
            for category, avgs in category_averages.items()
        }

        return group_averages, category_averages

    peer_averages, _ = calculate_monthly_averages(similar_user_ids)
    peer_avg_spent = peer_averages.get("Total Gastos", 0)

    # --- Step 7: Prompt ---

    month_str = spanish_months[target_month - 1]
    category_breakdown_str = "\n".join(f"  - {cat}: ${amt:,.0f}" for cat, amt in category_spending.items())

    prompt = f"""
    Eres un asesor financiero cálido, chileno y cercano. Cada mes acompañas al usuario con un resumen claro y simple de sus finanzas.

    Este mes estás analizando sus gastos de {month_str}. Usa un tono directo, amable y sin tecnicismos, como si hablaras con un conocido. Escribe **solo en formato de viñetas** (bullet points), sin saludos ni despedidas largas.

    Incluye ideas útiles basadas en los datos. Si un dato no aporta una recomendación concreta, puedes omitirlo.

    - 📊 Compara su gasto total con el promedio de su grupo etario y explica qué significa.
    - 💳 Comenta si hizo muchas compras chicas o pocas grandes, pero solo si puedes dar un consejo útil relacionado.
    - 💰 Si su gasto representa un porcentaje alto de su ingreso, analiza si es preocupante según su edad o contexto. Si no tienes suficiente contexto, puedes omitirlo.
    - 💡 Da una recomendación estándar: analiza [desglose por categoria, gastos por categoría o comercio], y si consideras que alguna esta particularmente alta o costosa, di cuánto representa del total, y sugiere una reducción. Puedes proponer reducir entre un 10% y 30% u otro monto razonable, o sugerir otra estrategia. Usa tu criterio para elegir la categoría o comercio con insight más relevante.
    - 💡 Si hay un patron de gasto repetitivo, da una idea creativa realista: reducir la frecuencia, buscar alternativas o invertir en algo útil. Estima cuánto podría ahorrar al mes, y si aplica, cuánto demoraría en recuperar la inversión.
    - 🤪 Cierra con una **idea creativa y poco convencional**, que lo motive a mejorar sin que suene a consejo repetido. Aquí pueden ser ideas del estilo (pero no exactamente..):  planificar un "dia sin gastar",  invertir en una cafetera, hacer competencia de cocina con amigos,hacer trueque con amigos, usar apps de descuentos, establecer desafíos personales de ahorro, etc. Sé atrevido y no te apegues solo a estos ejemplos. Usa un emoji como 🤪 o 🧠💥. 
    - 🙋‍♂️ Termina con una frase breve y profesional como: “Nos vemos el próximo mes para seguir avanzando.”

    Datos del usuario:
    - Total gastado: ${total_spent:,.0f}
    - Ingreso mensual: ${user_income:,.0f}{" (estimado)" if user_income else ""}
    - Transacciones totales: {num_tx}
    - Gasto mayor: ${largest_tx:,.0f}
    - Comercios únicos: {unique_merchants}
    - Comercio más frecuente: {top_merchant} ({merchant_count} compras)
    - Promedio gasto grupo etario ({decade_start}-{decade_end}): ${peer_avg_spent:,.0f}

    Desglose por categoría:
    {category_breakdown_str}

    Top comercios frecuentes del mes:
    {top_merchants_str}

    Categorías con más movimiento:
    {top_categories_str}

    Comercio más repetido: {top_merchant}
    Ticket promedio en ese comercio: ${avg_ticket_top_merchant:,.0f}
    ¿Ticket pequeño? {"Sí" if is_low_ticket else "No"}
    Ahorro mensual si evita 2 compras: ${monthly_savings_if_skipped_2:,.0f} (Asegúrate que si recomiendas evitar compras..sea por que hubo más transacciones en este comrercio al mes que lo que propones ahorrar)


    Redacta solo texto plano, breve, y con viñetas.
    """




    print("[DEBUG] Prompt\n", prompt)

    # --- Step 8: LLM ---
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": "Eres un experto en bienestar financiero que entrega consejos concretos y cercanos."},
            {"role": "user", "content": prompt}
        ],
        max_tokens=400,
        temperature=0.8
    )
    content = response.choices[0].message.content

    # --- Step 9: Save ---
    insight = Insight(
        user_id=user_id,
        year=target_year,
        month=target_month,
        version=1,
        content=content,
        tokens_used=response.usage.total_tokens if hasattr(response, 'usage') else None
    )
    session.add(insight)
    session.commit()

    insight_generated_this_month = True  # Because we just generated one
    all_insights = session.query(Insight).filter_by(user_id=user_id).order_by(
        Insight.year.desc(), Insight.month.desc(), Insight.version.desc()
    ).all()

    return render_template(
        "insights.html",
        all_insights=all_insights,
        current_month_name=month_str,
        current_year=target_year,
        insight_generated_this_month=insight_generated_this_month,
        spanish_months=spanish_months

    )

@app.route('/feedback', methods=['GET', 'POST'])
def feedback():
    user_email = flask_session.get("email")
    user = session.query(UserCharacteristic).filter_by(email=user_email).first()

    if not user:
        return redirect(url_for('questionnaire'))

    if request.method == 'POST':
        comment = request.form.get('comment')
        if comment:
            new_feedback = Feedback(user_id=user.user_id, comment=comment)
            session.add(new_feedback)
            session.commit()
            return render_template('feedback.html', user=user, submitted=True)

    return render_template('feedback.html', user=user, submitted=False)



if __name__ == '__main__':
    print("Starting Flask server...")
    print("Available Routes:")
    print("  /             -> Home")
    print("  /transactions -> Transactions")
    print("  /visualization -> Spending Visualization")
    app.run(debug=True, port=5000)
