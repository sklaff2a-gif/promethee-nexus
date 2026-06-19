# -*- coding: utf-8 -*-
"""Irrigation neurochimique du rappel — Incision A, Phase 1 (SHADOW pur, 18/06).

Prouve que la fonction Perfusion() est ÉTANCHE :
  - bornée (plancher disjoncteur 0.05) et à somme constante (= len(ZONES)) ;
  - sous menace montante, le sang va au TRONC et le CORTEX baisse ;
  - en calme dopaminergique, le CORTEX/associations lointaines s'ouvrent ;
  - une trace coping (proxy PREMIUM) remonte sous stress (anti-rumination) ;
  - le re-ranker pousse TRONC/PREMIUM en tête du tableau d'OBSERVATION sans
    JAMAIS altérer le tableau RÉEL retourné (invariant SHADOW).
"""
import math

import pytest

import core.irrigation as irr
from core.irrigation import Zone, ZONES


@pytest.fixture(autouse=True)
def _reset_buffers():
    irr.reset()
    yield
    irr.reset()


# --- Inférence de zone (read-side, rétro-compatible) -------------------------

class TestInferZone:
    def test_defaut_cortex(self):
        assert irr.infer_zone(None) == Zone.CORTEX
        assert irr.infer_zone({}) == Zone.CORTEX
        assert irr.infer_zone({"agent": "strategist"}) == Zone.CORTEX

    def test_source_vers_zone(self):
        assert irr.infer_zone({"source": "reptilian_alarme"}) == Zone.TRONC
        assert irr.infer_zone({"source": "amygdala_veto"}) == Zone.LIMBIQUE
        assert irr.infer_zone({"source": "hippocampus_recall"}) == Zone.TEMPORAL_MEDIAN

    def test_zone_explicite_prioritaire(self):
        # un flag zone explicite (Incision B) prime sur l'inférence par source
        assert irr.infer_zone({"zone": "LIMBIQUE", "source": "reptilian"}) == Zone.LIMBIQUE
        # une zone invalide est ignorée -> on retombe sur l'inférence
        assert irr.infer_zone({"zone": "INEXISTANTE", "source": "reptilian"}) == Zone.TRONC


# --- Affinité coping (flag explicite + proxy PREMIUM Phase 1) ----------------

class TestIsCoping:
    def test_proxy_premium(self):
        assert irr.is_coping({"tier_status": "PREMIUM"}) is True
        assert irr.is_coping({"tier_status": "STANDARD"}) is False
        assert irr.is_coping({}) is False

    def test_flag_explicite_prime_sur_proxy(self):
        assert irr.is_coping({"coping_affinity": True}) is True
        assert irr.is_coping({"coping_affinity": "true"}) is True
        # explicite False l'emporte même sur un PREMIUM
        assert irr.is_coping({"coping_affinity": False, "tier_status": "PREMIUM"}) is False


# --- Perfusion : bornes, somme constante, directions -------------------------

class TestPerfusion:
    def test_neutre_uniforme(self):
        w = irr.compute_perfusion({"d_threat_dt": 0.0, "dopamine_rel": 0.0}, smooth=False)
        for z in ZONES:
            assert math.isclose(w[z], 1.0, abs_tol=1e-9)

    def test_plancher_et_somme_constante(self):
        # même sous une crise extrême, chaque zone >= plancher et somme == len(ZONES)
        w = irr.compute_perfusion({"d_threat_dt": 999.0, "dopamine_rel": 0.0}, smooth=False)
        for z in ZONES:
            assert w[z] >= irr.PERFUSION_FLOOR - 1e-9, f"{z}={w[z]} sous le plancher"
        assert math.isclose(sum(w.values()), len(ZONES), abs_tol=1e-6)

    def test_menace_montante_irrigue_le_tronc(self):
        w = irr.compute_perfusion({"d_threat_dt": 0.04, "dopamine_rel": 0.0}, smooth=False)
        assert w[Zone.TRONC] > 1.0
        assert w[Zone.CORTEX] < 1.0
        assert w[Zone.TRONC] > w[Zone.CORTEX]

    def test_calme_dopaminergique_ouvre_le_cortex(self):
        w = irr.compute_perfusion({"d_threat_dt": 0.0, "dopamine_rel": 0.25}, smooth=False)
        assert w[Zone.CORTEX] > 1.0
        assert w[Zone.CORTEX] > w[Zone.TRONC]

    def test_ema_amortit_vers_la_cible(self):
        # 1er appel lissé : entre la mémoire neutre (1.0) et la cible de crise
        target = irr.compute_perfusion({"d_threat_dt": 0.04, "dopamine_rel": 0.0}, smooth=False)
        irr.reset()
        smoothed = irr.compute_perfusion({"d_threat_dt": 0.04, "dopamine_rel": 0.0}, smooth=True)
        # le lissé est plus proche de 1.0 que la cible brute (inertie)
        assert 1.0 < smoothed[Zone.TRONC] < target[Zone.TRONC]
        # l'EMA préserve les invariants
        assert math.isclose(sum(smoothed.values()), len(ZONES), abs_tol=1e-6)
        for z in ZONES:
            assert smoothed[z] >= irr.PERFUSION_FLOOR - 1e-9


# --- Document-level + re-ranker ---------------------------------------------

class TestSonde:
    """La sonde de threat (read_neuro_state) doit lire l'organe VIVANT, pas un nom
    inexistant. Régression du bug 19/06 : `import reptilian` (n'existe pas) -> threat=0
    immuable, shadow aveugle pendant 90 requêtes."""

    def test_lit_le_threat_via_get_organ(self, monkeypatch):
        import types as _t
        fake = _t.SimpleNamespace(threat_level=5.1)
        import core.organ_registry as reg
        monkeypatch.setattr(reg, "get_organ", lambda name: fake if name == "reptilian" else None)
        st = irr.read_neuro_state()
        assert st["threat"] == 5.1, "la sonde doit refléter le threat réel de l'organe"

    def test_organe_absent_fallback_zero(self, monkeypatch):
        import core.organ_registry as reg
        monkeypatch.setattr(reg, "get_organ", lambda name: None)
        # même sans organe registre, ne crash pas (fallback défensif)
        st = irr.read_neuro_state()
        assert "threat" in st and isinstance(st["threat"], float)


class TestPerfusionMixte:
    """Perfusion MIXTE niveau+dérivée (Phase 2). Le cas qui prouve le fix : une menace
    CHRONIQUE PLATE (d/dt=0) doit QUAND MÊME ouvrir le canal limbique via le niveau."""

    def test_menace_soutenue_plate_irrigue_le_tronc(self):
        # threat haut, dérivée NULLE -> avant le fix : uniforme (bug) ; après : TRONC>CORTEX
        w = irr.compute_perfusion({"threat": 5.1, "d_threat_dt": 0.0, "dopamine_rel": 0.0}, smooth=False)
        assert w[Zone.TRONC] > 1.0
        assert w[Zone.CORTEX] < 1.0
        assert w[Zone.TRONC] > w[Zone.CORTEX]

    def test_plancher_anti_bruit(self):
        # sous THREAT_LEVEL_FLOOR : le bruit de fond n'ouvre RIEN (anti-saturation)
        w = irr.compute_perfusion({"threat": 2.0, "d_threat_dt": 0.0, "dopamine_rel": 0.0}, smooth=False)
        for z in ZONES:
            assert abs(w[z] - 1.0) < 1e-9, f"{z} ne doit pas bouger sous le plancher de bruit"

    def test_crisis_intensity_mixte_bornee(self):
        # niveau seul (plat) : 0 < crisis < 1 ; dérivée forte : sature à 1 ; sous plancher : 0
        assert irr._crisis_intensity(2.0, 0.0) == 0.0
        c_level = irr._crisis_intensity(5.1, 0.0)
        assert 0.0 < c_level < 1.0
        assert irr._crisis_intensity(9.0, 999.0) == 1.0  # clamp haut

    def test_coping_remonte_sous_menace_soutenue(self):
        # la bouée doit remonter même à dérivée nulle (détresse chronique)
        state = {"threat": 5.1, "d_threat_dt": 0.0, "dopamine_rel": 0.0}
        perf = irr.compute_perfusion(state, smooth=False)
        coping = {"tier_status": "PREMIUM", "source": "reptilian"}
        plaie = {"source": "reptilian"}
        assert irr.perfusion_for_doc(coping, perf, state) > irr.perfusion_for_doc(plaie, perf, state)


class TestRerank:
    def test_coping_remonte_sous_stress(self):
        state = {"d_threat_dt": 0.04, "dopamine_rel": 0.0}
        perf = irr.compute_perfusion(state, smooth=False)
        coping = {"tier_status": "PREMIUM", "source": "reptilian"}   # bouée TRONC+coping
        plaie = {"source": "reptilian"}                              # TRONC non-coping
        assert irr.perfusion_for_doc(coping, perf, state) > irr.perfusion_for_doc(plaie, perf, state)

    def test_reranker_pousse_la_bouee_en_tete(self):
        # Une leçon PREMIUM avec un cosinus LÉGÈREMENT moins bon qu'un doc CORTEX :
        # sous crise, l'irrigation doit la faire passer devant.
        ids = ["cortex_doc", "premium_bouee"]
        distances = [0.30, 0.38]   # cortex meilleur en cosinus pur
        metas = [{"source": "strategist"}, {"tier_status": "PREMIUM", "source": "reptilian"}]
        # repos : l'ordre cosinus tient (cortex devant)
        calm = irr.rerank(ids, distances, metas, {"d_threat_dt": 0.0, "dopamine_rel": 0.0})
        assert calm["order"][0] == "cortex_doc"
        irr.reset()
        # crise : la bouée passe en tête (re-ranking d'irrigation)
        crise = irr.rerank(ids, distances, metas, {"d_threat_dt": 0.05, "dopamine_rel": 0.0})
        assert crise["order"][0] == "premium_bouee"

    def test_reorder_result_pur(self):
        result = {
            "ids": [["a", "b", "c"]],
            "distances": [[0.1, 0.2, 0.3]],
            "documents": [["da", "db", "dc"]],
            "metadatas": [[{"k": 1}, {"k": 2}, {"k": 3}]],
        }
        out = irr.reorder_result(result, ["c", "a", "b"])
        assert out["ids"] == [["c", "a", "b"]]
        assert out["documents"] == [["dc", "da", "db"]]
        assert out["distances"] == [[0.3, 0.1, 0.2]]


# --- Invariant SHADOW : le rappel servi reste BIT-IDENTIQUE ------------------

class TestShadowEtancheite:
    def _fake_self(self, calls):
        from core.vector_store import ChromaMemoryManager

        class _Fake:
            def _irrigation_observe(self, q, r):
                calls["observe"] += 1
            def _irrigation_apply(self, q, r):
                calls["apply"] += 1
                return {"reordered": True}
        # on appelle la vraie méthode _maybe_irrigate sur un self factice
        f = _Fake()
        f._maybe_irrigate = ChromaMemoryManager._maybe_irrigate.__get__(f)
        return f

    def test_shadow_retour_identique(self, monkeypatch):
        monkeypatch.setattr(irr, "IRRIGATION_ACTIVE", False)
        monkeypatch.setattr(irr, "IRRIGATION_SHADOW", True)
        calls = {"observe": 0, "apply": 0}
        f = self._fake_self(calls)
        result = {"ids": [["a", "b"]]}
        out = f._maybe_irrigate(["q"], result)
        assert out is result                         # BIT-IDENTIQUE
        assert calls["observe"] == 1 and calls["apply"] == 0

    def test_active_reordonne(self, monkeypatch):
        monkeypatch.setattr(irr, "IRRIGATION_ACTIVE", True)
        monkeypatch.setattr(irr, "IRRIGATION_SHADOW", True)
        calls = {"observe": 0, "apply": 0}
        f = self._fake_self(calls)
        out = f._maybe_irrigate(["q"], {"ids": [["a", "b"]]})
        assert out == {"reordered": True}            # le chemin ACTIVE applique
        assert calls["apply"] == 1 and calls["observe"] == 0

    def test_observe_ignore_resultat_unique(self, monkeypatch, tmp_path):
        # Le rappel de dédup (remember, n_results=1) ne doit RIEN logger : un seul
        # document ne peut pas être re-rangé -> aucun signal, pas de bruit dans le JSONL.
        from core.vector_store import ChromaMemoryManager
        log = tmp_path / "irr.jsonl"
        monkeypatch.setattr(irr, "IRRIGATION_LOG_PATH", str(log))

        class _Fake:
            pass
        f = _Fake()
        f._irrigation_observe = ChromaMemoryManager._irrigation_observe.__get__(f)
        f._irrigation_observe(["q"], {"ids": [["seul"]], "distances": [[0.1]], "metadatas": [[{}]]})
        assert not log.exists() or log.read_text(encoding="utf-8").strip() == ""

    def test_observe_logge_un_delta_multi(self, monkeypatch, tmp_path):
        # Avec >=2 docs : une ligne de delta est écrite (ordre cosinus vs irrigué).
        import json
        from core.vector_store import ChromaMemoryManager
        irr.reset()
        log = tmp_path / "irr.jsonl"
        monkeypatch.setattr(irr, "IRRIGATION_LOG_PATH", str(log))

        class _Fake:
            pass
        f = _Fake()
        f._irrigation_observe = ChromaMemoryManager._irrigation_observe.__get__(f)
        result = {
            "ids": [["a", "b"]],
            "distances": [[0.2, 0.3]],
            "metadatas": [[{"source": "strategist"}, {"tier_status": "PREMIUM"}]],
        }
        f._irrigation_observe(["ma requête"], result)
        assert log.exists()
        rec = json.loads(log.read_text(encoding="utf-8").strip().splitlines()[0])
        assert rec["cosine_order"] == ["a", "b"]
        assert "irrigated_order" in rec and "perfusion" in rec
        # le résultat passé n'est pas muté (observe = lecture seule)
        assert result["ids"] == [["a", "b"]]

    def test_commutateurs_defaut_surs(self):
        # En prod, par défaut (env non positionné) : SHADOW ON (on observe),
        # ACTIVE OFF (on n'agit jamais sur le rappel servi en Phase 1).
        import os
        assert (os.getenv("IRRIGATION_SHADOW", "1") != "0") is True
        assert (os.getenv("IRRIGATION_ACTIVE", "0") == "1") is False
