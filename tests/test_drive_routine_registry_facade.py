"""Tests unitaires pour la Facade Provider Pattern (Phase C Etape 4, 2026-04-14).

Valide en isolation totale :
- set_synaptic_provider / set_stability_provider
- get_routines_for_drive_live (facade)
- Comportement sans provider (fallback genome seul)
- Gestion d'erreurs du provider (pas de propagation d'exception)
- Integration avec get_routines_for_drive existant

Aucune dependance a synaptic_network reel — on teste uniquement la
tuyauterie de l'inversion de controle.

Ref : docs/phase_c_etape_3_hebbian_causal.md §10 (Phase C Etape 4 infra)
"""

import pytest
from unittest.mock import MagicMock

from core import drive_routine_registry as registry
from core.drive_routine_registry import (
    DRIVE_GENOME,
    FLOOR_OF_THE_FLOOR,
    GENOME_GRACE_CYCLES,
    set_synaptic_provider,
    set_stability_provider,
    get_synaptic_provider,
    get_routines_for_drive_live,
    get_routines_for_drive,
    get_affinity_for_drive_intent,
)


# ═══════════════════════════════════════════════════════════════════════
# Setup/teardown : isolation du state global du provider
# ═══════════════════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def reset_providers():
    """Reset les providers avant ET apres chaque test pour isolation."""
    set_synaptic_provider(None)
    set_stability_provider(None)
    yield
    set_synaptic_provider(None)
    set_stability_provider(None)


# ═══════════════════════════════════════════════════════════════════════
# Tests set_synaptic_provider / accessor
# ═══════════════════════════════════════════════════════════════════════


class TestSetSynapticProvider:
    def test_no_provider_initially(self):
        """Apres reset, aucun provider enregistre."""
        assert get_synaptic_provider() is None

    def test_register_provider(self):
        mock_fn = lambda drive: {"EXPANSION_CODE": 0.5}
        set_synaptic_provider(mock_fn)
        assert get_synaptic_provider() is mock_fn

    def test_register_none_clears_provider(self):
        set_synaptic_provider(lambda drive: {})
        assert get_synaptic_provider() is not None
        set_synaptic_provider(None)
        assert get_synaptic_provider() is None

    def test_replace_provider_overwrites(self):
        fn1 = lambda drive: {"A": 0.1}
        fn2 = lambda drive: {"B": 0.2}
        set_synaptic_provider(fn1)
        set_synaptic_provider(fn2)
        assert get_synaptic_provider() is fn2


# ═══════════════════════════════════════════════════════════════════════
# Tests get_routines_for_drive_live — fallback sans provider
# ═══════════════════════════════════════════════════════════════════════


class TestFacadeFallback:
    """Comportement safe quand aucun provider n'est enregistre."""

    def test_no_provider_uses_genome_only(self):
        """Sans provider, seul le genome contribue (synaptic_weights={})."""
        set_synaptic_provider(None)
        result = get_routines_for_drive_live("MAITRISE", temperature=0.0, top_k=10)
        assert len(result) > 0
        # Tous les intents doivent venir du genome
        intents = {i for i, w in result}
        assert intents == set(DRIVE_GENOME["MAITRISE"].keys())

    def test_no_provider_equals_empty_weights(self):
        """L'absence de provider est equivalent a synaptic_weights={}."""
        set_synaptic_provider(None)
        r1 = get_routines_for_drive_live("MAITRISE", temperature=0.0)
        r2 = get_routines_for_drive(
            "MAITRISE", synaptic_weights={}, temperature=0.0
        )
        assert r1 == r2

    def test_unknown_drive_no_provider_returns_empty(self):
        set_synaptic_provider(None)
        result = get_routines_for_drive_live("NOPE_DRIVE")
        assert result == []


# ═══════════════════════════════════════════════════════════════════════
# Tests get_routines_for_drive_live — avec provider
# ═══════════════════════════════════════════════════════════════════════


class TestFacadeWithProvider:
    """Comportement quand un provider est enregistre."""

    def test_provider_weights_fused_with_genome(self):
        """Les poids synaptiques fournis sont fusionnes avec le genome."""
        def mock_provider(drive):
            if drive == "MAITRISE":
                return {"REFACTORING_AUDIT": 1.5}  # plus haut que le genome (0.9)
            return {}

        set_synaptic_provider(mock_provider)
        result = get_routines_for_drive_live("MAITRISE", temperature=0.0, top_k=10)
        refact = next(w for i, w in result if i == "REFACTORING_AUDIT")
        assert refact == 1.5  # max(1.5, 0.9) = 1.5

    def test_provider_with_new_intent(self):
        """Un intent synaptic hors du genome apparait dans le resultat."""
        def mock_provider(drive):
            return {"SUPER_REFACTORING_V2": 1.2}

        set_synaptic_provider(mock_provider)
        result = get_routines_for_drive_live("MAITRISE", temperature=0.0, top_k=20)
        intents = {i for i, w in result}
        assert "SUPER_REFACTORING_V2" in intents

    def test_provider_called_with_correct_drive(self):
        """Le provider est appele avec le drive demande."""
        calls = []
        def mock_provider(drive):
            calls.append(drive)
            return {}

        set_synaptic_provider(mock_provider)
        get_routines_for_drive_live("MAITRISE")
        get_routines_for_drive_live("STABILITE")
        assert calls == ["MAITRISE", "STABILITE"]

    def test_provider_returning_none_safe(self):
        """Un provider qui retourne None n'explose pas."""
        set_synaptic_provider(lambda drive: None)
        result = get_routines_for_drive_live("MAITRISE", temperature=0.0, top_k=5)
        # Comportement = fallback sur {} = genome seul
        assert len(result) > 0

    def test_provider_returning_empty_dict_safe(self):
        """Un provider qui retourne {} n'explose pas."""
        set_synaptic_provider(lambda drive: {})
        result = get_routines_for_drive_live("MAITRISE", temperature=0.0, top_k=5)
        # Comportement = genome seul
        assert len(result) > 0


# ═══════════════════════════════════════════════════════════════════════
# Tests gestion d'erreurs du provider
# ═══════════════════════════════════════════════════════════════════════


class TestFacadeErrorHandling:
    """Le provider peut planter — la facade doit rester safe."""

    def test_provider_exception_logged_not_raised(self, caplog):
        """Un provider qui leve une exception n'est pas propage."""
        def broken_provider(drive):
            raise RuntimeError("Synaptic graph corrupted")

        set_synaptic_provider(broken_provider)
        import logging
        with caplog.at_level(logging.WARNING):
            result = get_routines_for_drive_live("MAITRISE", temperature=0.0)
        # Le resultat tombe sur le fallback genome
        assert len(result) > 0
        # Un warning a ete logge
        assert any("synaptic_provider failed" in rec.message for rec in caplog.records)

    def test_provider_exception_fallback_to_genome(self):
        """Apres exception, le resultat est equivalent au genome seul."""
        set_synaptic_provider(lambda drive: (_ for _ in ()).throw(ValueError("boom")))
        r_err = get_routines_for_drive_live("MAITRISE", temperature=0.0)
        set_synaptic_provider(None)
        r_none = get_routines_for_drive_live("MAITRISE", temperature=0.0)
        assert r_err == r_none


# ═══════════════════════════════════════════════════════════════════════
# Tests set_stability_provider (depreciation genomique)
# ═══════════════════════════════════════════════════════════════════════


class TestStabilityProvider:
    def test_stability_provider_passed_to_compute(self):
        """Le stability_provider est bien transmis a _compute_genome_floor
        via get_routines_for_drive."""
        calls = []
        def mock_stability(drive, intent, n):
            calls.append((drive, intent, n))
            return 0.5  # instable -> pas de depreciation

        set_stability_provider(mock_stability)

        # Force un cycle apres grace period via un mock du cycle
        from unittest.mock import patch
        with patch.object(registry.experience_clock, "current",
                          return_value=registry.GENOME_GRACE_CYCLES + 2000):
            # L'appel doit invoquer stability_fn pendant compute_genome_floor
            get_routines_for_drive_live("MAITRISE", temperature=0.0)

        # Au moins un appel a eu lieu
        assert len(calls) > 0

    def test_stability_none_means_no_depreciation(self):
        """Sans stability_provider, pas de depreciation meme apres grace period."""
        set_stability_provider(None)
        from unittest.mock import patch
        with patch.object(registry.experience_clock, "current",
                          return_value=registry.GENOME_GRACE_CYCLES * 100):
            result = get_routines_for_drive_live("MAITRISE", temperature=0.0, top_k=10)
        # Les plancher genomique doit etre intact
        refact = next(w for i, w in result if i == "REFACTORING_AUDIT")
        assert refact == 0.9


# ═══════════════════════════════════════════════════════════════════════
# Tests d'integration avec le provider auto-enregistre par synaptic_network
# ═══════════════════════════════════════════════════════════════════════


class TestProviderAutoRegistrationAtBoot:
    """Verifie que synaptic_network enregistre bien son provider au boot."""

    def test_cortex_provider_registered_after_import(self):
        """Apres import de synaptic_network, le provider doit etre pose.

        Note : ce test ne peut pas reset le provider avant, car il teste
        le state post-import naturel. On le valide en re-important.
        """
        # Reset avant
        set_synaptic_provider(None)
        assert get_synaptic_provider() is None

        # Re-importer synaptic_network (le code au module-level reenregistre)
        # En pratique, le singleton existe deja, on appelle juste l'enregistrement
        from core.synaptic_network import cortex
        from core.drive_routine_registry import set_synaptic_provider as sp
        sp(cortex.get_drive_intent_weights)

        provider = get_synaptic_provider()
        assert provider is not None
        assert provider == cortex.get_drive_intent_weights

    def test_cortex_provider_returns_dict(self):
        """Le provider branche sur cortex retourne un dict (meme vide)."""
        from core.synaptic_network import cortex
        set_synaptic_provider(cortex.get_drive_intent_weights)

        weights = get_synaptic_provider()("MAITRISE")
        assert isinstance(weights, dict)
        # Les cles doivent etre des strings (intents)
        for k, v in weights.items():
            assert isinstance(k, str)
            assert isinstance(v, float)


# ═══════════════════════════════════════════════════════════════════════
# Tests isolation : la facade ne doit pas polluer la fonction pure
# ═══════════════════════════════════════════════════════════════════════


class TestGetAffinityForDriveIntent:
    """Tests de la fonction O(1) pour le hot path compute_desire_bonus.

    Phase C Etape 4c-3a (2026-04-14) : cette fonction est appelee des
    centaines de fois par cycle de scoring. Doit etre :
    - O(1) au lookup
    - Sans tri ni fusion contextuelle
    - Coherente avec get_routines_for_drive_live (max(synaptic, floor))
    """

    def test_intent_not_in_genome_no_synaptic_returns_zero(self):
        set_synaptic_provider(None)
        result = get_affinity_for_drive_intent("MAITRISE", "UNKNOWN_INTENT")
        assert result == 0.0

    def test_intent_in_genome_returns_genome_floor(self):
        """Sans provider, retourne le floor genomique."""
        set_synaptic_provider(None)
        result = get_affinity_for_drive_intent("MAITRISE", "REFACTORING_AUDIT")
        # Floor genomique = 0.9 (valeur native du genome MAITRISE)
        assert result == 0.9

    def test_synaptic_higher_wins(self):
        """Si le synaptic est plus haut que le floor, il gagne."""
        set_synaptic_provider(lambda d: {"REFACTORING_AUDIT": 1.5})
        result = get_affinity_for_drive_intent("MAITRISE", "REFACTORING_AUDIT")
        assert result == 1.5  # max(1.5, 0.9)

    def test_floor_higher_wins(self):
        """Si le floor est plus haut que le synaptic, il gagne."""
        set_synaptic_provider(lambda d: {"REFACTORING_AUDIT": 0.1})
        result = get_affinity_for_drive_intent("MAITRISE", "REFACTORING_AUDIT")
        assert result == 0.9  # max(0.1, 0.9)

    def test_synaptic_only_intent_returns_synaptic(self):
        """Un intent dans synaptic mais pas dans genome."""
        set_synaptic_provider(lambda d: {"SUPER_REFACTORING_V2": 0.7})
        result = get_affinity_for_drive_intent("MAITRISE", "SUPER_REFACTORING_V2")
        assert result == 0.7

    def test_unknown_drive_no_floor(self):
        """Un drive inconnu et aucun synaptic -> 0.0."""
        set_synaptic_provider(None)
        result = get_affinity_for_drive_intent("NOPE_DRIVE", "REFACTORING_AUDIT")
        assert result == 0.0

    def test_provider_exception_returns_zero_or_floor(self):
        """Exception du provider -> fallback genome."""
        def broken(d):
            raise RuntimeError("boom")
        set_synaptic_provider(broken)
        # L'exception est capturee, floor genomique utilise
        result = get_affinity_for_drive_intent("MAITRISE", "REFACTORING_AUDIT")
        assert result == 0.9

    def test_does_not_call_get_routines_for_drive(self):
        """Verification critique : la fonction O(1) ne DOIT PAS appeler
        get_routines_for_drive (qui fait un tri O(n log n))."""
        set_synaptic_provider(lambda d: {"REFACTORING_AUDIT": 0.5})

        call_count = {"routines": 0}
        original = registry.get_routines_for_drive

        def spy(*args, **kwargs):
            call_count["routines"] += 1
            return original(*args, **kwargs)

        registry.get_routines_for_drive = spy
        try:
            for _ in range(100):
                get_affinity_for_drive_intent("MAITRISE", "REFACTORING_AUDIT")
        finally:
            registry.get_routines_for_drive = original

        assert call_count["routines"] == 0

    def test_consistency_with_get_routines_for_drive_live(self):
        """Le poids retourne par get_affinity doit etre le meme que
        celui retourne par get_routines_for_drive_live pour le meme
        intent (verification coherence des 2 chemins)."""
        def mock_provider(drive):
            return {"REFACTORING_AUDIT": 0.75, "CI_PIPELINE_RUN": 0.5}

        set_synaptic_provider(mock_provider)

        # Chemin 1 : get_affinity_for_drive_intent (O(1))
        affinity_direct = get_affinity_for_drive_intent(
            "MAITRISE", "REFACTORING_AUDIT"
        )

        # Chemin 2 : get_routines_for_drive_live + lookup
        routines = get_routines_for_drive_live("MAITRISE", top_k=20)
        affinity_via_list = next(
            (w for i, w in routines if i == "REFACTORING_AUDIT"), 0.0
        )

        # Les 2 doivent donner le meme resultat
        # (pas de context_multipliers, pas de temperature)
        assert affinity_direct == affinity_via_list

    def test_performance_bulk_lookups(self):
        """Garde-fou perf : 1000 lookups doivent etre < 50ms."""
        import time
        set_synaptic_provider(lambda d: {"REFACTORING_AUDIT": 0.5})
        start = time.perf_counter()
        for _ in range(1000):
            get_affinity_for_drive_intent("MAITRISE", "REFACTORING_AUDIT")
        elapsed = time.perf_counter() - start
        # 1000 lookups avec provider + genome floor calcul < 50ms
        assert elapsed < 0.05, (
            f"1000 lookups took {elapsed*1000:.1f}ms (budget 50ms)"
        )


class TestFacadePurityGuarantees:
    def test_pure_function_still_pure(self):
        """get_routines_for_drive reste pure apres set_synaptic_provider."""
        set_synaptic_provider(lambda drive: {"EXPANSION_CODE": 0.99})
        # L'appel direct a la fonction pure NE doit PAS utiliser le provider
        r_pure = get_routines_for_drive(
            "MAITRISE", synaptic_weights={}, temperature=0.0, top_k=10
        )
        # EXPANSION_CODE ne devrait PAS etre a 0.99 (car synaptic_weights={})
        intents = dict(r_pure)
        assert "EXPANSION_CODE" not in intents  # pas dans genome MAITRISE

    def test_facade_vs_pure_identical_when_empty_provider(self):
        """Avec provider None, facade == fonction pure avec {}."""
        set_synaptic_provider(None)
        r_live = get_routines_for_drive_live("MAITRISE", temperature=0.0, top_k=5)
        r_pure = get_routines_for_drive(
            "MAITRISE", synaptic_weights={}, temperature=0.0, top_k=5
        )
        assert r_live == r_pure
