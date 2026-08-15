#!/usr/bin/env python3
"""EENMALIG diagnose-script: dumpt de volledige ruwe ticketvelden (incl.
customFields) van de meest recente tickets in Zoho Desk, zodat we kunnen
zien welk veld/checkbox aangeeft dat een zaak is doorgestuurd naar de
Brandmeester/rechtsbijstand. Niet onderdeel van de reguliere pipeline --
na gebruik weer verwijderen."""
import os
import json
import urllib.request
import urllib.parse

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


def main():
    access_token = get_access_token()

    # 1) Probeer de layout/veld-metadata van tickets op te vragen -- dit geeft
    #    ALLE gedefinieerde velden (incl. custom fields) met hun API-naam en
    #    label, ongeacht of er al tickets mee zijn ingevuld.
    print("=== TICKET LAYOUT / FIELD METADATA ===")
    try:
        url = f"{DESK_BASE}/organizations/{ORG_ID}/fields?module=tickets"
        req = urllib.request.Request(url, headers={
            'Authorization': f'Zoho-oauthtoken {access_token}',
            'orgId': ORG_ID,
        })
        with urllib.request.urlopen(req, timeout=30) as resp:
            fields = json.loads(resp.read())
        for f in fields if isinstance(fields, list) else fields.get('data', []):
            print(json.dumps({
                'apiName': f.get('apiName'),
                'displayLabel': f.get('displayLabel'),
                'dataType': f.get('dataType'),
            }, ensure_ascii=False))
    except Exception as e:
        print(f"(fields-endpoint faalde: {e})")

    # 2) Haal de 10 meest recente tickets op MET customFields, en print de
    #    volledige ruwe JSON zodat we ook de daadwerkelijke waarden zien.
    print("\n=== RUWE TICKETS (incl. customFields) ===")
    url = f"{DESK_BASE}/tickets?" + urllib.parse.urlencode({
        'limit': 10,
        'sortBy': '-createdTime',
        'include': 'contacts,customFields',
    })
    req = urllib.request.Request(url, headers={
        'Authorization': f'Zoho-oauthtoken {access_token}',
        'orgId': ORG_ID,
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        tickets = json.loads(resp.read()).get('data', [])

    for t in tickets:
        print(f"\n--- Ticket {t.get('ticketNumber')} | {t.get('subject')} ---")
        print("Top-level keys:", sorted(t.keys()))
        cf = t.get('customFields') or {}
        print("customFields:", json.dumps(cf, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
