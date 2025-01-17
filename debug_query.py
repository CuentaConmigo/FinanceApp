from database_setup import session, Transaction, Merchant

def debug_query():
    transactions = (
        session.query(Transaction, Merchant)
        .join(Merchant, Transaction.merchant_id == Merchant.merchant_id)
        .filter(Transaction.user_id == 1)
        .all()
    )
    print(f"Fetched transactions count: {len(transactions)}")
    for transaction, merchant in transactions:
        print(f"Transaction: {transaction}, Merchant: {merchant}")

if __name__ == "__main__":
    debug_query()
