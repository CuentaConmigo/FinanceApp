from flask import Flask, render_template, request, redirect, url_for
from sqlalchemy import func, extract, and_
from database_setup import session, Transaction, Merchant, LeanMerchant
from datetime import datetime


app = Flask(__name__)

@app.route('/')
def home():
    return render_template('home.html')


@app.route('/transactions', methods=['GET', 'POST'])
def show_transactions():
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

        # Normalize the fixed_merchant_name for grouping
        normalized_fixed_name = ''.join(fixed_merchant_name.lower().split())

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

        # Update the `transactions` table for this merchant_id
        transactions_to_update = session.query(Transaction).filter(Transaction.merchant_id == merchant_id).all()
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
        Transaction.merchant_fixed.is_(None)
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
        .filter(Transaction.user_id == 1)  # Replace with dynamic user_id logic later
        .all()
    )

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

    return render_template('transactions.html', transactions=transactions, categories=list(categories.keys()), category_map=categories)


@app.route('/visualization', methods=['GET'])
def spending_visualization():
    # Get the most recent month
    current_year = datetime.now().year
    current_month = datetime.now().month

    # Prepare hierarchical data for sunburst chart
    category_data = (
        session.query(Transaction.category, Transaction.sub_category, func.sum(Transaction.amount))
        .filter(
            Transaction.user_id == 1,
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
        .filter(Transaction.user_id == 1)
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

if __name__ == '__main__':
    print("Starting Flask server...")
    print("Available Routes:")
    print("  /             -> Home")
    print("  /transactions -> Transactions")
    print("  /visualization -> Spending Visualization")
    app.run(debug=True)
