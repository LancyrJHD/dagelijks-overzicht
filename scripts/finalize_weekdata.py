#!/usr/bin/env python3
"""Bakt data/today.json permanent in als weekData[date] in index.html.

data/today.json wordt door analyze_granola_ai.py elke 30 minuten opnieuw
gegenereerd en overschreven. Zonder deze stap gaat de inhoud van elke dag
verloren zodra de volgende dag begint -- de historie werd tot nu toe alleen
permanent vastgelegd door een lokale Cowork-taak (17:30, alleen als de
laptop aan staat). Dit script draait in .github/workflows/update-dagrapport.yml
op elke cron-run, dus de hele keten (Zoho/Zoom/Granola-analyse + permanente
weekData-historie) draait voortaan volledig op GitHub's servers.

Zelfde veilige aanpak als tools/lancyr_safe_update.py: leest, valideert met
Node.js voor EN na de wijziging, schrijft alleen bij succes. Idempotent --
overschrijft gewoon dezelfde dag opnieuw als hij al bestaat.
"""
import json
import os
import subprocess
import sys
import tempfile

TODAY_JSON = 'data/today.json'
INDEX_PATH = 'index.html'
JS_RUNTIME = os.environ.get('JS_RUNTIME', 'node')  # override naar 'bun' etc. voor lokaal testen


def validate_weekdata(html_content):
    """Extraheer en valideer het weekData-blok met Node.js. Gooit exception bij fout."""
    idx_wd = html_content.find('const weekData = {')
    idx_mo = html_content.find('const MANUAL_OUTCOMES')
    if idx_wd < 0 or idx_mo < 0:
        raise ValueError('weekData of MANUAL_OUTCOMES niet gevonden!')

    wd_block = html_content[idx_wd:idx_mo]
    test_js = wd_block + """
const keys = Object.keys(weekData);
if (keys.length === 0) throw new Error('weekData leeg!');
for (const k of keys) {
  const d = weekData[k];
  if (typeof d.gesprekken !== 'number') throw new Error('gesprekken missing in ' + k);
  if (!Array.isArray(d.conversations)) throw new Error('conversations missing in ' + k);
}
console.log('OK', keys.length, 'dagen. Laatste:', keys[keys.length - 1]);
"""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.js', delete=False) as f:
        f.write(test_js)
        tmpfile = f.name
    try:
        result = subprocess.run([JS_RUNTIME, tmpfile], capture_output=True, text=True, timeout=15)
        if result.returncode != 0:
            raise ValueError(f'{JS_RUNTIME} validatie faalde:\n{result.stderr}')
        print('  validatie:', result.stdout.strip())
    finally:
        os.unlink(tmpfile)


def build_entry(day):
    entry = {
        'drukte': day.get('drukte', 'rustig'),
        'gesprekken': day.get('gesprekken', len(day.get('conversations', []))),
        'stats': day.get('stats') or {'opgelost': 0, 'brandmeester': 0, 'afkoop': 0, 'terugbel': 0, 'geenDekking': 0},
        'score': day.get('score', 0),
        'conversations': day.get('conversations', []),
        'acties': day.get('acties', []),
        'analyse': day.get('analyse') or {'goed': [], 'beter': []},
    }
    return json.dumps(entry, ensure_ascii=False)


def upsert(content, date_key_raw, entry_json):
    datum_key = f'"{date_key_raw}"'
    idx_mo = content.find('\n\nconst MANUAL_OUTCOMES')
    if idx_mo < 0:
        raise ValueError('MANUAL_OUTCOMES marker niet gevonden')

    if datum_key in content[:idx_mo]:
        # Bestaat al -- vervang de bestaande entry (idempotent overschrijven)
        idx_datum = content.find(datum_key + ': {')
        if idx_datum < 0:
            idx_datum = content.find(datum_key + ':{')
        if idx_datum < 0:
            raise ValueError(f'Kan bestaande entry voor {date_key_raw} niet lokaliseren')

        start = content.find('{', idx_datum)
        depth = 0
        end = start
        for i, ch in enumerate(content[start:], start):
            if ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break

        after = content[end:end + 3].strip()
        has_comma = after.startswith(',')
        new_entry = f'{datum_key}: {entry_json}'
        if has_comma:
            comma_end = content.find(',', end) + 1
            content = content[:idx_datum] + new_entry + content[comma_end:]
        else:
            content = content[:idx_datum] + new_entry + content[end:]
    else:
        chunk = content[:idx_mo]
        stripped_end = chunk.rstrip()
        if not stripped_end.endswith('}'):
            raise ValueError(f'Onverwacht einde weekData-blok: {stripped_end[-50:]!r}')

        last_brace_idx = len(stripped_end) - 1
        body_before_close = stripped_end[:last_brace_idx].rstrip()
        if body_before_close.endswith('}'):
            sep = ','
        elif body_before_close.endswith('},'):
            sep = ''
        else:
            raise ValueError(f'Onverwacht einde vorige entry: {body_before_close[-80:]!r}')

        new_tail = body_before_close + sep + '\n' + f'{datum_key}: {entry_json}\n' + content[last_brace_idx:idx_mo]
        content = content[:content.find(body_before_close)] + new_tail + content[idx_mo:]
    return content


def main():
    if not os.path.exists(TODAY_JSON):
        print(f'{TODAY_JSON} bestaat niet, niets te doen.')
        return
    with open(TODAY_JSON, 'r', encoding='utf-8') as f:
        day = json.load(f)

    date_key = day.get('date')
    if not date_key:
        print('data/today.json heeft geen "date" veld, sla over.')
        return

    with open(INDEX_PATH, 'r', encoding='utf-8') as f:
        content = f.read()

    print('Stap 1: valideer huidige index.html...')
    validate_weekdata(content)

    entry_json = build_entry(day)
    new_content = upsert(content, date_key, entry_json)

    print('Stap 2: valideer nieuwe index.html...')
    validate_weekdata(new_content)

    if new_content == content:
        print('Geen wijziging nodig.')
        return

    with open(INDEX_PATH, 'w', encoding='utf-8') as f:
        f.write(new_content)
    print(f'weekData["{date_key}"] bijgewerkt ({len(day.get("conversations", []))} gesprekken, score {day.get("score", 0)}).')


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print(f'FOUT: {e}', file=sys.stderr)
        sys.exit(1)
