#!/usr/bin/env python3
"""EENMALIG diagnose-script: zoek ticket #2542 (A. Zonneveld, "FW: Verzoek tot
inzage") op -- bevestigd door Jackie als daadwerkelijk doorgezet naar
Brandmeester -- en dump de volledige threads om het patroon te vinden."""
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
        print(f"  FOUT {e.code}: {e.read().decode(errors='replace')[:600]}")
        return None


def main():
    access_token = get_access_token()

    # Zoek ticket op nummer via de search-endpoint.
    search_url = f"{DESK_BASE}/tickets/search?" + urllib.parse.urlencode({
        'ticketNumber': '2542',
    })
    result = get(access_token, search_url)
    tickets = (result or {}).get('data', [])
    if not tickets:
        print("Niet gevonden via /tickets/search, probeer alternatieve aanpak...")
        return
    ticket_id = tickets[0].get('id')
    print(f"Ticket #2542 gevonden, id={ticket_id}, threadCount={tickets[0].get('threadCount')}")

    threads = get(access_token, f"{DESK_BASE}/tickets/{ticket_id}/threads")
    if threads:
        for th in threads.get('data', []):
            print(f"\n--- Thread {th.get('id')} | direction={th.get('direction')} | channel={th.get('channel')} ---")
            print("  fromEmailAddress:", th.get('fromEmailAddress'))
            print("  to:", th.get('to'))
            print("  cc:", th.get('cc'))
            print("  bcc:", th.get('bcc'))
            print("  summary:", th.get('summary'))
            print("  author:", th.get('author'))
            print("  createdTime:", th.get('createdTime'))

            # Volledige content ophalen van deze specifieke thread.
            detail = get(access_token, f"{DESK_BASE}/tickets/{ticket_id}/threads/{th.get('id')}")
            if detail:
                content = detail.get('content') or detail.get('plainText') or ''
                print("  content (eerste 1500 tekens):", content[:1500])


if __name__ == '__main__':
    main()
