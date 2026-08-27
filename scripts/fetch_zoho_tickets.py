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


def fetch_ticket_threads(access_token, ticket_id):
    """Haalt de e-mailthreads van een ticket op. Het customFields-vinkje
    "Doorgezet naar BrandMR" bleek onbetrouwbaar (0 van 30 gecontroleerde
    tickets stond op waar, ook een geval dat aantoonbaar wel was doorgezet).
    Het echte signaal is een uitgaande e-mail naar de BrandMR-intake --
    bevestigd op ticket #2542 (4 aug 2026): een thread met direction='out'
    naar intake@brandmr.nl met het vaste sjabloon."""
    url = f"{DESK_BASE}/tickets/{ticket_id}/threads"
    req = urllib.request.Request(url, headers={
        'Authorization': f'Zoho-oauthtoken {access_token}',
        'orgId': ORG_ID,
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read()).get('data', [])
    except Exception as e:
        print(f"  WAARSCHUWING: kon threads voor ticket {ticket_id} niet ophalen ({e})")
        return []


def is_doorgezet_naar_brandmeester(threads):
    """Feit, geen AI-inschatting: True als er een uitgaande e-mail is naar
    de BrandMR-intake (bevestigd patroon op ticket #2542)."""
    for th in threads:
        if th.get('direction') != 'out':
            continue
        to_field = (th.get('to') or '').lower()
        if 'brandmr.nl' in to_field:
            return True
    return False


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


CLOSED_ALIASES = ('gesloten', 'closed')
ONHOLD_ALIASES = ('wachtend', 'on hold', 'onhold', 'on-hold', 'in afwachting', 'in de wacht')


def _is_closed_status(status):
    s = (status or '').strip().lower()
    return any(alias in s for alias in CLOSED_ALIASES)


def _is_onhold_status(status):
    s = (status or '').strip().lower()
    return any(alias in s for alias in ONHOLD_ALIASES)


def fetch_tickets_for_stats(access_token, max_pages=10, page_size=100):
    """Haalt een brede, ongefilterde set tickets op (gesorteerd op
    -modifiedTime, dus meest recent aangeraakt eerst) om drie dingen te
    berekenen die de losse 'tickets van vandaag'-fetch hierboven niet kan
    leveren (die is gefilterd op createdTime = vandaag): hoeveel tickets
    zijn er vandaag GESLOTEN (kan aangemaakt zijn op een eerdere dag),
    hoeveel staan er in totaal nog open, en hoeveel daarvan staan 'in de
    wacht'. Best-effort: gepagineerd tot max_pages * page_size tickets
    (standaard 1000) -- ruim voldoende voor het dagvolume van dit
    kantoor. Dedupliceert op ticket-id; als paginering geen nieuwe
    tickets meer oplevert (mogelijk werkt 'from' anders dan verwacht in
    deze API-versie) stopt de fetch vroeg met een waarschuwing i.p.v.
    de cap te verspillen aan dubbele tickets."""
    all_tickets = []
    seen_ids = set()
    cap_reached = True
    for page in range(max_pages):
        from_index = page * page_size + 1
        url = f"{DESK_BASE}/tickets?" + urllib.parse.urlencode({
            'limit': page_size,
            'from': from_index,
            'sortBy': '-modifiedTime',
            'fields': 'id,status,statusType,createdTime,closedTime,modifiedTime',
        })
        req = urllib.request.Request(url, headers={
            'Authorization': f'Zoho-oauthtoken {access_token}',
            'orgId': ORG_ID,
        })
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                page_data = json.loads(resp.read()).get('data', [])
        except Exception as e:
            print(f"  WAARSCHUWING: kon pagina {page + 1} van ticketstats niet ophalen ({e})")
            cap_reached = False
            break
        if not page_data:
            cap_reached = False
            break
        new_count = 0
        for t in page_data:
            tid = t.get('id')
            if tid and tid in seen_ids:
                continue
            if tid:
                seen_ids.add(tid)
            all_tickets.append(t)
            new_count += 1
        if new_count == 0:
            print("  WAARSCHUWING: paginering ticketstats leverde alleen "
                  "duplicaten op, gestopt (resultaat mogelijk incompleet).")
            break
        if len(page_data) < page_size:
            cap_reached = False
            break
    return all_tickets, cap_reached


def compute_ticket_stats(nieuwe_vandaag_count, stats_tickets, cap_reached, today_str):
    status_breakdown = {}
    totaal_open = 0
    in_de_wacht = 0
    gesloten_vandaag = 0
    for t in stats_tickets:
        status = t.get('status') or '(geen status)'
        status_breakdown[status] = status_breakdown.get(status, 0) + 1
        if not _is_closed_status(status):
            totaal_open += 1
            if _is_onhold_status(status):
                in_de_wacht += 1
            continue
        closed_raw = t.get('closedTime', '')
        if not closed_raw:
            continue
        try:
            dt_utc = datetime.fromisoformat(closed_raw.replace('Z', '+00:00'))
            dt_ams = dt_utc + AMS_OFFSET
        except Exception:
            continue
        if dt_ams.date().isoformat() == today_str:
            gesloten_vandaag += 1
    return {
        'nieuweVandaag': nieuwe_vandaag_count,
        'geslotenVandaag': gesloten_vandaag,
        'totaalOpen': totaal_open,
        'inDeWacht': in_de_wacht,
        'statusBreakdown': status_breakdown,
        'capBereikt': cap_reached,
    }


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

        # "Doorgezet naar BrandMR" is een feit (geen AI-inschatting), maar
        # NIET via het onbetrouwbare customFields-vinkje -- via de e-mail-
        # threads van het ticket (zie fetch_ticket_threads hierboven).
        threads = fetch_ticket_threads(access_token, t.get('id'))
        doorgezet_brandmeester = is_doorgezet_naar_brandmeester(threads)

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

    stats_tickets, cap_reached = fetch_tickets_for_stats(access_token)
    ticket_stats = compute_ticket_stats(len(entries), stats_tickets, cap_reached, today_str)
    print(
        f"Ticketstats: {ticket_stats['nieuweVandaag']} nieuw, "
        f"{ticket_stats['geslotenVandaag']} gesloten vandaag, "
        f"{ticket_stats['totaalOpen']} nog open, "
        f"{ticket_stats['inDeWacht']} in de wacht "
        f"(status-breakdown: {ticket_stats['statusBreakdown']}, "
        f"cap bereikt: {ticket_stats['capBereikt']})"
    )

    result = {
        'date': today_str,
        'generated_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'tickets': entries,
        'channelCounts': channel_counts,
        'brandmeesterCount': brandmeester_count,
        'ticketStats': ticket_stats,
    }

    os.makedirs('data', exist_ok=True)
    with open(OUTPUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"Saved {len(entries)} Zoho tickets van vandaag naar {OUTPUT_PATH}")


if __name__ == '__main__':
    main()
