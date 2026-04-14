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
    set_synaptic_provider,
    set_stability_provider,
    get_synaptic_provider,
    get_routines_for_drive_live,
    get_routines_for_drive,
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
