"""Tests V8.1 (2026-04-21) : correctifs de persistance et d'ordre.

Fix 1 (Router V7.0) : la migration au boot doit persister sur disque,
  pas attendre un on_council_rule_learned qui peut ne jamais arriver.

Fix 2 (Autonomy V8.0) : _nap_was_productive doit etre robuste a l'ordre
  des operations dans exit_nap (qui vide self._nap_tasks_done avant le
  check). Avant V8.1 -> 100% siestes classees non-productives.
"""
import json
import os
from unittest.mock import patch, AsyncMock
import pytest

from core.router import RouterAgent, _keyword_signature
from core.autonomy_engine import AutonomyEngine


# ═══════════════════════════════════════════════════════════════════════
# Fix 1 - Persistance de la migration V7.0
# ═══════════════════════════════════════════════════════════════════════


class TestV7MigrationPersistence:
    """La migration pre-V7 -> V7 doit sauvegarder le nettoyage sur disque."""

    @pytest.fixture
    def tmp_rules_file(self, tmp_path, monkeypatch):
        tmp_file = tmp_path / "council_learned_rules.json"
        monkeypatch.setattr(RouterAgent, "_LEARNED_RULES_FILE", str(tmp_file))
        RouterAgent._learned_rules = []
        yield tmp_file
        RouterAgent._learned_rules = []

    def test_migration_writes_to_disk(self, tmp_rules_file):
        """V8.1 : apres migration (collapsed>0 ou migrated>0), le fichier
        doit etre mis a jour avec le nouvel etat, pas rester avec l'ancien."""
        # Ecriture d'un fichier pre-V7 : 3 regles identiques
        pre_v7 = [
            {"keywords": ["budget", "quotidien"], "agent": "strategist",
             "source": "council_chunking"},
            {"keywords": ["budget", "quotidien"], "agent": "strategist",
             "source": "council_chunking"},
            {"keywords": ["budget", "quotidien"], "agent": "strategist",
             "source": "council_chunking"},
        ]
        with open(tmp_rules_file, "w", encoding="utf-8") as f:
            json.dump(pre_v7, f)

        pre_size = os.path.getsize(tmp_rules_file)

        # Load declenche la migration ET la persistance
        RouterAgent._learned_rules = []
        RouterAgent._load_learned_rules()

        # Re-lire le fichier disque
        with open(tmp_rules_file, "r", encoding="utf-8") as f:
            disk_rules = json.load(f)

        # Doit contenir 1 regle uniquifiee (pas 3)
        assert len(disk_rules) == 1, (
            f"Migration non persistee : {len(disk_rules)} regles sur disque"
        )
        # Avec les champs V7
        assert "signature" in disk_rules[0]
        assert disk_rules[0]["weight"] == 3  # collapse de 3 doublons

    def test_no_migration_no_rewrite(self, tmp_rules_file):
        """Si le fichier est deja au format V7 (pas de migration), pas
        de save inutile. Verification via mtime."""
        # Ecriture d'un fichier deja V7
        v7_rules = [{
            "signature": _keyword_signature(["test", "unique"]),
            "keywords": ["test", "unique"],
            "agent": "coder",
            "source": "council_chunking",
            "weight": 5,
            "created_at": 1000.0,
            "last_seen": 1000.0,
            "last_decision": "",
        }]
        with open(tmp_rules_file, "w", encoding="utf-8") as f:
            json.dump(v7_rules, f)
        mtime_before = os.path.getmtime(tmp_rules_file)

        RouterAgent._learned_rules = []
        # Mock _save_learned_rules pour detecter l'appel
        with patch.object(RouterAgent, "_save_learned_rules") as mock_save:
            RouterAgent._load_learned_rules()
        # Pas de save si rien n'a change
        mock_save.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════
# Fix 2 - _nap_was_productive robuste a l'ordre des operations
# ═══════════════════════════════════════════════════════════════════════


class TestNapProductiveArgumentOverride:
    """V8.1 : _nap_was_productive doit accepter un tasks_done explicite,
    pour contourner le bug d'ordre dans exit_nap qui vide
    self._nap_tasks_done avant le check."""

    def _make_engine(self):
        engine = AutonomyEngine.__new__(AutonomyEngine)
        engine._nap_tasks_done = []
        engine._NAP_MIN_PRODUCTIVE_TASKS = 1
        return engine

    def test_productive_via_argument_even_if_attr_empty(self):
        """Meme si self._nap_tasks_done est vide (bug d'ordre simule),
        si on passe tasks_done=[...], le check retourne True."""
        engine = self._make_engine()
        engine._nap_tasks_done = []  # vide (simule exit_nap ligne 5190)
        # Mais la copie locale a 9 DREAM
        result = engine._nap_was_productive(tasks_done=["DREAM"] * 9)
        assert result is True

    def test_not_productive_via_argument_if_empty(self):
        engine = self._make_engine()
        engine._nap_tasks_done = ["DREAM"]  # peuple (irrelevant)
        # Si tasks_done passe est vide -> non productif
        result = engine._nap_was_productive(tasks_done=[])
        assert result is False

    def test_backward_compat_no_argument(self):
        """Sans argument, fallback sur self._nap_tasks_done (compat pre-V8.1)."""
        engine = self._make_engine()
        engine._nap_tasks_done = ["DREAM", "LORA_CODER"]
        assert engine._nap_was_productive() is True

        engine._nap_tasks_done = []
        assert engine._nap_was_productive() is False

    def test_threshold_tunable_with_argument(self):
        """Le seuil _NAP_MIN_PRODUCTIVE_TASKS marche avec l'argument."""
        engine = self._make_engine()
        engine._NAP_MIN_PRODUCTIVE_TASKS = 3
        # 2 taches via argument : < 3 -> non productif
        assert engine._nap_was_productive(tasks_done=["A", "B"]) is False
        # 3 taches : == 3 -> productif
        assert engine._nap_was_productive(tasks_done=["A", "B", "C"]) is True

    def test_bug_reproduction_v8_0_regression(self):
        """Reproduit le bug V8.0 : exit_nap sauvegarde tasks_done puis
        vide self._nap_tasks_done. Avec V8.1, passer tasks_done corrige."""
        engine = self._make_engine()
        # Simule l'etat apres 9 DREAM pendant la sieste
        engine._nap_tasks_done = ["DREAM"] * 9
        # Copie locale comme dans exit_nap ligne 5187
        tasks_done_snapshot = list(engine._nap_tasks_done)
        # Vidage comme dans exit_nap ligne 5190
        engine._nap_tasks_done = []
        # Avant V8.1 : self._nap_was_productive() retournait False -> bug
        # Apres V8.1 : on passe tasks_done -> retourne True
        assert engine._nap_was_productive(tasks_done=tasks_done_snapshot) is True
        # Sans l'argument (bug reproduit sans le fix applique a l'appelant) :
        assert engine._nap_was_productive() is False  # comme avant V8.1
