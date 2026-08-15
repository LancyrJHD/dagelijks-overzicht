#!/usr/bin/env python3
"""EENMALIG diagnose-script: het customFields-vinkje "Doorgezet naar BrandMR"
blijkt onbetrouwbaar. Het echte signaal is een uitgaande e-mail naar de
Brandmeester-intake met het vaste template. Test dit op ticket 2594
(W. vanArnhem, 14 aug) -- een geval waarvan Granola bevestigt dat het is
doorverwezen naar de Brandmeester -- om te zien hoe dat er in de
ticket-conversations uitziet."""
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

# Ticket-IDs verzameld in eerdere diagnose-rondes.
TEST_TICKETS = {
    '2594': '195464000013425284',  # W. vanArnhem, 14 aug -- bekend doorverwezen
    '2587': '195464000013425xxx',  # placeholder, wordt hieronder alsnog opgezocht indien nodig
}


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
        print(f"  FOUT {e.code}: {e.read().decode(errors='replace')[:600]}")
        return None


def main():
    access_token = get_access_token()

    ticket_id = '195464000013425284'  # ticket 2594, W. vanArnhem

    # Poging A: threads-endpoint
    print(">>> /tickets/{id}/threads")
    threads = get(access_token, f"{DESK_BASE}/tickets/{ticket_id}/threads")
    if threads:
        print(json.dumps(threads, ensure_ascii=False, indent=2)[:6000])

    print("\n>>> /tickets/{id}/conversations")
    conv = get(access_token, f"{DESK_BASE}/tickets/{ticket_id}/conversations")
    if conv:
        print(json.dumps(conv, ensure_ascii=False, indent=2)[:6000])


if __name__ == '__main__':
    main()
