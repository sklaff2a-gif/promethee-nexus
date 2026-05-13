"""Etape 0 Phase C — Analyse statistique des sessions Alfred et Stefan.

Objectif : confirmer ou infirmer la pathologie de fréquence/durée annoncée
le 09/05. Critère Gemini Q6 : pathologie confirmée si ratio_doublons > 20%
ET durée_moyenne < 5s.

Parsing :
  - Alfred : logs/coffee_breaks/cafe_YYYY-MM-DD.md (sections par café)
  - Stefan : logs/confrontations/confrontation_YYYY-MM-DD.txt (blocs ====)

Sortie : stats globales + détection des pathologies (doublons, troncatures,
compteurs cassés, conversations à 0s).
"""
from __future__ import annotations

import os
import re
import statistics
import sys
from collections import Counter
from glob import glob
from typing import List, Dict, Optional

LOGS = r"C:\MesProjets\PROMETHEE_V11_restructuration2026\logs"


# ============================================================
# Parsing Alfred (cafes_*.md)
# ============================================================

ALFRED_SECTION = re.compile(
    r"##\s+(\d{2}:\d{2})\s+—\s+Café avec Alfred\s*\n"
    r"\s*\n"
    r"-\s*\*\*Sujet de départ\*\*\s*:\s*([^\n]+)\n"
    r"-\s*\*\*Échanges\*\*\s*:\s*(\d+)\s*\|\s*\*\*Durée\*\*\s*:\s*(\d+)s",
    re.UNICODE,
)


def parse_alfred_file(path: str) -> List[Dict]:
    """Extrait toutes les sections café d'un fichier alfred markdown."""
    date = os.path.basename(path).replace("cafe_", "").replace(".md", "")
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    sessions = []
    for m in ALFRED_SECTION.finditer(content):
        time_str, sujet, echanges, duree = m.groups()
        sessions.append({
            "date": date,
            "time": time_str,
            "sujet": sujet.strip(),
            "echanges": int(echanges),
            "duree_s": int(duree),
        })
    return sessions


# ============================================================
# Parsing Stefan (confrontation_*.txt)
# ============================================================

STEFAN_BLOCK = re.compile(
    r"=+\s*\n"
    r"\[(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})\]\s+Confrontation\s+#(\d+)\s*\n"
    r"Source:\s*(\w+)\s*\n"
    r"Prométhée a dit:\s*\n"
    r"(.+?)\n"
    r"\n"
    r"Stefan demande:\s*\n"
    r"(.+?)\n"
    r"=+",
    re.DOTALL,
)


def parse_stefan_file(path: str) -> List[Dict]:
    """Extrait tous les blocs confrontation."""
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    sessions = []
    for m in STEFAN_BLOCK.finditer(content):
        timestamp, num, source, prom_said, stefan_ask = m.groups()
        sessions.append({
            "timestamp": timestamp.strip(),
            "confrontation_num": int(num),
            "source": source.strip(),
            "promethee_said": prom_said.strip(),
            "stefan_asked": stefan_ask.strip(),
            "truncated": not stefan_ask.strip().endswith((".", "?", "!", "...")),
        })
    return sessions


# ============================================================
# Stats helpers
# ============================================================

def safe_stats(values: List[float]) -> Dict:
    if not values:
        return {"n": 0}
    return {
        "n": len(values),
        "mean": round(statistics.mean(values), 2),
        "median": round(statistics.median(values), 2),
        "stdev": round(statistics.stdev(values), 2) if len(values) > 1 else 0,
        "min": min(values),
        "max": max(values),
    }


# ============================================================
# Main
# ============================================================

def main():
    print("=" * 80)
    print("ETAPE 0 — Analyse statistique sessions Alfred & Stefan")
    print("=" * 80)

    # --- Alfred ---
    alfred_files = sorted(glob(os.path.join(LOGS, "coffee_breaks", "cafe_*.md")))
    print(f"\nAlfred : {len(alfred_files)} fichiers (jours avec cafés)")

    all_alfred = []
    for path in alfred_files:
        all_alfred.extend(parse_alfred_file(path))

    print(f"  Total sessions café parsées : {len(all_alfred)}")

    if all_alfred:
        durees = [s["duree_s"] for s in all_alfred]
        echanges = [s["echanges"] for s in all_alfred]
        sujets = [s["sujet"] for s in all_alfred]

        print(f"\n  ── Durée (secondes) ──")
        stats = safe_stats(durees)
        for k, v in stats.items():
            print(f"    {k:8s} : {v}")
        durees_0s = sum(1 for d in durees if d == 0)
        durees_under5 = sum(1 for d in durees if d < 5)
        print(f"    durée=0s : {durees_0s}/{len(durees)} ({100*durees_0s/len(durees):.0f}%)")
        print(f"    durée<5s : {durees_under5}/{len(durees)} ({100*durees_under5/len(durees):.0f}%)")

        print(f"\n  ── Échanges ──")
        stats = safe_stats(echanges)
        for k, v in stats.items():
            print(f"    {k:8s} : {v}")

        print(f"\n  ── Top 10 sujets (détection répétitions) ──")
        sujets_count = Counter(sujets)
        doublons_alfred = sum(1 for c in sujets_count.values() if c > 1)
        sujets_repetes = sum(c for c in sujets_count.values() if c > 1)
        for sujet, count in sujets_count.most_common(10):
            tag = " ⚠️ RÉPÉTÉ" if count > 1 else ""
            print(f"    {count}x  {sujet[:60]}{tag}")
        ratio_doublons = sujets_repetes / len(sujets) if sujets else 0
        print(f"    Total sujets uniques : {len(sujets_count)}/{len(sujets)}")
        print(f"    Ratio sujets répétés : {100*ratio_doublons:.1f}%")

    # --- Stefan ---
    stefan_files = sorted(glob(os.path.join(LOGS, "confrontations", "confrontation_*.txt")))
    print(f"\n\nStefan : {len(stefan_files)} fichiers (jours avec confrontations)")

    all_stefan = []
    for path in stefan_files:
        all_stefan.extend(parse_stefan_file(path))

    print(f"  Total blocs confrontation parsés : {len(all_stefan)}")

    if all_stefan:
        # Compteur cassé : tous les #N à 1 = bug
        nums = [s["confrontation_num"] for s in all_stefan]
        print(f"\n  ── Compteurs confrontation_num ──")
        nums_count = Counter(nums)
        for n, c in sorted(nums_count.items()):
            print(f"    #{n} : {c} occurrences")
        if list(nums_count.keys()) == [1]:
            print(f"    ⚠️ TOUS LES BLOCS ONT #1 → compteur cassé (incrémentation HS)")

        # Doublons de timestamps (duplication rapprochée)
        timestamps = [s["timestamp"] for s in all_stefan]
        ts_count = Counter(timestamps)
        doublons_ts = sum(1 for c in ts_count.values() if c > 1)
        ts_dupliques = sum(c for c in ts_count.values() if c > 1)
        print(f"\n  ── Doublons timestamps (cooldown bypassé?) ──")
        print(f"    Timestamps uniques : {len(ts_count)}/{len(timestamps)}")
        for ts, c in ts_count.most_common(5):
            if c > 1:
                print(f"    {c}x  {ts}  ⚠️ DUPLIQUÉ")

        # Doublons de prométhée_said (Stefan se redéclenche sur même affirmation)
        saids = [s["promethee_said"] for s in all_stefan]
        saids_count = Counter(saids)
        print(f"\n  ── Doublons affirmations Prométhée (boucle?) ──")
        for said, c in saids_count.most_common(3):
            if c > 1:
                print(f"    {c}x  '{said[:80]}...'  ⚠️ MÊME AFFIRMATION")

        # Troncatures
        tronquees = sum(1 for s in all_stefan if s["truncated"])
        print(f"\n  ── Troncatures (questions coupées) ──")
        print(f"    Questions tronquées : {tronquees}/{len(all_stefan)} ({100*tronquees/len(all_stefan):.0f}%)")
        if tronquees > 0:
            print(f"    Exemples :")
            for s in all_stefan[:3]:
                if s["truncated"]:
                    print(f"      '{s['stefan_asked'][:60]}'")

    # --- Verdict ---
    print("\n" + "=" * 80)
    print("VERDICT — Critère Gemini Q6")
    print("=" * 80)

    if all_alfred:
        durees = [s["duree_s"] for s in all_alfred]
        mean_duree = statistics.mean(durees)
        sujets = [s["sujet"] for s in all_alfred]
        sujets_count = Counter(sujets)
        sujets_repetes_n = sum(c for c in sujets_count.values() if c > 1)
        ratio = sujets_repetes_n / len(sujets)

        critere_doublons = ratio > 0.20
        critere_duree = mean_duree < 5.0

        print(f"\nAlfred :")
        print(f"  ratio_doublons = {100*ratio:.1f}%  (seuil > 20% : {'OUI' if critere_doublons else 'NON'})")
        print(f"  durée_moyenne  = {mean_duree:.1f}s  (seuil < 5s : {'OUI' if critere_duree else 'NON'})")
        if critere_doublons and critere_duree:
            print(f"  → PATHOLOGIE CONFIRMÉE (Gemini Q6)")
        elif critere_doublons or critere_duree:
            print(f"  → PATHOLOGIE PARTIELLE (un critère sur deux)")
        else:
            print(f"  → Pas de pathologie statistique selon les critères Gemini")

    if all_stefan:
        nums = [s["confrontation_num"] for s in all_stefan]
        nums_distincts = len(set(nums))
        print(f"\nStefan :")
        print(f"  Compteur confrontation_num : {nums_distincts} valeur(s) distincte(s)")
        if nums_distincts == 1 and len(all_stefan) > 1:
            print(f"  → BUG STRUCTUREL : compteur ne s'incrémente jamais")
        timestamps = [s["timestamp"] for s in all_stefan]
        ts_count = Counter(timestamps)
        doublons_ts_n = sum(c for c in ts_count.values() if c > 1)
        print(f"  Doublons timestamps : {doublons_ts_n}/{len(timestamps)}")
        if doublons_ts_n > 0:
            print(f"  → BUG STRUCTUREL : cooldown 6h non respecté")


if __name__ == "__main__":
    sys.exit(main())
