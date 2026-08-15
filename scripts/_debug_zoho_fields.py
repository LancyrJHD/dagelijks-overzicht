#!/usr/bin/env python3
"""EENMALIG diagnose-script v4: 'include=customFields' gaf een 422 (niet
toegestane include-waarde). Haal nu het ticket-detail zonder include-param op
en dump ALLE top-level keys + volledige JSON, om te zien of custom fields
(bijv. cf_* velden) gewoon standaard aanwezig zijn."""
import os
import json
import urllib.request
import urllib.parse
import urllib.error

CLIENT_ID = os.environ.get('ZOHO_CLIENT_ID', '')
CLIENT_SECRET = os.environ.get('ZOHO_CLIENT_SECRET', '')
REFRESH_TOKEN = os.environ.get('ZOHO_REFRESH_TOKEN', '')
ORG_ID = os.environ.get('ZOHO_ORG_ID', '')

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


def get(access_token, url):
    req = urllib.request.Request(url, headers={
        'Authorization': f'Zoho-oauthtoken {access_token}',
        'orgId': ORG_ID,
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        print(f"  FOUT {e.code}: {e.read().decode(errors='replace')[:800]}")
        return None


def main():
    access_token = get_access_token()

    list_url = f"{DESK_BASE}/tickets?" + urllib.parse.urlencode({
        'limit': 3, 'sortBy': '-createdTime',
    })
    tickets = (get(access_token, list_url) or {}).get('data', [])

    for t in tickets:
        tid = t.get('id')
        print(f"\n=== Ticket-detail (zonder include) voor {t.get('ticketNumber')} | {t.get('subject')} ===")
        detail = get(access_token, f"{DESK_BASE}/tickets/{tid}")
        if detail:
            print(json.dumps(detail, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
