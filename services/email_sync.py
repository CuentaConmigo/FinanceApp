# services/email_sync.py

import re
from googleapiclient.discovery import build
from sqlalchemy.exc import IntegrityError
from database_setup import session, Transaction, Merchant, LeanMerchant, UserCharacteristic
from services.auth_helpers import get_credentials
from rapidfuzz import process
import pandas as pd
from flask import session as flask_session
from datetime import datetime
import psutil, os



# Dictionary to hold sync progress per user
sync_progress = {}

def memory_usage():
    process = psutil.Process(os.getpid())
    return process.memory_info().rss / 1024 / 1024  # MB


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
    raw_date = date.group(1)  # e.g., '06/01/2025 14:05'
    try:
        parsed_date = datetime.strptime(raw_date, "%d/%m/%Y %H:%M")
        formatted_date = parsed_date.strftime("%Y-%m-%d %H:%M")
    except ValueError:
        print(f"❌ Could not parse Banco de Chile date: {raw_date}")
        return None

    return {
        'Cost': float(cost.group(1).replace('.', '').replace('$', '')),
        'Merchant': merchant.group(1),
        'Date': formatted_date
    }



def extract_transaction_details_bci(email_body):
    cost = re.search(r"Monto\s*\$(\d{1,3}(?:\.\d{3})*)", email_body)
    merchant = re.search(r"Comercio\s*(.+)", email_body)
    date = re.search(r"Fecha\s*(\d{2}/\d{2}/\d{4})", email_body)
    time = re.search(r"Hora\s*(\d{2}:\d{2})", email_body)
    if not cost or not merchant or not date or not time:
        return None
    raw_date = f"{date.group(1)} {time.group(1)}"  # e.g., '06/01/2025 14:05'
    try:
        parsed_date = datetime.strptime(raw_date, "%d/%m/%Y %H:%M")
        formatted_date = parsed_date.strftime("%Y-%m-%d %H:%M")
    except ValueError:
        print(f"❌ Could not parse BCI date: {raw_date}")
        return None

    return {
        'Cost': float(cost.group(1).replace('.', '')),
        'Merchant': merchant.group(1),
        'Date': formatted_date
    }



def extract_transaction_details(body, sender_domain):
    if 'bancochile' in sender_domain or 'bancoedwards' in sender_domain or 'hotmail.com' in sender_domain:
        return extract_transaction_details_banco_de_chile(body)
    elif 'bci' in sender_domain or 'contacto' in sender_domain or 'gmail.com' in sender_domain:
        return extract_transaction_details_bci(body)
    return None



def sync_user_transactions(user_email, full_sync=False):
    from googleapiclient.errors import HttpError
    synced_count = 0
    if not user_email:
        user_email = flask_session.get("email")

    print(f"🔄 Starting Gmail sync for: {user_email}")
    print(f"🧠 Initial memory usage: {memory_usage():.2f} MB")

    try:
        creds = get_credentials(user_email)
        service = build('gmail', 'v1', credentials=creds)
        user = session.query(UserCharacteristic).filter_by(email=user_email).first()
        if not user:
            print("❌ User not found. Aborting sync.")
            return

        query = (
            'from:(enviodigital@bancochile.cl OR enviodigital@bancoedwards.cl '
            'OR contacto@bci.cl OR simon_gaucho@hotmail.com OR simongrasss@gmail.com)'
        )
        if not full_sync and user.last_synced:
            after_date = user.last_synced.strftime('%Y/%m/%d')
            query += f' after:{after_date}'
            print(f"🔍 Syncing only emails after {after_date}")
        else:
            print("📦 Doing full sync of all emails")

        next_page_token = None
        max_to_process = 50  # 🔒 Hard limit for testing
        processed_count = 0

        while True:
            response = service.users().messages().list(
                userId='me',
                q=query,
                pageToken=next_page_token,
                maxResults=20  # smaller batch = less memory
            ).execute()

            for message in response.get('messages', []):
                if processed_count >= max_to_process:
                    print("🚧 Reached processing limit for this session.")
                    break

                try:
                    msg = service.users().messages().get(userId='me', id=message['id'], format='full').execute()
                    processed_count += 1
                except HttpError as e:
                    print(f"⚠️ Failed to fetch message {message['id']}: {e}")
                    continue

                snippet = msg['snippet']
                headers = {h['name']: h['value'] for h in msg['payload']['headers']}
                sender_match = re.search(r'@([a-zA-Z0-9.-]+)', headers.get('From', ''))
                sender_domain = sender_match.group(1) if sender_match else ''
                tx = extract_transaction_details(snippet, sender_domain)
                if not tx:
                    print(f"⏭️ Skipping message: {snippet}")
                    continue

                try:
                    tx['Date'] = datetime.strptime(tx['Date'], "%Y-%m-%d %H:%M")
                    print(f"🕒 Parsed date: {tx['Date']}")
                except ValueError:
                    print(f"❌ Invalid date format: {tx['Date']}")
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
                    sync_progress[user_email] = synced_count

                except IntegrityError:
                    session.rollback()
                    print(f"⚠️ Skipped duplicate: {tx}")

            print(f"📨 Page processed. Memory: {memory_usage():.2f} MB")

            next_page_token = response.get('nextPageToken')
            if not next_page_token or processed_count >= max_to_process:
                break

        print(f"✅ Gmail sync complete. Synced {synced_count} transactions.")
        print(f"🧠 Final memory usage: {memory_usage():.2f} MB")
        sync_progress.pop(user_email, None)

        if user:
            user.last_synced = datetime.utcnow()
            session.commit()
        return synced_count

    finally:
        session.rollback()