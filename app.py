from flask import Flask, render_template
from sqlalchemy import func, extract, and_
from database_setup import session, Transaction, Merchant
from datetime import datetime

app = Flask(__name__)

@app.route('/')
def home():
    return """
    <h1>Welcome to Your Finance App</h1>
    <p><a href="/transactions">View Transactions</a></p>
    <p><a href="/visualization">View Spending Visualization</a></p>
    """

@app.route('/transactions')
def show_transactions():
    # Fetch transactions joined with merchant data
    transactions = (
        session.query(Transaction, Merchant)
        .join(Merchant, Transaction.merchant_id == Merchant.merchant_id)
        .filter(Transaction.user_id == 1)  # Replace with dynamic user_id logic later
        .all()
    )
    print(f"Fetched transactions count: {len(transactions)}")
    for transaction, merchant in transactions:
        print(f"Transaction: {transaction}, Merchant: {merchant}")
    return render_template('transactions.html', transactions=transactions)

@app.route('/visualization')
def spending_visualization():
    # Get the most recent month
    current_year = datetime.now().year
    current_month = datetime.now().month

    # Prepare data for the pie chart (last month only)
    category_totals = (
        session.query(Transaction.category, func.sum(Transaction.amount))
        .filter(
            and_(
                Transaction.user_id == 1,
                extract('year', Transaction.date) == current_year,
                extract('month', Transaction.date) == current_month
            )
        )
        .group_by(Transaction.category)
        .all()
    )
    chart_data = {
        'categories': [item[0] or "Other" for item in category_totals],
        'totals': [float(item[1]) for item in category_totals],  # Convert Decimal to float
    }

    # Debug print
    print("Pie Chart Data:", chart_data)

    # Prepare data for the bar chart (all months)
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
        'labels': [f"{int(item[0])}-{int(item[1]):02d}" for item in monthly_totals],  # e.g., "2025-01"
        'totals': [float(item[2]) for item in monthly_totals],  # Convert Decimal to float
    }

    # Debug print
    print("Bar Chart Data:", bar_data)

    return render_template('visualization.html', chartData=chart_data, barData=bar_data)


if __name__ == '__main__':
    print("Starting Flask server...")
    print("Available Routes:")
    print("  /             -> Home")
    print("  /transactions -> Transactions")
    print("  /visualization -> Spending Visualization")
    app.run(debug=True)
