"""Script de calibration Phase 14 — Sanity Check Local (Perroquet Syntaxique).

Usage : python tools/calibrate_bullshit_v1.py

Objectif : faire tourner les 6 signaux heuristiques (S1-S6) sur les livrables
archives dans memory/school/deliverables/, les matcher avec les entrees du
claude_journal.json (categorie satisfaction vs critique), et rapporter la
matrice de confusion par seuil.

Ne touche aucun fichier de code en production. Pur diagnostic.

Ce script ne sera pas integre en prod. Une fois les seuils calibres, on
portera les fonctions S1-S6 vers core/bullshit_detector.py (a creer).
"""
import json
import re
import sys
from pathlib import Path


PROJECT = Path(r"C:\MesProjets\PROMETHEE_V11_restructuration2026")
JOURNAL = PROJECT / "memory" / "claude_journal.json"
DELIVERABLES_DIR = PROJECT / "memory" / "school" / "deliverables"


# ============================================================================
# Heuristiques — les 6 signaux
# ============================================================================

FILLER_PATTERNS = [
    r"\bil est (?:important|crucial|essentiel|fondamental) (?:de|que)",
    r"\bil convient de (?:noter|souligner|mentionner|rappeler)",
    r"\ben conclusion\b",
    r"\ben d[ée]finitive\b",
    r"\bcela (?:dit|[ée]tant)\b",
    r"\bdans ce contexte\b",
    r"\bce qui nous am[èe]ne [àa]\b",
    r"\ben d'autres termes\b",
    r"\bpour r[ée]sumer\b",
    r"\bforce est de constater\b",
    r"\bil va sans dire\b",
    r"\bcela souligne l'importance\b",
    r"\bdans ce cadre\b",
    r"\bde mani[èe]re g[ée]n[ée]rale\b",
    r"\bnotons (?:que|[ée]galement)\b",
    r"\ben (?:outre|particulier)\b",
    r"\bil est essentiel\b",
    r"\bjoue un r[ôo]le (?:central|important|cl[ée])\b",
    r"\bc'est pourquoi\b",
]
FILLER_REGEX = re.compile("|".join(FILLER_PATTERNS), re.IGNORECASE)

NUM_RE = re.compile(r"\b\d+(?:[.,]\d+)?\b")
CAMELCASE_RE = re.compile(r"\b[A-Z][a-z]+(?:[A-Z][a-z]+)+\b")
ACRONYM_RE = re.compile(r"\b[A-Z]{2,}\b")
PATH_RE = re.compile(r"\b\w+\.(?:py|md|json|yml|yaml|toml|txt|sh|ps1|js|ts|sql)(?::\d+)?\b")
REF_RE = re.compile(r"\b[A-Z][a-z]+(?:\s+(?:et\s+al\.?|&|and)\s+[A-Z][a-z]+)?\s*[\(（]\d{4}[\)）]")
CODEBLOCK_RE = re.compile(r"```[\s\S]+?```")


def strip_header(text: str) -> str:
    """Retire le preambule (# Livrable, Note, Feedback, Challenge) pour ne
    garder que le corps produit par l'agent, pas les meta-annotations
    du professeur."""
    parts = text.split("\n---\n", 1)
    if len(parts) == 2 and len(parts[0]) < 2000:
        return parts[1]
    return text


def s1_factual_density(text: str):
    """Densite tokens factuels / 100 mots. Flag si < 2.0."""
    words = text.split()
    n = len(words)
    if n < 50:
        return 0.0, False
    count = (
        len(NUM_RE.findall(text))
        + len(CAMELCASE_RE.findall(text))
        + len(ACRONYM_RE.findall(text))
        + len(PATH_RE.findall(text))
    )
    density = count / (n / 100)
    return density, density < 2.0


def s2_mattr(text: str, window: int = 100):
    """Moving Average TTR fenetre 100. Flag si < 0.45."""
    words = [w.lower() for w in re.findall(r"\w+", text)]
    n = len(words)
    if n < 20:
        return 0.0, False
    if n < window:
        ttr = len(set(words)) / n
        return ttr, ttr < 0.45
    ratios = []
    for i in range(n - window + 1):
        ratios.append(len(set(words[i:i + window])) / window)
    mattr = sum(ratios) / len(ratios)
    return mattr, mattr < 0.45


def s3_fillers(text: str):
    """Tics / 100 mots. Flag si > 0.5."""
    words = text.split()
    n = len(words)
    if n < 50:
        return 0.0, False
    fillers = len(FILLER_REGEX.findall(text))
    ratio = fillers / (n / 100)
    return ratio, ratio > 0.5


def s4_anchoring(text: str, slot: str):
    """Ancrage slot-specifique. Return (has_anchor, flag)."""
    if slot in ("CODE_REVIEW", "WORKSHOP"):
        has = bool(CODEBLOCK_RE.search(text)) or bool(PATH_RE.search(text))
        return has, not has
    if slot == "RESEARCH":
        has_ref = bool(REF_RE.search(text))
        camel = len(CAMELCASE_RE.findall(text))
        has = has_ref or camel >= 3
        return has, not has
    return True, False  # CREATION, BULLETIN etc. skip


def s5_prompt_reuse(text: str, subject: str):
    """Recopie du sujet. Flag si > 0.25 des mots sont du prompt."""
    subject_words = {w.lower() for w in re.findall(r"\w+", subject) if len(w) > 3}
    if not subject_words:
        return 0.0, False
    text_words = [w.lower() for w in re.findall(r"\w+", text)]
    n = len(text_words)
    if n < 50:
        return 0.0, False
    reuses = sum(1 for w in text_words if w in subject_words)
    ratio = reuses / n
    return ratio, ratio > 0.25


def s6_skeleton(text: str):
    """Squelette creux (headers + bullets mono). Flag si > 1.5/100."""
    n_words = len(text.split())
    if n_words < 50:
        return 0.0, False
    headers = len(re.findall(r"^#+\s", text, re.MULTILINE))
    mono_bullets = len(re.findall(r"^\s*[-*•]\s*\S{1,30}\s*$", text, re.MULTILINE))
    ratio = (headers + mono_bullets) / (n_words / 100)
    return ratio, ratio > 1.5


WEIGHTS = {
    "s1": 0.30,  # factuel (dense = informatif)
    "s2": 0.15,  # diversite lexicale
    "s3": 0.20,  # tics
    "s4": 0.35,  # ancrage slot-specifique (poids fort)
    "s5": 0.10,  # recopie prompt
    "s6": 0.10,  # squelette creux
}
TOTAL_W = sum(WEIGHTS.values())  # 1.20


def evaluate(text: str, slot: str, subject: str) -> dict:
    body = strip_header(text)
    v1, f1 = s1_factual_density(body)
    v2, f2 = s2_mattr(body)
    v3, f3 = s3_fillers(body)
    v4, f4 = s4_anchoring(body, slot)
    v5, f5 = s5_prompt_reuse(body, subject)
    v6, f6 = s6_skeleton(body)
    flags = {"s1": f1, "s2": f2, "s3": f3, "s4": f4, "s5": f5, "s6": f6}
    flagged = sum(w for k, w in WEIGHTS.items() if flags[k])
    return {
        "score": flagged / TOTAL_W,
        "flags": flags,
        "vals": {"s1": v1, "s2": v2, "s3": v3, "s4": v4, "s5": v5, "s6": v6},
        "n_words": len(body.split()),
    }


# ============================================================================
# Matching journal → deliverables
# ============================================================================

FILE_RE = re.compile(
    r"(\d{4}-\d{2}-\d{2})_(CODE_REVIEW|RESEARCH|WORKSHOP|CREATION|BULLETIN)_(\d{4})\.md"
)
COURS_RE = re.compile(r"Cours (CODE_REVIEW|RESEARCH|WORKSHOP|CREATION|BULLETIN)\s*:")
SUBJECT_RE = re.compile(r"Sujet\s*:\s*([^\n.]+?)(?:\.\s|$)")


def find_deliverable(entry, deliverables):
    entry_date = entry["date"][:10]
    entry_hhmm = entry["date"][11:16].replace(":", "")
    m_slot = COURS_RE.search(entry["content"])
    if not m_slot:
        return None
    slot = m_slot.group(1)
    candidates = [
        f for f in deliverables
        if f.name.startswith(f"{entry_date}_{slot}_")
    ]
    if not candidates:
        return None

    def dist(f):
        m = FILE_RE.match(f.name)
        return abs(int(m.group(3)) - int(entry_hhmm)) if m else 99999

    return min(candidates, key=dist)


def main():
    journal = json.loads(JOURNAL.read_text(encoding="utf-8"))
    deliverables = list(DELIVERABLES_DIR.glob("*.md"))
    print(f"Journal : {len(journal)} entrees")
    print(f"Livrables archives : {len(deliverables)}\n")

    sains, hallucines = [], []

    for entry in journal:
        cat = entry.get("category", "")
        if cat not in ("satisfaction", "critique"):
            continue
        f = find_deliverable(entry, deliverables)
        if not f:
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        slot = COURS_RE.search(entry["content"]).group(1)
        m_sub = SUBJECT_RE.search(entry["content"])
        subject = m_sub.group(1).strip() if m_sub else ""
        r = evaluate(text, slot, subject)
        r["file"] = f.name
        r["subject"] = subject[:60]
        r["category"] = cat
        r["date"] = entry["date"][:16]
        (sains if cat == "satisfaction" else hallucines).append(r)

    print(f"Apparies : {len(sains)} sain + {len(hallucines)} hallucine\n")

    def dump(title, group):
        if not group:
            print(f"{title}: 0 echantillon\n")
            return
        scores = [r["score"] for r in group]
        print(f"{title} (n={len(group)}): "
              f"min={min(scores):.2f} avg={sum(scores)/len(scores):.2f} max={max(scores):.2f}")
        for r in sorted(group, key=lambda x: x["score"]):
            flags = "".join(k[-1] for k, v in r["flags"].items() if v) or "-"
            v = r["vals"]
            print(f"  {r['score']:.2f} flags={flags:6s} n={r['n_words']:4d} | "
                  f"s1={v['s1']:.1f} s2={v['s2']:.2f} s3={v['s3']:.1f} s4={int(v['s4'])} "
                  f"s5={v['s5']:.2f} s6={v['s6']:.1f} | {r['file']} — {r['subject'][:40]}")
        print()

    dump("SAINS (satisfaction)", sains)
    dump("HALLUCINES (critique)", hallucines)

    print("=" * 80)
    print("Matrice de confusion par seuil (positif = hallucine) :")
    print("=" * 80)
    print(f"{'seuil':>6s} | {'TP':>3s} {'FN':>3s} {'FP':>3s} {'TN':>3s} | "
          f"{'prec':>5s} {'rap':>5s} {'F1':>5s} {'acc':>5s}")
    print("-" * 80)
    for threshold in [0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60]:
        tp = sum(1 for r in hallucines if r["score"] >= threshold)
        fn = len(hallucines) - tp
        fp = sum(1 for r in sains if r["score"] >= threshold)
        tn = len(sains) - fp
        total = tp + fn + fp + tn
        prec = tp / max(1, tp + fp)
        rec = tp / max(1, tp + fn)
        f1 = 2 * prec * rec / max(1e-9, prec + rec)
        acc = (tp + tn) / max(1, total)
        print(f"{threshold:>6.2f} | {tp:>3d} {fn:>3d} {fp:>3d} {tn:>3d} | "
              f"{prec:>5.2f} {rec:>5.2f} {f1:>5.2f} {acc:>5.2f}")


if __name__ == "__main__":
    main()
