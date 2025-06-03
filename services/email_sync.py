# services/email_sync.py

import re
from googleapiclient.discovery import build
from sqlalchemy.exc import IntegrityError
from database_setup import session, Transaction, Merchant, LeanMerchant, UserCharacteristic
from services.auth_helpers import get_credentials
from rapidfuzz import process
import pandas as pd
from flask import session as flask_session

# Dictionary to hold sync progress per user
sync_progress = {}


def load_merchant_categories(file_path):
    data = pd.read_csv(file_path, delimiter='\t', encoding='utf-8')
    return dict(zip(data['Razón social'], data['Category']))


def extract_transaction_details_banco_de_chile(email_body):
    start_index = email_body.find('compra por')
    if start_index == -1:
        return None
    relevant_body = email_body[start_index:]
    cost = re.search(r"compra por (\$\d+\.?\d*)", relevant_body)
    merchant = re.search(r"en (.*?) el", relevant_body)
    date = re.search(r"el (\d{2}/\d{2}/\d{4} \d{2}:\d{2})", relevant_body)
    if not cost or not merchant or not date:
        return None
    return {
        'Cost': float(cost.group(1).replace('.', '').replace('$', '')),
        'Merchant': merchant.group(1),
        'Date': date.group(1)
    }


def extract_transaction_details_bci(email_body):
    cost = re.search(r"Monto\s*\$(\d{1,3}(?:\.\d{3})*)", email_body)
    merchant = re.search(r"Comercio\s*(.+)", email_body)
    date = re.search(r"Fecha\s*(\d{2}/\d{2}/\d{4})", email_body)
    time = re.search(r"Hora\s*(\d{2}:\d{2})", email_body)
    if not cost or not merchant or not date or not time:
        return None
    return {
        'Cost': float(cost.group(1).replace('.', '')),
        'Merchant': merchant.group(1),
        'Date': f"{date.group(1)} {time.group(1)}"
    }


def extract_transaction_details(body, sender_domain):
    if 'banco_de_chile' in sender_domain or 'hotmail.com' in sender_domain:
        return extract_transaction_details_banco_de_chile(body)
    elif 'bci' in sender_domain or 'gmail.com' in sender_domain:
        return extract_transaction_details_bci(body)
    return None



def sync_user_transactions(user_email, full_sync=False):
    synced_count = 0
    user_email = flask_session.get("email")  # We’ll use this to track per-user progress

    print(f"Starting Gmail sync for: {user_email}")
    try:
        creds = get_credentials()
        service = build('gmail', 'v1', credentials=creds)
        user = session.query(UserCharacteristic).filter_by(email=user_email).first()
        if not user:
            print("User not found. Aborting sync.")
            return

        query = 'from:(simon_gaucho@hotmail.com OR simongrasss@gmail.com)'  # 🔁 Update to match all banks/emails you want
        if not full_sync and user.last_synced:
            after_date = user.last_synced.strftime('%Y/%m/%d')  # Gmail expects YYYY/MM/DD format
            query += f' after:{after_date}'
            print(f"🔍 Syncing only emails after {after_date}")
        else:
            print("📦 Doing full sync of all emails")


        messages = []
        next_page_token = None

        while True:
            response = service.users().messages().list(
                userId='me',
                q=query,
                pageToken=next_page_token,
                maxResults=500  # 500 is the Gmail API max per page
            ).execute()

            batch = response.get('messages', [])
            messages.extend(batch)

            print(f"Loaded {len(messages)} messages so far...")

            next_page_token = response.get('nextPageToken')
            if not next_page_token:
                break


        for message in messages:
            msg = service.users().messages().get(userId='me', id=message['id'], format='full').execute()
            snippet = msg['snippet']
            headers = {h['name']: h['value'] for h in msg['payload']['headers']}
            sender_match = re.search(r'@([a-zA-Z0-9.-]+)', headers.get('From', ''))
            sender_domain = sender_match.group(1) if sender_match else ''
            tx = extract_transaction_details(snippet, sender_domain)

            if not tx:
                print(f"Skipping message: {snippet}")
                continue

            merchant = session.query(Merchant).filter_by(merchant_name=tx['Merchant']).first()
            lean = session.query(LeanMerchant).filter_by(merchant_raw=merchant.merchant_id).first() if merchant else None

            if not merchant:
                merchant = Merchant(
                    merchant_name=tx['Merchant'],
                    category="No Verificado",
                    sub_category="No Verificado"
                )
                session.add(merchant)
                session.commit()

            try:
                exists = session.query(Transaction).filter_by(
                    user_id=user.user_id,
                    merchant_id=merchant.merchant_id,
                    amount=tx['Cost'],
                    date=tx['Date']
                ).first()

                if exists:
                    continue

                transaction = Transaction(
                    user_id=user.user_id,
                    merchant_id=merchant.merchant_id,
                    lean_merchant_id=lean.id if lean else None,
                    amount=tx['Cost'],
                    date=tx['Date'],
                    category=lean.category if lean else merchant.category,
                    sub_category=lean.sub_category if lean else None,
                    merchant_fixed=lean.merchant_fixed if lean else None
                )
                session.add(transaction)
                session.commit()
                synced_count += 1
                if user_email:
                    sync_progress[user_email] = synced_count

            except IntegrityError:
                session.rollback()
                print(f"Skipped duplicate: {tx}")

        print("✅ Gmail sync complete.")
        sync_progress.pop(user_email, None)
        # Update last_synced
        if user:
            from datetime import datetime
            user.last_synced = datetime.utcnow()
            session.commit()
        return synced_count


    finally:
        session.rollback()  # <-- 🔧 This is the key addition
