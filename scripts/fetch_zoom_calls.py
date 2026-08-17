#!/usr/bin/env python3
"""EENMALIG diagnose-script: haalt Zoom Phone call history op voor vandaag
(of een opgegeven datum) en bepaalt per gemiste inbound-oproep of er later
diezelfde dag een outbound-oproep naar hetzelfde nummer is geweest
(= teruggebeld) of niet. Print een JSON-samenvatting zodat we de echte
Zoom-datavorm kunnen controleren voordat dit in de hoofdpipeline komt.

Vereist: ZOOM_ACCOUNT_ID, ZOOM_CLIENT_ID, ZOOM_CLIENT_SECRET (Server-to-Server
OAuth-app, scope phone:read:list_call_logs:admin).
"""
import os
import json
import base64
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timedelta

ACCOUNT_ID = os.environ.get('ZOOM_ACCOUNT_ID', '')
CLIENT_ID = os.environ.get('ZOOM_CLIENT_ID', '')
CLIENT_SECRET = os.environ.get('ZOOM_CLIENT_SECRET', '')

TOKEN_URL = 'https://zoom.us/oauth/token'
API_BASE = 'https://api.zoom.us/v2'
AMS_OFFSET = timedelta(hours=2)

# Resultaatwaarden die we als "gemist" beschouwen (nobody heeft live opgenomen).
# Zoom's call_result kent o.a.: Missed, Voicemail, Call connected, Rejected,
# Blocked, Busy, Wrong Number, No Answer, Call failed, e.a. We tellen Missed,
# No Answer en Voicemail mee als "gemist" -- dat is bewust ruim, zodat niets
# dat een terugbelactie verdient over het hoofd wordt gezien.
MISSED_RESULTS = {'missed', 'no answer', 'no_answer', 'voicemail'}


def get_access_token():
    print(f"Diagnose: ACCOUNT_ID len={len(ACCOUNT_ID)}, CLIENT_ID len={len(CLIENT_ID)}, CLIENT_SECRET len={len(CLIENT_SECRET)}")
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
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())['access_token']
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors='replace')
        print(f"Zoom token endpoint gaf HTTP {e.code} terug. Response body: {body}")
        raise


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
    today_str = (datetime.utcnow() + AMS_OFFSET).date().isoformat()
    date_str = os.environ.get('ZOOM_DATE', today_str)

    print(f"Ophalen call history voor {date_str}...")
    access_token = get_access_token()
    logs = get_call_history(access_token, date_str)
    print(f"Totaal opgehaald: {len(logs)} call log records\n")

    # Print een paar ruwe records zodat we de echte veldnamen kunnen zien.
    for log in logs[:3]:
        print("VOORBEELDRECORD:", json.dumps(log, indent=2))

    missed = []
    outbound_calls = []
    for log in logs:
        direction = (log.get('direction') or '').lower()
        result = (log.get('call_result') or '').lower()
        if direction == 'inbound' and result in MISSED_RESULTS:
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
        else:
            niet_gebeld.append({
                'nummer': m.get('caller_did_number'),
                'naam': m.get('caller_name'),
                'tijd': m.get('start_time'),
                'call_result': m.get('call_result'),
            })

    summary = {
        'datum': date_str,
        'totaalGemist': len(missed),
        'terugGebeld': gebeld_count,
        'nietTerugGebeld': len(niet_gebeld),
        'nietTerugGebeldNummers': niet_gebeld,
    }
    print("\nSAMENVATTING (JSON):")
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
  
