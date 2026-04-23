"""Tests V9.0 (Phase 12 - 2026-04-21) : Liberté d'Expression.

Audit runtime 21/04 : 100% des debats Council finissaient en max_rounds
a cause d'un baillonage par Bloom :
  - AUDIT_SURVIE (intent valide) detecte comme 'classe Python absente'
  - chromadb (package externe) detecte comme 'fonction absente'
Les agents ne pouvaient pas repondre -> 0% consensus.

Piste B : injection d'identifiants systeme (intents + modes + packages)
Piste A : argument whitelist optionnel dans check_prompt
"""
import os
import tempfile
from unittest.mock import patch
import pytest

from core.bloom_filter import BloomIndexManager, BloomFilter


# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════


@pytest.fixture
def built_manager():
    """Manager avec indexes construits sur le projet reel."""
    BloomIndexManager.reset_singleton()
    manager = BloomIndexManager()
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    manager.build_indexes(project_root)
    yield manager
    BloomIndexManager.reset_singleton()


# ═══════════════════════════════════════════════════════════════════════
# Piste B - Injection system identifiers
# ═══════════════════════════════════════════════════════════════════════


class TestSystemIdentifiersInjected:
    """Les intents, modes et packages doivent etre reconnus par Bloom."""

    def test_intent_audit_survie_not_veto(self, built_manager):
        """AUDIT_SURVIE est un intent valide -> pas de veto."""
        prompt = "Faut-il DEPRIORISER `AUDIT_SURVIE` ? Verdict requis."
        veto = built_manager.check_prompt("strategist", prompt)
        assert veto is None, f"AUDIT_SURVIE faussement veto : {veto}"

    def test_intent_veille_silencieuse_not_veto(self, built_manager):
        prompt = "Faut-il PRIORISER `VEILLE_SILENCIEUSE` (qualite 1.00) ?"
        veto = built_manager.check_prompt("researcher", prompt)
        assert veto is None

    def test_intent_council_debate_not_veto(self, built_manager):
        prompt = "Analyser les stats de `COUNCIL_DEBATE` dernieres 40."
        veto = built_manager.check_prompt("strategist", prompt)
        assert veto is None

    def test_package_chromadb_not_veto(self, built_manager):
        """chromadb est un package externe legitime."""
        prompt = "Il faudrait verifier chromadb() pour la persistance memoire."
        veto = built_manager.check_prompt("coder", prompt)
        assert veto is None

    def test_package_fastapi_not_veto(self, built_manager):
        prompt = "Le endpoint utilise fastapi(app) pour router les requetes."
        veto = built_manager.check_prompt("architect", prompt)
        assert veto is None

    def test_mode_survie_not_veto(self, built_manager):
        """Les modes strategiques doivent etre reconnus."""
        prompt = "Le mode `SURVIE` impose des routines 0-LLM uniquement."
        veto = built_manager.check_prompt("strategist", prompt)
        assert veto is None

    def test_mode_exhausted_not_veto(self, built_manager):
        prompt = "Budget `EXHAUSTED` -> bascule en `SURVIE`."
        veto = built_manager.check_prompt("strategist", prompt)
        assert veto is None

    def test_verdict_action_prioriser_not_veto(self, built_manager):
        prompt = "`PRIORISER` la routine si qualite > 0.8, sinon `MAINTENIR`."
        veto = built_manager.check_prompt("strategist", prompt)
        assert veto is None

    # V9.1 (21/04) : noms d'agents en MAJUSCULES ne doivent plus veto
    def test_agent_name_strategist_upper_not_veto(self, built_manager):
        """Critique entre agents : `STRATEGIST a tort`. Reforme V9.1."""
        prompt = "Comme le disait `STRATEGIST`, il faut consolider."
        veto = built_manager.check_prompt("evolution", prompt)
        assert veto is None, f"STRATEGIST toujours veto : {veto}"

    def test_agent_name_evolution_upper_not_veto(self, built_manager):
        prompt = "`EVOLUTION` a propose une optimisation douteuse."
        veto = built_manager.check_prompt("strategist", prompt)
        assert veto is None

    def test_agent_name_architect_upper_not_veto(self, built_manager):
        prompt = "`ARCHITECT` preside sans participer au debat."
        veto = built_manager.check_prompt("coder", prompt)
        assert veto is None

    def test_entity_promethee_upper_not_veto(self, built_manager):
        prompt = "`PROMETHEE` ressent une insecurite latente."
        veto = built_manager.check_prompt("strategist", prompt)
        assert veto is None


# ═══════════════════════════════════════════════════════════════════════
# Piste B - Les vraies hallucinations sont toujours veto
# ═══════════════════════════════════════════════════════════════════════


class TestGenuineHallucinationsStillCaught:
    """Non-regression : les vrais appels fabriques doivent toujours veto."""

    def test_fabricated_function_still_veto(self, built_manager):
        """Au moins UNE fonction fabriquee sur N doit veto (les faux
        positifs Bloom ~0.8% peuvent faire passer une collision)."""
        candidates = (
            "xyzabc_nonexistent_fn",
            "zzz_phantom_method_qwe",
            "qqq_mirage_helper_rst",
            "impossible_function_vxy",
        )
        vetos = []
        for name in candidates:
            built_manager._veto_count = 0  # reset
            # V4.3 : bloc code pour declencher le scan
            prompt = f"```python\n{name}()\n```"
            veto = built_manager.check_prompt("coder", prompt)
            if veto is not None:
                vetos.append((name, veto))
        assert len(vetos) >= 1, (
            f"Aucune des {len(candidates)} fonctions fabriquees n'a veto "
            f"(taux faux positifs Bloom anormal)"
        )

    def test_fabricated_class_still_veto(self, built_manager):
        """Meme principe pour les classes fabriquees."""
        candidates = (
            "ZzzPhantomClass",
            "QqqMirageClass",
            "XyzNonexistentEntity",
            "ImpossibleClassName",
        )
        vetos = []
        for name in candidates:
            built_manager._veto_count = 0
            # V4.3 : bloc code pour declencher le scan
            prompt = f"```python\n# Utiliser la classe `{name}` pour l'audit.\n```"
            veto = built_manager.check_prompt("coder", prompt)
            if veto is not None:
                vetos.append((name, veto))
        assert len(vetos) >= 1, (
            f"Aucune des {len(candidates)} classes fabriquees n'a veto"
        )


# ═══════════════════════════════════════════════════════════════════════
# Piste A - Whitelist parametre
# ═══════════════════════════════════════════════════════════════════════


class TestWhitelistParameter:
    """check_prompt accepte un whitelist optionnel pour declarer
    des tokens legitimes du contexte."""

    def _find_veto_name_fn(self, manager, candidates):
        """Helper : trouve un nom de fonction qui produit effectivement
        un veto (pas un faux positif Bloom). V4.3 : prompt avec bloc code."""
        for name in candidates:
            prompt = f"```python\n{name}()\n```"
            if manager.check_prompt("coder", prompt) is not None:
                return name
        return None

    def test_whitelist_bypasses_veto_function(self, built_manager):
        """V4.3 : ref absent de l'index mais whitelist -> pas de veto."""
        name = self._find_veto_name_fn(built_manager, (
            "xyzabc_nonexistent_fn", "zzz_phantom_method_qwe",
            "qqq_mirage_helper_rst", "impossible_function_vxy",
        ))
        assert name is not None, "Aucun nom de fonction ne vetoe (pb Bloom)"

        built_manager._veto_count = 0
        prompt = f"```python\n{name}()\n```"
        # Avec whitelist : pas de veto
        veto_with = built_manager.check_prompt(
            "coder", prompt, whitelist={name}
        )
        assert veto_with is None, (
            f"Whitelist n'a pas empeche le veto sur {name}"
        )

    def test_whitelist_bypasses_veto_class(self, built_manager):
        """Class whitelist bypass."""
        candidates = ("ZzzPhantomClass", "QqqMirageClass",
                      "XyzNonexistentEntity", "ImpossibleClassName")
        # Trouver une classe qui veto effectivement sans whitelist (V4.3 : bloc code)
        chosen = None
        for name in candidates:
            built_manager._veto_count = 0
            if built_manager.check_prompt(
                "coder", f"```\nClasse `{name}` utilisee.\n```"
            ) is not None:
                chosen = name
                break
        assert chosen is not None

        built_manager._veto_count = 0
        veto = built_manager.check_prompt(
            "coder", f"```\nClasse `{chosen}` utilisee.\n```",
            whitelist={chosen}
        )
        assert veto is None

    def test_whitelist_empty_equivalent_to_none(self, built_manager):
        """whitelist=None et whitelist=set() ont le meme comportement."""
        name = self._find_veto_name_fn(built_manager, (
            "xyzabc_nonexistent_fn", "zzz_phantom_method_qwe",
            "qqq_mirage_helper_rst", "impossible_function_vxy",
        ))
        assert name is not None
        prompt = f"```python\n{name}()\n```"

        built_manager._veto_count = 0
        veto1 = built_manager.check_prompt("coder", prompt, whitelist=None)
        built_manager._veto_count = 0
        veto2 = built_manager.check_prompt("coder", prompt, whitelist=set())
        # Les deux doivent veto (comportement identique)
        assert veto1 is not None
        assert veto2 is not None

    def test_whitelist_does_not_affect_valid_refs(self, built_manager):
        """Un prompt legitime reste legitime meme avec whitelist vide."""
        prompt = "Faut-il DEPRIORISER `AUDIT_SURVIE` ?"
        veto = built_manager.check_prompt(
            "strategist", prompt, whitelist=set()
        )
        assert veto is None  # car AUDIT_SURVIE est injecte par Piste B


# ═══════════════════════════════════════════════════════════════════════
# Piste B - Non regression : builtins et stdlib toujours reconnus
# ═══════════════════════════════════════════════════════════════════════


class TestBuiltinsStillRecognized:
    """Les builtins/stdlib (deja dans _BUILTIN_FUNCS pre-V9) restent OK."""

    def test_builtin_print_not_veto(self, built_manager):
        prompt = "print('hello world')"
        veto = built_manager.check_prompt("coder", prompt)
        assert veto is None

    def test_stdlib_json_not_veto(self, built_manager):
        prompt = "data = json.loads(content)"
        veto = built_manager.check_prompt("coder", prompt)
        assert veto is None
