# -*- coding: utf-8 -*-
"""Analyse A FROID de la telemetrie du Shadow Reader (memory/shadow_read_v2.jsonl).

Quantifie le taux de DISTORSION reel entre l'ancien retrieval (embedder anglais) et
le temoin multilingue sur les vraies requetes de production. A lancer quand le log a
accumule du volume. Usage : python analyze_shadow.py [chemin_du_jsonl]
"""
import json
import sys
from collections import Counter

DEFAULT = r"C:\MesProjets\PROMETHEE_V11_restructuration2026\memory\shadow_read_v2.jsonl"


def analyze(path):
    try:
        recs = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
    except FileNotFoundError:
        print(f"Aucune telemetrie ({path} absent).")
        return
    n = len(recs)
    if n == 0:
        print("Telemetrie vide.")
        return

    mismatch = sum(1 for r in recs if r.get("mismatch"))
    overlaps = [int(r.get("overlap", 0)) for r in recs]
    lats = sorted(float(r.get("lat_new_ms", 0) or 0) for r in recs)
    dist = Counter(overlaps)
    avg_ov = sum(overlaps) / n

    print("=" * 60)
    print(f"TELEMETRIE SHADOW READER — {n} comparaison(s)")
    print("=" * 60)
    print(f"  Taux de MISMATCH    : {mismatch}/{n}  ({100*mismatch/n:.0f}%)")
    print(f"  Overlap moyen       : {avg_ov:.2f} doc(s) en commun / top-k")
    print(f"  Distribution overlap: " + ", ".join(f"{k}->{dist[k]}" for k in sorted(dist)))
    if lats:
        print(f"  Latence temoin      : moy {sum(lats)/n:.0f}ms | med {lats[n//2]:.0f}ms | max {lats[-1]:.0f}ms")
    # interpretation
    div_total = dist.get(0, 0)
    print(f"\n  >>> {div_total}/{n} requetes ({100*div_total/n:.0f}%) en DIVERGENCE TOTALE (0 doc commun).")
    if avg_ov < 1.0:
        print("  >>> L'exil linguistique est SEVERE : l'anglais et le multilingue voient")
        print("      des memoires quasi disjointes. La bascule multilingue change radicalement le RAG.")
    elif avg_ov < 2.0:
        print("  >>> Distorsion FORTE : recouvrement partiel, le multilingue reordonne nettement.")
    else:
        print("  >>> Distorsion MODEREE : recouvrement frequent.")

    div = [r for r in recs if int(r.get("overlap", 0)) == 0][:6]
    if div:
        print("\n  Echantillons de divergence totale :")
        for r in div:
            print(f"    Q: {(r.get('query') or '')[:66]}")
    print("=" * 60)


if __name__ == "__main__":
    analyze(sys.argv[1] if len(sys.argv) > 1 else DEFAULT)
