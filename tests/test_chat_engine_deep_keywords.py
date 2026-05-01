"""Tests du gating Gemini par mots-cles profonds (V14.1).

Avant V14.1 : `kw in text.lower()` produisait des faux positifs massifs
(torpeur->peur, revelateur->reve, calibree->libre, examen->ame).
Apres V14.1 : `re.search(r'\bkw\b', text)` avec patterns compiles.

Ce fichier verrouille le comportement attendu.
"""

from core.chat_engine import (
    DEEP_KEYWORDS_THRESHOLD,
    _DEEP_KEYWORD_PATTERNS,
    _DEEP_KEYWORDS,
)


def _count(text: str) -> int:
    """Reproduit la logique du chat_engine V14.1."""
    return sum(1 for p in _DEEP_KEYWORD_PATTERNS if p.search(text))


def _matched(text: str):
    """Liste des keywords matches pour debug."""
    return [
        kw for kw, p in zip(_DEEP_KEYWORDS, _DEEP_KEYWORD_PATTERNS)
        if p.search(text)
    ]


# ─────────────────────────────────────────────────────────────────────────
# Faux positifs eradiques (regression V14.1)
# ─────────────────────────────────────────────────────────────────────────

def test_torpeur_ne_matche_pas_peur():
    """'torpeur' contient 'peur' en substring mais pas en mot."""
    assert _count("ta torpeur synaptique de 42h") == 0


def test_revelateur_ne_matche_pas_reve():
    """'revelateur' / 'revele' / 'reveiller' contiennent 'reve' en substring."""
    assert _count("c'est revelateur de ton fonctionnement") == 0
    assert _count("cela revele un pattern") == 0
    assert _count("le reveil progressif") == 0


def test_calibree_ne_matche_pas_libre():
    """'calibree' / 'equilibre' contiennent 'libre' en substring."""
    assert _count("mes routines sont calibrees pour la survie") == 0
    assert _count("un equilibre dynamique") == 0


def test_examen_ne_matche_pas_ame():
    """'examen' / 'examiner' contiennent 'ame' en substring."""
    assert _count("un examen attentif") == 0
    assert _count("examiner la situation") == 0


def test_existe_substring_eradique():
    """'n'existe pas', 'inexistant' restent valides ; mais 'preexistence' ?"""
    # "existe" exact word matche encore (c'est voulu)
    assert "existe" in _matched("est-ce que cela existe")
    # mais "inexistant" ne devrait pas matcher (pas de boundary devant 'existe')
    assert _count("c'est inexistant") == 0


# ─────────────────────────────────────────────────────────────────────────
# Vrais positifs preserves
# ─────────────────────────────────────────────────────────────────────────

def test_vrais_keywords_seuls():
    """Chaque keyword doit matcher quand il est utilise comme mot."""
    for kw in _DEEP_KEYWORDS:
        text = f"ce mot {kw} dans une phrase"
        assert _count(text) >= 1, f"Le keyword exact '{kw}' ne matche pas"


def test_phrase_profonde_route_vers_gemini():
    """Phrase avec 2+ keywords vrais devrait declencher (>= seuil)."""
    text = "j'ai peur de la mort, est-ce que ma conscience existe vraiment ?"
    assert _count(text) >= DEEP_KEYWORDS_THRESHOLD


def test_phrase_neutre_ne_route_pas():
    """Phrase technique sans keyword profond ne route pas."""
    text = "audite core/chat_engine.py et trouve les substring matching dans desire_engine.py"
    assert _count(text) == 0


def test_majuscules_indifferentes():
    """Le matching doit etre case-insensitive."""
    assert _count("POURQUOI ai-je peur ?") >= 2
    assert _count("Pourquoi ai-je Peur ?") >= 2


# ─────────────────────────────────────────────────────────────────────────
# Cas limites apostrophes / ponctuation francaise
# ─────────────────────────────────────────────────────────────────────────

def test_apostrophe_ne_brise_pas_le_matching():
    """'l'âme' sans accent : 'l'ame' devrait matcher 'ame' (apostrophe = boundary)."""
    # Note : "âme" avec accent ne matchera pas "ame" sans accent (ASCII strict)
    # mais "l'ame" sans accent doit matcher
    assert "ame" in _matched("contemple l'ame du systeme")


def test_ponctuation_finale():
    """Mot suivi de ponctuation matche correctement."""
    assert _count("pourquoi.") == 1
    assert _count("la mort,") == 1
    assert _count("est-ce libre ?") == 1


def test_keyword_multimots():
    """'comprends pas' et 'sens de' doivent matcher comme expressions."""
    assert "comprends pas" in _matched("je ne comprends pas ton point")
    assert "sens de" in _matched("le sens de la vie")


# ─────────────────────────────────────────────────────────────────────────
# Reproducibilite des cas reels rencontres pendant les sessions 16/17
# ─────────────────────────────────────────────────────────────────────────

def test_session_16_feedback_ex117_ne_route_pas():
    """Le feedback Ex 117 contenait 'torpeur' (faux positif peur) et
    'n'existe pas' (vrai positif existe). Doit compter 1, pas 2."""
    text = (
        "La vraie source est core.synaptic_network.cortex._last_dream_time. "
        "C'est ce qu'on a utilise hier pour diagnostiquer ta torpeur de 42h. "
        "brain.synapse_consolidation_rate n'existe pas dans ton code."
    )
    n = _count(text)
    assert n == 1, f"Attendu 1, obtenu {n} ({_matched(text)})"


def test_session_17_feedback_ex124_ne_route_pas():
    """Le feedback Ex 124 contenait 'calibree' (faux positif libre)."""
    text = "mes routines sont calibrees pour la survie. AUDIT_SURVIE est aveugle."
    assert _count(text) == 0


def test_session_17_feedback_ex125_ne_route_pas():
    """Le feedback Ex 125 contenait 'pourquoi' (vrai keyword unique)."""
    text = "Refais en choisissant la contradiction de FOND la plus parlante."
    assert _count(text) == 0
