# -*- coding: utf-8 -*-
"""TDD — Organe de digestion des brouillons (Phase 1, Approche A).

Co-concu par Promethee (Atelier III, 20/06/2026) : digerer A FROID le MOTIF de derive
recurrente des ebauches VETOEES (le "sillon"), JAMAIS le contenu. Phase 1 = conscience
pure (lecture + agregation), aucun retour comportemental.

Scope verrouille : 100% deterministe, 0 LLM ; lecture defensive (concurrence d'ecriture
du serveur LIVE) ; fenetre bornee ; fusible min_occurrences (un sillon = >= 3 recurrences).
"""
import json

from core.brouillon_digestion import digest_recent, _posture, _read_records


T0 = 1_780_000_000.0   # epoch de reference fige (tests deterministes)


def _veto(agent, slot, derive, ts):
    return {"ts": ts, "agent": agent, "slot": slot, "mode": "intro",
            "decision": "VETO", "attempt": 1, "lat_ms": 100.0, "derive": derive}


def _write_jsonl(path, records):
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in records),
                    encoding="utf-8")


# ── _posture : extraction de la categorie (jamais le lexeme) ───────────────
def test_posture_reconnait_les_trois_categories():
    assert _posture("ta trajectoire glisse vers l'ORNIERE descriptive") == "orniere"
    assert _posture("un LOGOS recopie en slogan mort") == "logos"
    assert _posture("une contradiction CACHEE, assume la friction") == "contradiction"
    assert _posture("diagnostic inconnu") == "autre"
    assert _posture("") == "autre"
    assert _posture("ORNIÈRE avec accent") == "orniere"


# ── digest_recent : le coeur ───────────────────────────────────────────────
def test_sillon_detecte_au_dela_du_seuil(tmp_path):
    """(agent, slot, posture) repete >= min_occurrences => un sillon."""
    p = tmp_path / "metab.jsonl"
    recs = [_veto("writer", "CREATION", "glisse vers l'ORNIERE", T0 - i * 3600)
            for i in range(4)]   # 4 occurrences identiques
    _write_jsonl(p, recs)
    d = digest_recent(path=str(p), now=T0, min_occurrences=3)
    assert len(d["sillons"]) == 1
    s = d["sillons"][0]
    assert s["agent"] == "writer" and s["slot"] == "CREATION" and s["posture"] == "orniere"
    assert s["count"] == 4
    assert d["dominant_posture"] == "orniere"
    assert d["total_vetos"] == 4
    assert "writer glisse vers orniere en CREATION (4x)" in d["summary"]


def test_sous_le_seuil_pas_de_sillon(tmp_path):
    """2 occurrences < min_occurrences=3 => aucun sillon, summary vide."""
    p = tmp_path / "metab.jsonl"
    _write_jsonl(p, [_veto("coder", "BULLETIN", "LOGOS slogan", T0 - 100),
                     _veto("coder", "BULLETIN", "LOGOS slogan", T0 - 200)])
    d = digest_recent(path=str(p), now=T0, min_occurrences=3)
    assert d["sillons"] == []
    assert d["summary"] == ""
    assert d["total_vetos"] == 2   # comptes mais pas erige en sillon


def test_fenetre_exclut_les_vieux_vetos(tmp_path):
    """Les vetos hors fenetre (window_days) sont ignores."""
    p = tmp_path / "metab.jsonl"
    recent = [_veto("writer", "CREATION", "ORNIERE", T0 - i * 3600) for i in range(3)]
    vieux = [_veto("writer", "CREATION", "ORNIERE", T0 - 30 * 86400) for _ in range(5)]
    _write_jsonl(p, recent + vieux)
    d = digest_recent(path=str(p), now=T0, window_days=7, min_occurrences=3)
    assert d["total_vetos"] == 3              # seuls les 3 recents comptent
    assert d["sillons"][0]["count"] == 3


def test_seuls_les_vetos_comptent(tmp_path):
    """Les PASS sont ignores (on digere les echecs, pas les reussites)."""
    p = tmp_path / "metab.jsonl"
    recs = [_veto("writer", "CREATION", "ORNIERE", T0 - i) for i in range(3)]
    recs += [{"ts": T0, "agent": "writer", "slot": "CREATION", "mode": "intro",
              "decision": "PASS", "attempt": 1, "lat_ms": 50.0}]
    _write_jsonl(p, recs)
    d = digest_recent(path=str(p), now=T0, min_occurrences=3)
    assert d["total_vetos"] == 3


def test_cold_start_fichier_absent():
    """Fichier inexistant => digest vide, jamais d'erreur."""
    d = digest_recent(path="chemin/inexistant/nope.jsonl", now=T0)
    assert d["sillons"] == [] and d["summary"] == "" and d["total_vetos"] == 0


def test_lecture_defensive_ligne_corrompue(tmp_path):
    """Une ligne JSON cassee (append concurrent) est sautee, pas fatale."""
    p = tmp_path / "metab.jsonl"
    good = [_veto("writer", "CREATION", "ORNIERE", T0 - i) for i in range(3)]
    content = "\n".join(json.dumps(r) for r in good) + "\n{ligne partielle corrompue"
    p.write_text(content, encoding="utf-8")
    d = digest_recent(path=str(p), now=T0, min_occurrences=3)
    assert d["total_vetos"] == 3              # 3 bonnes lignes, la corrompue ignoree
    assert d["sillons"][0]["count"] == 3


def test_max_lines_borne_la_fenetre(tmp_path):
    """max_lines ne lit que la queue du fichier (fenetre bornee)."""
    p = tmp_path / "metab.jsonl"
    recs = [_veto("writer", "CREATION", "ORNIERE", T0 - i) for i in range(10)]
    _write_jsonl(p, recs)
    d = digest_recent(path=str(p), now=T0, max_lines=3, min_occurrences=3)
    assert d["total_vetos"] == 3              # seules les 3 dernieres lignes lues


def test_plusieurs_sillons_tries_par_frequence(tmp_path):
    """Sillons multiples => tries par count decroissant, top-3 dans le summary."""
    p = tmp_path / "metab.jsonl"
    recs = []
    recs += [_veto("writer", "CREATION", "ORNIERE", T0 - i) for i in range(5)]
    recs += [_veto("coder", "BULLETIN", "LOGOS", T0 - i) for i in range(3)]
    _write_jsonl(p, recs)
    d = digest_recent(path=str(p), now=T0, min_occurrences=3)
    assert [s["count"] for s in d["sillons"]] == [5, 3]   # ordre decroissant
    assert d["sillons"][0]["agent"] == "writer"
    assert d["dominant_posture"] == "orniere"   # 5 orniere > 3 logos


def test_read_records_fichier_vide(tmp_path):
    p = tmp_path / "metab.jsonl"
    p.write_text("", encoding="utf-8")
    assert _read_records(str(p), 100) == []
