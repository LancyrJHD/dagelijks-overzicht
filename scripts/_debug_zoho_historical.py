#!/usr/bin/env python3
"""EENMALIG diagnose-script: haalt tickets op voor een reeks HISTORISCHE data
(niet alleen "vandaag") door de tickets-lijst te pagineren op createdTime,
en per ticket het customFields-veld "Doorgezet naar BrandMR" op te halen via
het detail-endpoint. Zoho Desk zelf bewaart alle historie -- alleen ONS eigen
zoho-tickets.json-bestand in git had beperkte historie, niet Zoho zelf."""
import os
import json
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timezone, timedelta

CLIENT_ID = os.environ.get('ZOHO_CLIENT_ID', '')
CLIENT_SECRET = os.environ.get('ZOHO_CLIENT_SECRET', '')
REFRESH_TOKEN = os.environ.get('ZOHO_REFRESH_TOKEN', '')
ORG_ID = os.environ.get('ZOHO_ORG_ID', '')

DESK_BASE = 'https://desk.zoho.eu/api/v1'
ACCOUNTS_URL = 'https://accounts.zoho.eu/oauth/v2/token'
AMS_OFFSET = timedelta(hours=2)

TARGET_DATES = ['2026-08-04', '2026-08-05', '2026-08-06', '2026-08-07',
                '2026-08-10', '2026-08-11', '2026-08-13', '2026-08-14']
EARLIEST = min(TARGET_DATES)


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
        print(f"  FOUT {e.code}: {e.read().decode(errors='replace')[:400]}")
        return None


def main():
    access_token = get_access_token()

    all_tickets = []
    start = 0
    page_size = 100
    while True:
        url = f"{DESK_BASE}/tickets?" + urllib.parse.urlencode({
            'limit': page_size,
            'from': start,
            'sortBy': '-createdTime',
            'include': 'contacts',
            'fields': 'ticketNumber,subject,status,priority,createdTime,webUrl,channel,email',
        })
        page = (get(access_token, url) or {}).get('data', [])
        if not page:
            break
        all_tickets.extend(page)
        oldest_in_page = page[-1].get('createdTime', '')
        print(f"  pagina from={start}: {len(page)} tickets, oudste: {oldest_in_page}")
        if oldest_in_page and oldest_in_page.split('T')[0] < EARLIEST:
            break
        start += page_size
        if start > 1000:
            print("  STOP: veiligheidslimiet van 1000 tickets bereikt")
            break

    print(f"\nTotaal opgehaald: {len(all_tickets)} tickets\n")

    by_date = {}
    for t in all_tickets:
        created_raw = t.get('createdTime', '')
        try:
            dt_utc = datetime.fromisoformat(created_raw.replace('Z', '+00:00'))
            dt_ams = dt_utc + AMS_OFFSET
        except Exception:
            continue
        date_str = dt_ams.date().isoformat()
        if date_str not in TARGET_DATES:
            continue
        contact = t.get('contact') or {}
        klant = ((contact.get('firstName') or '') + ' ' + (contact.get('lastName') or '')).strip()
        if not klant:
            klant = t.get('email') or 'Onbekend'
        by_date.setdefault(date_str, []).append({
            'tijd': dt_ams.strftime('%H:%M'),
            'ticketNumber': t.get('ticketNumber'),
            'id': t.get('id'),
            'titel': t.get('subject'),
            'klant': klant,
        })

    for date_str in TARGET_DATES:
        entries = sorted(by_date.get(date_str, []), key=lambda e: e['tijd'])
        print(f"\n=== {date_str} ({len(entries)} tickets) ===")
        for e in entries:
            detail = get(access_token, f"{DESK_BASE}/tickets/{e['id']}") or {}
            cf = detail.get('customFields') or {}
            raw = cf.get('Doorgezet naar BrandMR')
            doorgezet = str(raw).strip().lower() == 'true'
            print(f"  {e['tijd']} | #{e['ticketNumber']} | {e['klant']} | {e['titel']} | DoorgezetBrandMR={doorgezet} (raw={raw!r})")


if __name__ == '__main__':
    main()
