#!/usr/bin/env python3
"""EENMALIG diagnose-script: haalt voor elke werkdag van deze en vorige week
het aantal tickets op en telt hoeveel daarvan (op basis van de e-mailthreads,
de betrouwbare methode) zijn doorgezet naar BrandMR. Resultaat wordt geprint
zodat het handmatig in weekData in index.html kan worden verwerkt."""
import os
import json
import urllib.request
import urllib.parse
from datetime import datetime, timedelta

CLIENT_ID = os.environ.get('ZOHO_CLIENT_ID', '')
CLIENT_SECRET = os.environ.get('ZOHO_CLIENT_SECRET', '')
REFRESH_TOKEN = os.environ.get('ZOHO_REFRESH_TOKEN', '')
ORG_ID = os.environ.get('ZOHO_ORG_ID', '')

DESK_BASE = 'https://desk.zoho.eu/api/v1'
ACCOUNTS_URL = 'https://accounts.zoho.eu/oauth/v2/token'
AMS_OFFSET = timedelta(hours=2)
# Vorige week (ma 3 t/m vr 7 aug) + deze week (ma 10 t/m za 15 aug).
TARGET_DATES = [
    '2026-08-03', '2026-08-04', '2026-08-05', '2026-08-06', '2026-08-07',
    '2026-08-10', '2026-08-11', '2026-08-12', '2026-08-13', '2026-08-14', '2026-08-15',
]
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
    except Exception as e:
        print(f"  FOUT: {e}")
        return None


def is_doorgezet_naar_brandmeester(threads):
    for th in threads:
        if th.get('direction') != 'out':
            continue
        to_field = (th.get('to') or '').lower()
        if 'brandmr.nl' in to_field:
            return True
    return False


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
            'fields': 'ticketNumber,subject,createdTime',
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
        if start > 2000:
            print("  STOP: veiligheidslimiet van 2000 tickets bereikt")
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
        by_date.setdefault(date_str, []).append(t)

    summary = {}
    for date_str in TARGET_DATES:
        tickets = by_date.get(date_str, [])
        brandmr_count = 0
        for t in tickets:
            threads = (get(access_token, f"{DESK_BASE}/tickets/{t.get('id')}/threads") or {}).get('data', [])
            if is_doorgezet_naar_brandmeester(threads):
                brandmr_count += 1
                print(f"  -> #{t.get('ticketNumber')} ({date_str}) is doorgezet naar BrandMR: {t.get('subject')}")
        summary[date_str] = {'totaal': len(tickets), 'brandmr': brandmr_count}
        print(f"=== {date_str}: {len(tickets)} tickets, {brandmr_count} doorgezet naar BrandMR ===")

    print("\n\nSAMENVATTING (JSON):")
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
