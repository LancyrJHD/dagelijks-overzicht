#!/usr/bin/env python3
"""
Fetch Lancyr Juridische Helpdesk meetings from Granola API
and generate data/today.json for the dagrapport dashboard.

Requires: GRANOLA_API_KEY environment variable
"""
import os
import json
import requests
from datetime import datetime, date, timezone, timedelta

GRANOLA_TOKEN = os.environ.get('GRANOLA_API_KEY', '')
LANCYR_FOLDER_NAME = 'Lancyr Juridische Helpdesk'
BASE_URL = 'https://public-api.granola.ai/v1'
OUTPUT_PATH = 'data/today.json'

# Amsterdam offset: UTC+2 (CEST summer) — good enough for business hours
AMS_OFFSET = timedelta(hours=2)

HEADERS = {
    'Authorization': f'Bearer {GRANOLA_TOKEN}',
    'Content-Type': 'application/json',
}

UITKOMST_MAP = {
    'telefonisch-opgelost': ['outcome-opgelost', 'Telefonisch opgelost'],
    'terugbellen':          ['outcome-terugbel', 'Terugbellen'],
    'geen-dekking':         ['outcome-geendekking', 'Geen dekking'],
    'doorverbonden':        ['outcome-brandmeester', 'Doorverwezen naar Brandmeester'],
}


def get_all_today_notes():
    """Fetch all notes created today (Amsterdam time)."""
    today_ams = (datetime.now(timezone.utc) + AMS_OFFSET).date()
    today_str = today_ams.isoformat()
    print(f"Fetching notes for {today_str} (Amsterdam)...")

    notes = []
    cursor = None
    while True:
        params = {'page_size': 30, 'created_after': today_str}
        if cursor:
            params['cursor'] = cursor
        resp = requests.get(f'{BASE_URL}/notes', headers=HEADERS, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        notes.extend(data.get('notes', []))
        if not data.get('hasMore'):
            break
        cursor = data.get('cursor')

    print(f"  → {len(notes)} total notes today")
    return notes


def get_note_detail(note_id):
    """Get full note including summary and folder membership."""
    resp = requests.get(
        f'{BASE_URL}/notes/{note_id}',
        headers=HEADERS,
        params={'include': 'transcript'},
        timeout=30
    )
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()


def is_lancyr_note(detail):
    """Check if note is in the Lancyr Juridische Helpdesk folder."""
    for folder in (detail.get('folder_membership') or []):
        if LANCYR_FOLDER_NAME in folder.get('name', ''):
            return True
    return False


def is_failed_call(title):
    """Detect failed calls (VM, no answer, etc.) that should be skipped."""
    skip_keywords = ['vm uitgeschakeld', 'voicemail', 'geen gehoor', 'niet opgenomen',
                     'gebeld maar', 'vm in', 'bericht ingesproken']
    t = title.lower()
    return any(k in t for k in skip_keywords)


def extract_uitkomst(title, summary_md, summary_text):
    """Classify meeting outcome using keyword heuristics."""
    text = ((title or '') + ' ' + (summary_md or '') + ' ' + (summary_text or '')).lower()

    if 'geen dekking' in text or 'niet gedekt' in text or 'valt niet onder de polis' in text \
            or 'buiten de dekking' in text or 'geen dekking' in text:
        return 'geen-dekking'

    if 'doorverwezen naar brandmeester' in text or 'intakegesprek brandmeester' in text \
            or 'brandmeester' in text:
        return 'doorverbonden'

    if 'terugbel' in text or 'terug te bellen' in text or 'teruggebeld' in text \
            or 'callback' in text or 'belt terug' in text or 'belt u terug' in text:
        return 'terugbellen'

    # Default: resolved by phone
    return 'telefonisch-opgelost'


def extract_duration_minutes(detail):
    """Estimate call duration from transcript or calendar event."""
    transcript = detail.get('transcript') or []
    if len(transcript) >= 2:
        try:
            t0 = datetime.fromisoformat(transcript[0]['start_time'].replace('Z', '+00:00'))
            t1 = datetime.fromisoformat(transcript[-1]['end_time'].replace('Z', '+00:00'))
            return max(1, round((t1 - t0).total_seconds() / 60))
        except Exception:
            pass

    cal = detail.get('calendar_event') or {}
    start = cal.get('scheduled_start_time')
    end = cal.get('scheduled_end_time')
    if start and end:
        try:
            t0 = datetime.fromisoformat(start.replace('Z', '+00:00'))
            t1 = datetime.fromisoformat(end.replace('Z', '+00:00'))
            return max(1, round((t1 - t0).total_seconds() / 60))
        except Exception:
            pass

    return 10  # fallback default


def main():
    if not GRANOLA_TOKEN:
        print("ERROR: GRANOLA_API_KEY not set")
        exit(1)

    today_ams = (datetime.now(timezone.utc) + AMS_OFFSET).date()
    today_str = today_ams.isoformat()

    notes = get_all_today_notes()
    entries = []

    for note in notes:
        note_id = note.get('id', '')
        title = note.get('title') or ''
        created_at = note.get('created_at', '')

        # Skip VM/failed calls
        if is_failed_call(title):
            print(f"  SKIP (failed call): {title}")
            continue

        # Get full detail
        detail = get_note_detail(note_id)
        if not detail:
            print(f"  SKIP (404): {title}")
            continue

        # Only Lancyr folder
        if not is_lancyr_note(detail):
            continue

        summary_md = detail.get('summary_markdown') or ''
        summary_text = detail.get('summary_text') or ''

        uitkomst = extract_uitkomst(title, summary_md, summary_text)

        # Time in Amsterdam
        try:
            dt_utc = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
            dt_ams = dt_utc + AMS_OFFSET
            tijdstip = dt_ams.strftime('%H:%M')
        except Exception:
            tijdstip = '00:00'

        duur = extract_duration_minutes(detail)

        owner = detail.get('owner') or {}
        medewerker = owner.get('name') or 'Jackie Stam'

        # Extract a short action note from summary if available
        notitie = ''
        if summary_md:
            lines = [l.strip() for l in summary_md.split('\n') if l.strip()]
            # Take first non-header line as notitie
            for line in lines:
                if not line.startswith('#') and len(line) > 10:
                    notitie = line[:200]
                    break

        entry = {
            'id': note_id,
            'tijdstip': tijdstip,
            'titel': title,
            'uitkomst': uitkomst,
            'duur': duur,
            'medewerker': medewerker,
            'notitie': notitie,
            'terugbel': uitkomst == 'terugbellen',
        }
        entries.append(entry)
        print(f"  ✓ {tijdstip} | {uitkomst:25s} | {title[:60]}")

    # Sort chronologically
    entries.sort(key=lambda x: x['tijdstip'])

    result = {
        'date': today_str,
        'generated_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'entries': entries,
    }

    os.makedirs('data', exist_ok=True)
    with open(OUTPUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"\n✅ Saved {len(entries)} Lancyr entries to {OUTPUT_PATH}")


if __name__ == '__main__':
    main()
