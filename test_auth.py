import os
import pickle
import re
import pandas as pd
import subprocess  # Add this for starting app.py
from rapidfuzz import process
from sqlalchemy.exc import IntegrityError
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from database_setup import session, Transaction, Merchant, UserCharacteristic, LeanMerchant

SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']

# Load the merchants and their categories from the TXT file
def load_merchant_categories(file_path):
    data = pd.read_csv(file_path, delimiter='\t', encoding='utf-8')
    merchant_dict = dict(zip(data['Razón social'], data['Category']))
    return merchant_dict

def extract_transaction_details_banco_de_chile(email_body):
    """
    Extract transaction details from the email body.
    Focuses only on the portion of the email after the phrase 'compra por'.
    """
    # Find the starting index of the relevant portion
    start_index = email_body.find('compra por')

    # If 'compra por' is not found, return None (irrelevant email)
    if start_index == -1:
        return None

    # Extract the portion of the email starting from 'compra por'
    relevant_body = email_body[start_index:]

    # Regular expression patterns to find cost, merchant, and date
    cost_pattern = r"compra por (\$\d+\.?\d*)"
    merchant_pattern = r"en (.*?) el"
    date_pattern = r"el (\d{2}/\d{2}/\d{4} \d{2}:\d{2})"

    cost = re.search(cost_pattern, relevant_body)
    merchant = re.search(merchant_pattern, relevant_body)
    date = re.search(date_pattern, relevant_body)

    # If any of the required fields are missing, return None
    if not cost or not merchant or not date:
        return None

    # Handle amount formatting (e.g., "33.000" -> "33000")
    cost_value = cost.group(1).replace('.', '').replace('$', '')

    # Return extracted details
    return {
        'Cost': float(cost_value),  # Convert cleaned value to float
        'Merchant': merchant.group(1),
        'Date': date.group(1)
    }

def extract_transaction_details_bci(email_body):
    """
    Extract transaction details from BCI emails.
    """
    cost_pattern = r"Monto\s*\$(\d{1,3}(?:\.\d{3})*)"
    merchant_pattern = r"Comercio\s*(.+)"
    date_pattern = r"Fecha\s*(\d{2}/\d{2}/\d{4})"
    time_pattern = r"Hora\s*(\d{2}:\d{2})"
    cost = re.search(cost_pattern, email_body)
    merchant = re.search(merchant_pattern, email_body)
    date = re.search(date_pattern, email_body)
    time = re.search(time_pattern, email_body)
    if not cost or not merchant or not date or not time:
        return None
    cost_value = int(cost.group(1).replace('.', ''))
    date_time = f"{date.group(1)} {time.group(1)}"
    return {
        'Cost': float(cost_value),
        'Merchant': merchant.group(1),
        'Date': date_time
    }


def extract_transaction_details(email_body, sender_domain):
    """
    Dispatch email parsing logic based on sender domain.
    """
    if 'hotmail.com' in sender_domain or 'banco_de_chile' in sender_domain:
        return extract_transaction_details_banco_de_chile(email_body)
    elif 'gmail.com' in sender_domain or 'bci' in sender_domain:
        return extract_transaction_details_bci(email_body)
    else:
        print(f"Unsupported sender domain: {sender_domain}")
        return None


def find_best_match(merchant_name, merchant_dict, threshold=90):
    """
    Use RapidFuzz to find the best match for a given merchant_name.
    If no match exceeds the threshold, return "Other" as the category.
    """
    if merchant_name:
        best_match = process.extractOne(merchant_name, merchant_dict.keys(), score_cutoff=threshold)
        if best_match:
            return best_match[0], merchant_dict[best_match[0]]  # Return matched merchant and category
    return merchant_name, "Otro"  # If no match, assign to "Other"



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

    # Get the user's email address
    profile = service.users().getProfile(userId='me').execute()
    user_email = profile['emailAddress']

    # Check if the user already exists in the `user_characteristic` table
    user = session.query(UserCharacteristic).filter_by(email=user_email).first()
    if not user:
        # Add the user if they don't exist
        user = UserCharacteristic(email=user_email)
        session.add(user)
        session.commit()

    user_id = user.user_id  # Get the user's ID


    results = service.users().messages().list(
        userId='me',
        q='from:(simon_gaucho@hotmail.com OR simongrasss@gmail.com)'
    ).execute()

    messages = results.get('messages', [])

    # Load merchant categories into a dictionary for fallback matching
    merchant_dict = load_merchant_categories('cleaned_merchants_with_categories.txt')

    if not messages:
        print('No messages found.')
    else:
        for message in messages:
            msg = service.users().messages().get(userId='me', id=message['id'], format='full').execute()
            snippet = msg['snippet']
            headers = {header['name']: header['value'] for header in msg['payload']['headers']}
            sender = headers.get('From', '')
            # Extract domain using regex to handle variations in the 'From' format
            sender_domain_match = re.search(r'@([a-zA-Z0-9.-]+)', sender)
            sender_domain = sender_domain_match.group(1) if sender_domain_match else ''
            print(f"Parsed sender domain: {sender_domain}")

            transaction_details = extract_transaction_details(snippet, sender_domain)

            
            # Skip emails that don't have complete transaction details
            if not transaction_details:
                print(f"Email skipped: Missing transaction details. Snippet: {snippet}")
                continue  # Move to the next email

            merchant_name = transaction_details['Merchant']

            # Step 1: Check for an exact match in LeanMerchant
            merchant = session.query(Merchant).filter_by(merchant_name=merchant_name).first()

            if merchant:  # Check if the merchant exists
                merchant_id = merchant.merchant_id
                # Step 2: Query LeanMerchant with merchant_id
                lean_merchant = session.query(LeanMerchant).filter_by(merchant_raw=merchant_id).first()
            else:
                print(f"Merchant name '{merchant_name}' not found in the database.")
                lean_merchant = None


            if lean_merchant:
                # Use the verified LeanMerchant data
                category = lean_merchant.category
                sub_category = lean_merchant.sub_category
                merchant_fixed = lean_merchant.merchant_fixed
                merchant_id = lean_merchant.merchant_raw  # Use the Merchant ID from LeanMerchant
            else:
                # Step 2: Fall back to finding the best match in the Merchant table
                matched_merchant, merchant_category = find_best_match(merchant_name, merchant_dict, threshold=90)

                # Add a new merchant to the Merchant table if it doesn't exist
                merchant = session.query(Merchant).filter_by(merchant_name=merchant_name).first()
                if not merchant:
                    merchant = Merchant(merchant_name=merchant_name, category=merchant_category)
                    session.add(merchant)
                    session.commit()

                # Do NOT add a new LeanMerchant here.
                category = merchant_category
                sub_category = None
                merchant_fixed = None
                merchant_id = merchant.merchant_id


            try:
                # Check if the transaction already exists in the database
                existing_transaction = session.query(Transaction).filter_by(
                    user_id=user_id,
                    merchant_id=merchant_id,
                    amount=transaction_details['Cost'],
                    date=transaction_details['Date']
                ).first()

                if existing_transaction:
                    print(f"Transaction already exists: {transaction_details}")
                    continue  # Skip this transaction

                # Create a new transaction object
                transaction = Transaction(
                    user_id=user_id,
                    merchant_id=merchant_id,
                    lean_merchant_id=lean_merchant.id if lean_merchant else None,  # Handle None case
                    amount=transaction_details['Cost'],
                    date=transaction_details['Date'],
                    category=category,
                    sub_category=sub_category,
                    merchant_fixed=merchant_fixed
                )
                session.add(transaction)
                session.commit()  # Commit the transaction
            except IntegrityError:
                session.rollback()  # Skip duplicate transaction and continue
                print(f"Skipped duplicate transaction: {transaction_details}")

    # Start app.py in the same virtual environment
    python_executable = os.path.join(os.getcwd(), "venv", "Scripts", "python.exe")
    subprocess.Popen([python_executable, "app.py"])

    # Inform the user to open the Flask app
    print("Setup complete! Open http://127.0.0.1:5000/ in your browser.")

if __name__ == '__main__':
    main()
