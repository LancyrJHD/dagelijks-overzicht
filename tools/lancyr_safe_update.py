#!/usr/bin/env python3
"""
Lancyr dagrapport — veilige weekData updater
============================================
Gebruik: python3 lancyr_safe_update.py

Dit script voegt een weekData-entry toe aan index.html.
Het valideert ALTIJD met Node.js voor het schrijft.
Bij elke fout stopt het en schrijft NIET.

Aanpassen: vul DATUM en ENTRY_DATA hieronder in.
"""

import subprocess, sys, re, shutil, os, tempfile
from datetime import datetime

INDEX_PATH = '/Users/Jackie/Documents/Claude/Artifacts/lancyr-dagrapport/index.html'

# ─────────────────────────────────────────────────────────────
# STAP 1: Pas dit aan voor de dag die je wil toevoegen/updaten
# ─────────────────────────────────────────────────────────────
DATUM = "2026-07-01"  # pas aan

ENTRY_DATA = """{
  drukte: "rustig",
  gesprekken: 0,
  stats: { opgelost: 0, brandmeester: 0, afkoop: 0, terugbel: 0, geenDekking: 0 },
  score: 0,
  conversations: [],
  acties: [],
  analyse: { goed: [], beter: [] }
}"""

# ─────────────────────────────────────────────────────────────
# Validatiefunctie — gebruik Node.js om JS te parsen
# ─────────────────────────────────────────────────────────────
def validate_weekdata(html_content):
    """Extraheer en valideer weekData block met Node.js. Gooit exception bij fout."""
    idx_wd = html_content.find('const weekData = {')
    idx_mo = html_content.find('const MANUAL_OUTCOMES')
    if idx_wd < 0 or idx_mo < 0:
        raise ValueError("weekData of MANUAL_OUTCOMES niet gevonden!")

    wd_block = html_content[idx_wd:idx_mo]

    test_js = wd_block + """
const keys = Object.keys(weekData);
if (keys.length === 0) throw new Error('weekData leeg!');
for (const k of keys) {
  const d = weekData[k];
  if (typeof d.gesprekken !== 'number') throw new Error('gesprekken missing in ' + k);
  if (!Array.isArray(d.conversations)) throw new Error('conversations missing in ' + k);
}
console.log('OK gesprekken:', keys.length, 'dagen. Laatste:', keys[keys.length-1]);
"""

    with tempfile.NamedTemporaryFile(mode='w', suffix='.js', delete=False) as f:
        f.write(test_js)
        tmpfile = f.name

    try:
        result = subprocess.run(
            ['node', tmpfile],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0:
            raise ValueError(f"Node.js fout:\n{result.stderr}")
        print(f"  Node.js validatie: {result.stdout.strip()}")
    finally:
        os.unlink(tmpfile)


# ─────────────────────────────────────────────────────────────
# Hoofdfunctie
# ─────────────────────────────────────────────────────────────
def main():
    print(f"Lancyr safe update — datum: {DATUM}")

    # 1. Lees file
    with open(INDEX_PATH, 'r', encoding='utf-8') as f:
        content = f.read()
    print(f"File geladen: {len(content)} chars")

    # 2. Valideer HUIDIGE file eerst
    print("Stap 1: valideer huidige file...")
    validate_weekdata(content)

    # 3. Controleer of datum al bestaat
    datum_key = f'"{DATUM}"'
    if datum_key in content:
        print(f"\nWaarschuwing: {DATUM} bestaat al in weekData.")
        print("Bestaande entry wordt VERVANGEN.")

        # Vind de entry en vervang hem
        idx_datum = content.find(datum_key + ': {')
        if idx_datum < 0:
            idx_datum = content.find(datum_key + ':{')
        if idx_datum < 0:
            raise ValueError(f"Kan entry voor {DATUM} niet vinden om te vervangen")

        # Vind het einde van deze entry door braces te tellen
        start = content.find('{', idx_datum)
        depth = 0
        end = start
        for i, ch in enumerate(content[start:], start):
            if ch == '{': depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break

        # Bepaal of er een comma na de entry staat
        after = content[end:end+3].strip()
        has_comma = after.startswith(',')

        new_entry = f'{datum_key}: {ENTRY_DATA.strip()}'

        if has_comma:
            # Vervang entry + comma
            comma_end = content.find(',', end) + 1
            content = content[:idx_datum] + new_entry + content[comma_end:]
        else:
            content = content[:idx_datum] + new_entry + content[end:]

    else:
        print(f"\nStap 2: voeg {DATUM} toe aan weekData...")

        # Vind het einde van de vorige entry (de laatste entry voor weekData sluit)
        # Patroon: de twee afsluitende } voor \n\nconst MANUAL_OUTCOMES
        # nl: laatste entry sluit met }\n dan weekData sluit met }\n\n
        idx_mo = content.find('\n\nconst MANUAL_OUTCOMES')

        # Vind patroon: \n}\n} vlak voor \n\nconst MANUAL_OUTCOMES
        # Dit is: \n} (weekData sluit) vlak voor MANUAL_OUTCOMES
        # De entry daarvoor sluit met \n} gevolgd door ,\n of \n

        # Meest betrouwbare aanpak: voeg toe VOOR de weekData-sluitende }
        # Zoek de LAATSTE } voor MANUAL_OUTCOMES
        chunk = content[:idx_mo]
        # Trim whitespace at end
        stripped_end = chunk.rstrip()
        # The last } closes weekData
        if not stripped_end.endswith('}'):
            raise ValueError(f"Unexpected: weekData block ends with: {repr(stripped_end[-50:])}")

        # Alles tot en met de op-een-na-laatste } is de body
        last_brace_idx = len(stripped_end) - 1
        # Check: de entry daarvoor moet NIET eindigen op , al (want we voegen nieuwe toe als laatste)
        body_before_weekdata_close = stripped_end[:last_brace_idx].rstrip()

        if body_before_weekdata_close.endswith('}'):
            # Voeg comma toe na vorige entry en voeg nieuwe toe
            new_tail = (
                body_before_weekdata_close + ',\n' +
                f'{datum_key}: {ENTRY_DATA.strip()}\n' +
                content[last_brace_idx:idx_mo]
            )
        elif body_before_weekdata_close.endswith('},'):
            # Vorige entry heeft al comma
            new_tail = (
                body_before_weekdata_close + '\n' +
                f'{datum_key}: {ENTRY_DATA.strip()}\n' +
                content[last_brace_idx:idx_mo]
            )
        else:
            raise ValueError(f"Onverwacht einde van entry: {repr(body_before_weekdata_close[-80:])}")

        content = content[:content.find(body_before_weekdata_close)] + new_tail + content[idx_mo:]

    # 4. Valideer NIEUWE file met Node.js (kritiek!)
    print("Stap 3: valideer nieuwe file met Node.js...")
    validate_weekdata(content)

    # 5. Extra controle: datum aanwezig?
    assert datum_key in content, f"{DATUM} niet gevonden na insert!"

    # 6. Backup maken
    backup_path = INDEX_PATH + f'.bak_{datetime.now().strftime("%Y%m%d_%H%M%S")}'
    shutil.copy2(INDEX_PATH, backup_path)
    print(f"Stap 4: backup gemaakt: {backup_path}")

    # 7. Schrijf file
    with open(INDEX_PATH, 'w', encoding='utf-8') as f:
        f.write(content)
    print(f"Stap 5: file geschreven ({len(content)} chars)")
    print(f"\nKlaar! Vergeet niet: git add index.html && git commit && git push origin main")


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print(f"\nFOUT: {e}")
        print("File is NIET gewijzigd.")
        sys.exit(1)
