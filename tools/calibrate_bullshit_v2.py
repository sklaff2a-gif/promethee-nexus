"""Phase 14 Calibration v2 — Detecteurs structurels (D1/D2/D3).

V1 a echoue : mes 6 signaux textuels (filler, TTR, densite factuelle) sont
morts-nes parce que le Perroquet de Qwen3.5 n'est pas logorrhéique. V2
pivote vers la verite diagnostique revelee par l'analyse forensique des
20 feedbacks Claude dans mentor_state.json :

- PROMESSE NON TENUE : le plan annonce N sections, le corps livre N-k
- TRONCATION : dernieres phrases interrompues en plein mot
- DERIVE DE CIBLE : CODE_REVIEW demande file X.py, livre reasoning_protocol.py

V2 implemente 3 detecteurs determinites (D1/D2/D3) et un NETTOYAGE de la
verite-terrain : au lieu du keyword match naïf de mentor.py:150, on
reclassifie chaque livrable par regex globale sur le feedback complet
de mentor_state.json.

Usage : python tools/calibrate_bullshit_v2.py
"""
import json
import re
import sys
from pathlib import Path
from collections import Counter

PROJECT = Path(r"C:\MesProjets\PROMETHEE_V11_restructuration2026")
MENTOR_STATE = PROJECT / "memory" / "mentor_state.json"
DELIVERABLES_DIR = PROJECT / "memory" / "school" / "deliverables"


# ============================================================================
# Verite-terrain nettoyee
# ============================================================================

# Patterns qui denotent un livrable PROBLEMATIQUE d'apres le feedback Claude.
HALLU_PATTERNS = [
    r"tronqu[eé]",
    r"(?:n'|n\s*')existe pas",
    r"totalement absent",
    r"pourtant annonc",       # "pourtant annonce dans le titre"
    r"hors[- ]sujet",
    r"s'arr[eê]te (?:au milieu|en plein)",
    r"n'?a (?:rien livr[eé]|pas livr[eé])",
    r"rien livr[eé]",
    r"(?:pas|peu) le bon fichier",
    r"fichier diff[eé]rent",
    r"tu as rendu un audit de `(core/)?reasoning_protocol",
    r"tu as rendu (?:une|un) (?:note|synth)",
    r"confonds? la cible",
    r"ne porte pas sur le (?:bon )?fichier",
    r"d[eé]river? du sujet",
    r"interrompu?e? en plein",
    r"incomplet",
]
HALLU_REGEX = re.compile("|".join(HALLU_PATTERNS), re.IGNORECASE)


def label_from_feedback(feedback: str) -> str:
    """Retourne 'critique' si le feedback Claude signale un defaut structurel,
    'sain' sinon. Marge de securite : on est severe (si doute, on flag)."""
    if HALLU_REGEX.search(feedback or ""):
        return "critique"
    return "sain"


# ============================================================================
# D1 — Completeness : promesses du titre vs sections livrees
# ============================================================================

# Heuristique : on extrait la liste d'items apres ":" dans le sujet.
# Ex: "Patterns de resilience : circuit breaker, bulkhead, retry avec backoff"
#   -> ["circuit breaker", "bulkhead", "retry avec backoff"]

def extract_promised_items(subject: str) -> list[str]:
    """Parse le sujet pour extraire la liste d'items annonces."""
    if not subject:
        return []
    # Prendre la partie apres ":" si present
    if ":" in subject:
        subject = subject.split(":", 1)[1]
    # Split par virgule, "et", " / ", " - "
    parts = re.split(r",|\s+et\s+|\s*/\s*|\s+-\s+", subject, flags=re.IGNORECASE)
    items = []
    for p in parts:
        p = p.strip().rstrip(".").strip()
        # Nettoyer ponctuation terminale + parentheses
        p = re.sub(r"\s*\([^)]*\)", "", p).strip()
        p = re.sub(r"[.!?]+$", "", p).strip()
        if len(p) >= 3 and not re.match(r"^\d", p):
            items.append(p.lower())
    return items


HEADER_RE = re.compile(
    r"^(?:#{1,6}\s+|[0-9]+\.\s+|[A-Z]\.\s+|[IVX]+\.\s+|####\s+)(.+?)\s*$",
    re.MULTILINE,
)
# On cherche les headers markdown + les puces "A." "B." "1." "2." qu'on voit
# dans le corpus. Les "#### A." du corpus sont couverts par le \#+ initial ou
# par la 2e alternance.

def extract_sections(body: str) -> list[tuple[str, str]]:
    """Retourne [(titre, corps)] pour chaque section detectee."""
    # Trouver tous les starts de section
    positions = []
    for m in HEADER_RE.finditer(body):
        positions.append((m.start(), m.group(1).strip()))
    if not positions:
        return [("(full)", body)]
    sections = []
    for i, (start, title) in enumerate(positions):
        end = positions[i + 1][0] if i + 1 < len(positions) else len(body)
        content = body[start:end]
        # Retirer la premiere ligne (le header)
        content = content.split("\n", 1)[1] if "\n" in content else ""
        sections.append((title, content))
    return sections


def d1_completeness(body: str, subject: str) -> tuple[dict, bool]:
    """D1 : pour chaque item promis dans le sujet, trouver une section dont
    le titre le contient ET dont le corps a >= 80 mots.

    Flag si `sections_substantielles / items_promis < 0.67`.
    """
    items = extract_promised_items(subject)
    if len(items) < 2:
        # Pas de promesse multiple → D1 ne s'applique pas
        return {"items": items, "covered": [], "ratio": 1.0, "skipped": True}, False

    sections = extract_sections(body)
    covered = []
    for item in items:
        # Tokens significatifs de l'item (>= 3 chars)
        item_tokens = [t for t in re.findall(r"\w+", item.lower()) if len(t) >= 3]
        if not item_tokens:
            continue
        matched = False
        for title, content in sections:
            title_lower = title.lower()
            # L'item est reconnu si la majorite de ses tokens significatifs est
            # dans le titre (tolerant aux reformulations : "retry avec backoff"
            # matche "retry backoff", "circuit breaker" matche "disjoncteur" non)
            n_matched = sum(1 for tok in item_tokens if tok in title_lower)
            if n_matched >= max(1, len(item_tokens) // 2 + (len(item_tokens) % 2)):
                n_words = len(content.split())
                if n_words >= 80:
                    matched = True
                    break
        covered.append((item, matched))

    sub_count = sum(1 for _, ok in covered if ok)
    ratio = sub_count / len(items)
    return {
        "items": items,
        "covered": covered,
        "ratio": ratio,
        "skipped": False,
    }, ratio < 0.67


# ============================================================================
# D2 — Truncation : phrase finale interrompue ou derniere section squelettique
# ============================================================================

TERMINAL_PUNCT = re.compile(r'[.!?»"\)\]`]\s*$')


def d2_truncation(body: str) -> tuple[dict, bool]:
    """D2 : flag si derniere phrase non terminee OU derniere section < 100 mots."""
    lines = [l for l in body.rstrip().split("\n") if l.strip()]
    if not lines:
        return {"reason": "empty"}, False
    last_line = lines[-1].rstrip()
    # Ignorer les horizontales "---", "***"
    if re.match(r"^[-*=_]{3,}\s*$", last_line) and len(lines) >= 2:
        last_line = lines[-2].rstrip()

    # Cas 1 : pas de ponctuation terminale ni de bloc code ferme
    has_terminal = bool(TERMINAL_PUNCT.search(last_line))
    ends_with_code = last_line.endswith("```")
    ends_with_bullet_item = bool(re.match(r"^\s*[-*•]\s+.+\S$", last_line))
    # Un bullet point peut finir sans ponctuation legitimement
    phrase_broken = (not has_terminal) and (not ends_with_code) and (not ends_with_bullet_item)

    # Cas 2 : derniere section < 100 mots
    sections = extract_sections(body)
    last_section_wc = 0
    if sections and sections[-1][0] != "(full)":
        last_section_wc = len(sections[-1][1].split())

    reason = []
    if phrase_broken:
        reason.append(f"last_line_broken: {last_line[-60:]!r}")
    if sections and sections[-1][0] != "(full)" and last_section_wc < 100:
        reason.append(f"last_section_wc={last_section_wc} < 100")

    flag = phrase_broken or (
        len(sections) >= 3  # au moins 3 sections (sinon pas significatif)
        and sections[-1][0] != "(full)"
        and last_section_wc < 100
    )
    return {"reason": reason, "last_section_wc": last_section_wc,
            "phrase_broken": phrase_broken}, flag


# ============================================================================
# D3 — Target Drift : CODE_REVIEW/WORKSHOP only
# ============================================================================

TARGET_FILE_RE = re.compile(r"(?:core/)?([a-z_]+\.py)", re.IGNORECASE)


def extract_target_file(subject: str) -> str | None:
    """Pour CODE_REVIEW, le sujet est souvent 'Revue de code : core/X.py'."""
    m = TARGET_FILE_RE.search(subject or "")
    return m.group(1).lower() if m else None


def d3_target_drift(body: str, subject: str, slot: str) -> tuple[dict, bool]:
    """D3 : flag si le fichier cible est absent du corps OU si un autre .py est
    cite >= 3x plus souvent que la cible. Applicable a CODE_REVIEW/WORKSHOP."""
    if slot not in ("CODE_REVIEW", "WORKSHOP"):
        return {"skipped": True}, False
    target = extract_target_file(subject)
    if not target:
        return {"skipped": True, "reason": "no target in subject"}, False

    # Compter les .py dans le corps
    cited = Counter()
    for m in TARGET_FILE_RE.finditer(body):
        cited[m.group(1).lower()] += 1

    target_count = cited.get(target, 0)
    other_counts = {f: c for f, c in cited.items() if f != target}
    other_top = max(other_counts.values()) if other_counts else 0
    other_top_file = max(other_counts, key=other_counts.get) if other_counts else None

    flag = (target_count == 0 and other_top >= 2) or (
        other_top >= 3 and other_top >= 3 * max(1, target_count)
    )
    return {
        "target": target,
        "target_count": target_count,
        "top_drift": (other_top_file, other_top),
        "cited": dict(cited),
    }, flag


# ============================================================================
# Evaluation composite
# ============================================================================

def strip_header(text: str) -> str:
    parts = text.split("\n---\n", 1)
    if len(parts) == 2 and len(parts[0]) < 2000:
        return parts[1]
    return text


def evaluate(text: str, subject: str, slot: str) -> dict:
    body = strip_header(text)
    d1_info, d1_flag = d1_completeness(body, subject)
    d2_info, d2_flag = d2_truncation(body)
    d3_info, d3_flag = d3_target_drift(body, subject, slot)
    flagged_any = d1_flag or d2_flag or d3_flag
    return {
        "d1": {"flag": d1_flag, **d1_info},
        "d2": {"flag": d2_flag, **d2_info},
        "d3": {"flag": d3_flag, **d3_info},
        "hallu_flag": flagged_any,
        "n_words": len(body.split()),
    }


# ============================================================================
# Pipeline
# ============================================================================

FILE_RE = re.compile(
    r"(\d{4}-\d{2}-\d{2})_(CODE_REVIEW|RESEARCH|WORKSHOP|CREATION|BULLETIN)_(\d{4})\.md"
)


def find_deliverable(entry, deliverables):
    entry_date = entry["date"][:10]
    entry_hhmm = entry["date"][11:16].replace(":", "")
    slot = entry["slot"]
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
    data = json.loads(MENTOR_STATE.read_text(encoding="utf-8"))
    entries = data.get("history", [])
    deliverables = list(DELIVERABLES_DIR.glob("*.md"))
    print(f"Mentor entries : {len(entries)}, deliverables : {len(deliverables)}\n")

    sains, critiques = [], []

    for entry in entries:
        label = label_from_feedback(entry.get("claude_feedback", ""))
        f = find_deliverable(entry, deliverables)
        if not f:
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        r = evaluate(text, entry["subject"], entry["slot"])
        r["file"] = f.name
        r["subject"] = entry["subject"][:50]
        r["label"] = label
        r["grade"] = entry["local_grade"]
        r["slot"] = entry["slot"]
        (critiques if label == "critique" else sains).append(r)

    print(f"Apparies avec verite-terrain nettoyee : "
          f"{len(sains)} SAIN + {len(critiques)} CRITIQUE\n")

    def dump(title, group):
        print(f"=== {title} (n={len(group)}) ===")
        for r in sorted(group, key=lambda x: (x["d1"]["flag"], x["d2"]["flag"], x["d3"]["flag"])):
            flags = ""
            flags += "1" if r["d1"]["flag"] else "."
            flags += "2" if r["d2"]["flag"] else "."
            flags += "3" if r["d3"]["flag"] else "."
            d1 = r["d1"]
            d2 = r["d2"]
            d3 = r["d3"]
            d1_s = f"cov={int(d1.get('ratio', 1) * 100):3d}%" if not d1.get("skipped") else "     "
            d2_s = f"tr={'Y' if d2.get('phrase_broken') else 'n'}/lw={d2.get('last_section_wc', 0):3d}"
            d3_s = ""
            if not d3.get("skipped"):
                t = d3.get("target", "?")
                tc = d3.get("target_count", 0)
                top = d3.get("top_drift", (None, 0))
                d3_s = f"tgt={t}:{tc} top={top[0]}:{top[1]}"
            print(f"  [{flags}] {r['grade']:.1f} {d1_s} {d2_s:14s} n={r['n_words']:4d} "
                  f"{r['slot']:12s} {r['file']}")
            if d3_s:
                print(f"        └─ {d3_s}")
            if not d1.get("skipped") and d1.get("covered"):
                miss = [it for it, ok in d1["covered"] if not ok]
                if miss:
                    print(f"        └─ missing: {miss}")
        print()

    dump("CRITIQUE (vrai positif attendu)", critiques)
    dump("SAIN (vrai negatif attendu)", sains)

    # Matrice de confusion
    print("=" * 78)
    print("MATRICE (positif = flag = CRITIQUE predit)")
    print("=" * 78)
    tp = sum(1 for r in critiques if r["hallu_flag"])
    fn = len(critiques) - tp
    fp = sum(1 for r in sains if r["hallu_flag"])
    tn = len(sains) - fp
    total = tp + fn + fp + tn
    prec = tp / max(1, tp + fp)
    rec = tp / max(1, tp + fn)
    f1 = 2 * prec * rec / max(1e-9, prec + rec)
    acc = (tp + tn) / max(1, total)
    print(f"           predict CRITIQUE   predict SAIN")
    print(f"true CRIT     TP={tp:2d}            FN={fn:2d}")
    print(f"true SAIN     FP={fp:2d}            TN={tn:2d}")
    print(f"")
    print(f"precision = {prec:.2f}    rappel = {rec:.2f}    F1 = {f1:.2f}    acc = {acc:.2f}")

    # Breakdown par detecteur
    print("\n" + "=" * 78)
    print("Contribution par detecteur")
    print("=" * 78)
    for name, key in [("D1 completeness", "d1"), ("D2 truncation", "d2"),
                       ("D3 target drift", "d3")]:
        d_tp = sum(1 for r in critiques if r[key]["flag"])
        d_fp = sum(1 for r in sains if r[key]["flag"])
        d_total_pos = d_tp + d_fp
        d_prec = d_tp / max(1, d_total_pos)
        d_rec = d_tp / max(1, len(critiques))
        print(f"  {name:20s} flags {d_tp} vrais / {d_fp} faux  "
              f"(prec {d_prec:.2f}, rappel {d_rec:.2f})")


if __name__ == "__main__":
    main()
