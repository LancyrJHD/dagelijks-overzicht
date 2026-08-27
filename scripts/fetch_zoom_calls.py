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
resultaat bij de uiteindelijke medewerker. Voor 100% zekerheid zou per call
ook de "Get call path"-API bevraagd moeten worden (scope
phone:read:call_log:admin) -- dat gebeurt in deze versie nog niet.

BEVINDING 24 aug 2026: Zoom's call_result kent geen vaste, volledig
gedocumenteerde lijst met waarden (o.a. "abandoned" -- een call die ophangt
terwijl die in de wachtrij staat -- ontbreekt zelfs in Zoom's eigen
supportartikel). Een allowlist van "gemiste" resultaten bleek daardoor
onbetrouwbaar: een echte gemiste oproep (Zoom-app toonde 'm als "Missed")
kwam binnen met call_result="abandoned", wat niet in de allowlist stond en
dus stilzwijgend NIET als gemist werd geteld. Dit script gebruikt daarom nu
het omgekeerde: een denylist van resultaten die aantoonbaar een mens aan de
lijn kregen (HANDLED_RESULTS). Alles daarbuiten telt als "gemist / actie
nodig" -- bewust ruim, zodat een nieuwe/onbekende call_result-waarde nooit
stilzwijgend wordt genegeerd. Controleer bij twijfel de Zoom-telefonielog
zelf.

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

# Diagnose-only modus: print volledige ruwe record(s) voor een specifiek
# nummer en STOP dan zonder data/zoom-calls.json te schrijven. Bedoeld om
# handmatig (via workflow_dispatch + ZOOM_DATE) een specifieke, betwiste
# oproep te inspecteren zonder de productiedata van 'vandaag' te overschrijven.
DIAGNOSE_ONLY = os.environ.get('ZOOM_DIAGNOSE_ONLY', '').lower() in ('1', 'true', 'yes')
DIAGNOSE_NUMBER = os.environ.get('ZOOM_DIAGNOSE_NUMBER', '').strip()

TOKEN_URL = 'https://zoom.us/oauth/token'
API_BASE = 'https://api.zoom.us/v2'
OUTPUT_PATH = 'data/zoom-calls.json'
HISTORY_PATH = 'data/zoom-history.json'
AMS_OFFSET = timedelta(hours=2)

# Resultaatwaarden die aantonen dat een MENS de oproep heeft beantwoord.
# Alleen deze tellen als "geen actie nodig"; alles wat hier niet in staat
# (voicemail, abandoned, missed, no_answer, busy, rejected, blocked,
# wrong_number, call_failed, en elke toekomstige/onbekende waarde) telt als
# "gemist" en komt in de terugbel-lijst terecht. Zie docstring hierboven
# voor waarom dit bewust een denylist is i.p.v. een allowlist.
HANDLED_RESULTS = {'answered', 'connected'}

# BEVINDING 25 aug 2026: dit Zoom-account heeft meerdere wachtrijen op
# dezelfde helpdesk-afdeling (o.a. "Lancyr Juridische Helpdesk", ext 807,
# +31294799077 EN "HTJZ - Juridische Helpdesk", ext 811, +318002300743).
# Zonder filter haalt get_call_history() ALLE wachtrijen van het account
# op, waardoor een oproep die nooit de Lancyr-lijn heeft bereikt (bewezen
# geval: +31433514571, voicemail op ext 811 op 24 aug) toch als "gemiste
# Lancyr-oproep" werd gerapporteerd. Daarom nu expliciet filteren op de
# Lancyr-lijn, voor zowel inbound (callee) als outbound (caller = onze
# queue-DID bij een agent die terugbelt).
LANCYR_QUEUE_DID = '+31294799077'
LANCYR_QUEUE_EXT = '807'


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


def get_call_path(access_token, call_id):
    """Get call path (phone:read:call_log:admin) -- geeft het volledige
    routeringspad van een call terug (auto receptionist / wachtrij /
    medewerker per segment), zodat we kunnen zien of een top-level
    call_result als 'voicemail'/'abandoned' misleidend is (bv. omdat een
    medewerker het gesprek elders in het pad wel degelijk heeft gehad)."""
    url = f"{API_BASE}/phone/call_history/{urllib.parse.quote(call_id, safe='')}"
    req = urllib.request.Request(url, headers={
        'Authorization': f'Bearer {access_token}',
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def main():
    missing = [n for n, v in [
        ('ZOOM_ACCOUNT_ID', ACCOUNT_ID), ('ZOOM_CLIENT_ID', CLIENT_ID),
        ('ZOOM_CLIENT_SECRET', CLIENT_SECRET),
    ] if not v]
    if missing:
        print(f"ERROR: missing env vars: {', '.join(missing)}")
        raise SystemExit(1)

    today_str = (datetime.now(timezone.utc) + AMS_OFFSET).date().isoformat()
    # FIX 25 aug 2026: ZOOM_DATE staat via workflow_dispatch ALTIJD in de
    # omgeving (ook als lege string ''), dus os.environ.get(..., today_str)
    # viel niet terug op vandaag. 'or' behandelt zowel ontbrekend als leeg
    # als 'gebruik vandaag'.
    date_str = os.environ.get('ZOOM_DATE') or today_str

    print(f"Ophalen call history voor {date_str}...")
    access_token = get_access_token()
    logs = get_call_history(access_token, date_str)
    print(f"Totaal opgehaald: {len(logs)} call log records")
    if logs:
        print("Voorbeeldrecord:", json.dumps(logs[0], indent=2, ensure_ascii=False))

    if DIAGNOSE_ONLY:
        print(f"--- DIAGNOSE-ONLY MODUS: zoeken op {DIAGNOSE_NUMBER!r} "
              f"(caller_did_number of callee_did_number) ---")
        matches = [
            log for log in logs
            if DIAGNOSE_NUMBER and (
                DIAGNOSE_NUMBER in (log.get("caller_did_number") or "")
                or DIAGNOSE_NUMBER in (log.get("callee_did_number") or "")
            )
        ]
        print(f"Gevonden: {len(matches)} record(en)")
        for m in matches:
            print(json.dumps(m, indent=2, ensure_ascii=False))
            call_id = m.get('id')
            print(f"--- CALL PATH voor {call_id!r} ---")
            try:
                path = get_call_path(access_token, call_id)
                print(json.dumps(path, indent=2, ensure_ascii=False))
            except urllib.error.HTTPError as e:
                body = e.read().decode(errors='replace')
                print(f"  (kon call path niet ophalen: HTTP {e.code} -- {body})")
            except Exception as e:
                print(f"  (kon call path niet ophalen: {e})")
        print("--- EINDE DIAGNOSE-ONLY (data/zoom-calls.json NIET geschreven) ---")
        return

    # Filter op de Lancyr Juridische Helpdesk-lijn (zie BEVINDING 25 aug 2026
    # hierboven). Alles hierna (result_counts, queue_counts, missed/outbound)
    # werkt bewust op de GEFILTERDE lijst.
    ongefilterd_totaal = len(logs)
    logs = [
        log for log in logs
        if (
            (log.get("direction") or "").lower() == "inbound"
            and log.get("callee_did_number") == LANCYR_QUEUE_DID
        ) or (
            (log.get("direction") or "").lower() == "outbound"
            and log.get("caller_did_number") == LANCYR_QUEUE_DID
        )
    ]
    print(f"Gefilterd op Lancyr Juridische Helpdesk-lijn ({LANCYR_QUEUE_DID}): "
          f"{len(logs)} van {ongefilterd_totaal} opgehaalde records blijven over.")

    # Lichte, blijvende diagnose: welke call_result-waarden komen vandaag
    # voor bij inbound gesprekken? Handig om in de Actions-log te zien of er
    # een nieuwe/onbekende waarde opduikt, zonder elke run alle records
    # individueel te loggen.
    result_counts = {}
    for log in logs:
        if (log.get('direction') or '').lower() == 'inbound':
            r = log.get('call_result') or '(leeg)'
            result_counts[r] = result_counts.get(r, 0) + 1
    print(f"Inbound call_result-waarden vandaag: {result_counts}")

    # TIJDELIJKE DIAGNOSE: op welke lijn/wachtrij (callee) komen de
    # binnenkomende oproepen vandaag binnen? Een gebruiker meldde een
    # "gemiste oproep" te zien voor een nummer dat volgens hen nooit heeft
    # gebeld -- mogelijke oorzaak: dit account heeft meerdere wachtrijen
    # (bv. een algemene HTJZ-lijn naast de specifieke Lancyr-helpdesklijn)
    # en dit script haalt nu ALLE wachtrijen van het account op, niet
    # gefilterd op de Lancyr-helpdesk.
    queue_counts = {}
    for log in logs:
        if (log.get('direction') or '').lower() == 'inbound':
            key = (log.get('callee_name'), log.get('callee_ext_number'), log.get('callee_did_number'))
            queue_counts[key] = queue_counts.get(key, 0) + 1
    print("Inbound-oproepen per (callee_name, callee_ext_number, callee_did_number):")
    for key, count in queue_counts.items():
        print(f"  {key}: {count}")

    # TIJDELIJKE DIAGNOSE: check of 'answered'-oproepen ECHT door een mens
    # zijn opgepakt. Bekende beperking (zie docstring bovenaan): bij een
    # call queue kan het top-level call_result al 'answered' tonen zodra de
    # wachtrij zelf de oproep in behandeling neemt, ook als de beller ophangt
    # voordat een medewerker daadwerkelijk opneemt. Een echt beantwoorde
    # oproep hoort een answer_time te hebben EN een spreekduur (duration) >
    # 0. Print elke 'answered' inbound-oproep zonder answer_time of met
    # duration <= 0 -- die zijn hoogstwaarschijnlijk GEEN echte antwoorden
    # en zouden als gemist geteld moeten worden.
    verdachte_answered = [
        log for log in logs
        if (log.get('direction') or '').lower() == 'inbound'
        and (log.get('call_result') or '').lower() == 'answered'
        and (not log.get('answer_time') or (log.get('duration') or 0) <= 0)
    ]
    print(f"Verdachte 'answered'-oproepen (geen answer_time of duration<=0): {len(verdachte_answered)}")
    for log in verdachte_answered:
        print(f"  id={log.get('id')!r} caller={log.get('caller_did_number')!r} "
              f"start={log.get('start_time')!r} answer_time={log.get('answer_time')!r} "
              f"duration={log.get('duration')!r}")

    missed = []
    outbound_calls = []
    totaal_inbound = 0
    for log in logs:
        direction = (log.get('direction') or '').lower()
        result = (log.get('call_result') or '').lower()
        if direction == 'inbound':
            totaal_inbound += 1
            if result not in HANDLED_RESULTS:
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

    # TIJDELIJKE DIAGNOSE: voor elke als "gemist" geclassificeerde oproep
    # het volledige call path ophalen, zodat we kunnen verifieren of het
    # top-level resultaat (bv. "voicemail") klopt of dat een medewerker
    # het gesprek elders in het pad al wel degelijk heeft gehad.
    if missed:
        print(f"--- CALL PATH DIAGNOSE voor {len(missed)} gemiste oproep(en) ---")
        for m in missed:
            call_id = m.get('id')
            print(f"call_id={call_id!r} caller={m.get('caller_did_number')!r} "
                  f"top_level_result={m.get('call_result')!r} duration={m.get('duration')!r}")
            try:
                path = get_call_path(access_token, call_id)
                print(json.dumps(path, indent=2, ensure_ascii=False))
            except urllib.error.HTTPError as e:
                body = e.read().decode(errors='replace')
                print(f"  (kon call path niet ophalen: HTTP {e.code} -- {body})")
            except Exception as e:
                print(f"  (kon call path niet ophalen: {e})")
        print("--- EINDE CALL PATH DIAGNOSE ---")

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

    # Geaccumuleerde historie bijwerken (per datum), zodat het dashboard ook
    # 'Gisteren'/'Deze week'/'Vorige week' kan tonen voor gemiste oproepen.
    # Bestaande datums blijven staan; alleen de datum van DEZE run wordt
    # overschreven (idempotent -- veilig om dezelfde dag meerdere keren
    # per dag te draaien, zoals de geplande run elke 30 minuten doet).
    try:
        with open(HISTORY_PATH, 'r', encoding='utf-8') as f:
            history = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        history = {}
    history[date_str] = result
    with open(HISTORY_PATH, 'w', encoding='utf-8') as f:
        json.dump(history, f, ensure_ascii=False, indent=2, sort_keys=True)
    print(f"Historie bijgewerkt: {len(history)} datum(s) in {HISTORY_PATH}")


if __name__ == '__main__':
    try:
        main()
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors='replace')
        print(f"ERROR: Zoom API gaf HTTP {e.code} terug. Response body: {body}")
        raise SystemExit(1)
