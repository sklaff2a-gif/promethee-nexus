"""TDD — Vérificateur analytique V3.4 (compute_analytical_factuality).

Contrat (atelier diagnostic famine épistémique 31/05/2026) :
  - Slots RESEARCH/BULLETIN : la technicité n'est PAS un marqueur de vérité.
  - V1 (tech_ratio) NEUTRALISÉ : un cours technique (AST, code) n'est plus puni.
  - V2 (couverture) assoupli : actif dès 1 terme-clé.
  - Plancher de substance (diversité lexicale) : toujours actif -> jamais de
    bypass -1.0 ; rejette le remplissage répétitif / dilué (anti-farming).
  - NON-RÉGRESSION : compute_creation_factuality (V3.3) reste inchangée
    (le bouclier anti-fuite-de-code tient pour les fables CREATION).

Module en TDD : compute_analytical_factuality n'existe pas encore -> rouge.
"""
import pytest

from core.factuality_verifier import (
    compute_creation_factuality,  # existant (V3.3) — pour la non-régression
)

SEUIL = 0.6  # seuil de closure épistémique

# --------------------------------------------------------------------------
# Fixtures calibrées sur les vrais livrables (memory/school/deliverables)
# --------------------------------------------------------------------------

# Cas 1 — RESEARCH en PROSE (tech_ratio ~0, couvre les termes-clés)
CONSIGNE_PROSE = (
    "Approfondis le Predictive Processing selon Karl Friston et explique "
    "le principe de Free Energy comme moteur de la cognition."
)
RESEARCH_PROSE = (
    "Le Predictive Processing propose une lecture renversante de la cognition : "
    "le cerveau n'attend pas passivement les stimuli, il anticipe sans cesse ses "
    "propres sensations futures. Karl Friston formalise cette intuition par le "
    "principe de Free Energy, une borne supérieure sur la surprise que tout "
    "organisme vivant cherche à minimiser. Percevoir devient alors un acte "
    "inférentiel : confronter une prédiction descendante à l'erreur sensorielle "
    "ascendante, puis réviser le modèle interne lorsque l'écart persiste. "
    "L'attention module la précision accordée à ces erreurs, hiérarchisant les "
    "signaux fiables et négligeant le bruit. Cette architecture unifie perception, "
    "action et apprentissage sous une même monnaie : réduire l'incertitude. "
    "L'agent qui agit ne subit plus le monde, il sélectionne les observations qui "
    "confirment ses hypothèses, fermant ainsi la boucle entre prédiction et réalité."
)

# Cas 2 — RESEARCH TECHNIQUE (sujet AST -> tech_ratio élevé, V1 ancien le tuerait)
CONSIGNE_TECH = (
    "Conçois une transformation AST en Python : une phase d'analyse parcourt "
    "l'arbre, une phase de réécriture transforme les noeuds ciblés."
)
RESEARCH_TECH = (
    "La transformation procède en deux temps distincts. La phase d'analyse "
    "parcourt récursivement l'arbre syntaxique pour repérer les motifs candidats, "
    "sans jamais muter la structure. Voici l'ossature du visiteur :\n\n"
    "```python\n"
    "import ast\n\n"
    "class Analyseur(ast.NodeVisitor):\n"
    "    def visit_FunctionDef(self, node):\n"
    "        self.cibles.append(node.name)\n"
    "        return self.generic_visit(node)\n"
    "```\n\n"
    "La seconde phase applique la réécriture via un NodeTransformer, qui "
    "reconstruit les noeuds modifiés tout en préservant les positions sources. "
    "Cette séparation entre lecture et écriture garantit l'idempotence : analyser "
    "ne produit aucun effet de bord, transformer reste déterministe. "
    "La récursion descendante explore chaque branche, la remontée recompose "
    "l'arbre enrichi, et la sérialisation finale régénère un code lisible."
)

# Cas 3 — Consigne PAUVRE en noms propres (V2 inactif -> le plancher doit sauver)
CONSIGNE_PAUVRE = "complète la section avec des exemples concrets et des cas d'usage variés."
LIVRABLE_SUBSTANTIEL = (
    "Considérons d'abord un registre distribué où chaque participant valide les "
    "transactions selon un quorum local. Premier exemple concret : un capteur "
    "industriel signe ses mesures, un agrégateur vérifie la cohérence temporelle, "
    "puis archive uniquement les déviations significatives. Deuxième cas d'usage : "
    "une file de messages priorise les alertes critiques tout en lissant la charge "
    "des notifications routinières. Troisième illustration : un cache adaptatif "
    "invalide ses entrées obsolètes en suivant la fréquence réelle des accès plutôt "
    "qu'un délai fixe. Chacune de ces situations partage une même logique : observer "
    "le comportement effectif, distinguer le signal pertinent du bruit ambiant, et "
    "réagir avec parcimonie plutôt que par automatisme aveugle."
)

# Cas 4 — ANTI-FARMING : répétition pauvre (faible diversité -> sous le seuil)
FARM_REPETITIF = ("Le système est stable et le système fonctionne bien. " * 25)

# Cas 5 — NON-RÉGRESSION CREATION : une fable polluée de code reste punie par V3.3
CONSIGNE_FABLE = "Écris une fable courte sur un renard rusé."
FABLE_AVEC_CODE = (
    "Le renard observait le corbeau.\n\n"
    "```python\n"
    "def ruser(proie):\n"
    "    import flatterie\n"
    "    return flatterie.appliquer(proie)\n"
    "```\n\n"
    "Et le fromage tomba."
)


# --------------------------------------------------------------------------
# Contrat du vérificateur analytique
# --------------------------------------------------------------------------

def _score(content, challenge):
    from core.factuality_verifier import compute_analytical_factuality
    score, _details = compute_analytical_factuality(content, challenge)
    return score


def test_research_prose_clos():
    """Un cours en prose qui couvre son sujet doit clôturer (>= 0.6)."""
    assert _score(RESEARCH_PROSE, CONSIGNE_PROSE) >= SEUIL


def test_research_technique_nest_plus_puni():
    """Cœur du fix : un cours technique (code/AST) ne doit PLUS être vétoé par V1."""
    assert _score(RESEARCH_TECH, CONSIGNE_TECH) >= SEUIL


def test_consigne_pauvre_ne_tombe_pas_en_bypass():
    """Sans terme-clé (V2 inactif), le plancher de substance évite le -1.0 et
    permet à un livrable riche de clôturer."""
    s = _score(LIVRABLE_SUBSTANTIEL, CONSIGNE_PAUVRE)
    assert s != -1.0, "ne doit jamais retomber en bypass"
    assert s >= SEUIL


def test_farming_repetitif_rejete():
    """Le plancher de substance rejette le remplissage répétitif (faible diversité)."""
    assert _score(FARM_REPETITIF, CONSIGNE_PROSE) < SEUIL


def test_non_regression_creation_fable_avec_code():
    """V3.3 inchangée : une fable polluée de code reste punie (anti-fuite intact)."""
    score, _ = compute_creation_factuality(FABLE_AVEC_CODE, CONSIGNE_FABLE)
    assert score < SEUIL


def test_signature_retour():
    """compute_analytical_factuality retourne (float, dict) comme la V3.3."""
    from core.factuality_verifier import compute_analytical_factuality
    out = compute_analytical_factuality(RESEARCH_PROSE, CONSIGNE_PROSE)
    assert isinstance(out, tuple) and len(out) == 2
    assert isinstance(out[0], float) and isinstance(out[1], dict)


# Cas 7 — IN-VIVO 31/05 (premier vrai cours forcé) : la consigne réelle avait
# pour seul mot capitalisé un VERBE d'instruction ("Complétez") -> key-term
# fantôme -> coverage=0 -> un cours dense (502 mots distincts) recalé à 0.40.
# La substance étant le PILIER (preuve du travail), V2 ne doit plus le plomber.
CONSIGNE_PIEGE = (
    "Complétez l'analyse en développant les mécanismes sous-jacents et "
    "leurs implications concrètes."
)


def test_consigne_verbe_capitalise_ne_plombe_pas_un_cours_dense():
    """Un livrable dense ne doit pas couler à cause d'un key-term fantôme
    (verbe d'instruction). La substance l'emporte -> closure."""
    from core.factuality_verifier import extract_key_terms
    kt = extract_key_terms(CONSIGNE_PIEGE)
    assert "Complétez" in kt, f"le verbe capitalisé est bien le key-term fantôme : {kt}"
    s = _score(LIVRABLE_SUBSTANTIEL, CONSIGNE_PIEGE)
    assert s >= SEUIL, f"cours dense plombé par un key-term fantôme (score={s})"
