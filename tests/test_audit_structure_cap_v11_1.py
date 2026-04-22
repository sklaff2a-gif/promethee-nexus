"""V11.1 (22/04/2026) — Cap adaptatif AUDIT_STRUCTURE (release valve STABILITE).

Contexte : diagnostic 22/04 midi — Prométhée en boucle d'impuissance
homéostatique. 50 cycles AUDIT_SURVIE consécutifs sur 9h, goals STABILITE
abandonnés par fruitless_cycles=8. Cause : cap hardcodé à 3/jour sans
issue de secours en régime critique (STABILITE.deprivation = 95.77).

Fix : cap adaptatif. Reste 3 en régime normal, passe à 8 si
STABILITE.deprivation > 80. Permet 5 audits supplémentaires × -10 = -50
sur STABILITE → retour sous le seuil → cap redescend.

Test unitaire du helper _compute_audit_structure_cap (static, pur).
"""
import pytest

from core.autonomy_engine import AutonomyEngine


class TestAuditStructureCapAdaptive:

    def test_cap_3_at_zero_deprivation(self):
        assert AutonomyEngine._compute_audit_structure_cap(0.0) == 3

    def test_cap_3_at_normal_deprivation(self):
        assert AutonomyEngine._compute_audit_structure_cap(50.0) == 3

    def test_cap_3_at_threshold_exactly(self):
        # Seuil 80 strict : egal = pas crise
        assert AutonomyEngine._compute_audit_structure_cap(80.0) == 3

    def test_cap_3_just_below_threshold(self):
        assert AutonomyEngine._compute_audit_structure_cap(79.9) == 3

    def test_cap_8_just_above_threshold(self):
        assert AutonomyEngine._compute_audit_structure_cap(80.1) == 8

    def test_cap_8_at_high_deprivation(self):
        assert AutonomyEngine._compute_audit_structure_cap(95.77) == 8

    def test_cap_8_at_max_deprivation(self):
        assert AutonomyEngine._compute_audit_structure_cap(100.0) == 8

    def test_cap_handles_negative_safely(self):
        # Defensive : deprivation negative ne devrait pas arriver mais restons stables
        assert AutonomyEngine._compute_audit_structure_cap(-5.0) == 3


class TestAuditStructureCapDocumentation:
    """Verifie que le helper est bien une static method documented.
    Protege contre une refacto accidentelle qui changerait la signature."""

    def test_is_static_method(self):
        import inspect
        # On peut l'appeler sans self, signe qu'il est static
        sig = inspect.signature(AutonomyEngine._compute_audit_structure_cap)
        params = list(sig.parameters.keys())
        assert "self" not in params
        assert "stab_deprivation" in params

    def test_has_docstring(self):
        doc = AutonomyEngine._compute_audit_structure_cap.__doc__
        assert doc is not None
        assert "V11.1" in doc
        assert "STABILITE" in doc
