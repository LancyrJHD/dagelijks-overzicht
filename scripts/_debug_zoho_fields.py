#!/usr/bin/env python3
"""EENMALIG diagnose-script v3: v1/v2 lieten zien dat de tickets-LIJST-endpoint
geen customFields teruggeeft (ook niet via fields= of include=). Probeer nu
het losse ticket-detail endpoint (/tickets/{id}) met include=customFields,
wat volgens de Zoho Desk docs de plek is waar customFields wel verschijnen."""
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
        print(f"  FOUT {e.code}: {e.read().decode(errors='replace')[:500]}")
        return None


def main():
    access_token = get_access_token()

    # Recente tickets ophalen om een ticket-ID te pakken
    list_url = f"{DESK_BASE}/tickets?" + urllib.parse.urlencode({
        'limit': 5, 'sortBy': '-createdTime',
    })
    tickets = (get(access_token, list_url) or {}).get('data', [])

    for t in tickets:
        tid = t.get('id')
        print(f"\n=== Ticket-detail voor {t.get('ticketNumber')} | {t.get('subject')} (id={tid}) ===")
        detail_url = f"{DESK_BASE}/tickets/{tid}?" + urllib.parse.urlencode({
            'include': 'customFields',
        })
        detail = get(access_token, detail_url)
        if detail:
            print("Top-level keys:", sorted(detail.keys()))
            print("customFields:", json.dumps(detail.get('customFields'), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
