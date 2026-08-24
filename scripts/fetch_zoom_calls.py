#!/usr/bin/env python3
"""Haalt de inbound-oproepen van vandaag op uit Zoom Phone call history:
totaal aantal binnenkomende oproepen, hoeveel daarvan gemist zijn, hoeveel
er zijn teruggebeld en (met naam/nummer/tijd) wie nog niet is teruggebeld.
Schrijft data/zoom-calls.json voor het dagrapport-dashboard (zelfde patroon
als scripts/fetch_zoho_tickets.py).

LET OP -- bekende beperking (bron: Zoom-documentatie "Understand Zoom Phone
call history"): een top-level call_history-record toont bij doorgeroute
gesprekken (auto receptionist / wachtrij) soms alleen het resultaat van het
EERSTE segment (bv. "answered" door de auto receptionist), niet het
resultaat bij de uiteindelijke medewerker. Als Lancyr-nummers via een auto
receptionist of wachtrij binnenkomen, kan dit script gemiste oproepen
ONDERSCHATTEN. Voor 100% zekerheid zou per call ook de "Get call path"-API
bevraagd moeten worden (scope phone:read:call_log:admin) -- dat gebeurt in
deze versie nog niet. Controleer de eerste resultaten steekproefsgewijs
tegen de Zoom-telefonielog voordat je hierop stuurt.

Vereist: ZOOM_ACCOUNT_ID, ZOOM_CLIENT_ID, ZOOM_CLIENT_SECRET (Server-to-
Server OAuth-app, scope phone:read:list_call_logs:admin). Optioneel:
ZOOM_DATE (YYYY-MM-DD) om een andere dag dan vandaag op te halen.
"""
import os
import json
import base64
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timezone, timedelta

ACCOUNT_ID = os.environ.get('ZOOM_ACCOUNT_ID', '')
CLIENT_ID = os.environ.get('ZOOM_CLIENT_ID', '')
CLIENT_SECRET = os.environ.get('ZOOM_CLIENT_SECRET', '')

TOKEN_URL = 'https://zoom.us/oauth/token'
API_BASE = 'https://api.zoom.us/v2'
OUTPUT_PATH = 'data/zoom-calls.json'
AMS_OFFSET = timedelta(hours=2)

# Resultaatwaarden die we als "gemist" beschouwen (niemand heeft live
# opgenomen). Zoom's call_result kent o.a.: Missed, Voicemail, Call
# connected, Rejected, Blocked, Busy, Wrong Number, No Answer, Call failed.
# We tellen Missed, No Answer en Voicemail mee -- bewust ruim, zodat niets
# dat een terugbelactie verdient over het hoofd wordt gezien.
MISSED_RESULTS = {'missed', 'no answer', 'no_answer', 'voicemail'}


def get_access_token():
    creds = f'{CLIENT_ID}:{CLIENT_SECRET}'.encode()
    basic = base64.b64encode(creds).decode()
    data = urllib.parse.urlencode({
        'grant_type': 'account_credentials',
        'account_id': ACCOUNT_ID,
    }).encode()
    req = urllib.request.Request(TOKEN_URL, data=data, method='POST', headers={
        'Authorization': f'Basic {basic}',
        'Content-Type': 'application/x-www-form-urlencoded',
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())['access_token']


def get_call_history(access_token, date_str):
    all_logs = []
    next_token = ''
    while True:
        params = {
            'from': date_str,
            'to': date_str,
            'page_size': 300,
            'type': 'all',
        }
        if next_token:
            params['next_page_token'] = next_token
        url = f"{API_BASE}/phone/call_history?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={
            'Authorization': f'Bearer {access_token}',
        })
        with urllib.request.urlopen(req, timeout=30) as resp:
            page = json.loads(resp.read())
        logs = page.get('call_logs', [])
        all_logs.extend(logs)
        next_token = page.get('next_page_token', '')
        if not next_token:
            break
    return all_logs


def main():
    missing = [n for n, v in [
        ('ZOOM_ACCOUNT_ID', ACCOUNT_ID), ('ZOOM_CLIENT_ID', CLIENT_ID),
        ('ZOOM_CLIENT_SECRET', CLIENT_SECRET),
    ] if not v]
    if missing:
        print(f"ERROR: missing env vars: {', '.join(missing)}")
        raise SystemExit(1)

    today_str = (datetime.now(timezone.utc) + AMS_OFFSET).date().isoformat()
    date_str = os.environ.get('ZOOM_DATE', today_str)

    print(f"Ophalen call history voor {date_str}...")
    access_token = get_access_token()
    logs = get_call_history(access_token, date_str)
    print(f"Totaal opgehaald: {len(logs)} call log records")
    if logs:
        print("Voorbeeldrecord:", json.dumps(logs[0], indent=2, ensure_ascii=False))

    missed = []
    outbound_calls = []
    totaal_inbound = 0
    for log in logs:
        direction = (log.get('direction') or '').lower()
        result = (log.get('call_result') or '').lower()
        if direction == 'inbound':
            totaal_inbound += 1
            if result in MISSED_RESULTS:
                missed.append(log)
        elif direction == 'outbound':
            outbound_calls.append(log)

    def called_back(missed_call):
        caller_number = missed_call.get('caller_did_number')
        missed_time = missed_call.get('start_time')
        if not caller_number or not missed_time:
            return False
        for ob in outbound_calls:
            if ob.get('callee_did_number') == caller_number and ob.get('start_time', '') > missed_time:
                return True
        return False

    niet_gebeld = []
    gebeld_count = 0
    for m in missed:
        if called_back(m):
            gebeld_count += 1
            continue
        start_raw = m.get('start_time', '') or ''
        try:
            dt_utc = datetime.fromisoformat(start_raw.replace('Z', '+00:00'))
            tijd = (dt_utc + AMS_OFFSET).strftime('%H:%M')
        except Exception:
            tijd = start_raw
        niet_gebeld.append({
            'nummer': m.get('caller_did_number', '') or '',
            'naam': m.get('caller_name') or 'Onbekend',
            'tijd': tijd,
            'callResult': m.get('call_result', '') or '',
        })

    niet_gebeld.sort(key=lambda x: x['tijd'])

    result = {
        'date': date_str,
        'generated_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'totaalInbound': totaal_inbound,
        'totaalGemist': len(missed),
        'terugGebeld': gebeld_count,
        'nietTerugGebeld': len(niet_gebeld),
        'nietTerugGebeldNummers': niet_gebeld,
    }
    os.makedirs('data', exist_ok=True)
    with open(OUTPUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"Saved: {totaal_inbound} binnenkomend, {len(missed)} gemist, "
          f"{gebeld_count} teruggebeld, {len(niet_gebeld)} niet teruggebeld "
          f"-> {OUTPUT_PATH}")


if __name__ == '__main__':
    try:
        main()
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors='replace')
        print(f"ERROR: Zoom API gaf HTTP {e.code} terug. Response body: {body}")
        raise SystemExit(1)
