from flask import session as flask_session, url_for
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow, Flow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from database_setup import session, OAuthToken
from google.auth.exceptions import RefreshError
import os
from cryptography.fernet import Fernet
from dotenv import load_dotenv

load_dotenv()
fernet = Fernet(os.getenv("ENCRYPTION_KEY"))


SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']

def create_oauth_flow():
    return Flow.from_client_config(
        {
            "web": {
                "client_id": os.getenv("GOOGLE_CLIENT_ID"),
                "client_secret": os.getenv("GOOGLE_CLIENT_SECRET"),
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [url_for('oauth2callback', _external=True)]
            }
        },
        scopes=SCOPES,
        redirect_uri=url_for('oauth2callback', _external=True)
    )



def get_credentials(user_email):
    email = flask_session.get("email")
    if not email:
        raise Exception("No user email in session. Cannot retrieve credentials.")

    token_record = session.query(OAuthToken).filter_by(email=email).first()
    creds = None

    if token_record and all([
        token_record.token,
        token_record.refresh_token,
        token_record.token_uri,
        token_record.client_id,
        token_record.client_secret
    ]):
        creds = Credentials(
            token=fernet.decrypt(token_record.token.encode()).decode(),
            refresh_token=fernet.decrypt(token_record.refresh_token.encode()).decode() if token_record.refresh_token else None,
            token_uri=token_record.token_uri,
            client_id=token_record.client_id,
            client_secret=token_record.client_secret,
            scopes=token_record.scopes.split(',') if token_record.scopes else SCOPES
        )


    # If creds don't exist or can't be refreshed, force re-auth
    if not creds or not creds.valid:
        try:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                raise RefreshError("Missing or invalid credentials — re-auth required.")
        except Exception as e:
            print("🔁 Re-authenticating due to credential error:", str(e))
            raise RefreshError("Missing or invalid credentials — re-auth required.")


    return creds
