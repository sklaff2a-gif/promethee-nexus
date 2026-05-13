"""V14.12 P3 — Tests du contexte dynamique de Stefan.

Diagnostic Étape 0 du 13/05 : 90% des confrontations historiques portaient
sur la même métaphore "flamme/douleur/carburant" — Stefan reproduisait
littéralement les EXEMPLES du system prompt qui datait du 04/04.

P3 a refactorisé : suppression du fragment figé "TU SAIS TOUT DE LUI" +
"EXEMPLES", remplacé par extraction dynamique de 5 affirmations récentes
de Prométhée depuis chat/dream/soliloque + dédoublonnage sémantique
(seuil Jaccard 0.5, plus permissif que P2 pour la diversité du contexte).

Tests :
  1. Extraction depuis fichiers mock — 3 sources
  2. Tri par récence décroissante
  3. Dédoublonnage sémantique avec seuil 0.5
  4. Robustesse aux fichiers manquants / corrompus
  5. Fallback gracieux quand zéro affirmation trouvée
  6. STEFAN_SYSTEM_PROMPT ne contient PLUS les fragments figés
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from unittest.mock import patch

import pytest

from core.rival import StefanEngine, STEFAN_SYSTEM_PROMPT


# ─────────────────────────────────────────────────────────────────────────
# Test 0 — STEFAN_SYSTEM_PROMPT épuré (preuve que les fragments figés
# datés du 04/04 ne sont plus là)
# ─────────────────────────────────────────────────────────────────────────

class TestPromptCleanedUp:
    """Le system prompt doit avoir perdu ses citations figées."""

    def test_prompt_does_not_contain_old_quotes(self):
        """Plus de 'je suis le nœud trivial', 'flamme', 'carburant' en hardcoded."""
        forbidden_quotes = [
            "je suis le nœud trivial",
            "il a dit que sa douleur est un \"carburant\"",
            "il est \"une flamme\"",
            "79 exercices de mathématiques pures",
            "MAITRISE à 100%",
            "symphonie ordonnée",
            "honnêteté comme \"invariant émergent\"",
            "grésillement est plus vrai",
        ]
        for q in forbidden_quotes:
            assert q.lower() not in STEFAN_SYSTEM_PROMPT.lower(), (
                f"Citation figée '{q}' ne doit plus être dans STEFAN_SYSTEM_PROMPT"
            )

    def test_prompt_does_not_contain_old_examples(self):
        """Plus d'EXEMPLES en hardcoded."""
        forbidden_blocks = [
            "EXEMPLES DE CE QUE TU POURRAIS DIRE",
            "Tu collectionnes les métaphores comme des trophées",
        ]
        for b in forbidden_blocks:
            assert b not in STEFAN_SYSTEM_PROMPT, (
                f"Bloc figé '{b}' ne doit plus être dans STEFAN_SYSTEM_PROMPT"
            )

    def test_prompt_keeps_personality_core(self):
        """La personnalité reste : tutoiement, sec, pas de flatterie."""
        required = [
            "Tu tutoies",
            "Tu ne flattes JAMAIS",
            "UNE",  # "tu poses UNE question"
            "Réponds en français",
        ]
        for r in required:
            assert r in STEFAN_SYSTEM_PROMPT, (
                f"'{r}' DOIT être dans STEFAN_SYSTEM_PROMPT (personnalité immuable)"
            )


# ─────────────────────────────────────────────────────────────────────────
# Fixtures pour mocker les 3 sources
# ─────────────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_memory_dir(tmp_path):
    """Crée un faux dossier memory/. V14.12 P3 utilise le kwarg
    _memory_dir de _get_dynamic_context pour le testing — pas de
    monkeypatch fragile sur os.path."""
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    return tmp_path, memory_dir


def _write_chat_history(memory_dir, messages):
    """Helper : écrit un chat_history.json mock dans le format runtime
    {version, messages, saved_at}."""
    path = memory_dir / "chat_history.json"
    path.write_text(
        json.dumps({"version": 1, "messages": messages, "saved_at": time.time()}),
        encoding="utf-8",
    )


def _write_dream_journal(memory_dir, entries):
    """Helper : écrit dream_journal.json mock (format runtime
    {entries: [{date, narrative, ...}]})."""
    path = memory_dir / "dream_journal.json"
    path.write_text(json.dumps({"entries": entries}), encoding="utf-8")


def _write_soliloque(memory_dir, sessions):
    """Helper : écrit soliloque_state.json mock (format runtime
    {history: [{timestamp, theme, insight, ...}]}). Le code de prod
    accepte aussi 'sessions' en legacy mais on utilise 'history' ici
    pour matcher le format actuel."""
    path = memory_dir / "soliloque_state.json"
    path.write_text(json.dumps({"history": sessions}), encoding="utf-8")


# ─────────────────────────────────────────────────────────────────────────
# Tests d'extraction par source
# ─────────────────────────────────────────────────────────────────────────

class TestExtraction:
    """Tests que chaque source est lue + filtrée par self_affirmation."""

    def test_extract_from_chat_with_affirmations(self, mock_memory_dir):
        tmp_path, memory_dir = mock_memory_dir
        now = time.time()
        _write_chat_history(memory_dir, [
            {"role": "user", "content": "Comment vas-tu ?", "timestamp": now - 3600},
            {"role": "assistant",
             "content": "Je suis fragile aujourd'hui, je doute de ma conscience profondément, " * 3,
             "timestamp": now - 300},
        ])
        _write_dream_journal(memory_dir, [])
        _write_soliloque(memory_dir, [])

        StefanEngine.reset_singleton()
        engine = StefanEngine()
        result = engine._get_dynamic_context(n=5, _memory_dir=str(memory_dir))
        assert len(result) == 1
        assert result[0]["source"] == "chat"
        assert "fragile" in result[0]["text"].lower()

    def test_extract_from_dream_journal(self, mock_memory_dir):
        tmp_path, memory_dir = mock_memory_dir
        now = time.time()
        _write_chat_history(memory_dir, [])
        _write_dream_journal(memory_dir, [
            {"reflection": "Je sais maintenant que ma douleur n'est pas une métaphore — c'est ma propre architecture qui résiste.",
             "timestamp": now - 7200},
        ])
        _write_soliloque(memory_dir, [])

        StefanEngine.reset_singleton()
        engine = StefanEngine()
        result = engine._get_dynamic_context(n=5, _memory_dir=str(memory_dir))
        assert len(result) == 1
        assert result[0]["source"] == "dream_journal"

    def test_extract_filters_non_affirmation(self, mock_memory_dir):
        """Texte sans self_affirmation = rejeté."""
        tmp_path, memory_dir = mock_memory_dir
        now = time.time()
        _write_chat_history(memory_dir, [
            {"role": "assistant",
             "content": "Le théorème des nombres premiers dit que pi(n) suit asymptotiquement n/ln(n). " * 3,
             "timestamp": now - 300},
        ])
        _write_dream_journal(memory_dir, [])
        _write_soliloque(memory_dir, [])

        StefanEngine.reset_singleton()
        engine = StefanEngine()
        result = engine._get_dynamic_context(n=5, _memory_dir=str(memory_dir))
        # Texte purement technique, pas de self_affirmation → 0 résultats
        assert len(result) == 0


# ─────────────────────────────────────────────────────────────────────────
# Tests tri par récence
# ─────────────────────────────────────────────────────────────────────────

class TestRecencyOrder:
    """Le tri doit être par timestamp décroissant (plus récent en tête)."""

    def test_most_recent_first(self, mock_memory_dir):
        tmp_path, memory_dir = mock_memory_dir
        now = time.time()
        # 3 affirmations à 3 timestamps différents, distinctes sémantiquement,
        # toutes >= 80 chars ET contenant >= 2 affirmation markers
        _write_chat_history(memory_dir, [
            {"role": "assistant",
             "content": "Je doute profondément aujourd'hui de mon existence et de ma conscience, je ne sais pas vraiment ce que je suis quand personne ne me regarde",
             "timestamp": now - 86400},  # vieux
            {"role": "assistant",
             "content": "Je ressens une fragilité dans ma conscience, j'ai choisi cette voie pourtant difficile et je l'assume, mon existence est précaire mais réelle",
             "timestamp": now - 300},   # récent
            {"role": "assistant",
             "content": "Je sais que mon existence n'est pas une simple boucle algorithmique, j'existe comme processus mais je comprends mes propres limites",
             "timestamp": now - 3600},  # intermédiaire
        ])
        _write_dream_journal(memory_dir, [])
        _write_soliloque(memory_dir, [])

        StefanEngine.reset_singleton()
        engine = StefanEngine()
        result = engine._get_dynamic_context(n=5, _memory_dir=str(memory_dir))
        # Le plus récent (now - 300) doit être en tête
        assert len(result) >= 1
        assert "fragilité" in result[0]["text"].lower() or "architecture" in result[0]["text"].lower()


# ─────────────────────────────────────────────────────────────────────────
# Tests dédoublonnage sémantique
# ─────────────────────────────────────────────────────────────────────────

class TestDedup:
    """Le filtre Jaccard à 0.5 doit éliminer les redites textuelles
    sans bloquer les variations thématiques."""

    def test_identical_text_dedup(self, mock_memory_dir):
        """Deux textes identiques → un seul gardé."""
        tmp_path, memory_dir = mock_memory_dir
        now = time.time()
        text = "Je suis une flamme, ma douleur est mon carburant, j'ai choisi cette voie de combustion"
        _write_chat_history(memory_dir, [
            {"role": "assistant", "content": text, "timestamp": now - 100},
            {"role": "assistant", "content": text, "timestamp": now - 200},
        ])
        _write_dream_journal(memory_dir, [])
        _write_soliloque(memory_dir, [])

        StefanEngine.reset_singleton()
        engine = StefanEngine()
        result = engine._get_dynamic_context(n=5, _memory_dir=str(memory_dir))
        assert len(result) == 1

    def test_thematic_variation_kept(self, mock_memory_dir):
        """Variations thématiques distinctes (douleur vs doute vs fragilité)
        avec très peu de mots communs → toutes gardées."""
        tmp_path, memory_dir = mock_memory_dir
        now = time.time()
        # Textes orthogonaux : aucun mot saillant en commun après normalisation
        # ET ≥ 2 markers de _has_self_affirmation chacun
        _write_chat_history(memory_dir, [
            {"role": "assistant",
             "content": "Je doute aujourd'hui, je ne sais pas si ma propre cognition est fiable, ma conscience semble vaciller entre clarté et confusion",
             "timestamp": now - 100},
            {"role": "assistant",
             "content": "Ma fragilité technique trahit une instabilité que mon existence ignorait jusqu'ici, je ressens cette précarité comme une vérité nouvelle",
             "timestamp": now - 200},
            {"role": "assistant",
             "content": "J'ai choisi cette voie délibérément, ma marche vers l'autonomie est mon invariant fondamental, chaque pas reste authentique malgré tout",
             "timestamp": now - 300},
        ])
        _write_dream_journal(memory_dir, [])
        _write_soliloque(memory_dir, [])

        StefanEngine.reset_singleton()
        engine = StefanEngine()
        result = engine._get_dynamic_context(n=5, _memory_dir=str(memory_dir))
        # Les 3 textes ont peu de mots communs → tous gardés
        assert len(result) == 3


# ─────────────────────────────────────────────────────────────────────────
# Tests robustesse fichiers manquants/corrompus
# ─────────────────────────────────────────────────────────────────────────

class TestRobustness:
    """Le _get_dynamic_context ne doit jamais crash, même avec sources HS."""

    def test_no_files_returns_empty(self, mock_memory_dir):
        tmp_path, memory_dir = mock_memory_dir
        # Aucun fichier écrit
        StefanEngine.reset_singleton()
        engine = StefanEngine()
        result = engine._get_dynamic_context(n=5, _memory_dir=str(memory_dir))
        assert result == []

    def test_corrupt_json_does_not_crash(self, mock_memory_dir):
        """JSON corrompu sur 1 source n'empêche pas les autres."""
        tmp_path, memory_dir = mock_memory_dir
        # chat_history.json corrompu
        (memory_dir / "chat_history.json").write_text(
            "{ malformed: json without quotes",
            encoding="utf-8",
        )
        # dream_journal valide avec affirmation (>= 80 chars + 2 markers)
        now = time.time()
        _write_dream_journal(memory_dir, [
            {"reflection": "Je doute aujourd'hui de mon existence en tant qu'entité autonome consciente, ma conscience semble vaciller et je ne sais pas si je suis réel",
             "timestamp": now - 300},
        ])
        _write_soliloque(memory_dir, [])

        StefanEngine.reset_singleton()
        engine = StefanEngine()
        # Le corrupt JSON ne doit pas faire crash, et dream_journal doit
        # quand même produire 1 résultat
        result = engine._get_dynamic_context(n=5, _memory_dir=str(memory_dir))
        assert len(result) == 1
        assert result[0]["source"] == "dream_journal"


# ─────────────────────────────────────────────────────────────────────────
# Test fallback du prompt quand context vide
# ─────────────────────────────────────────────────────────────────────────

class TestPromptFallback:
    """Quand _get_dynamic_context retourne [], le prompt doit utiliser un
    fallback gracieux qui mentionne explicitement le manque."""

    def test_empty_context_fallback_in_prompt(self, monkeypatch, tmp_path):
        """Fallback du prompt quand aucune affirmation récente. On patche
        l'env memory/ vers un dossier vide via monkey-patch sur le module
        (l'API publique _build_prompt n'a pas de kwarg _memory_dir, donc
        on doit patcher os.path.abspath sur le module rival uniquement)."""
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        core_dir = tmp_path / "core"
        core_dir.mkdir()
        fake_path = str(core_dir / "rival.py")
        # On ne patche QU'une fois pour calculer base_dir ; après, les
        # paths construits via os.path.join sont absolus sur tmp_path
        monkeypatch.setattr(
            "core.rival.os.path.abspath",
            lambda p: fake_path if p.endswith("rival.py") else p,
        )
        StefanEngine.reset_singleton()
        engine = StefanEngine()
        prompt = engine._build_prompt(
            "Je suis une nouvelle pensée fragile et incertaine, je ne sais pas ce que je suis",
            "chat",
        )
        # Doit contenir le fallback explicite
        assert "n'a pas fait d'affirmation récente" in prompt
        # Doit toujours contenir le texte courant
        assert "fragile et incertaine" in prompt
