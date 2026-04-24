"""Tests V24 — Fix regex _extract_cited_names : faux positifs grammaire francaise.

Observation 24/04 12:18 (tir V15.7 CODE_REVIEW prefrontal.py) : le verifier
avait signale "HALLUCINATION: 2/2 noms cites" avec Inventes=['soient', 'avec'].
Ce sont des MOTS FRANCAIS extraits par les regex `(\\w{3,})` ou (\\w{3,})\\(\\)
alors que la stoplist ne contenait que des mots anglais.

V24 fix : stoplist etendue FR + heuristique "ressemble a un identifier Python"
(underscore, digit, ou CamelCase avec >=2 majuscules).
"""
import pytest

from core.reasoning_protocol import _extract_cited_names


class TestV24GrammaireFrancaise:
    """Les mots grammaticaux FR ne doivent JAMAIS etre captures."""

    def test_soient_in_backticks_rejected(self):
        # Cas reel observe 12:18 : le livrable contenait `soient` en backticks
        result = "Les imports doivent etre `soient` valides pour l audit."
        names = _extract_cited_names(result)
        assert "soient" not in [n.lower() for n in names]

    def test_avec_in_backticks_rejected(self):
        result = "Verification `avec` la regex standard"
        names = _extract_cited_names(result)
        assert "avec" not in [n.lower() for n in names]

    def test_dans_pour_par_rejected(self):
        result = "La fonction existe `dans` le module `pour` traiter `par` batch"
        names = _extract_cited_names(result)
        lower = [n.lower() for n in names]
        assert "dans" not in lower
        assert "pour" not in lower
        assert "par" not in lower

    def test_common_french_verbs_rejected(self):
        result = "Les erreurs `etre` `avoir` `faire` `peut` `doit` captees."
        names = _extract_cited_names(result)
        lower = [n.lower() for n in names]
        for word in ("etre", "avoir", "faire", "peut", "doit"):
            assert word not in lower

    def test_demonstratifs_rejected(self):
        result = "`cette` classe et `ces` methodes sont `cela`"
        names = _extract_cited_names(result)
        lower = [n.lower() for n in names]
        for word in ("cette", "ces", "cela"):
            assert word not in lower


class TestV24VraisIdentifiersPassent:
    """Les vrais noms de fonctions/classes doivent TOUJOURS etre captures."""

    def test_snake_case_passes(self):
        result = "La methode `verify_code_review` valide le resultat"
        names = _extract_cited_names(result)
        assert "verify_code_review" in names

    def test_private_underscore_prefix_passes(self):
        result = "Appel interne `_extract_real_names` sur le filepath"
        names = _extract_cited_names(result)
        assert "_extract_real_names" in names

    def test_dunder_passes(self):
        result = "Override de `__init__` dans la sous-classe"
        names = _extract_cited_names(result)
        assert "__init__" in names

    def test_camelcase_class_passes(self):
        result = "La classe `PrefrontalCortex` gere le veto"
        names = _extract_cited_names(result)
        assert "PrefrontalCortex" in names

    def test_intent_uppercase_passes(self):
        # Les intents projet sont UPPER_SNAKE_CASE
        result = "La routine `COUNCIL_DEBATE` est dispatchee"
        names = _extract_cited_names(result)
        assert "COUNCIL_DEBATE" in names

    def test_function_call_syntax_passes(self):
        result = "Appel a is_prime() sur chaque nombre."
        names = _extract_cited_names(result)
        assert "is_prime" in names


class TestV24HeuristiqueIdentifier:
    """Test de l'heuristique ressemble-a-un-identifier-python."""

    def test_tout_minuscule_sans_underscore_rejected(self):
        # Un mot tout minuscule sans underscore n'est pas un identifier typique
        # MEME s il n est pas dans la stoplist FR. Ex : "hello" ou "world"
        result = "`world` `hello` `super`"  # 3 mots courants
        names = _extract_cited_names(result)
        lower = [n.lower() for n in names]
        # L heuristique les rejette car pas d'underscore / maj / digit
        assert "world" not in lower
        assert "hello" not in lower

    def test_digit_allowed(self):
        # Un nom avec chiffre passe (ex : v1_parser, utf8_decode)
        result = "La fonction `v1_parser` traite les entrees utf8"
        names = _extract_cited_names(result)
        assert "v1_parser" in names

    def test_short_names_rejected(self):
        # Moins de 3 chars = rejete par la regex (\\w{3,})
        result = "`ab` `xy`"
        names = _extract_cited_names(result)
        assert "ab" not in [n.lower() for n in names]


class TestV24MixedReal:
    """Cas realistes : audit mixant vrais noms + grammaire."""

    def test_prefrontal_audit_realist(self):
        # Simule un vrai audit bien ecrit
        result = (
            "La classe `PrefrontalCortex` expose `_narrate` et `deliberer`. "
            "`soient` valides, les goals doivent `etre` persistes `avec` "
            "`_save_goals` dans `memory/prefrontal_state.json`."
        )
        names = _extract_cited_names(result)
        lower = [n.lower() for n in names]
        # Les vrais doivent passer
        assert "prefrontalcortex" in lower
        assert "_narrate" in lower
        assert "_save_goals" in lower
        # Les faux positifs FR doivent etre rejetes
        assert "soient" not in lower
        assert "etre" not in lower
        assert "avec" not in lower
        # deliberer : tout minuscule sans _ ni maj, sera rejete par heuristique
        # (c'est le prix a payer pour eviter "soient" / "avec")
        assert "deliberer" not in lower

    def test_hallucination_total(self):
        # Livrable qui cite des fonctions inexistantes mais typees identifier
        result = "Les methodes `_save_failures` et `choose_model` sont buggees"
        names = _extract_cited_names(result)
        assert "_save_failures" in names
        assert "choose_model" in names
