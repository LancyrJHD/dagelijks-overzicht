#!/usr/bin/env python3
"""EENMALIG diagnose-script v2: dumpt de volledige ruwe ticketvelden (incl.
customFields) van de meest recente tickets in Zoho Desk. v1 gaf een 422 op
include=customFields -- customFields moet blijkbaar via het fields-park
worden opgevraagd i.p.v. include. Niet onderdeel van de reguliere pipeline."""
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


def try_fetch(access_token, params, label):
    url = f"{DESK_BASE}/tickets?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        'Authorization': f'Zoho-oauthtoken {access_token}',
        'orgId': ORG_ID,
    })
    print(f"\n>>> Poging: {label}")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read()).get('data', [])
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors='replace')
        print(f"  FOUT {e.code}: {body[:500]}")
        return None


def main():
    access_token = get_access_token()

    # Poging A: customFields via 'fields' param
    tickets = try_fetch(access_token, {
        'limit': 5,
        'sortBy': '-createdTime',
        'include': 'contacts',
        'fields': 'ticketNumber,subject,status,priority,createdTime,webUrl,channel,email,customFields',
    }, "fields= ...,customFields")

    if not tickets:
        # Poging B: gewoon alles ophalen zonder fields-restrictie
        tickets = try_fetch(access_token, {
            'limit': 5,
            'sortBy': '-createdTime',
            'include': 'contacts',
        }, "geen fields-restrictie (default response)")

    print("\n=== RESULTAAT ===")
    for t in (tickets or []):
        print(f"\n--- Ticket {t.get('ticketNumber')} | {t.get('subject')} ---")
        print("Top-level keys:", sorted(t.keys()))
        if 'customFields' in t:
            print("customFields:", json.dumps(t.get('customFields'), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
