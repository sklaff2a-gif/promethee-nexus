# tests/test_organ_registry.py — Tests du registre d'organes
import pytest
from core.organ_registry import (
    register_organ, get_organ, get_all_organs, reset_registry,
    _import_organ_fallback,
)


@pytest.fixture(autouse=True)
def clean_registry():
    """Reset le registre avant et après chaque test."""
    reset_registry()
    yield
    reset_registry()


class TestRegisterAndGet:
    """Tests d'enregistrement et d'accès."""

    def test_register_and_get(self):
        """Un organe enregistré est retrouvé par son nom."""
        mock_organ = object()
        register_organ("test_organ", mock_organ)
        assert get_organ("test_organ") is mock_organ

    def test_get_unknown_returns_none(self):
        """Un organe inconnu (sans fallback) retourne None."""
        assert get_organ("organe_inexistant") is None

    def test_register_overwrites(self):
        """Un re-enregistrement écrase l'ancien organe."""
        old = object()
        new = object()
        register_organ("x", old)
        register_organ("x", new)
        assert get_organ("x") is new

    def test_get_all_organs(self):
        """get_all_organs retourne tous les organes enregistrés."""
        a, b = object(), object()
        register_organ("a", a)
        register_organ("b", b)
        all_organs = get_all_organs()
        assert len(all_organs) == 2
        assert all_organs["a"] is a
        assert all_organs["b"] is b

    def test_reset_clears_all(self):
        """reset_registry vide le registre."""
        register_organ("x", object())
        assert len(get_all_organs()) == 1
        reset_registry()
        assert len(get_all_organs()) == 0


class TestFallbackImport:
    """Tests du fallback d'import dynamique."""

    def test_fallback_cardiac(self):
        """Le fallback importe correctement cardiac_engine."""
        heart = _import_organ_fallback("cardiac")
        assert heart is not None
        assert type(heart).__name__ == "CardiacEngine"

    def test_fallback_desire(self):
        """Le fallback importe correctement desire_engine."""
        desires = _import_organ_fallback("desire")
        assert desires is not None
        assert type(desires).__name__ == "DesireEngine"

    def test_fallback_unknown(self):
        """Un nom inconnu retourne None."""
        assert _import_organ_fallback("toto") is None

    def test_fallback_auto_registers(self):
        """Le fallback enregistre automatiquement l'organe trouvé."""
        reset_registry()
        _import_organ_fallback("cardiac")
        assert "cardiac" in get_all_organs()

    def test_get_organ_with_fallback(self):
        """get_organ utilise le fallback si l'organe n'est pas registré."""
        reset_registry()
        organ = get_organ("psyche")
        assert organ is not None
        assert type(organ).__name__ == "PsycheEngine"


class TestOrganMap:
    """Tests de la couverture de la table d'organes."""

    EXPECTED_ORGANS = [
        "cardiac", "desire", "psyche", "prefrontal", "inner_voice",
        "dopamine", "callosum", "synaptic", "hypothalamus", "insula",
        "cingulate", "basal_ganglia", "dmn", "reptilian", "awareness",
        "spreading", "thalamus", "amygdala", "sensorium", "incubation",
        "curiosity", "tissue", "roadmap", "objectives", "hippocampus",
        "temporal",
    ]

    def test_all_organs_importable(self):
        """Tous les organes de la table sont importables via fallback."""
        failures = []
        for name in self.EXPECTED_ORGANS:
            reset_registry()
            organ = _import_organ_fallback(name)
            if organ is None:
                failures.append(name)
        assert not failures, f"Organes non-importables: {failures}"

    def test_organ_map_covers_scoring_layers(self):
        """Les couches de scoring ont toutes un mapping dans le registre."""
        scoring_layers = [
            "objectives", "awareness", "spreading", "synaptic", "desire",
            "prefrontal", "inner_voice", "dopamine", "callosum", "cardiac",
            "roadmap", "tissue", "thalamus", "amygdala", "hypothalamus",
            "insula", "cingulate", "basal_ganglia", "incubation", "curiosity",
            "sensorium", "dmn",
        ]
        for layer in scoring_layers:
            organ = _import_organ_fallback(layer)
            assert organ is not None, f"Couche de scoring '{layer}' non trouvée dans le registre"


class TestCouplingGuardrail:
    """Guardrail : empêche le couplage de croître silencieusement."""

    # Nombre max d'imports directs entre organes connu au 2026-03-11
    # Si ce test échoue, c'est qu'un nouveau couplage a été ajouté.
    # Vérifier si le bus serait plus approprié, ou mettre à jour le seuil.
    MAX_KNOWN_ORGAN_IMPORTS = 360  # Mesuré: 356 au 2026-03-28 (+ phi_log snapshot school/dopamine/cardiac) + marge

    def test_organ_import_count_bounded(self):
        """Le nombre d'imports croisés ne dépasse pas le seuil connu."""
        import os
        import re
        core_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                                "core")
        organ_modules = {
            "cardiac_engine", "desire_engine", "psyche", "prefrontal",
            "inner_voice", "dopamine_system", "corpus_callosum",
            "synaptic_network", "hypothalamus", "insula", "cingulate_cortex",
            "basal_ganglia", "default_mode_network", "reptilian_core",
            "self_awareness", "spreading_activation", "autonomy_engine",
            "neural_tissue", "thalamus", "amygdala", "sensorium",
            "incubation_cognitive", "curiosity_reflex",
        }
        pattern = re.compile(
            r"from\s+core\.(" + "|".join(organ_modules) + r")\s+import"
        )
        total_imports = 0
        for fname in os.listdir(core_dir):
            if not fname.endswith(".py"):
                continue
            fpath = os.path.join(core_dir, fname)
            with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    if pattern.search(line):
                        total_imports += 1
        assert total_imports <= self.MAX_KNOWN_ORGAN_IMPORTS, (
            f"Couplage inter-organes en hausse : {total_imports} imports "
            f"(max connu: {self.MAX_KNOWN_ORGAN_IMPORTS}). "
            f"Utiliser le bus ou get_organ() pour les nouveaux couplages."
        )
