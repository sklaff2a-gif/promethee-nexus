# -*- coding: utf-8 -*-
"""Brique B (i) — Chassis des tiers : schema de metadonnees + VALIDATEUR D'ECRITURE.

Sandbox ISOLE (ne touche PAS au ChromaDB live). Invariant : TOUT acquis ecrit
porte un statut des sa naissance -> la QUARANTAINE commence a l'ecriture.

Champs de metadonnees (graves dans la base vectorielle a terme) :
  - tier_status (Enum)          : CHURN (defaut) | TAMPON | PREMIUM
  - is_flagged (bool)           : commutateur de contradiction
  - contradiction_source (str)  : internal_inference | external_verification (si flagged)
  - injected_label (str)        : DERIVE (jamais fourni a la main), lu par le routeur

SECURITE (faille 2 resolue, gelee le 07/06) : un flag NE DEGRADE JAMAIS le tier.
Un PREMIUM drapote RESTE premium ; seul son label change pour porter le doute.
La degradation est une operation SEPAREE, reservee au gate JM (pas le validateur).
"""
from enum import Enum


class TierStatus(str, Enum):
    CHURN = "CHURN"      # defaut : memoire courante ordinaire (le churn qui decaye)
    TAMPON = "TAMPON"    # piste exploratoire, non verifiee (quarantaine)
    PREMIUM = "PREMIUM"  # valeur certifiee par le gate humain


class ContradictionSource(str, Enum):
    INTERNAL = "internal_inference"     # contradiction detectee en interne (faillible)
    EXTERNAL = "external_verification"  # refutation factuelle externe (!calc/source) -> prioritaire


class SchemaError(ValueError):
    """Ecriture rejetee : metadonnees non conformes au chassis des tiers."""


def derive_label(tier_status, is_flagged: bool) -> str:
    """Label injecte, lu par le routeur de contexte. DERIVE, jamais fourni a la main."""
    t = TierStatus(tier_status)
    if t == TierStatus.PREMIUM:
        return "[CERTIFIE - CONTRADICTION SIGNALEE]" if is_flagged else "[CERTIFIE]"
    if t == TierStatus.TAMPON:
        return "[PISTE NON VERIFIEE]"
    return "[memoire courante]"


def validate_metadata(meta: dict) -> dict:
    """Valide + NORMALISE un dict de metadonnees AVANT ecriture. Retourne le dict
    normalise (avec injected_label derive) ; leve SchemaError si non conforme."""
    out = dict(meta or {})

    # 1. tier_status : defaut CHURN, type strict
    raw_tier = out.get("tier_status", TierStatus.CHURN.value)
    try:
        tier = TierStatus(raw_tier).value
    except ValueError:
        raise SchemaError(
            f"tier_status invalide: {raw_tier!r} (attendu CHURN/TAMPON/PREMIUM)")
    out["tier_status"] = tier

    # 2. is_flagged : booleen strict
    flag = out.get("is_flagged", False)
    if not isinstance(flag, bool):
        raise SchemaError(f"is_flagged doit etre booleen, recu {type(flag).__name__}")
    out["is_flagged"] = flag

    # 3. coherence flag <-> source
    if flag:
        raw_src = out.get("contradiction_source")
        if raw_src is None:
            raise SchemaError("is_flagged=True exige contradiction_source")
        try:
            out["contradiction_source"] = ContradictionSource(raw_src).value
        except ValueError:
            raise SchemaError(f"contradiction_source invalide: {raw_src!r}")
    else:
        out["contradiction_source"] = None  # pas de flag -> pas de source

    # 4. injected_label : TOUJOURS derive (on ecrase tout label fourni a la main)
    out["injected_label"] = derive_label(tier, flag)

    # 5. SECURITE faille 2 : le validateur NE TOUCHE JAMAIS a tier_status a cause du
    #    flag. Un PREMIUM flagge reste PREMIUM (cf etape 1, tier inchange par 2/3/4).
    return out


def flag_contradiction(meta: dict, source) -> dict:
    """Pose un drapeau de contradiction SANS degrader le tier (faille 2).
    Le PREMIUM garde son statut ET son poids ; seul le label porte le doute.
    Renvoie les metadonnees normalisees (a router vers premium_review.md)."""
    m = dict(meta or {})
    m["is_flagged"] = True
    m["contradiction_source"] = ContradictionSource(source).value
    return validate_metadata(m)  # tier inchange, label -> "[... CONTRADICTION SIGNALEE]"
