#!/usr/bin/env python3
"""
Generate owner token.pickle for single-user mode (like WZML-X).
Run: python gen_scripts/generate_drive_token.py
Requires: client_secrets.json in root
"""
import os
import pickle
from google_auth_oauthlib.flow import InstalledAppFlow
from config import Config

def main():
    if not os.path.exists(Config.CLIENT_SECRETS_FILE):
        print(f"❌ {Config.CLIENT_SECRETS_FILE} not found!")
        print("Download it from Google Cloud Console → APIs & Services → Credentials → OAuth Client (Desktop)")
        return
    flow = InstalledAppFlow.from_client_secrets_file(Config.CLIENT_SECRETS_FILE, scopes=Config.OAUTH_SCOPES)
    creds = flow.run_local_server(port=0)
    with open("token.pickle", "wb") as f:
        pickle.dump(creds, f)
    print("✅ token.pickle generated! Bot can now upload as owner without per-user OAuth.")

if __name__ == "__main__":
    main()
