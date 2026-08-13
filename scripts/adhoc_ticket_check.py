#!/usr/bin/env python3
"""Eenmalig diagnose-scriptje: print het meest recent GEWIJZIGDE ticket.
Gebruikt dezelfde Zoho self-client credentials als fetch_zoho_tickets.py.
Wordt na gebruik weer verwijderd (geen onderdeel van de vaste pipeline)."""
import os
import json
import urllib.request
import urllib.parse

CLIENT_ID = os.environ['ZOHO_CLIENT_ID']
CLIENT_SECRET = os.environ['ZOHO_CLIENT_SECRET']
REFRESH_TOKEN = os.environ['ZOHO_REFRESH_TOKEN']
ORG_ID = os.environ['ZOHO_ORG_ID']

DESK_BASE = 'https://desk.zoho.eu/api/v1'
ACCOUNTS_URL = 'https://accounts.zoho.eu/oauth/v2/token'


def get_access_token():
    data = urllib.parse.urlencode({
        'grant_type': 'refresh_token',
        'client_id': CLIENT_ID,
        'client_secret': CLIENT_SECRET,
        'refresh_token': REFRESH_TOKEN,
    }).encode()
    req = urllib.request.Request(ACCOUNTS_URL, data=data, method='POST')
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())['access_token']


def fetch(access_token, sort_by):
    url = f"{DESK_BASE}/tickets?" + urllib.parse.urlencode({
        'limit': 5,
        'sortBy': sort_by,
        'include': 'contacts',
    })
    req = urllib.request.Request(url, headers={
        'Authorization': f'Zoho-oauthtoken {access_token}',
        'orgId': ORG_ID,
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read()).get('data', [])


def main():
    token = get_access_token()
    for sort_by in ('-modifiedTime', '-modifiedDate'):
        try:
            tickets = fetch(token, sort_by)
            print(f"OK met sortBy={sort_by}")
            for t in tickets:
                contact = t.get('contact') or {}
                klant = (contact.get('firstName', '') + ' ' + contact.get('lastName', '')).strip() or t.get('email', 'onbekend')
                print(json.dumps({
                    'ticketNumber': t.get('ticketNumber'),
                    'subject': t.get('subject'),
                    'klant': klant,
                    'status': t.get('status'),
                    'modifiedTime': t.get('modifiedTime'),
                    'createdTime': t.get('createdTime'),
                    'webUrl': t.get('webUrl'),
                }, ensure_ascii=False))
            return
        except Exception as e:
            print(f"FOUT met sortBy={sort_by}: {e}")
    print("Geen van de sortBy-varianten werkte.")


if __name__ == '__main__':
    main()
