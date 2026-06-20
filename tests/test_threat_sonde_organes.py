# -*- coding: utf-8 -*-
"""Sondes de threat des organes — micro-incision 20/06.

Régression du bug `from core.reptilian_core import reptilian` (nom inexistant →
ImportError avalé → threat invisible) sur base_agent (contexte forward-pass) et
body_schema (tampon proprioceptif). On lit désormais l'organe vivant via
get_organ("reptilian"). (Le 3e site, digestion_routine Porte A, est DIFFÉRÉ : il
collisionne avec le Fantôme process_mem live.)
"""
import types


def _fake_reptile(threat=5.0):
    return types.SimpleNamespace(threat_level=threat, get_threat_level=lambda: threat)


class TestBodySchemaSonde:
    def test_gather_state_lit_le_threat(self, monkeypatch):
        import core.organ_registry as reg
        from core.body_schema import gather_state
        monkeypatch.setattr(reg, "get_organ",
                            lambda name: _fake_reptile(5.0) if name == "reptilian" else None)
        st = gather_state()
        assert st["reptilian"]["threat_level"] == 5.0   # plus aveugle


class TestBaseAgentSonde:
    def test_snapshot_injecte_le_threat(self, monkeypatch):
        import core.organ_registry as reg
        from core.base_agent import BaseAgent
        monkeypatch.setattr(reg, "get_organ",
                            lambda name: _fake_reptile(5.0) if name == "reptilian" else None)
        st = BaseAgent._snapshot_bio_state()
        assert st.get("threat") == 5.0

    def test_snapshot_pas_de_threat_sous_seuil(self, monkeypatch):
        import core.organ_registry as reg
        from core.base_agent import BaseAgent
        monkeypatch.setattr(reg, "get_organ",
                            lambda name: _fake_reptile(0.2) if name == "reptilian" else None)
        st = BaseAgent._snapshot_bio_state()
        assert "threat" not in st   # 0.2 < seuil 0.3 → non injecté
