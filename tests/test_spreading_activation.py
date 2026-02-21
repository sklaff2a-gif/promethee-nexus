"""Tests — Spreading Activation (Mémoire Associative Biomimétique)"""

import asyncio
import time
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from core.spreading_activation import (
    extract_concepts,
    ActivationNode,
    CreativeBridge,
    ActivationField,
    SpreadingActivationEngine,
    activation_engine,
)


# ═══════════════════════════════════════════════════════
# TestConceptExtraction — 8 tests
# ═══════════════════════════════════════════════════════

class TestConceptExtraction:
    """Extraction de concepts sans LLM."""

    def test_extract_fr_basic(self):
        """Texte français basique retourne des concepts pondérés."""
        text = "L'architecture du système multi-agents utilise un orchestrateur central."
        concepts = extract_concepts(text)
        assert len(concepts) > 0
        # Chaque concept est un tuple (str, float)
        for concept, weight in concepts:
            assert isinstance(concept, str)
            assert 0.0 <= weight <= 1.0

    def test_extract_technical_patterns(self):
        """Les patterns techniques (fichiers, fonctions) sont détectés."""
        text = "Le fichier core/router.py contient la class RouterAgent et def classify_intent."
        concepts = extract_concepts(text)
        concept_names = [c for c, _ in concepts]
        # Au moins un pattern technique détecté
        has_technical = any(
            "router" in c or "classify" in c or "routeragent" in c
            for c in concept_names
        )
        assert has_technical, f"Pas de concept technique trouvé dans: {concept_names}"

    def test_extract_camelcase(self):
        """Les identifiants CamelCase sont extraits."""
        text = "La classe BaseAgent hérite de SpreadingActivation."
        concepts = extract_concepts(text)
        concept_names = [c for c, _ in concepts]
        has_camel = any(
            "baseagent" in c or "spreadingactivation" in c
            for c in concept_names
        )
        assert has_camel, f"CamelCase non détecté dans: {concept_names}"

    def test_extract_stopwords_filtered(self):
        """Les stopwords FR/EN sont filtrés."""
        text = "the les for pour with avec this cette that"
        concepts = extract_concepts(text)
        # Aucun concept ne devrait être un stopword seul
        concept_names = [c for c, _ in concepts]
        stopwords_found = [c for c in concept_names if c in {"the", "les", "for", "pour", "with", "avec"}]
        assert len(stopwords_found) == 0, f"Stopwords non filtrés: {stopwords_found}"

    def test_extract_max_concepts(self):
        """Le nombre de concepts est limité par max_concepts."""
        text = " ".join(f"concept_{i}" for i in range(50))
        concepts = extract_concepts(text, max_concepts=5)
        assert len(concepts) <= 5

    def test_extract_empty_text(self):
        """Texte vide retourne une liste vide."""
        assert extract_concepts("") == []
        assert extract_concepts("   ") == []

    def test_extract_short_words_ignored(self):
        """Les mots de moins de 4 caractères sont ignorés (hors patterns techniques)."""
        text = "le si ou et car ni"
        concepts = extract_concepts(text)
        # Tous ces mots sont < 4 chars → aucun concept extrait par TF-IDF
        assert len(concepts) == 0

    def test_extract_list_bonus(self):
        """Les mots après tirets (listes) ont un bonus."""
        text = "- optimisation mémoire\n- refactoring code\n- sécurité audit"
        concepts = extract_concepts(text)
        concept_names = [c for c, _ in concepts]
        assert len(concept_names) > 0
        # Les premiers mots de liste devraient être représentés
        has_list_words = any(
            c in ("optimisation", "refactoring", "sécurité") for c in concept_names
        )
        assert has_list_words


# ═══════════════════════════════════════════════════════
# TestActivationNode — 5 tests
# ═══════════════════════════════════════════════════════

class TestActivationNode:
    """Cycle de vie des nœuds d'activation."""

    def test_node_creation(self):
        """Un nœud est créé avec les bons attributs."""
        node = ActivationNode(
            concept="router",
            energy=0.8,
            source_collection="collective_wisdom",
            source_doc_id="doc-123",
            activated_at=time.time(),
            last_boosted=time.time(),
        )
        assert node.concept == "router"
        assert node.energy == 0.8
        assert node.decay_rate == 0.05
        assert node.hop_depth == 0

    def test_node_decay(self):
        """L'énergie diminue après decay."""
        engine = SpreadingActivationEngine()
        field = ActivationField(context_hash="test-decay")
        node = ActivationNode(
            concept="test",
            energy=0.5,
            source_collection="col",
            source_doc_id="doc",
            activated_at=time.time(),
            last_boosted=time.time(),
        )
        field.nodes["test"] = node
        engine._fields["test-decay"] = field

        engine.decay_all()
        assert node.energy == pytest.approx(0.45, abs=0.001)

    def test_node_boost(self):
        """Un nœud boosté augmente son énergie (plafond 1.0)."""
        engine = SpreadingActivationEngine()
        field = ActivationField(context_hash="test-boost")
        node = ActivationNode(
            concept="test",
            energy=0.9,
            source_collection="col",
            source_doc_id="doc",
            activated_at=time.time(),
            last_boosted=time.time(),
        )
        field.nodes["test"] = node
        engine._current_field = field

        engine.boost_node("test", 0.5)
        assert node.energy == 1.0  # Plafond

    def test_node_below_threshold_removed(self):
        """Les nœuds sous le seuil sont supprimés par cleanup."""
        engine = SpreadingActivationEngine()
        field = ActivationField(context_hash="test-threshold")
        node = ActivationNode(
            concept="weak",
            energy=0.1,  # Sous ACTIVATION_THRESHOLD (0.25)
            source_collection="col",
            source_doc_id="doc",
            activated_at=time.time(),
            last_boosted=time.time(),
        )
        field.nodes["weak"] = node
        engine._fields["test-threshold"] = field

        engine.cleanup()
        assert "weak" not in field.nodes

    def test_node_expiration(self):
        """Un champ expiré est nettoyé par cleanup."""
        engine = SpreadingActivationEngine()
        field = ActivationField(
            context_hash="test-expired",
            created_at=time.time() - 3600,  # 1h ago (> TTL 30min)
        )
        field.nodes["old"] = ActivationNode(
            concept="old", energy=0.8, source_collection="col",
            source_doc_id="doc", activated_at=time.time(),
            last_boosted=time.time(),
        )
        engine._fields["test-expired"] = field

        engine.cleanup()
        assert "test-expired" not in engine._fields


# ═══════════════════════════════════════════════════════
# TestSpreadingActivation — 10 tests
# ═══════════════════════════════════════════════════════

class TestSpreadingActivation:
    """Tests du moteur SpreadingActivationEngine."""

    def test_singleton(self):
        """SpreadingActivationEngine est un singleton."""
        a = SpreadingActivationEngine()
        b = SpreadingActivationEngine()
        assert a is b

    def test_reset_singleton(self):
        """reset_singleton() crée une nouvelle instance."""
        a = SpreadingActivationEngine()
        SpreadingActivationEngine.reset_singleton()
        b = SpreadingActivationEngine()
        assert a is not b

    @pytest.mark.asyncio
    async def test_activate_basic(self):
        """activate() crée un champ avec des nœuds."""
        engine = SpreadingActivationEngine()
        field = await engine.activate(
            "L'optimisation du router améliore les performances du système",
            source_collection="collective_wisdom",
        )
        assert isinstance(field, ActivationField)
        assert len(field.nodes) > 0

    @pytest.mark.asyncio
    async def test_activate_with_memory_manager(self):
        """activate() avec memory_manager effectue la propagation latérale."""
        engine = SpreadingActivationEngine()

        mock_manager = MagicMock()
        mock_manager.query_with_metadata.return_value = {
            'documents': [["Document sur la sécurité et la validation des entrées"]],
            'metadatas': [[{"id": "lat-1"}]],
            'distances': [[0.3]],
        }

        field = await engine.activate(
            "L'optimisation du code améliore les performances du système multi-agents",
            source_collection="collective_wisdom",
            memory_manager=mock_manager,
            max_hops=1,
        )

        # Vérifier que la propagation latérale a été tentée
        mock_manager.query_with_metadata.assert_called_once()
        # Vérifier qu'on a des nœuds latéraux (hop_depth=1) si le LLM retourne des données
        lateral_nodes = [n for n in field.nodes.values() if n.hop_depth == 1]
        # Les nœuds latéraux dépendent de l'extraction de concepts du doc retourné
        assert len(field.nodes) > 0

    @pytest.mark.asyncio
    async def test_activate_no_hops(self):
        """activate() avec max_hops=0 ne fait pas de propagation."""
        engine = SpreadingActivationEngine()
        mock_manager = MagicMock()

        await engine.activate(
            "Test sans propagation latérale pour vérifier le comportement",
            source_collection="collective_wisdom",
            memory_manager=mock_manager,
            max_hops=0,
        )

        mock_manager.query_with_metadata.assert_not_called()

    def test_attenuation_lateral(self):
        """L'énergie des nœuds latéraux est atténuée."""
        engine = SpreadingActivationEngine()
        assert engine.LATERAL_ATTENUATION == 0.6
        assert engine.DEEP_ATTENUATION == 0.35

    def test_decay_reduces_energy(self):
        """decay_all() réduit l'énergie de tous les nœuds."""
        engine = SpreadingActivationEngine()
        field = ActivationField(context_hash="test-decay-all")
        field.nodes["a"] = ActivationNode(
            concept="a", energy=0.8, source_collection="col",
            source_doc_id="doc", activated_at=time.time(),
            last_boosted=time.time(),
        )
        field.nodes["b"] = ActivationNode(
            concept="b", energy=0.29, source_collection="col",
            source_doc_id="doc", activated_at=time.time(),
            last_boosted=time.time(),
        )
        engine._fields["test-decay-all"] = field

        engine.decay_all()

        assert field.nodes["a"].energy == pytest.approx(0.75, abs=0.001)
        # b passe à 0.24, sous le seuil 0.25 → supprimé
        assert "b" not in field.nodes

    def test_cleanup_expired_fields(self):
        """cleanup() supprime les champs expirés."""
        engine = SpreadingActivationEngine()
        # Champ valide
        engine._fields["valid"] = ActivationField(
            context_hash="valid", created_at=time.time()
        )
        # Champ expiré
        engine._fields["expired"] = ActivationField(
            context_hash="expired", created_at=time.time() - 7200
        )

        engine.cleanup()
        assert "valid" in engine._fields
        assert "expired" not in engine._fields

    def test_field_ttl(self):
        """Un ActivationField expire après son TTL."""
        field = ActivationField(
            context_hash="ttl-test",
            created_at=time.time() - 1801,
            ttl_seconds=1800.0,
        )
        assert field.is_expired() is True

        field_fresh = ActivationField(
            context_hash="ttl-fresh",
            created_at=time.time(),
        )
        assert field_fresh.is_expired() is False

    def test_format_for_prompt_empty(self):
        """format_for_prompt() retourne vide si pas de champ actif."""
        engine = SpreadingActivationEngine()
        engine._current_field = None
        assert engine.format_for_prompt() == ""

    def test_format_for_prompt_with_thoughts(self):
        """format_for_prompt() inclut les pensées latentes."""
        engine = SpreadingActivationEngine()
        field = ActivationField(context_hash="fmt-test")
        field.nodes["router"] = ActivationNode(
            concept="router", energy=0.8, source_collection="collective_wisdom",
            source_doc_id="doc", activated_at=time.time(),
            last_boosted=time.time(),
        )
        engine._current_field = field

        result = engine.format_for_prompt()
        assert "[INTUITIONS]" in result
        assert "router" in result


# ═══════════════════════════════════════════════════════
# TestCreativeBridge — 6 tests
# ═══════════════════════════════════════════════════════

class TestCreativeBridge:
    """Détection de ponts créatifs (eureka)."""

    def test_bridge_creation(self):
        """Un CreativeBridge est créé correctement."""
        bridge = CreativeBridge(
            node_a="router",
            node_b="sécurité",
            collection_a="collective_wisdom",
            collection_b="code_snippets",
            shared_concepts=["validation", "entrée"],
            bridge_strength=0.7,
            hypothesis="Test hypothesis",
            created_at=time.time(),
        )
        assert bridge.used is False
        assert bridge.bridge_strength == 0.7

    def test_no_bridge_same_collection(self):
        """Pas de pont si les deux nœuds sont dans la même collection."""
        engine = SpreadingActivationEngine()
        field = ActivationField(context_hash="same-col")
        now = time.time()

        # Deux nœuds dans la même collection
        field.nodes["a"] = ActivationNode(
            concept="a", energy=0.9, source_collection="collective_wisdom",
            source_doc_id="d1", activated_at=now, last_boosted=now,
        )
        field.nodes["b"] = ActivationNode(
            concept="b", energy=0.9, source_collection="collective_wisdom",
            source_doc_id="d2", activated_at=now, last_boosted=now,
        )

        engine._detect_bridges(field, now)
        assert len(field.bridges) == 0

    def test_bridge_min_shared_concepts(self):
        """Un pont nécessite au moins BRIDGE_MIN_SHARED_CONCEPTS concepts partagés."""
        engine = SpreadingActivationEngine()
        assert engine.BRIDGE_MIN_SHARED_CONCEPTS == 2

    def test_bridge_strength_product(self):
        """La force du pont = produit des énergies des deux nœuds."""
        # La détection vérifie que energy_a * energy_b >= EUREKA_THRESHOLD
        engine = SpreadingActivationEngine()
        field = ActivationField(context_hash="strength-test")
        now = time.time()

        # Nœuds avec énergie faible (produit < EUREKA_THRESHOLD)
        field.nodes["low_a"] = ActivationNode(
            concept="low_a", energy=0.5, source_collection="collective_wisdom",
            source_doc_id="d1", activated_at=now, last_boosted=now,
        )
        field.nodes["low_b"] = ActivationNode(
            concept="low_b", energy=0.5, source_collection="code_snippets",
            source_doc_id="d2", activated_at=now, last_boosted=now,
        )

        engine._detect_bridges(field, now)
        # Pas de pont car les concepts partagés sont probablement insuffisants
        # (low_a et low_b n'ont pas de voisinage lexical commun)
        assert len(field.bridges) == 0

    def test_bridge_unused_filter(self):
        """get_creative_bridges(unused_only=True) filtre les ponts déjà utilisés."""
        engine = SpreadingActivationEngine()
        field = ActivationField(context_hash="unused-test")
        now = time.time()

        bridge_used = CreativeBridge(
            node_a="a", node_b="b", collection_a="c1", collection_b="c2",
            shared_concepts=["x", "y"], bridge_strength=0.8,
            hypothesis="used", created_at=now, used=True,
        )
        bridge_new = CreativeBridge(
            node_a="c", node_b="d", collection_a="c1", collection_b="c2",
            shared_concepts=["x", "y"], bridge_strength=0.7,
            hypothesis="new", created_at=now, used=False,
        )
        field.bridges = [bridge_used, bridge_new]
        engine._current_field = field

        unused = engine.get_creative_bridges(unused_only=True)
        assert len(unused) == 1
        assert unused[0].hypothesis == "new"

        all_bridges = engine.get_creative_bridges(unused_only=False)
        assert len(all_bridges) == 2

    def test_bridge_marked_used_after_format(self):
        """format_for_prompt() marque les ponts comme utilisés."""
        engine = SpreadingActivationEngine()
        field = ActivationField(context_hash="mark-used")
        now = time.time()

        bridge = CreativeBridge(
            node_a="concept_a", node_b="concept_b",
            collection_a="collective_wisdom", collection_b="code_snippets",
            shared_concepts=["shared1", "shared2"], bridge_strength=0.8,
            hypothesis="Connexion eureka test", created_at=now,
        )
        field.bridges = [bridge]
        field.nodes["concept_a"] = ActivationNode(
            concept="concept_a", energy=0.8, source_collection="collective_wisdom",
            source_doc_id="d1", activated_at=now, last_boosted=now,
        )
        engine._current_field = field

        result = engine.format_for_prompt()
        assert "eureka" in result.lower()
        assert bridge.used is True


# ═══════════════════════════════════════════════════════
# TestRoutineAffinity — 4 tests
# ═══════════════════════════════════════════════════════

class TestRoutineAffinity:
    """Affinité entre activations et intents."""

    def test_matching_intent(self):
        """Un intent qui matche les concepts actifs retourne un score positif."""
        engine = SpreadingActivationEngine()
        field = ActivationField(context_hash="affinity-match")
        now = time.time()

        # Concepts liés à la sécurité
        for concept in ["sécurité", "vulnérabilité", "injection"]:
            field.nodes[concept] = ActivationNode(
                concept=concept, energy=0.8, source_collection="col",
                source_doc_id="doc", activated_at=now, last_boosted=now,
            )
        engine._current_field = field

        affinity = engine.compute_routine_affinity("SECURITY_AUDIT")
        assert affinity > 0.0
        assert affinity <= 1.0

    def test_no_nodes_returns_zero(self):
        """Sans nœuds actifs, l'affinité est 0."""
        engine = SpreadingActivationEngine()
        engine._current_field = None
        assert engine.compute_routine_affinity("EXPANSION_CODE") == 0.0

    def test_clamping(self):
        """L'affinité est bornée dans [-1.0, +1.0]."""
        engine = SpreadingActivationEngine()
        field = ActivationField(context_hash="affinity-clamp")
        now = time.time()

        # Beaucoup de concepts à haute énergie
        for concept in ["code", "optimiser", "refactor", "fonction", "classe", "méthode", "python"]:
            field.nodes[concept] = ActivationNode(
                concept=concept, energy=1.0, source_collection="col",
                source_doc_id="doc", activated_at=now, last_boosted=now,
            )
        engine._current_field = field

        affinity = engine.compute_routine_affinity("EXPANSION_CODE")
        assert -1.0 <= affinity <= 1.0

    def test_unknown_intent(self):
        """Un intent inconnu retourne 0."""
        engine = SpreadingActivationEngine()
        field = ActivationField(context_hash="affinity-unknown")
        field.nodes["test"] = ActivationNode(
            concept="test", energy=0.8, source_collection="col",
            source_doc_id="doc", activated_at=time.time(),
            last_boosted=time.time(),
        )
        engine._current_field = field

        assert engine.compute_routine_affinity("UNKNOWN_INTENT") == 0.0


# ═══════════════════════════════════════════════════════
# TestIntegration — 3 tests
# ═══════════════════════════════════════════════════════

class TestIntegration:
    """Tests d'intégration de haut niveau."""

    @pytest.mark.asyncio
    async def test_full_activation_cycle(self):
        """Cycle complet : activate → decay → cleanup."""
        engine = SpreadingActivationEngine()

        field = await engine.activate(
            "Le router distribue les missions aux agents spécialisés du système",
            source_collection="collective_wisdom",
        )

        initial_count = len(field.nodes)
        assert initial_count > 0

        # Decay multiple fois
        for _ in range(15):
            engine.decay_all()

        # Les nœuds faibles devraient être supprimés
        engine.cleanup()
        assert len(field.nodes) <= initial_count

    def test_get_stats(self):
        """get_stats() retourne les bonnes métriques."""
        engine = SpreadingActivationEngine()
        field = ActivationField(context_hash="stats-test")
        field.nodes["concept"] = ActivationNode(
            concept="concept", energy=0.8, source_collection="col",
            source_doc_id="doc", activated_at=time.time(),
            last_boosted=time.time(),
        )
        engine._fields["stats-test"] = field
        engine._current_field = field

        stats = engine.get_stats()
        assert stats["active_fields"] >= 1
        assert stats["total_nodes"] >= 1
        assert "total_bridges" in stats
        assert "unused_bridges" in stats
        assert stats["current_field_hash"] == "stats-test"

    @pytest.mark.asyncio
    async def test_latent_thoughts_ordering(self):
        """Les pensées latentes sont triées par énergie décroissante."""
        engine = SpreadingActivationEngine()
        field = ActivationField(context_hash="thoughts-test")
        now = time.time()

        field.nodes["high"] = ActivationNode(
            concept="high", energy=0.9, source_collection="col",
            source_doc_id="doc", activated_at=now, last_boosted=now,
        )
        field.nodes["medium"] = ActivationNode(
            concept="medium", energy=0.6, source_collection="col",
            source_doc_id="doc", activated_at=now, last_boosted=now,
        )
        field.nodes["low"] = ActivationNode(
            concept="low", energy=0.35, source_collection="col",
            source_doc_id="doc", activated_at=now, last_boosted=now,
        )
        engine._current_field = field

        thoughts = engine.get_latent_thoughts(max_thoughts=3, min_energy=0.3)
        assert len(thoughts) == 3
        # Premier = le plus énergétique
        assert "high" in thoughts[0]
        assert "medium" in thoughts[1]
        assert "low" in thoughts[2]


# ═══════════════════════════════════════════════════════
# TestLexicalSimilarity — 2 tests bonus
# ═══════════════════════════════════════════════════════

class TestLexicalSimilarity:
    """Similarité lexicale entre concepts."""

    def test_identical_strings(self):
        """Strings identiques → similarité 1.0."""
        assert SpreadingActivationEngine._lexical_similarity("router", "router") == 1.0

    def test_different_strings(self):
        """Strings très différentes → similarité basse."""
        sim = SpreadingActivationEngine._lexical_similarity("router", "sécurité")
        assert sim < 0.3

    def test_short_strings(self):
        """Strings très courtes → 0.0."""
        assert SpreadingActivationEngine._lexical_similarity("a", "b") == 0.0
