"""Phase 14 (sequel V12.0) — Sanity Check Local anti-Perroquet Syntaxique.

Detecte 3 symptomes structurels observes dans 20/20 feedbacks Claude du
corpus `mentor_state.json` (calibration 22/04/2026) :

  D1 Completeness — le plan annonce N items, le corps livre N-k sections
     substantielles (>= 80 mots). Skip BULLETIN/CREATION (sujets narratifs).
  D2 Truncation — phrase finale interrompue ou derniere section < 100 mots
     (qui signale une generation coupee par token limit).
  D3 Target Drift — CODE_REVIEW/WORKSHOP demande X.py, le livrable ne cite
     pas X.py (attention collapse sur un attracteur du modele local).

Applique un multiplicateur **gradue** sur le grade (Reward Shaping) pour
eviter le desert de recompense sur la V12.0 MDP :
  0 flag → 1.0   (grade preserve)
  1 flag → 0.5
  2 flags → 0.25
  3 flags → 0.1  (clipping fort)

0 LLM, 0 GPU, ~5ms / livrable. Deterministe.
"""
import ast
import logging
import re
from collections import Counter
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)


# ─── Parsing utilities ────────────────────────────────────────────────────

HEADER_RE = re.compile(
    r"^(?:#{1,6}\s+|[0-9]+\.\s+|[A-Z]\.\s+|[IVX]+\.\s+)(.+?)\s*$",
    re.MULTILINE,
)

TARGET_FILE_RE = re.compile(r"(?:core/)?([a-z_]+\.py)", re.IGNORECASE)
TERMINAL_PUNCT = re.compile(r'[.!?»"\)\]`]\s*$')

# D4a : blocs code Python entre triple-backticks (optionnellement tagges
# "python"). DOTALL pour capturer les blocs multi-lignes.
CODE_BLOCK_RE = re.compile(r"```(?:python)?\s*\n(.*?)\n```", re.DOTALL)

# Slots a sujets narratifs (pas d'enumeration explicite dans le sujet)
D1_SKIP_SLOTS = frozenset({"BULLETIN", "CREATION"})

# V30.6 / V30.6.1 — Slots a structure narrative courte (conclusion concise
# par nature : ~50 mots). Le test 2 de d2_truncation (derniere section
# < 100 mots) est ignore pour ces slots. Le test 1 (phrase finale
# interrompue) reste actif pour TOUS les slots.
_NARRATIVE_LENGTH_SKIP = frozenset({"BULLETIN", "CREATION"})


def strip_header(text: str) -> str:
    """Retire le preambule d'evaluation (# Livrable, Note, Feedback, Challenge).

    Les livrables school ont la forme :
        # Livrable: SLOT
        Date: ...
        Note: X/10
        Feedback: ...
        Challenge: ...
        ---
        <corps du livrable>

    On garde uniquement le corps pour l'analyse structurelle.
    """
    parts = text.split("\n---\n", 1)
    if len(parts) == 2 and len(parts[0]) < 2000:
        return parts[1]
    return text


def extract_promised_items(subject: str) -> List[str]:
    """Extrait les items annonces dans le sujet apres le premier ':'.

    Ex: 'Patterns de resilience : circuit breaker, bulkhead, retry avec backoff'
        -> ['circuit breaker', 'bulkhead', 'retry avec backoff']
    """
    if not subject or ":" not in subject:
        return []
    remainder = subject.split(":", 1)[1]
    # Separateurs : virgule, "et", " - ". Le '/' est conserve (pub/sub, I/O).
    parts = re.split(r",|\s+et\s+|\s+-\s+", remainder, flags=re.IGNORECASE)
    items = []
    for p in parts:
        p = p.strip().rstrip(".").strip()
        p = re.sub(r"\s*\([^)]*\)", "", p).strip()  # retirer parentheses
        p = re.sub(r"[.!?]+$", "", p).strip()
        if len(p) >= 3 and not re.match(r"^\d", p):
            items.append(p.lower())
    return items


def extract_sections(body: str) -> List[Tuple[str, str]]:
    """Retourne [(titre, corps)] pour chaque section detectee (# / ## / 1. / A.)."""
    positions = [(m.start(), m.group(1).strip()) for m in HEADER_RE.finditer(body)]
    if not positions:
        return [("(full)", body)]
    sections = []
    for i, (start, title) in enumerate(positions):
        end = positions[i + 1][0] if i + 1 < len(positions) else len(body)
        content = body[start:end]
        content = content.split("\n", 1)[1] if "\n" in content else ""
        sections.append((title, content))
    return sections


# ─── Detecteurs ──────────────────────────────────────────────────────────

def d1_completeness(body: str, subject: str, slot: str,
                    min_section_words: int = 80,
                    coverage_threshold: float = 0.67) -> bool:
    """D1 : flag si moins de `coverage_threshold` des items promis dans le
    sujet sont couverts par une section du corps >= `min_section_words` mots.

    Skipped sur BULLETIN et CREATION (sujets narratifs sans enumeration).
    Skipped aussi si le sujet n'enumere pas au moins 2 items."""
    if slot in D1_SKIP_SLOTS:
        return False
    items = extract_promised_items(subject)
    if len(items) < 2:
        return False
    sections = extract_sections(body)
    covered = 0
    for item in items:
        tokens = [t for t in re.findall(r"\w+", item.lower()) if len(t) >= 3]
        if not tokens:
            continue
        needed = max(1, (len(tokens) + 1) // 2)
        for title, content in sections:
            title_lower = title.lower()
            matches = sum(1 for tok in tokens if tok in title_lower)
            if matches >= needed and len(content.split()) >= min_section_words:
                covered += 1
                break
    return (covered / len(items)) < coverage_threshold


def d2_truncation(
    body: str,
    slot: Optional[str] = None,
    min_last_section_words: int = 100,
) -> bool:
    """D2 : flag si phrase finale interrompue OU derniere section (parmi 3+)
    fait < `min_last_section_words` mots.

    V19 (2026-04-24 17:15) : exception pour le format V18 map-reduce qui
    termine les livrables par "Note globale : X/10". Sans cette exception,
    Phase 14 clippe systematiquement x0.5 un livrable structurellement
    complet mais dont la derniere ligne ne porte pas de ponctuation
    terminale au sens strict.

    V30.6 (2026-04-26) — OPTION C : ne pas penaliser la concision du
    BULLETIN. Diagnostic : un BULLETIN narratif quotidien fait typiquement
    200-400 mots avec 3-5 sections, sa derniere section (conclusion) est
    courte par nature (~50 mots). La condition "derniere section < 100
    mots" flag systematiquement BULLETIN -> grade x0.5 chronique.
    Fix : si slot == "BULLETIN", garder uniquement la detection de phrase
    finale interrompue (vrai signal de truncation LLM) et ignorer le
    check de longueur de section.

    V30.6.1 (2026-04-27) — AMNISTIE ETENDUE A CREATION.
    Diagnostic ce matin : la pulsion CREATION a explose (+23.68 en 24h)
    et la zone tissulaire creativity s'est effondree (-43%). Cause :
    SCHOOL_CREATION du 27/04 04:25 a ete clip x0.5 (3.75/10) pour
    "truncation (phrase/section finale coupee)" — meme syndrome que
    BULLETIN. Les livrables narratifs courts (poemes, dialogues, lettres
    fictives) ont la meme structure : 200-400 mots, conclusion concise.
    Extension : ajouter CREATION au set _NARRATIVE_LENGTH_SKIP.
    """
    lines = [l for l in body.rstrip().split("\n") if l.strip()]
    if not lines:
        return False
    last_line = lines[-1].rstrip()
    # Ignorer barre horizontale finale
    if re.match(r"^[-*=_]{3,}\s*$", last_line) and len(lines) >= 2:
        last_line = lines[-2].rstrip()
    ends_terminal = bool(TERMINAL_PUNCT.search(last_line))
    ends_code = last_line.endswith("```")
    ends_bullet = bool(re.match(r"^\s*[-*•]\s+.+\S$", last_line))
    # V19 : format map-reduce legitime "Note globale : X/10"
    ends_note = bool(re.search(
        r"Note\s+globale\s*:\s*\d+(?:\.\d+)?\s*/\s*10",
        last_line, re.IGNORECASE,
    ))
    if not (ends_terminal or ends_code or ends_bullet or ends_note):
        return True
    # V30.6 / V30.6.1 — Slots narratifs courts exemptes du check
    # de longueur de la derniere section (BULLETIN, CREATION).
    if slot in _NARRATIVE_LENGTH_SKIP:
        return False
    # Squelette : derniere section trop courte
    sections = extract_sections(body)
    if len(sections) >= 3 and sections[-1][0] != "(full)":
        if len(sections[-1][1].split()) < min_last_section_words:
            return True
    return False


def d4a_syntax_parse(body: str) -> bool:
    """D4a (Phase 14.1, 23/04/2026) : flag si un bloc ```python du livrable
    contient une SyntaxError.

    Attrape :
    - Code tronque par limite de tokens (parentheses non fermees,
      indentation incomplete, string litteral non termine).
    - Erreurs grossieres de generation (keyword mal tokenise, operateur
      invalide).

    Immunite au Goodhart : un LLM ne 'choisit' pas de faire une erreur
    de syntaxe — c'est un accident de generation. Punir avec un malus
    (flag) n'incite a aucun comportement strategique d'evitement.

    Ne detecte PAS les bugs semantiques (type mismatch, variable non
    initialisee, await sur un non-coroutine) : ces cas passent l'AST.
    La vraie detection de bugs d'execution exigerait une sandbox
    dynamique (roadmap V15+).
    """
    for match in CODE_BLOCK_RE.finditer(body):
        code = match.group(1)
        if not code.strip():
            continue
        try:
            ast.parse(code)
        except SyntaxError:
            return True
        except Exception:
            # Autres erreurs (encoding, recursion depth) → conservateur,
            # on ne flag pas.
            pass
    return False


def d3_target_drift(body: str, subject: str, slot: str) -> bool:
    """D3 : flag si CODE_REVIEW/WORKSHOP demande un fichier X.py et :
       - X.py n'est pas cite dans le corps, OU
       - un autre Y.py est cite >= 3x plus souvent que X.py."""
    if slot not in ("CODE_REVIEW", "WORKSHOP"):
        return False
    m = TARGET_FILE_RE.search(subject or "")
    if not m:
        return False
    target = m.group(1).lower()
    cited = Counter(
        f.group(1).lower() for f in TARGET_FILE_RE.finditer(body)
    )
    target_count = cited.get(target, 0)
    if target_count == 0:
        return True
    others = {f: c for f, c in cited.items() if f != target}
    if others and max(others.values()) >= 3 * max(1, target_count):
        return True
    return False


# ─── Verdict ─────────────────────────────────────────────────────────────

def grade_multiplier(n_flags: int) -> float:
    """Reward shaping : multiplicateur gradue.

    Evite le desert de recompense (Reward Sparsity) qui aplatirait la
    fonction Q de la V12.0 sur un systeme en defaillance permanente.

    Phase 14.1 : extension a 4 flags (D4a SyntaxError cumule).
    """
    return {0: 1.0, 1: 0.5, 2: 0.25, 3: 0.1, 4: 0.05}.get(
        min(4, max(0, n_flags)), 0.05
    )


def evaluate_deliverable(deliverable: str, subject: str, slot: str) -> dict:
    """Applique D1/D2/D3 sur un livrable et retourne le multiplicateur a
    appliquer au grade.

    Args:
        deliverable: contenu brut (avec ou sans header d'evaluation)
        subject: sujet du cours
        slot: CODE_REVIEW, RESEARCH, WORKSHOP, CREATION, BULLETIN

    Returns:
        dict {d1_completeness, d2_truncation, d3_target_drift, n_flags,
              multiplier, reasons}
    """
    body = strip_header(deliverable)
    d1 = d1_completeness(body, subject, slot)
    d2 = d2_truncation(body, slot=slot)
    d3 = d3_target_drift(body, subject, slot)
    d4a = d4a_syntax_parse(body)
    n_flags = int(d1) + int(d2) + int(d3) + int(d4a)
    reasons: List[str] = []
    if d1:
        reasons.append("completeness (items promis absents)")
    if d2:
        reasons.append("truncation (phrase/section finale coupee)")
    if d3:
        reasons.append("target drift (fichier cible non cite)")
    if d4a:
        reasons.append("d4a (SyntaxError dans un bloc code)")
    return {
        "d1_completeness": d1,
        "d2_truncation": d2,
        "d3_target_drift": d3,
        "d4a_syntax_parse": d4a,
        "n_flags": n_flags,
        "multiplier": grade_multiplier(n_flags),
        "reasons": reasons,
    }
