import os
import pickle
import re
import pandas as pd
from rapidfuzz import process
from sqlalchemy.exc import IntegrityError
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from database_setup import session, Transaction, Merchant

SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']

# Load the merchants and their categories from the TXT file
def load_merchant_categories(file_path):
    data = pd.read_csv(file_path, delimiter='\t', encoding='utf-8')
    merchant_dict = dict(zip(data['Razón social'], data['Category']))
    return merchant_dict

def extract_transaction_details(email_body):
    # Regular expression patterns to find cost, merchant, and date
    cost_pattern = r"compra por (\$\d+\.?\d*)"
    merchant_pattern = r"en (.*?) el"
    date_pattern = r"el (\d{2}/\d{2}/\d{4} \d{2}:\d{2})"

    cost = re.search(cost_pattern, email_body)
    merchant = re.search(merchant_pattern, email_body)
    date = re.search(date_pattern, email_body)
    
    # Handle amount formatting (e.g., "33.000" -> "33000")
    cost_value = cost.group(1).replace('.', '').replace('$', '') if cost else '0'
    
    return {
        'Cost': float(cost_value),  # Convert cleaned value to float
        'Merchant': merchant.group(1) if merchant else None,
        'Date': date.group(1) if date else None
    }

def find_best_match(merchant_name, merchant_dict, threshold=90):
    """
    Use RapidFuzz to find the best match for a given merchant_name.
    If no match exceeds the threshold, return "Other" as the category.
    """
    if merchant_name:
        best_match = process.extractOne(merchant_name, merchant_dict.keys(), score_cutoff=threshold)
        if best_match:
            return best_match[0], merchant_dict[best_match[0]]  # Return matched merchant and category
    return merchant_name, "Other"  # If no match, assign to "Other"

def main():
    creds = None
    if os.path.exists('token.pickle'):
        with open('token.pickle', 'rb') as token:
            creds = pickle.load(token)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
            creds = flow.run_local_server(port=5000)
        with open('token.pickle', 'wb') as token:
            pickle.dump(creds, token)

    service = build('gmail', 'v1', credentials=creds)
    results = service.users().messages().list(userId='me', q='from:simon_gaucho@hotmail.com').execute()
    messages = results.get('messages', [])

    # Load merchant categories into a dictionary for fast lookup
    merchant_dict = load_merchant_categories('merchants_with_categories.txt')

    if not messages:
        print('No messages found.')
    else:
        for message in messages:
            msg = service.users().messages().get(userId='me', id=message['id'], format='full').execute()
            snippet = msg['snippet']
            transaction_details = extract_transaction_details(snippet)

            # Find the merchant and its category using RapidFuzz
            merchant_name = transaction_details['Merchant']
            matched_merchant, merchant_category = find_best_match(merchant_name, merchant_dict, threshold=90)

            # Use the email merchant name but add the matched category
            merchant = session.query(Merchant).filter_by(merchant_name=merchant_name).first()
            if not merchant:
                merchant = Merchant(merchant_name=merchant_name, category=merchant_category)
                session.add(merchant)
                session.commit()  # Create new merchant if not found

            try:
                # Create a new transaction object
                transaction = Transaction(
                    user_id=1,  # Assuming a fixed user_id for now
                    merchant_id=merchant.merchant_id,
                    amount=transaction_details['Cost'],
                    date=transaction_details['Date'],
                    category=merchant_category,  # Fill with the matched category
                    sub_category=''  # Placeholder for sub-category
                )
                session.add(transaction)
                session.commit()  # Commit the transaction
            except IntegrityError:
                session.rollback()  # Skip duplicate transaction and continue
                print(f"Skipped duplicate transaction: {transaction_details}")

if __name__ == '__main__':
    main()
