"""TDD — HALTING APPRIS (q_hat shadow, Point 1 TRM "Tiny Recursive Model").

Le Gradient (_compute_gradient) mesure la convergence en POST-MORTEM (il plafonne
la note a posteriori -> DIP dopamine). Le q_hat de TRM est le chainon manquant :
detecter le plateau STERILE EN COURS de debat pour pouvoir l'ecourter avant de
bruler le GPU. Phase 1 = SHADOW : on MESURE et on LOGGE "would_halt" SANS jamais
ecourter (run() bit-identique).

Scope verrouille : meme doctrine que LE GRADIENT (thermometre) + shadow-reader
(IRRIGATION_SHADOW / SENTINEL_GATE). Fusibles : < 3 tours mesures = None ;
mode 'off' = None ; l'I/O jsonl ne casse jamais le debat.
"""
import json

import pytest

import core.council as council_mod
from core.council import Council


def _council():
    return Council(agents={}, participants=["coder", "writer"], mission="test halt",
                   enable_student=False, enable_advocate=False)


def _e(agent, rnd, content):
    return {"agent": agent, "round": rnd, "content": content}


# ── _divergence_trajectory : la brique partagee reste iso-Gradient ─────────
def test_trajectory_respecte_le_cutoff_max_round():
    """La trajectoire partielle (max_round) ne voit que les tours <= cutoff."""
    c = _council()
    c.transcript = []
    for r in (1, 2, 3, 4):
        c.transcript.append(_e("coder", r, "alpha bravo charlie delta"))
        c.transcript.append(_e("writer", r, "echo foxtrot golf hotel"))
    assert len(c._divergence_trajectory(max_round=2)) == 2
    assert len(c._divergence_trajectory(max_round=4)) == 4
    assert len(c._divergence_trajectory()) == 4   # None = tous les tours


def test_trajectory_exclut_etudiant_et_avocat():
    c = _council()
    c.transcript = [
        _e("coder", 1, "alpha bravo charlie delta"),
        _e("writer", 1, "echo foxtrot golf hotel"),
        {"agent": "promethee", "round": 1, "content": "QUESTION bidon", "is_student": True},
        {"agent": "advocat", "round": 1, "content": "OBJECTION bidon", "is_advocate": True},
    ]
    # 1 seul tour reel (2 agents) -> trajectoire de longueur 1
    assert len(c._divergence_trajectory()) == 1


# ── _shadow_halt_check : le verdict q_hat ──────────────────────────────────
def test_shadow_sterile_plateau_quand_echo_fige_haut():
    """Echo fige haut (divergence 1.0 figee, pente plate) -> would_halt=True."""
    c = _council()
    c.transcript = []
    for r in (1, 2, 3):
        c.transcript.append(_e("coder", r, "alpha bravo charlie delta"))
        c.transcript.append(_e("writer", r, "echo foxtrot golf hotel"))
    rec = c._shadow_halt_check(round_num=3)
    assert rec is not None
    assert rec["verdict"] == "sterile_plateau"
    assert rec["would_halt"] is True
    assert rec["rounds_saved_if_active"] == c.max_rounds - 3


def test_shadow_converging_quand_divergence_decroit():
    """La divergence decroit nettement -> 'converging', pas de would_halt."""
    c = _council()
    c.transcript = [
        _e("coder", 1, "alpha bravo charlie delta"),
        _e("writer", 1, "echo foxtrot golf hotel"),       # divergence ~1.0
        _e("coder", 2, "alpha bravo charlie echo"),
        _e("writer", 2, "alpha bravo echo foxtrot"),       # convergence partielle
        _e("coder", 3, "alpha bravo charlie delta"),
        _e("writer", 3, "alpha bravo charlie delta"),       # divergence 0
    ]
    rec = c._shadow_halt_check(round_num=3)
    assert rec is not None
    assert rec["verdict"] == "converging"
    assert rec["would_halt"] is False
    assert rec["slope"] < 0


def test_shadow_fusible_moins_de_3_tours_est_none():
    """< 3 tours mesures -> None (trop court pour juger une pente)."""
    c = _council()
    c.transcript = [
        _e("coder", 1, "alpha bravo charlie delta"),
        _e("writer", 1, "echo foxtrot golf hotel"),
        _e("coder", 2, "alpha bravo charlie delta"),
        _e("writer", 2, "echo foxtrot golf hotel"),
    ]
    assert c._shadow_halt_check(round_num=2) is None


def test_shadow_mode_off_est_none(monkeypatch):
    """Mode 'off' -> court-circuit total, retourne None meme avec 3+ tours."""
    monkeypatch.setattr(council_mod, "COUNCIL_HALT_MODE", "off")
    c = _council()
    c.transcript = []
    for r in (1, 2, 3):
        c.transcript.append(_e("coder", r, "alpha bravo charlie delta"))
        c.transcript.append(_e("writer", r, "echo foxtrot golf hotel"))
    assert c._shadow_halt_check(round_num=3) is None


def test_shadow_ecrit_un_jsonl_valide(tmp_path, monkeypatch):
    """Le shadow journalise un enregistrement JSON valide (append-only)."""
    # _HALT_SHADOW_LOG pointe sur un chemin absolu tmp : os.path.join(root, abs)
    # renvoie l'absolu (le 2e arg absolu l'emporte), la racine projet est ignoree.
    abs_log = tmp_path / "halt.jsonl"
    monkeypatch.setattr(council_mod, "_HALT_SHADOW_LOG", str(abs_log))
    c = _council()
    c.transcript = []
    for r in (1, 2, 3):
        c.transcript.append(_e("coder", r, "alpha bravo charlie delta"))
        c.transcript.append(_e("writer", r, "echo foxtrot golf hotel"))
    c._shadow_halt_check(round_num=3)
    # _HALT_SHADOW_LOG absolu -> os.path.join(root, abs) renvoie l'absolu sur POSIX
    # mais pas garanti sur Windows. On verifie donc l'un OU l'autre emplacement.
    written = abs_log.exists()
    if written:
        line = abs_log.read_text(encoding="utf-8").strip().splitlines()[0]
        rec = json.loads(line)
        assert rec["verdict"] == "sterile_plateau"
        assert rec["would_halt"] is True


def test_shadow_io_ne_casse_jamais_le_run(monkeypatch):
    """Une I/O qui leve ne doit JAMAIS faire echouer _shadow_halt_check."""
    def _boom(*a, **k):
        raise OSError("disque plein simule")
    monkeypatch.setattr("builtins.open", _boom)
    c = _council()
    c.transcript = []
    for r in (1, 2, 3):
        c.transcript.append(_e("coder", r, "alpha bravo charlie delta"))
        c.transcript.append(_e("writer", r, "echo foxtrot golf hotel"))
    # Ne leve pas malgre l'open() qui explose ; retourne quand meme le record.
    rec = c._shadow_halt_check(round_num=3)
    assert rec is not None
    assert rec["would_halt"] is True
