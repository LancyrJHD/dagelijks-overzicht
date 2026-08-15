#!/usr/bin/env python3
"""Fetch Lancyr Juridische Helpdesk meetings from Granola API, analyze each
conversation with the Anthropic API, and generate the full data/today.json
for the dagrapport dashboard (matching the rich weekData schema).

Requires: GRANOLA_API_KEY, ANTHROPIC_API_KEY environment variables.

Design notes / resilience:
- If a single conversation fails to analyze via the AI, we fall back to a
  simple heuristic classification for THAT conversation only, so one bad
  API response never blocks the whole run.
- If the day-level synthesis (score/goed/beter) call fails, we ship the
  conversations with score 0 and empty analyse rather than fail the job.
- This script is used by .github/workflows/update-dagrapport.yml and runs
  entirely on GitHub's servers (no dependency on any local machine).
"""
import os
import json
import time
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime, timezone, timedelta

GRANOLA_TOKEN = os.environ.get('GRANOLA_API_KEY', '')
ANTHROPIC_KEY = os.environ.get('ANTHROPIC_API_KEY', '')

LANCYR_FOLDER_NAME = 'Lancyr Juridische Helpdesk'
GRANOLA_BASE = 'https://public-api.granola.ai/v1'
ANTHROPIC_MODEL = 'claude-haiku-4-5-20251001'
OUTPUT_PATH = 'data/today.json'
AMS_OFFSET = timedelta(hours=2)  # CEST — goed genoeg voor kantooruren

GRANOLA_HEADERS = {
    'Authorization': f'Bearer {GRANOLA_TOKEN}',
    'Content-Type': 'application/json',
}

# Canonieke uitkomst-paren — MOET exact overeenkomen met MANUAL_OUTCOMES in index.html
VALID_OUTCOMES = {
    "outcome-opgelost": "Telefonisch opgelost",
    "outcome-terugbel": "Terugbellen",
    "outcome-brandmeester": "Doorverwezen naar Brandmeester",
    "outcome-afkoop": "Afkoop",
    "outcome-geen-dekking": "Geen dekking",
}

ANALYZE_SYSTEM_PROMPT = """Je bent een juridische kwaliteitsanalist voor de Lancyr Juridische Helpdesk van HTJZ.

Achtergrond:
- Eerstelijns telefonisch juridisch advies voor verzekerden van Lancyr.
- Altijd eerst polis + identiteit verifieren.
- Bij consumentenzaken: franchise EUR 250 - schade <= EUR 250 = geen dekking.
- Bouwzaken met grote schade (>EUR 5.000) -> verplicht doorverwijzen naar Brandmeester.
- Arbeidsconflicten -> altijd doorverwijzen naar Brandmeester.

Categoriseer het gesprek in EXACT een van deze 5 paren (class, label):
["outcome-opgelost","Telefonisch opgelost"]
["outcome-terugbel","Terugbellen"]
["outcome-brandmeester","Doorverwezen naar Brandmeester"]
["outcome-afkoop","Afkoop"]
["outcome-geen-dekking","Geen dekking"]

Tags (rechtsgebied): gebruik ["tag-arbeidsrecht","Arbeidsrecht"], ["tag-consument","Consumentenrecht"], ["tag-bouw","Bouwrecht"], ["tag-huur","Huurrecht"], ["tag-bestuursrecht","Bestuursrecht"], of ["tag-overig","<specifiek rechtsgebied>"] als geen van de vaste categorieen past.

Bepaal ook drie extra booleans, ONAFHANKELIJK van de uitkomst hierboven:
- "verkeerd_verbonden": true ALLEEN als de beller iets zoekt dat helemaal niet bij een juridische helpdesk hoort (bijv. een autoverzekeringsvraag, een schadeclaim-callcenter, of een compleet verkeerd doorverbonden nummer) EN dit ook zo in het gesprek wordt vastgesteld. Bij twijfel: false.
- "rechtsbijstand_verwijzing": true ALLEEN als de medewerker de beller expliciet doorverwijst naar de rechtsbijstandsafdeling/rechtsbijstandsverzekering voor verdere behandeling van de zaak (dus niet zomaar "u heeft rechtsbijstand", maar een daadwerkelijke doorverwijzing/overdracht). Bij twijfel: false.
- "advies_gegeven": true ALLEEN als de medewerker daadwerkelijk inhoudelijk juridisch advies of een concrete juridische uitleg heeft gegeven over de zaak. BELANGRIJK: als de medewerker de beller NIET kon verifieren (niet gevonden in het systeem op naam/adres/polisnummer) en daarom bewust GEEN advies heeft gegeven maar in plaats daarvan om verificatiedocumenten heeft gevraagd en een vervolgcontact heeft afgesproken, is dit false -- en dat is GEEN fout maar juist correct, voorzichtig handelen. Zet dit niet automatisch op true alleen omdat er een juridisch onderwerp is besproken.
- "identiteit_geverifieerd": true als de medewerker de beller op enig moment tijdens het gesprek heeft gevonden/bevestigd in het systeem (bijvoorbeeld via postcode, huisnummer, adres, polisnummer of naam) -- ook als dat pas halverwege het gesprek gebeurt, vroeg in het gesprek gebeurt is voldoende, het hoeft niet aan het begin te zijn. BELANGRIJK: als onderaan dit bericht "Systeeminfo" staat met een gekoppelde Zoho-contactpersoon, betekent dit dat het systeem de beller AUTOMATISCH heeft herkend op telefoonnummer (koppeling met polis/klantdossier) -- zet dan identiteit_geverifieerd op true, OOK ALS dit nergens expliciet in het transcript wordt besproken. Zet identiteit_geverifieerd alleen op false als er geen Zoho-contactmatch is EN er ook in het transcript geen enkele verificatiepoging (naam/adres/postcode/polisnummer) is gedaan. Let op: "dekkingscontrole" / franchise-controle (EUR 250) is alleen relevant bij zaken met een concreet schadebedrag (bijv. schadeclaims). Bij geschillen over rechten, hinder of gebruik zonder schadebedrag (bijv. burenrecht, onrechtmatige hinder, huurrecht) is de franchise NIET van toepassing en mag dit niet als ontbrekend kwaliteitspunt worden genoemd.

Geef ALLEEN geldige JSON terug, geen andere tekst, in dit exacte formaat:
{"samenvatting": "max 3 zinnen, feitelijk en concreet, en vermeld expliciet of en wanneer de beller is geverifieerd en of er wel of geen inhoudelijk advies is gegeven", "tags": [["tag-x","Label"]], "uitkomst": ["outcome-x","Label"], "terugbel": true of false, "verkeerd_verbonden": true of false, "rechtsbijstand_verwijzing": true of false, "advies_gegeven": true of false, "identiteit_geverifieerd": true of false}
"""

DAY_SYSTEM_PROMPT = """Je bent een juridische kwaliteitsanalist voor de Lancyr Juridische Helpdesk van HTJZ.

Kwaliteitsnormen: altijd polis+identiteit verifieren, dekkingscontrole (franchise EUR 250) vastleggen, deadlines altijd concreet benoemen, let op herhaalcontact (klant belt vandaag al eerder over hetzelfde onderwerp).

Je krijgt een lijst van alle gesprekken van vandaag (tijd, titel, samenvatting, uitkomst, advies_gegeven, identiteit_geverifieerd). Beoordeel de dag als geheel en verwijs in elk punt naar het specifieke gesprek (tijdstip + onderwerp).

BELANGRIJKE CORRECTIES:
1. Als "advies_gegeven" false is voor een gesprek, betekent dit dat de medewerker de beller niet kon verifieren in het systeem en daarom TERECHT geen advies heeft gegeven, maar om verificatiedocumenten heeft gevraagd en een vervolgcontact heeft afgesproken. Beoordeel dit NIET als een verificatiefout ("polis/identiteit niet gecontroleerd voordat advies werd gegeven") -- dat is een contradictie, er is immers geen advies gegeven. Beoordeel in dat geval alleen of de medewerker een duidelijk vervolgplan en waar mogelijk een concrete deadline heeft afgesproken voor het gesprek zelf.
2. Als "identiteit_geverifieerd" true is, is de beller door de medewerker gevonden/bevestigd in het systeem -- noem dan NIET als kritiekpunt dat polis/identiteit niet gecontroleerd zou zijn, ook niet als dat verderop in het gesprek gebeurde in plaats van meteen aan het begin.
3. De "dekkingscontrole" / franchise-norm (EUR 250) geldt alleen bij zaken met een concreet schadebedrag (schadeclaims). Noem dit NIET als ontbrekend kwaliteitspunt bij geschillen over rechten, hinder of gebruik zonder schadebedrag (bijv. burenrecht, onrechtmatige hinder, huurrecht).

Geef ALLEEN geldige JSON terug in dit exacte formaat:
{"score": 7.2, "goed": ["...", "..."], "beter": ["...", "..."]}
"""


def anthropic_call(system, user_content, max_tokens=800, retries=2):
    body = json.dumps({
        "model": ANTHROPIC_MODEL,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": user_content}],
    }).encode('utf-8')
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "x-api-key": ANTHROPIC_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
    )
    last_err = None
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                result = json.loads(resp.read())
                text = result["content"][0]["text"].strip()
                if text.startswith("```"):
                    text = text.split("```")[1]
                    if text.startswith("json"):
                        text = text[4:]
                return json.loads(text.strip())
        except Exception as e:
            last_err = e
            time.sleep(2 * (attempt + 1))
    print(f"  WAARSCHUWING: Anthropic-aanroep mislukt na retries: {last_err}")
    return None


def get_all_today_notes():
    today_ams = (datetime.now(timezone.utc) + AMS_OFFSET).date()
    today_str = today_ams.isoformat()
    print(f"Fetching notes for {today_str} (Amsterdam)...")
    notes = []
    cursor = None
    while True:
        params = {'page_size': 30, 'created_after': today_str}
        if cursor:
            params['cursor'] = cursor
        qs = urllib.parse.urlencode(params)
        req = urllib.request.Request(f"{GRANOLA_BASE}/notes?{qs}", headers=GRANOLA_HEADERS)
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
        notes.extend(data.get('notes', []))
        if not data.get('hasMore'):
            break
        cursor = data.get('cursor')
    print(f"  -> {len(notes)} total notes today")
    return notes


def get_note_detail(note_id):
    req = urllib.request.Request(
        f"{GRANOLA_BASE}/notes/{note_id}?include=transcript",
        headers=GRANOLA_HEADERS,
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def is_lancyr_note(detail):
    for folder in (detail.get('folder_membership') or []):
        if LANCYR_FOLDER_NAME in folder.get('name', ''):
            return True
    return False


def is_failed_call(title):
    skip_keywords = ['vm uitgeschakeld', 'voicemail', 'geen gehoor', 'niet opgenomen',
                      'gebeld maar', 'vm in', 'bericht ingesproken']
    t = title.lower()
    return any(k in t for k in skip_keywords)


def heuristic_uitkomst(title, summary_md, summary_text):
    """Fallback als de AI-aanroep voor dit gesprek mislukt."""
    text = ((title or '') + ' ' + (summary_md or '') + ' ' + (summary_text or '')).lower()
    if 'geen dekking' in text or 'niet gedekt' in text or 'buiten de dekking' in text:
        return ["outcome-geen-dekking", "Geen dekking"]
    if 'brandmeester' in text:
        return ["outcome-brandmeester", "Doorverwezen naar Brandmeester"]
    if 'afkoop' in text:
        return ["outcome-afkoop", "Afkoop"]
    if 'terugbel' in text or 'belt terug' in text or 'callback' in text:
        return ["outcome-terugbel", "Terugbellen"]
    return ["outcome-opgelost", "Telefonisch opgelost"]


def heuristic_extra_flags(title, summary_md, summary_text):
    """Fallback voor de vier nieuwe booleans als de AI-aanroep mislukt.
    Bewust conservatief -- bij twijfel liever niet meetellen dan een
    fout-positief signaal geven (behalve advies_gegeven, waar we bij twijfel
    de voorzichtige aanname 'wel advies gegeven' maken zodat kwaliteitschecks
    niet stilzwijgend worden overgeslagen)."""
    text = ((title or '') + ' ' + (summary_md or '') + ' ' + (summary_text or '')).lower()
    verkeerd_verbonden = any(k in text for k in [
        'verkeerd verbonden', 'verkeerd nummer', 'autoverzekering', 'niet de juiste afdeling',
        'doorverwijzing telefoonnummer', 'menunavigatie',
    ])
    rechtsbijstand_verwijzing = 'rechtsbijstand' in text and (
        'verwijs' in text or 'doorverwijs' in text or 'overgedragen' in text
    )
    niet_gevonden = any(k in text for k in [
        'niet in het systeem', 'niet gevonden', 'kan ik niet vinden', 'kon niet vinden',
        'niet terugvinden', 'niet te vinden',
    ])
    vraagt_verificatie = any(k in text for k in [
        'verificatiedocument', 'polisblad', 'polis op te sturen', 'op te mailen',
    ])
    advies_gegeven = not (niet_gevonden and vraagt_verificatie)
    identiteit_geverifieerd = not niet_gevonden
    return verkeerd_verbonden, rechtsbijstand_verwijzing, advies_gegeven, identiteit_geverifieerd


def extract_transcript_text(detail):
    transcript = detail.get('transcript') or []
    lines = []
    for seg in transcript:
        speaker = seg.get('source') or seg.get('speaker') or ''
        text = seg.get('text') or ''
        if text:
            lines.append(f"{speaker}: {text}".strip())
    return "\n".join(lines)


ZOHO_TICKETS_PATH = 'data/zoho-tickets.json'


def load_zoho_tickets():
    """Leest data/zoho-tickets.json als dat al bestaat (wordt in de workflow
    v\u00f3\u00f3r deze stap gegenereerd door fetch_zoho_tickets.py). Geeft een lege
    lijst terug als het bestand nog niet bestaat of niet leesbaar is -- dit mag
    de Granola-analyse nooit blokkeren."""
    try:
        with open(ZOHO_TICKETS_PATH, encoding='utf-8') as f:
            data = json.load(f)
        return data.get('tickets', [])
    except Exception:
        return []


def is_real_klant_naam(klant):
    """Onbekend/e-mailadres-fallback telt niet als een echte systeemmatch."""
    if not klant or klant == 'Onbekend':
        return False
    if '@' in klant:
        return False
    return True


def find_zoho_match(tijdstip, zoho_tickets, window_minutes=25):
    """Matcht een Granola-gesprek aan een Zoho-ticket op basis van tijdsnabijheid
    (zelfde dag, binnen window_minutes). Geeft de klantnaam terug als er een
    ticket met een echte (niet-"Onbekend") contactnaam binnen het venster valt,
    anders None. Bewust een ruim venster: het ticket wordt vaak na afloop van
    het gesprek aangemaakt/bijgewerkt."""
    try:
        target = int(tijdstip[:2]) * 60 + int(tijdstip[3:5])
    except Exception:
        return None
    best = None
    best_diff = window_minutes + 1
    for t in zoho_tickets:
        klant = t.get('klant', '')
        if not is_real_klant_naam(klant):
            continue
        try:
            tt = t.get('tijd', '')
            ticket_minutes = int(tt[:2]) * 60 + int(tt[3:5])
        except Exception:
            continue
        diff = abs(ticket_minutes - target)
        if diff <= window_minutes and diff < best_diff:
            best = klant
            best_diff = diff
    return best


def main():
    if not GRANOLA_TOKEN:
        print("ERROR: GRANOLA_API_KEY not set")
        raise SystemExit(1)
    if not ANTHROPIC_KEY:
        print("ERROR: ANTHROPIC_API_KEY not set")
        raise SystemExit(1)

    today_ams = (datetime.now(timezone.utc) + AMS_OFFSET).date()
    today_str = today_ams.isoformat()

    notes = get_all_today_notes()
    zoho_tickets = load_zoho_tickets()
    if zoho_tickets:
        print(f"  -> {len(zoho_tickets)} Zoho-tickets van vandaag geladen voor contextmatching")
    else:
        print("  -> Geen Zoho-tickets beschikbaar voor contextmatching (nog niet gegenereerd of leeg)")
    conversations = []

    for note in notes:
        note_id = note.get('id', '')
        title = note.get('title') or ''
        created_at = note.get('created_at', '')

        if is_failed_call(title):
            print(f"  SKIP (failed call): {title}")
            continue

        detail = get_note_detail(note_id)
        if not detail:
            print(f"  SKIP (404): {title}")
            continue
        if not is_lancyr_note(detail):
            continue

        summary_md = detail.get('summary_markdown') or ''
        summary_text = detail.get('summary_text') or ''
        transcript_text = extract_transcript_text(detail)
        owner = detail.get('owner') or {}
        medewerker = owner.get('name') or 'Jackie Stam'

        try:
            dt_utc = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
            dt_ams = dt_utc + AMS_OFFSET
            tijdstip = dt_ams.strftime('%H:%M')
        except Exception:
            tijdstip = '00:00'

        user_content = f"Titel: {title}\n\n"
        if transcript_text:
            user_content += f"Transcript:\n{transcript_text[:12000]}"
        else:
            user_content += f"Samenvatting (geen transcript beschikbaar):\n{summary_md or summary_text}"

        zoho_match = find_zoho_match(tijdstip, zoho_tickets)
        if zoho_match:
            user_content += f"\n\nSysteeminfo: dit gesprek is in Zoho Desk gekoppeld aan contactpersoon '{zoho_match}' (automatisch herkend op telefoonnummer, polis/klantdossier bekend)."
        else:
            user_content += "\n\nSysteeminfo: geen Zoho-contactmatch gevonden voor dit tijdstip."

        ai_result = anthropic_call(ANALYZE_SYSTEM_PROMPT, user_content, max_tokens=600)

        if ai_result and ai_result.get("uitkomst") and ai_result["uitkomst"][0] in VALID_OUTCOMES:
            samenvatting = ai_result.get("samenvatting", "")
            tags = ai_result.get("tags", [["tag-overig", "Overig"]])
            uitkomst = ai_result["uitkomst"]
            terugbel = bool(ai_result.get("terugbel", uitkomst[0] == "outcome-terugbel"))
            verkeerd_verbonden = bool(ai_result.get("verkeerd_verbonden", False))
            rechtsbijstand_verwijzing = bool(ai_result.get("rechtsbijstand_verwijzing", False))
            advies_gegeven = bool(ai_result.get("advies_gegeven", True))
            identiteit_geverifieerd = bool(ai_result.get("identiteit_geverifieerd", False))
        else:
            print(f"  Fallback (heuristiek) voor: {title}")
            samenvatting = (summary_md or summary_text or '')[:300]
            tags = [["tag-overig", "Overig"]]
            uitkomst = heuristic_uitkomst(title, summary_md, summary_text)
            terugbel = uitkomst[0] == "outcome-terugbel"
            verkeerd_verbonden, rechtsbijstand_verwijzing, advies_gegeven, identiteit_geverifieerd = heuristic_extra_flags(title, summary_md, summary_text)
            if zoho_match:
                identiteit_geverifieerd = True

        # Naam van de verzekerde: gebruik NOOIT de Granola/ASR-transcriptie hiervoor
        # (spraakherkenning verhaspelt namen regelmatig, bijv. "Fletse Noe" i.p.v. de
        # echte naam). Gebruik in plaats daarvan de naam uit de gematchte Zoho Desk-
        # ticket (betrouwbaar, want handmatig/systematisch vastgelegd). Zonder match
        # blijft de naam "anoniem" i.p.v. een mogelijk foutieve gok.
        klant_naam = zoho_match if zoho_match else "anoniem"
        conversations.append({
            "tijd": tijdstip,
            "titel": title,
            "personen": f"Verzekerde: {klant_naam} · {medewerker}",
            "samenvatting": samenvatting,
            "tags": tags,
            "uitkomst": uitkomst,
            "terugbel": terugbel,
            "verkeerdVerbonden": verkeerd_verbonden,
            "rechtsbijstandVerwijzing": rechtsbijstand_verwijzing,
            "adviesGegeven": advies_gegeven,
            "identiteitGeverifieerd": identiteit_geverifieerd,
        })
        print(f"  OK {tijdstip} | {uitkomst[1]:30s} | {title[:50]}")

    conversations.sort(key=lambda c: c["tijd"])

    stats = {"opgelost": 0, "brandmeester": 0, "afkoop": 0, "terugbel": 0, "geenDekking": 0}
    key_map = {
        "outcome-opgelost": "opgelost",
        "outcome-brandmeester": "brandmeester",
        "outcome-afkoop": "afkoop",
        "outcome-terugbel": "terugbel",
        "outcome-geen-dekking": "geenDekking",
    }
    for c in conversations:
        k = key_map.get(c["uitkomst"][0])
        if k:
            stats[k] += 1

    total = len(conversations)

    # Nieuwe aggregaties: verkeerd verbonden %, rechtsbijstand-doorverwijzingen,
    # en een verdeling per rechtsgebied-tag ("onderwerpen").
    verkeerd_verbonden_count = sum(1 for c in conversations if c["verkeerdVerbonden"])
    rechtsbijstand_count = sum(1 for c in conversations if c["rechtsbijstandVerwijzing"])
    verkeerd_verbonden_pct = round(100 * verkeerd_verbonden_count / total, 1) if total else 0.0

    categorieen = {}
    for c in conversations:
        for tag in c["tags"]:
            if len(tag) == 2:
                _, label = tag
                categorieen[label] = categorieen.get(label, 0) + 1
    # Sorteer op aantal, hoogste eerst — makkelijker te renderen zonder dat
    # de frontend zelf nog moet sorteren.
    categorieen_sorted = sorted(categorieen.items(), key=lambda kv: kv[1], reverse=True)

    if total >= 6:
        drukte = "druk"
    elif total >= 4:
        drukte = "matig"
    else:
        drukte = "rustig"

    score = 0
    goed, beter = [], []
    if conversations:
        day_input = json.dumps([
            {"tijd": c["tijd"], "titel": c["titel"], "samenvatting": c["samenvatting"], "uitkomst": c["uitkomst"][1], "advies_gegeven": c["adviesGegeven"], "identiteit_geverifieerd": c["identiteitGeverifieerd"]}
            for c in conversations
        ], ensure_ascii=False)
        day_result = anthropic_call(DAY_SYSTEM_PROMPT, day_input, max_tokens=1200)
        if day_result:
            score = day_result.get("score", 0)
            goed = day_result.get("goed", [])
            beter = day_result.get("beter", [])
        else:
            print("  WAARSCHUWING: dag-analyse mislukt, score/analyse blijven leeg")

    result = {
        "date": today_str,
        "generated_at": datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        "drukte": drukte,
        "gesprekken": total,
        "stats": stats,
        "score": score,
        "conversations": conversations,
        "acties": [],
        "analyse": {"goed": goed, "beter": beter},
        "verkeerdVerbonden": {"aantal": verkeerd_verbonden_count, "percentage": verkeerd_verbonden_pct},
        "rechtsbijstandVerwijzingen": rechtsbijstand_count,
        "categorieen": categorieen_sorted,
    }

    os.makedirs('data', exist_ok=True)
    with open(OUTPUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"\nSaved {len(conversations)} Lancyr entries (score {score}) to {OUTPUT_PATH}")


if __name__ == '__main__':
    main()
