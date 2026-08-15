#!/usr/bin/env python3
"""EENMALIG diagnose-script v2: /tickets/search gaf SCOPE_MISMATCH (ons
refresh-token heeft niet de juiste OAuth-scope voor die endpoint). Zoek ticket
#2542 daarom via de tickets-LIJST-endpoint (die werkte al eerder voor de
historische dump), gefilterd op 4 augustus 2026 en ticketNumber '2542'. Dump
daarna de threads om het BrandMR-detectiepatroon te vinden."""
import os
import json
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timedelta

CLIENT_ID = os.environ.get('ZOHO_CLIENT_ID', '')
CLIENT_SECRET = os.environ.get('ZOHO_CLIENT_SECRET', '')
REFRESH_TOKEN = os.environ.get('ZOHO_REFRESH_TOKEN', '')
ORG_ID = os.environ.get('ZOHO_ORG_ID', '')

DESK_BASE = 'https://desk.zoho.eu/api/v1'
ACCOUNTS_URL = 'https://accounts.zoho.eu/oauth/v2/token'
AMS_OFFSET = timedelta(hours=2)


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

    # Ticket #2542 valt op 4 augustus 2026 (bekend uit eerdere historische
    # dump). Pagineer de tickets-lijst tot we die datum voorbij zijn.
    target_date = '2026-08-04'
    ticket_id = None
    start = 0
    page_size = 100
    while ticket_id is None:
        url = f"{DESK_BASE}/tickets?" + urllib.parse.urlencode({
            'limit': page_size,
            'from': start,
            'sortBy': '-createdTime',
            'fields': 'ticketNumber,subject,createdTime',
        })
        page = (get(access_token, url) or {}).get('data', [])
        if not page:
            print("Geen paginas meer, ticket niet gevonden.")
            break
        for t in page:
            if str(t.get('ticketNumber')) == '2542':
                ticket_id = t.get('id')
                print(f"Gevonden: #2542, id={ticket_id}, subject={t.get('subject')}, createdTime={t.get('createdTime')}")
                break
        oldest = page[-1].get('createdTime', '')
        print(f"  pagina from={start}: {len(page)} tickets, oudste: {oldest}")
        if oldest and oldest.split('T')[0] < target_date:
            print("  Voorbij doelperiode, stop met zoeken.")
            break
        start += page_size
        if start > 1500:
            print("  STOP: veiligheidslimiet bereikt")
            break

    if not ticket_id:
        print("Ticket #2542 niet gevonden via lijst-endpoint.")
        return

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

            detail = get(access_token, f"{DESK_BASE}/tickets/{ticket_id}/threads/{th.get('id')}")
            if detail:
                content = detail.get('content') or detail.get('plainText') or ''
                print("  content (eerste 1500 tekens):", content[:1500])
    else:
        print("Geen threads-data terugontvangen.")


if __name__ == '__main__':
    main()
