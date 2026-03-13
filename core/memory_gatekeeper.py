"""
Memory Gatekeeper — Filtre qualite deterministe pour la memoire RAG.

Module utilitaire 100% deterministe (zero LLM, zero BaseAgent).
Filtre le bruit, les doublons semantiques et les hallucinations
AVANT l'ecriture en memoire ChromaDB.
"""
import re
from typing import Optional

# Patterns de bruit connus (reponses vides / generiques)
_NOISE_PATTERNS = [
    re.compile(r"^R\.?A\.?S\.?\s*$", re.IGNORECASE),
    re.compile(r"aucune vuln[ée]rabilit[ée]", re.IGNORECASE),
    re.compile(r"rien [àa] signaler", re.IGNORECASE),
    re.compile(r"pas de probl[èe]me d[ée]tect[ée]", re.IGNORECASE),
    re.compile(r"tout est en ordre", re.IGNORECASE),
    re.compile(r"aucun probl[èe]me", re.IGNORECASE),
    re.compile(r"^ok\.?\s*$", re.IGNORECASE),
    re.compile(r"aucune am[ée]lioration", re.IGNORECASE),
    re.compile(r"aucun fichier", re.IGNORECASE),
    re.compile(r"pas de modification", re.IGNORECASE),
]

# Stopwords francais/anglais (pour calcul densite informationnelle)
_STOPWORDS = frozenset({
    "le", "la", "les", "de", "du", "des", "un", "une", "et", "en", "est",
    "que", "qui", "dans", "pour", "pas", "sur", "au", "ce", "il", "ne",
    "se", "par", "avec", "plus", "son", "sont", "mais", "ou", "a", "je",
    "sa", "ses", "on", "nous", "vous", "ils", "elle", "tout", "cette",
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "shall",
    "should", "may", "might", "can", "could", "to", "of", "in", "for",
    "on", "with", "at", "by", "from", "as", "into", "through", "not",
    "and", "but", "or", "if", "it", "its", "this", "that", "these",
})

# Mots techniques indicateurs de contenu informatif
# Inclut termes français (le système pense en français) + domaine neurosciences simulées
_TECHNICAL_WORDS = frozenset({
    "python", "async", "await", "import", "class", "def", "function",
    "agent", "orchestrator", "router", "bus", "event", "pipeline",
    "chromadb", "ollama", "gemini", "fastapi", "websocket",
    "memory", "rag", "vector", "embedding", "collection",
    "error", "exception", "traceback", "debug", "fix", "bug",
    "test", "pytest", "assert", "mock", "fixture",
    "config", "json", "api", "endpoint", "middleware",
    "refactor", "optimisation", "performance", "cache",
    "security", "audit", "vulnerability", "injection",
    "evolution", "spec", "catalog", "deploy", "rollback",
    "council", "consensus", "debate", "grimoire",
    "prometheus", "promethee", "nexus", "sentinel",
    # Termes français du domaine
    "routine", "autonomie", "conscience", "synaptique", "neuronal",
    "pulsion", "dopamine", "reptilien", "prefrontal", "thalamus",
    "hypothalamus", "amygdale", "insula", "cingulaire", "cortex",
    "consolidation", "memoire", "apprentissage", "pattern", "anomalie",
    "synthese", "analyse", "architecture", "module", "singleton",
    "scoring", "seuil", "budget", "cooldown", "throttle",
    "fichier", "fonction", "methode", "variable", "parametre",
    "strategie", "objectif", "priorite", "conflit", "resonance",
})


def _unique_words_ratio(text: str) -> float:
    """Ratio mots uniques / mots totaux. Bas = repetition excessive."""
    words = text.lower().split()
    if len(words) < 5:
        return 1.0
    return len(set(words)) / len(words)


def _is_noise(text: str) -> bool:
    """Detecte les patterns de bruit connus (R.A.S., aucune vulnerabilite, etc.)."""
    stripped = text.strip()
    # Texte tres court = souvent du bruit
    if len(stripped) < 30:
        return True
    for pattern in _NOISE_PATTERNS:
        if pattern.search(stripped):
            return True
    return False


def _info_density(text: str) -> float:
    """Ratio de mots techniques vs stopwords. Bas = peu informatif."""
    words = text.lower().split()
    if not words:
        return 0.0
    technical_count = sum(1 for w in words if w in _TECHNICAL_WORDS)
    return technical_count / len(words)


def _bigram_overlap(text_a: str, text_b: str) -> float:
    """Overlap de bigrams entre deux textes. Haut = doublon probable."""
    if not text_a or not text_b:
        return 0.0
    words_a = text_a.lower().split()
    words_b = text_b.lower().split()
    if len(words_a) < 2 or len(words_b) < 2:
        return 0.0
    bigrams_a = set(zip(words_a[:-1], words_a[1:]))
    bigrams_b = set(zip(words_b[:-1], words_b[1:]))
    if not bigrams_a or not bigrams_b:
        return 0.0
    intersection = bigrams_a & bigrams_b
    union = bigrams_a | bigrams_b
    return len(intersection) / len(union) if union else 0.0


def evaluate(text: str, metadata: dict = None,
             existing_text: str = None, existing_distance: float = 1.0) -> dict:
    """Evalue la qualite d'un texte avant sauvegarde en memoire.

    Retourne {accept: bool, reason: str, quality_score: float}.
    """
    if not text or not text.strip():
        return {"accept": False, "reason": "texte vide", "quality_score": 0.0}

    stripped = text.strip()

    # 1. Ratio mots uniques < 30% -> reject (bruit/repetition)
    uwr = _unique_words_ratio(stripped)
    if uwr < 0.30:
        return {
            "accept": False,
            "reason": f"repetition excessive (unique_ratio={uwr:.2f})",
            "quality_score": uwr,
        }

    # 2. Patterns de bruit connus
    if _is_noise(stripped):
        return {
            "accept": False,
            "reason": "pattern de bruit detecte (R.A.S / aucune vulnerabilite / etc.)",
            "quality_score": 0.1,
        }

    # 3. Densite informationnelle (quand le texte est long)
    # Seuil abaisse de 0.02→0.01 : le système pense en français, les mots
    # techniques anglais sont dilues. Ancien seuil rejetait quasi-tout.
    density = _info_density(stripped)
    words_count = len(stripped.split())
    if words_count > 30 and density < 0.01:
        return {
            "accept": False,
            "reason": f"densite informationnelle trop basse ({density:.3f})",
            "quality_score": density,
        }

    # 4. Bigram overlap > 70% avec existing_text -> reject (doublon)
    if existing_text and existing_distance < 0.5:
        overlap = _bigram_overlap(stripped, existing_text)
        if overlap > 0.70:
            return {
                "accept": False,
                "reason": f"doublon bigram (overlap={overlap:.2f})",
                "quality_score": 1.0 - overlap,
            }

    # 5. Score composite
    quality_score = min(1.0, (uwr * 0.3 + density * 3.0 + 0.5))
    return {"accept": True, "reason": "ok", "quality_score": quality_score}
