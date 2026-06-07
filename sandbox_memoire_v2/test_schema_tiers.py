# -*- coding: utf-8 -*-
"""TDD Brique B (i) — validateur d'ecriture du chassis des tiers."""
import pytest
from schema_tiers import (
    validate_metadata, flag_contradiction, derive_label,
    TierStatus, SchemaError,
)


def test_defaut_churn_si_tier_absent():
    out = validate_metadata({})
    assert out["tier_status"] == "CHURN"
    assert out["injected_label"] == "[memoire courante]"
    assert out["is_flagged"] is False
    assert out["contradiction_source"] is None


def test_premium_non_flagge_label_certifie():
    out = validate_metadata({"tier_status": "PREMIUM"})
    assert out["tier_status"] == "PREMIUM"
    assert out["injected_label"] == "[CERTIFIE]"


def test_tampon_label_quarantaine():
    out = validate_metadata({"tier_status": "TAMPON"})
    assert out["injected_label"] == "[PISTE NON VERIFIEE]"


def test_tier_invalide_rejete():
    with pytest.raises(SchemaError):
        validate_metadata({"tier_status": "GOLD"})


def test_is_flagged_non_booleen_rejete():
    with pytest.raises(SchemaError):
        validate_metadata({"tier_status": "PREMIUM", "is_flagged": "oui"})


def test_flag_sans_source_rejete():
    with pytest.raises(SchemaError):
        validate_metadata({"tier_status": "PREMIUM", "is_flagged": True})


def test_source_invalide_rejetee():
    with pytest.raises(SchemaError):
        validate_metadata({"tier_status": "PREMIUM", "is_flagged": True,
                           "contradiction_source": "rumeur"})


def test_label_fourni_a_la_main_est_ecrase():
    # on ne fait jamais confiance a un label fourni : il est re-derive
    out = validate_metadata({"tier_status": "TAMPON",
                             "injected_label": "[CERTIFIE]"})
    assert out["injected_label"] == "[PISTE NON VERIFIEE]"


def test_FAILLE2_un_flag_ne_degrade_jamais_le_premium():
    # COEUR DE LA SECURITE : un premium drapote RESTE premium, label porte le doute
    out = flag_contradiction({"tier_status": "PREMIUM"}, "internal_inference")
    assert out["tier_status"] == "PREMIUM", "violation faille 2 : le tier a ete degrade !"
    assert out["is_flagged"] is True
    assert out["injected_label"] == "[CERTIFIE - CONTRADICTION SIGNALEE]"


def test_source_externe_prioritaire_conservee():
    out = flag_contradiction({"tier_status": "PREMIUM"}, "external_verification")
    assert out["contradiction_source"] == "external_verification"
    assert out["tier_status"] == "PREMIUM"


def test_derive_label_exhaustif():
    assert derive_label("PREMIUM", False) == "[CERTIFIE]"
    assert derive_label("PREMIUM", True) == "[CERTIFIE - CONTRADICTION SIGNALEE]"
    assert derive_label("TAMPON", False) == "[PISTE NON VERIFIEE]"
    assert derive_label("CHURN", False) == "[memoire courante]"
