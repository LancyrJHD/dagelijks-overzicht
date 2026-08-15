#!/usr/bin/env python3
"""
Fetch today's tickets from the Zoho Desk "HTJZ | Lancyr" portal and write
data/zoho-tickets.json for the dagrapport dashboard.

Requires: ZOHO_CLIENT_ID, ZOHO_CLIENT_SECRET, ZOHO_REFRESH_TOKEN, ZOHO_ORG_ID
environment variables. Runs entirely on GitHub's servers (no dependency on
any local machine).
"""
import os
import json
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta

CLIENT_ID = os.environ.get('ZOHO_CLIENT_ID', '')
CLIENT_SECRET = os.environ.get('ZOHO_CLIENT_SECRET', '')
REFRESH_TOKEN = os.environ.get('ZOHO_REFRESH_TOKEN', '')
ORG_ID = os.environ.get('ZOHO_ORG_ID', '')

DESK_BASE = 'https://desk.zoho.eu/api/v1'
ACCOUNTS_URL = 'https://accounts.zoho.eu/oauth/v2/token'
OUTPUT_PATH = 'data/zoho-tickets.json'
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


def fetch_ticket_detail(access_token, ticket_id):
    """Haalt het volledige ticket-detail op (incl. customFields). De
    tickets-lijst-endpoint geeft customFields niet terug, het detail-endpoint
    wel, standaard zonder extra include/fields-parameters."""
    url = f"{DESK_BASE}/tickets/{ticket_id}"
    req = urllib.request.Request(url, headers={
        'Authorization': f'Zoho-oauthtoken {access_token}',
        'orgId': ORG_ID,
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except Exception as e:
        print(f"  WAARSCHUWING: kon ticket-detail {ticket_id} niet ophalen ({e})")
        return None


def fetch_tickets(access_token, limit=100):
    url = f"{DESK_BASE}/tickets?" + urllib.parse.urlencode({
        'limit': limit,
        'sortBy': '-createdTime',
        'include': 'contacts',
        # Expliciet opvragen — net als modifiedTime eerder bleek, geeft Zoho
        # niet elk veld standaard terug zonder dit expliciet te vragen.
        'fields': 'ticketNumber,subject,status,priority,createdTime,webUrl,channel,email',
    })
    req = urllib.request.Request(url, headers={
        'Authorization': f'Zoho-oauthtoken {access_token}',
        'orgId': ORG_ID,
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read()).get('data', [])


def main():
    missing = [n for n, v in [
        ('ZOHO_CLIENT_ID', CLIENT_ID), ('ZOHO_CLIENT_SECRET', CLIENT_SECRET),
        ('ZOHO_REFRESH_TOKEN', REFRESH_TOKEN), ('ZOHO_ORG_ID', ORG_ID),
    ] if not v]
    if missing:
        print(f"ERROR: missing env vars: {', '.join(missing)}")
        raise SystemExit(1)

    today_ams = (datetime.now(timezone.utc) + AMS_OFFSET).date()
    today_str = today_ams.isoformat()

    access_token = get_access_token()
    tickets = fetch_tickets(access_token)

    entries = []
    for t in tickets:
        created_raw = t.get('createdTime', '')
        try:
            dt_utc = datetime.fromisoformat(created_raw.replace('Z', '+00:00'))
            dt_ams = dt_utc + AMS_OFFSET
        except Exception:
            continue
        if dt_ams.date().isoformat() != today_str:
            # Tickets zijn gesorteerd op -createdTime, dus we kunnen stoppen
            # zodra we voorbij vandaag zijn.
            if entries:
                break
            continue

        contact = t.get('contact') or {}
        klant = ((contact.get('firstName') or '') + ' ' + (contact.get('lastName') or '')).strip()
        if not klant:
            klant = t.get('email') or 'Onbekend'

        # "Doorgezet naar BrandMR" is een echt Zoho-veld (custom field op de
        # ticket) -- dus een FEIT, geen AI-inschatting. Alleen te achterhalen
        # via het ticket-detail-endpoint (zie fetch_ticket_detail hierboven).
        doorgezet_brandmeester = False
        detail = fetch_ticket_detail(access_token, t.get('id'))
        if detail:
            cf = detail.get('customFields') or {}
            raw = cf.get('Doorgezet naar BrandMR')
            doorgezet_brandmeester = str(raw).strip().lower() == 'true'

        entries.append({
            'ticketNumber': t.get('ticketNumber', ''),
            'tijd': dt_ams.strftime('%H:%M'),
            'titel': t.get('subject', '(geen onderwerp)'),
            'klant': klant,
            'status': t.get('status', ''),
            'statusType': t.get('statusType', ''),
            'prioriteit': t.get('priority') or 'Niet ingesteld',
            'webUrl': t.get('webUrl', ''),
            'channel': t.get('channel') or 'ONBEKEND',
            'doorgezetBrandmeester': doorgezet_brandmeester,
        })

    entries.sort(key=lambda e: e['tijd'])

    # Kanaalverdeling (e-mail vs. telefonisch vs. overig) — telkens het
    # ruwe Zoho-channel-veld, zodat de frontend zelf de labels kan bepalen
    # en we niets verzinnen over kanalen die we niet kennen.
    channel_counts = {}
    for e in entries:
        channel_counts[e['channel']] = channel_counts.get(e['channel'], 0) + 1

    brandmeester_count = sum(1 for e in entries if e.get('doorgezetBrandmeester'))

    result = {
        'date': today_str,
        'generated_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'tickets': entries,
        'channelCounts': channel_counts,
        'brandmeesterCount': brandmeester_count,
    }

    os.makedirs('data', exist_ok=True)
    with open(OUTPUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"Saved {len(entries)} Zoho tickets van vandaag naar {OUTPUT_PATH}")


if __name__ == '__main__':
    main()
