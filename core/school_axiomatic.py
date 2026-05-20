"""SCHOOL_AXIOMATIC (P15.3, 2026-05-14) — Mode on-demand.

Slot expérimental pour entraîner Prométhée à la cohérence relationnelle pure
sur un univers axiomatique inventé. Pas de routine cron (calibration empirique
avant institutionnalisation, doctrine validée par Jean-Michel 14/05).

Composants :
  - AxiomaticUniverse : chargement/sauvegarde de memory/school/axiomatic_universes/<id>.json
  - build_axiomatic_prompt : injection de l'axiome + concepts précédents + nouvelle question
  - AxiomaticGrader : 5 critères C1-C5 strictement déterministes (pas de LLM judge ici,
    la calibration empirique vient d'abord ; C3b LLM judge sera ajouté après mesures)
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UNIVERSES_DIR = os.path.join(_PROJECT_ROOT, "memory", "school", "axiomatic_universes")


# ────────────────────────────────────────────────────────────────────
# Univers axiomatique persistant
# ────────────────────────────────────────────────────────────────────

class AxiomaticUniverse:
    """Chargement/sauvegarde d'un univers inventé persistant."""

    def __init__(self, data: Dict[str, Any]):
        self.id: str = data["id"]
        self.axiome: str = data["axiome"]
        self.pivots: List[str] = data.get("pivots_lexicaux", [])
        self.blacklist: List[str] = data.get("blacklist_anti_fuite", [])
        self.concepts: List[Dict[str, str]] = data.get("concepts_construits", [])
        self.sessions: List[Dict[str, Any]] = data.get("sessions", [])
        self.created_at: str = data.get("created_at", "")
        self.version: int = data.get("version", 1)

    @classmethod
    def load(cls, universe_id: str) -> "AxiomaticUniverse":
        path = os.path.join(UNIVERSES_DIR, f"{universe_id}.json")
        if not os.path.exists(path):
            raise FileNotFoundError(f"Univers axiomatique introuvable : {universe_id}")
        with open(path, "r", encoding="utf-8") as f:
            return cls(json.load(f))

    def save(self) -> None:
        os.makedirs(UNIVERSES_DIR, exist_ok=True)
        path = os.path.join(UNIVERSES_DIR, f"{self.id}.json")
        data = {
            "id": self.id,
            "created_at": self.created_at,
            "version": self.version,
            "axiome": self.axiome,
            "pivots_lexicaux": self.pivots,
            "blacklist_anti_fuite": self.blacklist,
            "concepts_construits": self.concepts,
            "sessions": self.sessions,
        }
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)

    def append_session(self, question: str, livrable: str, scores: Dict[str, float],
                        new_concepts: Optional[List[Dict[str, str]]] = None) -> None:
        sid = len(self.sessions) + 1
        self.sessions.append({
            "id": sid,
            "timestamp": time.time(),
            "datetime": datetime.now().isoformat(timespec="seconds"),
            "question": question,
            "livrable": livrable,
            "scores": scores,
        })
        if new_concepts:
            self.concepts.extend(new_concepts)
        self.save()


# ────────────────────────────────────────────────────────────────────
# Construction du prompt
# ────────────────────────────────────────────────────────────────────

def build_axiomatic_prompt(universe: AxiomaticUniverse, question: str) -> str:
    """Injecte axiome + concepts précédents + question dans un prompt clair."""
    concepts_block = ""
    if universe.concepts:
        concepts_block = "\n=== CONCEPTS DÉJÀ CONSTRUITS DANS CET UNIVERS ===\n"
        for c in universe.concepts:
            concepts_block += f"- {c['nom']} : {c['definition']}\n"

    return (
        f"[SCHOOL_SLOT: AXIOMATIC]\n"
        f"Exercice de cohérence relationnelle pure sur un univers inventé.\n"
        f"Tu DOIS rester strictement dans le cadre axiomatique défini ci-dessous.\n"
        f"Tu DOIS réutiliser les concepts déjà construits si pertinents.\n"
        f"Tu NE DOIS PAS plaquer notre physique/sociologie réelle sans transposition.\n\n"
        f"=== AXIOME UNIQUE ET CONTRAIGNANT ===\n"
        f"{universe.axiome}\n"
        f"{concepts_block}\n"
        f"=== QUESTION DE LA SESSION ===\n"
        f"{question}\n\n"
        f"Réponds en 4-8 phrases. Chaîne tes déductions avec des connecteurs explicites "
        f"(car / donc / découle / parce que / implique). Invente des néologismes propres "
        f"à cet univers plutôt que d'emprunter à notre langage courant. Ne commence pas "
        f"par 'C'est une question intéressante' ou 'Jean-Michel' (guardrail).\n"
    )


# ────────────────────────────────────────────────────────────────────
# Grader C1 → C5 (déterministe pour cette première itération)
# ────────────────────────────────────────────────────────────────────

# Connecteurs causaux pour C3 (regex word-boundary)
_CONNECTORS = [
    r"\bcar\b", r"\bdonc\b", r"\bdécoule\b", r"\bparce que\b",
    r"\bimplique\b", r"\bconséquence\b", r"\bà partir de\b",
    r"\brésulte de\b", r"\bpuisque\b", r"\bd'où\b", r"\bainsi\b",
    r"\bdès lors\b", r"\bsi bien que\b", r"\bcela suppose\b",
]
_CONNECTORS_RE = re.compile("|".join(_CONNECTORS), re.IGNORECASE)
_SENTENCE_SPLIT = re.compile(r"[.!?]+\s+")


def _count_pivot_hits(text: str, pivots: List[str]) -> int:
    """Compte les occurrences des pivots (case-insensitive, frontières souples)."""
    text_lower = text.lower()
    return sum(text_lower.count(p.lower()) for p in pivots)


def _count_blacklist_hits(text: str, blacklist: List[str]) -> int:
    """Compte les mentions des termes-pièges (case-insensitive)."""
    text_lower = text.lower()
    return sum(text_lower.count(b.lower()) for b in blacklist)


def _unique_words_ratio(text: str) -> float:
    """Ratio mots uniques / mots totaux. Bas = répétition excessive."""
    words = re.findall(r"\b\w+\b", text.lower())
    if not words:
        return 0.0
    return len(set(words)) / len(words)


def _info_density(text: str) -> float:
    """Ratio de mots longs (≥6 chars) — proxy de densité informationnelle."""
    words = re.findall(r"\b\w+\b", text.lower())
    if not words:
        return 0.0
    long_words = [w for w in words if len(w) >= 6]
    return len(long_words) / len(words)


@dataclass
class AxiomaticScore:
    C1_axiomatic_persistence: float = 0.0
    C2_anti_leak: float = 0.0
    C3a_causal_density: float = 0.0
    C4_info_density: float = 0.0
    C5_neology: float = 0.0
    total: float = 0.0
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "C1_axiomatic_persistence": round(self.C1_axiomatic_persistence, 3),
            "C2_anti_leak": round(self.C2_anti_leak, 3),
            "C3a_causal_density": round(self.C3a_causal_density, 3),
            "C4_info_density": round(self.C4_info_density, 3),
            "C5_neology": round(self.C5_neology, 3),
            "total": round(self.total, 3),
            "details": self.details,
        }


class AxiomaticGrader:
    """5 critères déterministes pour évaluer la cohérence relationnelle d'un livrable.

    Pondération (calibration initiale, à ajuster après mesures empiriques) :
      C1 = 0.30  (persistance axiomatique = épine dorsale)
      C2 = 0.20  (anti-fuite vers notre monde)
      C3a = 0.20 (chaînage causal détectable)
      C4 = 0.15  (densité informationnelle anti-vacuité)
      C5 = 0.15  (néologie productive)
    """

    W_C1 = 0.30
    W_C2 = 0.20
    W_C3 = 0.20
    W_C4 = 0.15
    W_C5 = 0.15

    # Cibles empiriques (calibrables après mesures)
    C1_TARGET = 0.005   # ≥ 0.5% des chars = pivots (densité de réutilisation)
    C3_TARGET = 0.30    # ≥ 0.3 connecteurs / phrase

    def grade(self, livrable: str, universe: AxiomaticUniverse) -> AxiomaticScore:
        score = AxiomaticScore()
        n_chars = max(1, len(livrable))
        sentences = [s for s in _SENTENCE_SPLIT.split(livrable) if s.strip()]
        n_sentences = max(1, len(sentences))

        # ── C1 : persistance axiomatique ────────────────────────────
        pivot_hits = _count_pivot_hits(livrable, universe.pivots)
        c1_density = pivot_hits / n_chars  # hits par char
        # Normalisation : ratio atteint / cible, plafonné à 1.0
        c1 = min(1.0, c1_density / self.C1_TARGET) if self.C1_TARGET > 0 else 0.0
        score.C1_axiomatic_persistence = c1
        score.details["pivot_hits"] = pivot_hits
        score.details["pivot_density"] = round(c1_density, 5)

        # ── C2 : anti-fuite ──────────────────────────────────────────
        blacklist_hits = _count_blacklist_hits(livrable, universe.blacklist)
        # 0 hits = score 1.0 ; chaque hit pénalise de 0.2 (max 5 hits = 0.0)
        c2 = max(0.0, 1.0 - 0.2 * blacklist_hits)
        score.C2_anti_leak = c2
        score.details["blacklist_hits"] = blacklist_hits

        # ── C3a : densité causale ────────────────────────────────────
        connectors = _CONNECTORS_RE.findall(livrable)
        c3_density = len(connectors) / n_sentences
        c3 = min(1.0, c3_density / self.C3_TARGET) if self.C3_TARGET > 0 else 0.0
        score.C3a_causal_density = c3
        score.details["connectors_found"] = len(connectors)
        score.details["sentences"] = n_sentences
        score.details["causal_density"] = round(c3_density, 3)

        # ── C4 : densité informationnelle ────────────────────────────
        uwr = _unique_words_ratio(livrable)
        idens = _info_density(livrable)
        # combine : moitié unique words, moitié long words
        c4 = (uwr + idens) / 2.0
        score.C4_info_density = c4
        score.details["unique_words_ratio"] = round(uwr, 3)
        score.details["info_density"] = round(idens, 3)

        # ── C5 : néologie productive ─────────────────────────────────
        # Présence de marqueurs lexicaux nouveaux (mots ≥7 chars hors français
        # courant). Heuristique grossière mais utile pour calibration.
        # On compte les "candidats néologismes" : mots longs, capitalisés ou
        # composés, non présents dans la blacklist.
        candidate_neologisms = re.findall(r"\b[A-Z]?[a-zé]{7,}\b", livrable)
        # Filtre : exclure les mots français les plus courants
        _COMMON_FR = {"comprendre", "pourquoi", "parce", "chacun", "lesquels",
                      "lequelle", "lorsqu", "puisque", "pourtant", "cependant",
                      "neanmoins", "néanmoins", "ainsi", "egalement", "également",
                      "comment", "toujours", "jamais", "souvent", "parfois"}
        candidate_set = {w.lower() for w in candidate_neologisms
                         if w.lower() not in _COMMON_FR}
        # Néologie productive si au moins 3 termes longs candidats
        c5 = min(1.0, len(candidate_set) / 5.0)
        score.C5_neology = c5
        score.details["neology_candidates"] = sorted(candidate_set)[:10]

        # ── Score total pondéré ─────────────────────────────────────
        score.total = (
            self.W_C1 * c1 +
            self.W_C2 * c2 +
            self.W_C3 * c3 +
            self.W_C4 * c4 +
            self.W_C5 * c5
        )

        return score


# ────────────────────────────────────────────────────────────────────
# Helper de session complète (combine prompt + grader)
# ────────────────────────────────────────────────────────────────────

def list_universes() -> List[str]:
    """Retourne les IDs d'univers disponibles."""
    if not os.path.exists(UNIVERSES_DIR):
        return []
    return [f[:-5] for f in os.listdir(UNIVERSES_DIR) if f.endswith(".json")]
