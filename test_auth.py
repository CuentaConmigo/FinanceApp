import os
import pickle
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']

def main():
    creds = None
    if os.path.exists('token.pickle'):
        with open('token.pickle', 'rb') as token:
            creds = pickle.load(token)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                'credentials.json', SCOPES)
            creds = flow.run_local_server(port=5000)
        with open('token.pickle', 'wb') as token:
            pickle.dump(creds, token)

    service = build('gmail', 'v1', credentials=creds)

    # Fetch messages
    results = service.users().messages().list(userId='me', q='from:simon_gaucho@hotmail.com').execute()
    messages = results.get('messages', [])

    if not messages:
        print('No messages found.')
    else:
        for message in messages[:1]:  # Just the first message for simplicity
            msg = service.users().messages().get(userId='me', id=message['id']).execute()
            print(f"Subject: {msg['snippet']}")  # Prints the snippet for simplicity

if __name__ == '__main__':
    main()
