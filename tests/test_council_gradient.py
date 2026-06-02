"""TDD — LE GRADIENT (V1, atelier council 31/05, déployé 01/06).

Capteur de pente de convergence inter-agents : distingue un débat FERTILE
(divergence qui décroît tour après tour) d'un débat STÉRILE (divergence figée
haute = l'écho). Découple le score d'un council du simple fait d'avoir tenu N
tours -> tue l'opium du q=1.0 sur max_rounds.

Scope verrouillé par le pilote : thermomètre, PAS arbitre. Il mesure, il ne
tranche pas le débat. Fusibles : consensus = immunité ; < 3 tours = n/a ;
jamais de pénalité si la divergence décroît (droit d'explorer).
"""
import pytest

from core.council import Council


def _council():
    return Council(agents={}, participants=["coder", "writer"], mission="test gradient",
                   enable_student=False, enable_advocate=False)


def _e(agent, rnd, content):
    return {"agent": agent, "round": rnd, "content": content}


# ── _compute_gradient : la métrique de pente ──────────────────────────────
def test_gradient_fertile_quand_divergence_decroit():
    c = _council()
    c.transcript = [
        _e("coder", 1, "alpha bravo charlie delta"),
        _e("writer", 1, "echo foxtrot golf hotel"),       # divergence ~1.0
        _e("coder", 2, "alpha bravo charlie echo"),
        _e("writer", 2, "alpha bravo echo foxtrot"),      # convergence partielle
        _e("coder", 3, "alpha bravo charlie delta"),
        _e("writer", 3, "alpha bravo charlie delta"),     # divergence 0
    ]
    g = c._compute_gradient(consensus_reached=False)
    assert g["verdict"] == "fertile"
    assert g["slope"] < 0
    assert g["rounds"] == 3


def test_gradient_sterile_quand_echo_fige_haut():
    c = _council()
    c.transcript = []
    for r in (1, 2, 3, 4):
        c.transcript.append(_e("coder", r, "alpha bravo charlie delta"))
        c.transcript.append(_e("writer", r, "echo foxtrot golf hotel"))  # divergence 1.0 figée
    g = c._compute_gradient(consensus_reached=False)
    assert g["verdict"] == "sterile"


def test_gradient_consensus_est_immunise():
    c = _council()
    c.transcript = []
    for r in (1, 2, 3):
        c.transcript.append(_e("coder", r, "alpha bravo charlie delta"))
        c.transcript.append(_e("writer", r, "echo foxtrot golf hotel"))
    g = c._compute_gradient(consensus_reached=True)
    assert g["verdict"] == "n/a"   # il a conclu -> jamais pénalisé


def test_gradient_trop_court_est_na():
    c = _council()
    c.transcript = [
        _e("coder", 1, "alpha bravo charlie delta"),
        _e("writer", 1, "echo foxtrot golf hotel"),
        _e("coder", 2, "alpha bravo charlie delta"),
        _e("writer", 2, "echo foxtrot golf hotel"),
    ]
    g = c._compute_gradient(consensus_reached=False)
    assert g["verdict"] == "n/a"   # 2 tours < 3 : droit d'explorer


def test_gradient_exclut_etudiant_et_avocat():
    """L'étudiant et l'avocat (déterministes) ne faussent pas la divergence."""
    c = _council()
    c.transcript = [
        _e("coder", 1, "alpha bravo charlie delta"),
        _e("writer", 1, "echo foxtrot golf hotel"),
        {"agent": "promethee", "round": 1, "content": "QUESTION bidon", "is_student": True},
        {"agent": "advocat", "round": 1, "content": "OBJECTION bidon", "is_advocate": True},
    ]
    g = c._compute_gradient(consensus_reached=False)
    # 1 seul tour réel (2 agents) -> n=1 -> n/a, et l'étudiant/avocat ignorés
    assert g["rounds"] == 1
    assert g["verdict"] == "n/a"


# ── _score_result_quality : le câblage anti-opium ─────────────────────────
_LONG = ("Un resume de debat correct, en francais, suffisamment long pour ne pas "
         "etre penalise par le plancher de longueur du scoring mecanique du moteur.")


def _resp(verdict):
    return {"status": "max_rounds", "result": _LONG, "gradient": {"verdict": verdict}}


def test_score_council_sterile_est_plafonne():
    from core.autonomy_engine import autonomy
    q = autonomy._score_result_quality(_resp("sterile"), "COUNCIL_DEBATE")
    assert q <= 0.3, "un débat stérile ne doit plus valoir q=1.0 (fin de l'opium)"


def test_score_council_fertile_garde_sa_note():
    from core.autonomy_engine import autonomy
    q = autonomy._score_result_quality(_resp("fertile"), "COUNCIL_DEBATE")
    assert q > 0.3, "un débat fertile garde une note honnête"


def test_score_council_sans_gradient_inchange():
    """Rétrocompat : un council virtuel/ancien sans champ gradient n'est pas puni."""
    from core.autonomy_engine import autonomy
    resp = {"status": "max_rounds", "result": _LONG}
    q = autonomy._score_result_quality(resp, "COUNCIL_DEBATE")
    assert q > 0.3
