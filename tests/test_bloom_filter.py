"""Tests V4.2 Bloom Filter.

Verifie :
  - Operations Bloom basiques (add, contains)
  - Zero faux negatif (propriete absolue)
  - Extraction des references (regex)
  - Veto strict : 1 faux negatif suffit
  - Fallback gracieux si index non construit
  - Overhead < 1 ms par check_prompt
"""
import os

os.environ.setdefault("PROMETHEE_TEST_MODE", "1")

import time
import pytest

from core.bloom_filter import (
    BLOOM_K,
    BLOOM_M,
    BloomFilter,
    BloomIndexManager,
    bloom_pre_llm,
)


class TestBloomFilterBasic:
    def test_empty_filter_contains_nothing(self):
        bf = BloomFilter()
        assert not bf.contains("anything")
        assert bf.count == 0

    def test_add_then_contains(self):
        bf = BloomFilter()
        bf.add("hello")
        assert bf.contains("hello")
        assert bf.count == 1

    def test_multiple_items(self):
        bf = BloomFilter()
        items = ["alpha", "beta", "gamma", "delta"]
        for item in items:
            bf.add(item)
        for item in items:
            assert bf.contains(item)
        assert bf.count == 4

    def test_hashes_are_deterministic(self):
        bf = BloomFilter()
        h1 = bf._hashes("test")
        h2 = bf._hashes("test")
        assert h1 == h2

    def test_k_different_positions(self):
        bf = BloomFilter()
        positions = bf._hashes("test")
        assert len(positions) == BLOOM_K
        # Les positions doivent etre distribuees (pas toutes identiques)
        assert len(set(positions)) >= 3

    def test_memory_size(self):
        bf = BloomFilter(m=20000)
        assert bf.memory_kb() == pytest.approx(2.5, abs=0.1)


class TestZeroFalseNegative:
    """La propriete absolue : si Bloom dit NON, c'est NON."""

    def test_zero_false_negative_exhaustive(self):
        """Ajoute 1000 items, verifie que TOUS sont retrouves."""
        bf = BloomFilter()
        items = [f"item_{i}" for i in range(1000)]
        for item in items:
            bf.add(item)
        for item in items:
            assert bf.contains(item), f"FAUX NEGATIF sur {item}"

    def test_zero_false_negative_with_realistic_names(self):
        """Noms de fonctions realistes : verifie zero FN."""
        bf = BloomFilter()
        realistic = [
            "verify_code_review", "_extract_real_names", "dispatch_task",
            "process_task", "generate_content", "remember", "recall",
            "ensure_node", "hebbian_strengthen", "compute_factuality_score",
            "check_prompt", "extract_references", "_apply_causal_delta",
        ] * 20  # 240 noms, avec doublons tolérés (count = 240 mais unique ~12)
        for name in realistic:
            bf.add(name)
        for name in set(realistic):
            assert bf.contains(name)


class TestFalsePositiveRate:
    def test_fp_rate_within_design(self):
        """Avec 2090 entrees sur m=20000, k=7, FP doit etre < 1%."""
        bf = BloomFilter(m=20000, k=7)
        for i in range(2090):
            bf.add(f"entry_{i}")
        # Mesurer FP sur 5000 items inconnus
        fp = 0
        n_test = 5000
        for i in range(2090, 2090 + n_test):
            if bf.contains(f"entry_{i}"):
                fp += 1
        rate = fp / n_test
        # Theorique ~0.8%, on tolere jusqu'a 2%
        assert rate < 0.02, f"FP rate {rate} > 2%"


class TestIndexManager:
    @pytest.fixture
    def manager(self):
        BloomIndexManager.reset_singleton()
        return BloomIndexManager()

    def test_initial_state_not_built(self, manager):
        assert not manager._built
        # Sans build, check_prompt retourne None (fallback gracieux)
        assert manager.check_prompt("test", "some prompt") is None

    def test_build_on_real_project(self, manager):
        """Construit les indexes sur le vrai projet Promethee."""
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        stats = manager.build_indexes(project_root)
        # Au moins quelques centaines de fonctions dans le projet
        assert stats["functions"] > 100
        # Quelques dizaines de classes
        assert stats["classes"] > 10
        # Au moins quelques fichiers .py
        assert stats["files"] > 50
        # Taille memoire totale negligeable
        assert stats["memory_kb_total"] < 10  # 3 filtres x 2.5 KB = 7.5 KB


class TestReferenceExtraction:
    @pytest.fixture
    def manager(self):
        BloomIndexManager.reset_singleton()
        return BloomIndexManager()

    def test_extract_function_call(self, manager):
        refs = manager.extract_references("Utilise ma_fonction(x, y) pour calculer.")
        assert "ma_fonction" in refs["functions"]

    def test_extract_backtick_function(self, manager):
        refs = manager.extract_references("Appelle `validate_factuality`.")
        assert "validate_factuality" in refs["functions"]

    def test_extract_module_function_backtick(self, manager):
        refs = manager.extract_references("Voir `core.prefrontal.compute_causal_drop`")
        # La derniere partie est extraite
        assert "compute_causal_drop" in refs["functions"]

    def test_extract_class_backtick_only(self, manager):
        """Classes en backticks : extraites. Sans backticks : non."""
        refs = manager.extract_references(
            "La classe `PrometheusAgent` gere tout. BIOLOGIE est un mot en prose."
        )
        assert "PrometheusAgent" in refs["classes"]
        # BIOLOGIE ne doit PAS etre extrait (pas de backticks)
        assert "BIOLOGIE" not in refs["classes"]

    def test_extract_file_path(self, manager):
        refs = manager.extract_references("Modifie core/prefrontal.py ligne 45.")
        assert "core/prefrontal.py" in refs["files"]

    def test_ignore_builtins(self, manager):
        refs = manager.extract_references("J utilise print() et len() souvent.")
        assert "print" not in refs["functions"]
        assert "len" not in refs["functions"]

    def test_empty_prompt(self, manager):
        refs = manager.extract_references("")
        assert refs["functions"] == []
        assert refs["classes"] == []
        assert refs["files"] == []

    def test_prompt_without_any_ref(self, manager):
        """Prose pure, sans reference -> vide."""
        refs = manager.extract_references("Bonjour comment vas-tu aujourd'hui.")
        assert refs["functions"] == []
        assert refs["classes"] == []
        assert refs["files"] == []


class TestStrictVeto:
    """Seuil STRICT : 1 faux negatif Bloom = veto immediat."""

    @pytest.fixture
    def built_manager(self, tmp_path):
        BloomIndexManager.reset_singleton()
        m = BloomIndexManager()
        # Simuler un index minimal en ajoutant directement
        m.functions.add("verify_code_review")
        m.functions.add("dispatch_task")
        m.classes.add("BaseAgent")
        m.files.add("core/base_agent.py")
        m._built = True
        return m

    def test_no_ref_returns_none(self, built_manager):
        """Prose sans reference -> pas de veto."""
        veto = built_manager.check_prompt("test", "Comment ca va ?")
        assert veto is None

    def test_valid_function_no_veto(self, built_manager):
        """Fonction existante -> pas de veto."""
        veto = built_manager.check_prompt("test", "Utilise verify_code_review() ici.")
        assert veto is None

    def test_invalid_function_triggers_veto(self, built_manager):
        """Fonction inventee -> veto IMMEDIAT."""
        veto = built_manager.check_prompt("test", "Appelle fake_function_xyz() maintenant.")
        assert veto is not None
        assert veto.ref_kind == "function"
        assert veto.ref_name == "fake_function_xyz"
        assert "introuvable" in veto.response.lower()

    def test_invalid_class_triggers_veto(self, built_manager):
        """Classe inventee en backticks -> veto."""
        veto = built_manager.check_prompt(
            "test", "Instancie `FakeAgentClass` pour le test."
        )
        assert veto is not None
        assert veto.ref_kind == "class"

    def test_invalid_file_triggers_veto(self, built_manager):
        """Fichier inventé -> veto."""
        veto = built_manager.check_prompt(
            "test", "Modifie core/nonexistent_module.py ligne 10."
        )
        assert veto is not None
        assert veto.ref_kind == "file"

    def test_one_false_negative_is_enough(self, built_manager):
        """Meme avec 10 refs valides + 1 fausse, le veto se declenche."""
        prompt = (
            "Pour la mission, utilise verify_code_review() et dispatch_task(). "
            "Instancie `BaseAgent`. Modifie core/base_agent.py. "
            "Puis appelle fake_invented_fn() a la fin."
        )
        veto = built_manager.check_prompt("test", prompt)
        assert veto is not None
        assert veto.ref_name == "fake_invented_fn"

    def test_index_not_built_no_veto(self):
        """Si index non construit, fallback gracieux (pas de veto)."""
        BloomIndexManager.reset_singleton()
        m = BloomIndexManager()
        veto = m.check_prompt("test", "Appelle fake_function() maintenant.")
        assert veto is None

    def test_veto_counter_increments(self, built_manager):
        """Le compteur de vetos est incremente."""
        c0 = built_manager._veto_count
        built_manager.check_prompt("test", "Appelle fake_fn_1() maintenant.")
        built_manager.check_prompt("test", "Appelle fake_fn_2() maintenant.")
        assert built_manager._veto_count == c0 + 2


class TestPerformance:
    """Overhead doit rester < 1 ms par check_prompt."""

    def test_overhead_under_1ms(self):
        BloomIndexManager.reset_singleton()
        m = BloomIndexManager()
        for i in range(3000):
            m.functions.add(f"fn_{i}")
        for i in range(50):
            m.classes.add(f"Class{i}")
        for i in range(400):
            m.files.add(f"core/mod_{i}.py")
        m._built = True

        prompt = (
            "Pour accomplir cette tache, utilise fn_100() et fn_2500(). "
            "Instancie `Class5`. Verifie core/mod_200.py ligne 45. "
            "Faut aussi que tu consultes recall et generate_content."
        )
        # Warm-up
        m.check_prompt("test", prompt)
        # Mesurer sur 100 iterations
        t0 = time.perf_counter()
        for _ in range(100):
            m.check_prompt("test", prompt)
        elapsed_per_call_ms = (time.perf_counter() - t0) / 100 * 1000
        assert elapsed_per_call_ms < 1.0, (
            f"Overhead {elapsed_per_call_ms:.3f} ms > 1 ms seuil"
        )


class TestRealProject:
    """Test d'integration : construit sur le vrai projet + verifie
    qu'une fonction reellement existante passe, qu'une inventee fail."""

    @pytest.fixture
    def real_manager(self):
        BloomIndexManager.reset_singleton()
        m = BloomIndexManager()
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        m.build_indexes(project_root)
        return m

    def test_real_function_passes(self, real_manager):
        """dispatch_task existe reellement -> pas de veto."""
        veto = real_manager.check_prompt(
            "test", "Utilise dispatch_task() pour envoyer la mission."
        )
        assert veto is None

    def test_real_file_passes(self, real_manager):
        """core/base_agent.py existe -> pas de veto."""
        veto = real_manager.check_prompt(
            "test", "Lis core/base_agent.py pour comprendre."
        )
        assert veto is None

    def test_obvious_hallucination_caught(self, real_manager):
        """Fonction clairement inventee -> veto."""
        veto = real_manager.check_prompt(
            "test",
            "Appelle compute_magic_rainbow_unicorn_v99() pour continuer.",
        )
        assert veto is not None
        assert "magic_rainbow_unicorn_v99" in veto.response
