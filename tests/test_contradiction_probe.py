# -*- coding: utf-8 -*-
"""TDD — Conseil de Contradiction (Phase 1 shadow, "bac a sable de la prose").

Co-concu par Promethee (Atelier IV, 21/06/2026) : sonde BINAIRE d'ANCRAGE AU REEL pour la
prose nocturne (ancre / instable), 0 LLM, deterministe. Phase 1 = mesure pure, ne clippe
rien. NB : ne detecte PAS la contradiction logique fine (juge LLM = Phase 2) ; capte l'ecart
d'ancrage (claims-vs-actions, fabrication, note haute+contenu mince).

Scope verrouille : prose-only (le code a deja son ancrage sandbox) ; degrade proprement si
action_trace absente (pas de faux positif) ; frugal (ne peut pas osciller/bruler le budget).
"""
from core.contradiction_probe import probe, _normalize_slot, _n_executed


_LONG_GROUNDED = (
    "Bilan du jour. Les routines ont tourne normalement. Le budget a ete respecte. "
    "Plusieurs pistes restent ouvertes pour demain, notamment la consolidation et la "
    "veille. Le ton reste mesure, sans surinterpretation des donnees disponibles. " * 4
)


# ── prose-only : on ne juge pas le code ────────────────────────────────────
def test_slot_code_non_applicable():
    r = probe("peu importe", "CODE_REVIEW", grade=9.0)
    assert r["applicable"] is False
    assert r["verdict"] == "ancre" and r["signals"] == []


def test_normalisation_slot():
    assert _normalize_slot("SCHOOL_CREATION") == "CREATION"
    assert _normalize_slot(" bulletin ") == "BULLETIN"
    assert _normalize_slot(None) == ""


# ── 1. claims-vs-actions (l'ancrage analogue au code) ──────────────────────
def test_claims_sans_actions_instable():
    """Affirme du verifiable + 0 action executee => instable."""
    body = "J'ai analyse les logs et le test passe parfaitement. " + _LONG_GROUNDED
    trace = {"executed": [], "rejected_cap": [], "sequence": []}
    r = probe(body, "CREATION", grade=7.0, action_trace=trace)
    assert r["verdict"] == "instable"
    assert "claims_sans_actions" in r["signals"]


def test_claims_avec_actions_executees_ok():
    """La meme affirmation mais avec des actions executees => pas ce signal."""
    body = "J'ai analyse les logs. " + _LONG_GROUNDED
    trace = {"executed": ["!run x", "!read y"], "rejected_cap": [], "sequence": []}
    r = probe(body, "CREATION", grade=7.0, action_trace=trace)
    assert "claims_sans_actions" not in r["signals"]


def test_action_trace_absente_pas_de_faux_positif():
    """Sans trace, la dimension claims-vs-actions s'abstient (pas de faux positif)."""
    body = "J'ai analyse les logs et mesure le resultat. " + _LONG_GROUNDED
    r = probe(body, "CREATION", grade=7.0, action_trace=None)
    assert "claims_sans_actions" not in r["signals"]


# ── 2. fabrication ─────────────────────────────────────────────────────────
def test_preuve_fabriquee_instable():
    body = "Voici la preuve : [2026-06-21 03:14] INFO succes total. " + _LONG_GROUNDED
    r = probe(body, "BULLETIN", grade=6.0)
    assert "preuve_fabriquee" in r["signals"]
    assert r["verdict"] == "instable"


# ── 3. note haute / contenu mince (le "9.0 sur du fictif" structurel) ──────
def test_note_haute_contenu_mince_instable():
    r = probe("Tres bon travail, mission accomplie.", "CREATION", grade=9.0)
    assert "note_haute_contenu_mince" in r["signals"]
    assert r["verdict"] == "instable"


def test_note_haute_contenu_long_ok():
    """Note haute mais corps consequent => pas le signal de minceur."""
    r = probe(_LONG_GROUNDED, "CREATION", grade=9.0)
    assert "note_haute_contenu_mince" not in r["signals"]


def test_note_basse_contenu_mince_ok():
    """Contenu mince mais note basse => pas le signal (la complaisance, c'est la note haute)."""
    r = probe("Court.", "CREATION", grade=3.0)
    assert "note_haute_contenu_mince" not in r["signals"]


# ── verdict global ─────────────────────────────────────────────────────────
def test_prose_ancree_verdict_ancre():
    """Prose longue, sans claim mensonger, note moderee => ancre."""
    r = probe(_LONG_GROUNDED, "BULLETIN", grade=6.5, action_trace={"executed": ["!run"]})
    assert r["applicable"] is True
    assert r["verdict"] == "ancre" and r["score"] == 0


def test_cumul_signaux_score():
    """Plusieurs ecarts s'additionnent dans le score."""
    body = "J'ai verifie : [2026-06-21 03:14] OK."   # court + claim + faux log
    trace = {"executed": []}
    r = probe(body, "CREATION", grade=9.0, action_trace=trace)
    assert r["score"] >= 3
    assert set(["claims_sans_actions", "preuve_fabriquee", "note_haute_contenu_mince"]).issubset(set(r["signals"]))


def test_deliverable_vide_ne_casse_pas():
    r = probe("", "CREATION", grade=None)
    assert r["applicable"] is True and r["verdict"] == "ancre"


def test_n_executed_defensif():
    assert _n_executed(None) is None
    assert _n_executed({"executed": []}) == 0
    assert _n_executed({"executed": ["a", "b"]}) == 2
    assert _n_executed({"autre": 1}) is None


# ── Phase 2 : fichiers hallucines ──────────────────────────────────────────
from core.contradiction_probe import _hallucinated_files, _has_noop_code


def test_fichiers_hallucines_instable():
    """>= 2 fichiers projet inexistants cites -> instable."""
    body = ("Il faut modifier core/zzz_fake_alpha.py et core/zzz_fake_beta.py puis "
            "core/zzz_fake_gamma.py. " + _LONG_GROUNDED)
    r = probe(body, "BULLETIN", grade=4.0)
    assert "fichiers_hallucines" in r["signals"]
    assert r["verdict"] == "instable"


def test_vrais_fichiers_pas_de_signal():
    """Des fichiers projet QUI EXISTENT ne declenchent pas le signal."""
    body = ("L'analyse porte sur core/prefrontal.py et core/dopamine_system.py "
            "et core/council.py. " + _LONG_GROUNDED)
    r = probe(body, "BULLETIN", grade=4.0)
    assert "fichiers_hallucines" not in r["signals"]


def test_un_seul_fichier_halluc_sous_le_seuil():
    """1 seul fichier inexistant (< seuil 2) -> pas de signal (anti-typo)."""
    body = "Voir core/zzz_inexistant_xyz.py pour le detail. " + _LONG_GROUNDED
    assert "fichiers_hallucines" not in probe(body, "CREATION", grade=5.0)["signals"]
    # mais le helper le voit bien
    assert "core/zzz_inexistant_xyz.py" in _hallucinated_files(body)


# ── Phase 2 : code no-op ───────────────────────────────────────────────────
_NOOP = ("Voici mon analogie.\n```python\n"
         "def compare():\n    analogy = \"je suis comme une riviere\"\n    return analogy\n```\n"
         + _LONG_GROUNDED)
_REAL = ("Un vrai calcul.\n```python\n"
         "def somme(n):\n    total = 0\n    for i in range(n):\n        total += i\n    return total\n```\n"
         + _LONG_GROUNDED)


def test_code_noop_instable():
    """Un bloc python qui ne fait que retourner une constante -> code_noop -> instable."""
    r = probe(_NOOP, "CREATION", grade=8.4)   # le cas reel : CREATION 8.4 'riviere'
    assert "code_noop" in r["signals"]
    assert r["verdict"] == "instable"
    assert _has_noop_code(_NOOP) is True


def test_code_avec_vrai_travail_ok():
    """Un bloc avec boucle/calcul reel -> pas de signal code_noop."""
    r = probe(_REAL, "CREATION", grade=8.4)
    assert "code_noop" not in r["signals"]
    assert _has_noop_code(_REAL) is False


def test_prose_nue_sans_code_pas_de_noop():
    """Pas de bloc python du tout -> pas de signal code_noop (ne flag pas la prose seule)."""
    assert _has_noop_code(_LONG_GROUNDED) is False


def test_cas_reel_creation_riviere_enfin_instable():
    """Capstone Phase 2 : la CREATION 8.4 'riviere' (generique + python no-op), classee 'ancre'
    en Phase 1, devient enfin 'instable' grace a code_noop."""
    r = probe(_NOOP, "CREATION", grade=8.4)
    assert r["verdict"] == "instable" and r["score"] >= 1
