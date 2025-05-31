from flask import session as flask_session
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from database_setup import session, OAuthToken
from google.auth.exceptions import RefreshError


SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']

def get_credentials():
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
            token=token_record.token,
            refresh_token=token_record.refresh_token,
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
            flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
            try:
                creds = flow.run_local_server(port=5200)
            except OSError:
                creds = flow.run_local_server(port=5210)

            email_service = build('gmail', 'v1', credentials=creds)
            profile = email_service.users().getProfile(userId='me').execute()
            email = profile['emailAddress']
            flask_session['email'] = email

            # Refresh DB token after login
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

    return creds
