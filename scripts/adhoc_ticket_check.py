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


def fetch_page(access_token, from_index, limit=100):
    url = f"{DESK_BASE}/tickets?" + urllib.parse.urlencode({
        'limit': limit,
        'from': from_index,
        'sortBy': 'modifiedTime',  # niet vertrouwd, we sorteren zelf client-side
        'include': 'contacts',
        'fields': 'ticketNumber,subject,status,modifiedTime,createdTime,webUrl',
    })
    req = urllib.request.Request(url, headers={
        'Authorization': f'Zoho-oauthtoken {access_token}',
        'orgId': ORG_ID,
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read()).get('data', [])


def main():
    token = get_access_token()
    # Haal ALLE tickets op (niet slechts de eerste N) en sorteer zelf client-side
    # op modifiedTime, om niet afhankelijk te zijn van ongedocumenteerd
    # sorteergedrag van de Zoho-API. Loop tot een lege pagina, met een
    # veiligheidslimiet zodat dit nooit oneindig door kan lopen.
    all_tickets = []
    from_index = 0
    hit_safety_cap = True
    for _ in range(50):  # 50 x 100 = 5000 tickets max
        page = fetch_page(token, from_index)
        if not page:
            hit_safety_cap = False
            break
        all_tickets.extend(page)
        from_index += 100

    print(f"Totaal opgehaald: {len(all_tickets)} tickets "
          f"({'VEILIGHEIDSLIMIET GERAAKT, niet alles opgehaald!' if hit_safety_cap else 'volledig, lege pagina bereikt'})")
    missing_modified = [t for t in all_tickets if not t.get('modifiedTime')]
    print(f"Tickets zonder modifiedTime-veld in response: {len(missing_modified)}")

    with_modified = [t for t in all_tickets if t.get('modifiedTime')]
    if not with_modified:
        print("GEEN ENKEL ticket heeft een modifiedTime-waarde gekregen van de API "
              "(zelfs niet met expliciet fields=modifiedTime). Kan de vraag dus niet "
              "betrouwbaar beantwoorden via deze route.")
        return

    with_modified.sort(key=lambda t: t['modifiedTime'], reverse=True)
    print("Top 5 meest recent gewijzigd (client-side gesorteerd):")
    for t in with_modified[:5]:
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


if __name__ == '__main__':
    main()
