"""Tests V7.0 (2026-04-20) : Filtre Mnemonique du Router Chunking.

Phase 9 - 3 reformes du systeme d'apprentissage des regles de routage :
  - Deduplication ponderee (hash signature + weight Hebbien)
  - Nettoyage semantique (_INFRA_STOPWORDS)
  - Enregistrement de la decision du Council
  + bonus : routage par match le plus pondere (Option A)

Audit Phase 8 : council_learned_rules.json contenait 23 entrees dont
78% de doublons. V7.0 transforme l'append FIFO aveugle en memoire
associative ponderee.
"""
import json
import os
import tempfile
from unittest.mock import patch
import pytest

from core.router import (
    RouterAgent,
    _keyword_signature,
    _INFRA_STOPWORDS,
    _FR_STOPWORDS,
    _ALL_STOPWORDS,
)


# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════


@pytest.fixture
def tmp_rules_file(tmp_path, monkeypatch):
    """Isole le fichier learned_rules sur un chemin temporaire."""
    tmp_file = tmp_path / "council_learned_rules.json"
    monkeypatch.setattr(RouterAgent, "_LEARNED_RULES_FILE", str(tmp_file))
    # Reset le cache statique
    RouterAgent._learned_rules = []
    yield tmp_file
    RouterAgent._learned_rules = []


def _event(mission, participants, decision=""):
    return {
        "mission": mission,
        "participants": participants,
        "decision": decision,
    }


# ═══════════════════════════════════════════════════════════════════════
# 1. Signature stable et discriminante
# ═══════════════════════════════════════════════════════════════════════


class TestKeywordSignature:

    def test_signature_stable_across_order(self):
        """Meme ensemble -> meme signature (ordre-insensible)."""
        s1 = _keyword_signature(["budget", "quotidien", "presque"])
        s2 = _keyword_signature(["presque", "budget", "quotidien"])
        assert s1 == s2

    def test_signature_different_for_different_keywords(self):
        s1 = _keyword_signature(["budget", "quotidien"])
        s2 = _keyword_signature(["erreur", "crash"])
        assert s1 != s2

    def test_signature_dedup_duplicates_in_list(self):
        """Un mot double dans la liste ne change pas la signature."""
        s1 = _keyword_signature(["budget", "quotidien"])
        s2 = _keyword_signature(["budget", "budget", "quotidien"])
        assert s1 == s2

    def test_signature_is_short_hex(self):
        sig = _keyword_signature(["test", "routing"])
        assert len(sig) == 12
        assert all(c in "0123456789abcdef" for c in sig)


# ═══════════════════════════════════════════════════════════════════════
# 2. Stopwords d'infrastructure
# ═══════════════════════════════════════════════════════════════════════


class TestInfraStopwords:

    def test_infra_words_in_stopset(self):
        """Les mots polluants identifies a l'audit doivent etre bloques."""
        for word in ("debat", "autonome", "promethee", "ressent", "besoin",
                     "conseil", "systeme", "discussion"):
            assert word in _INFRA_STOPWORDS, f"Manque '{word}'"

    def test_bracket_artefacts_in_stopset(self):
        """Les artefacts de formatage [debat, autonome] doivent etre bloques."""
        assert "[debat" in _INFRA_STOPWORDS
        assert "autonome]" in _INFRA_STOPWORDS

    def test_all_stopwords_is_union(self):
        assert _ALL_STOPWORDS >= _INFRA_STOPWORDS
        assert _ALL_STOPWORDS >= _FR_STOPWORDS


# ═══════════════════════════════════════════════════════════════════════
# 3. Deduplication ponderee
# ═══════════════════════════════════════════════════════════════════════


class TestDeduplicationWeighting:

    @pytest.mark.asyncio
    async def test_first_rule_gets_weight_1(self, tmp_rules_file):
        await RouterAgent.on_council_rule_learned(_event(
            "Le budget mensuel est critique.", ["strategist"], "decision 1"
        ))
        assert len(RouterAgent._learned_rules) == 1
        assert RouterAgent._learned_rules[0]["weight"] == 1

    @pytest.mark.asyncio
    async def test_same_keywords_increments_weight(self, tmp_rules_file):
        """3 events avec meme keywords -> 1 regle weight=3."""
        for i in range(3):
            await RouterAgent.on_council_rule_learned(_event(
                "Le budget mensuel est critique.", ["strategist"],
                f"decision {i}"
            ))
        assert len(RouterAgent._learned_rules) == 1
        assert RouterAgent._learned_rules[0]["weight"] == 3

    @pytest.mark.asyncio
    async def test_different_keywords_creates_new_rules(self, tmp_rules_file):
        await RouterAgent.on_council_rule_learned(_event(
            "Le budget mensuel est critique.", ["strategist"]
        ))
        await RouterAgent.on_council_rule_learned(_event(
            "Crash traceback exception runtime.", ["coder"]
        ))
        assert len(RouterAgent._learned_rules) == 2

    @pytest.mark.asyncio
    async def test_signature_persisted_in_json(self, tmp_rules_file):
        await RouterAgent.on_council_rule_learned(_event(
            "Le budget mensuel est critique.", ["strategist"]
        ))
        with open(tmp_rules_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert "signature" in data[0]
        assert "weight" in data[0]
        assert "created_at" in data[0]
        assert "last_seen" in data[0]


# ═══════════════════════════════════════════════════════════════════════
# 4. Decision persistee
# ═══════════════════════════════════════════════════════════════════════


class TestDecisionPersisted:

    @pytest.mark.asyncio
    async def test_decision_stored_in_rule(self, tmp_rules_file):
        await RouterAgent.on_council_rule_learned(_event(
            "Le budget mensuel est critique.", ["strategist"],
            decision="Il faut depriorizer les routines lourdes et activer le mode degrade."
        ))
        rule = RouterAgent._learned_rules[0]
        assert "last_decision" in rule
        assert "depriorizer" in rule["last_decision"]

    @pytest.mark.asyncio
    async def test_decision_updated_on_reinforcement(self, tmp_rules_file):
        """Quand une regle est renforcee, la decision la plus recente
        ecrase la precedente."""
        await RouterAgent.on_council_rule_learned(_event(
            "Le budget mensuel est critique.", ["strategist"],
            decision="Ancienne decision."
        ))
        await RouterAgent.on_council_rule_learned(_event(
            "Le budget mensuel est critique.", ["strategist"],
            decision="Nouvelle decision plus pertinente."
        ))
        assert len(RouterAgent._learned_rules) == 1
        assert "Nouvelle decision" in RouterAgent._learned_rules[0]["last_decision"]

    @pytest.mark.asyncio
    async def test_decision_truncated_to_500_chars(self, tmp_rules_file):
        long_decision = "x" * 1000
        await RouterAgent.on_council_rule_learned(_event(
            "Le budget mensuel est critique.", ["strategist"],
            decision=long_decision
        ))
        assert len(RouterAgent._learned_rules[0]["last_decision"]) <= 500


# ═══════════════════════════════════════════════════════════════════════
# 5. Nettoyage semantique applique
# ═══════════════════════════════════════════════════════════════════════


class TestSemanticCleanup:

    @pytest.mark.asyncio
    async def test_infra_stopwords_filtered_from_keywords(self, tmp_rules_file):
        """'Promethee ressent le besoin de discuter systeme' -> keywords vides."""
        await RouterAgent.on_council_rule_learned(_event(
            "Promethee ressent le besoin de discuter systeme.",
            ["strategist"]
        ))
        # Tous les mots >= 4 chars sont dans _INFRA_STOPWORDS -> keywords vide
        # -> regle rejetee par `if not keywords: return`
        assert len(RouterAgent._learned_rules) == 0

    @pytest.mark.asyncio
    async def test_brackets_stripped_before_filtering(self, tmp_rules_file):
        """'[DEBAT AUTONOME] Le budget critique' -> [debat/autonome] strippes."""
        await RouterAgent.on_council_rule_learned(_event(
            "[DEBAT AUTONOME] Le budget critique reclame attention.",
            ["strategist"]
        ))
        # Apres strip + stopwords, il doit rester des keywords utiles
        assert len(RouterAgent._learned_rules) == 1
        kws = RouterAgent._learned_rules[0]["keywords"]
        # Aucun artefact [debat ou autonome]
        assert not any("[" in kw or "]" in kw for kw in kws)
        # Pas de mots infra
        assert "debat" not in kws
        assert "autonome" not in kws
        # Mais les mots utiles passent
        assert any(kw in ("budget", "critique", "reclame", "attention") for kw in kws)


# ═══════════════════════════════════════════════════════════════════════
# 6. Migration au load + collapse des doublons heritage
# ═══════════════════════════════════════════════════════════════════════


class TestMigrationAndCollapse:

    def test_pre_v7_rules_get_signature_injected(self, tmp_rules_file):
        """Les regles sans signature recoivent une signature au load."""
        pre_v7_rules = [
            {
                "mission_preview": "test",
                "keywords": ["budget", "quotidien"],
                "agent": "strategist",
                "source": "council_chunking",
            }
        ]
        with open(tmp_rules_file, "w", encoding="utf-8") as f:
            json.dump(pre_v7_rules, f)

        RouterAgent._learned_rules = []
        RouterAgent._load_learned_rules()

        assert len(RouterAgent._learned_rules) == 1
        assert "signature" in RouterAgent._learned_rules[0]
        assert RouterAgent._learned_rules[0]["weight"] == 1

    def test_heritage_duplicates_collapsed_at_load(self, tmp_rules_file):
        """10 regles identiques heritage -> 1 regle weight=10 apres load."""
        same_rule = {
            "mission_preview": "Le budget quotidien est presque épuisé.",
            "keywords": ["budget", "quotidien", "presque"],
            "agent": "strategist",
            "source": "council_chunking",
        }
        pre_v7 = [dict(same_rule) for _ in range(10)]
        with open(tmp_rules_file, "w", encoding="utf-8") as f:
            json.dump(pre_v7, f)

        RouterAgent._learned_rules = []
        RouterAgent._load_learned_rules()

        # 10 doublons -> 1 unique
        assert len(RouterAgent._learned_rules) == 1
        # Les weights sont additionnes (chaque regle pre-V7 weight=1 apres
        # migration, puis sum au collapse)
        assert RouterAgent._learned_rules[0]["weight"] == 10

    def test_mixed_rules_partially_collapsed(self, tmp_rules_file):
        """Melange : 3 identiques + 1 unique -> 2 regles (weight=3, weight=1)."""
        rule_a = {
            "keywords": ["budget", "quotidien"], "agent": "strategist",
            "source": "council_chunking",
        }
        rule_b = {
            "keywords": ["erreur", "crash"], "agent": "coder",
            "source": "council_chunking",
        }
        pre_v7 = [dict(rule_a), dict(rule_a), dict(rule_a), dict(rule_b)]
        with open(tmp_rules_file, "w", encoding="utf-8") as f:
            json.dump(pre_v7, f)

        RouterAgent._learned_rules = []
        RouterAgent._load_learned_rules()

        assert len(RouterAgent._learned_rules) == 2
        weights = sorted(r["weight"] for r in RouterAgent._learned_rules)
        assert weights == [1, 3]


# ═══════════════════════════════════════════════════════════════════════
# 7. Eviction par poids (remplacement du FIFO aveugle)
# ═══════════════════════════════════════════════════════════════════════


class TestEvictionByWeight:

    @pytest.mark.asyncio
    async def test_max_rules_respected(self, tmp_rules_file, monkeypatch):
        """Depassement de MAX -> troncation."""
        monkeypatch.setattr(RouterAgent, "_MAX_LEARNED_RULES", 3)
        # Creer 5 regles avec des keywords differents
        for i in range(5):
            await RouterAgent.on_council_rule_learned(_event(
                f"Mission variante numero {i} specifique.", ["strategist"]
            ))
        assert len(RouterAgent._learned_rules) <= 3

    @pytest.mark.asyncio
    async def test_high_weight_rules_survive_eviction(
            self, tmp_rules_file, monkeypatch):
        """Au depassement MAX, les regles les plus ponderees survivent."""
        monkeypatch.setattr(RouterAgent, "_MAX_LEARNED_RULES", 2)
        # Regle A : 5 hits
        for _ in range(5):
            await RouterAgent.on_council_rule_learned(_event(
                "Le budget critique urgent.", ["strategist"]
            ))
        # Regle B : 3 hits
        for _ in range(3):
            await RouterAgent.on_council_rule_learned(_event(
                "Crash traceback imminent.", ["coder"]
            ))
        # Regle C unique : 1 hit, declenche eviction
        await RouterAgent.on_council_rule_learned(_event(
            "Refactor architectural profond.", ["architect"]
        ))

        # A et B doivent survivre (weights 5 et 3), C doit etre evincee
        # (MAX=2). Verifier que les agents A et B sont presents.
        agents = {r["agent"] for r in RouterAgent._learned_rules}
        assert "strategist" in agents
        assert "coder" in agents
        assert "architect" not in agents


# ═══════════════════════════════════════════════════════════════════════
# 8. Routage par match le plus pondere (Option A)
# ═══════════════════════════════════════════════════════════════════════


class TestCheckLearnedRulesByWeight:

    @pytest.mark.asyncio
    async def test_highest_weight_wins_on_competing_match(self, tmp_rules_file):
        """Si 2 regles matchent, celle au weight le plus eleve gagne."""
        # Regle A : "budget" -> strategist, weight 5
        for _ in range(5):
            await RouterAgent.on_council_rule_learned(_event(
                "Le budget critique reclame attention.", ["strategist"]
            ))
        # Regle B : "budget" + "autre" -> coder, weight 1
        # Mais "budget" est un keyword partage -> les deux regles matchent
        # quand on cherche "budget"
        await RouterAgent.on_council_rule_learned(_event(
            "Budget marginal secondaire.", ["coder"]
        ))

        # Forcer les deux regles a partager le keyword "budget"
        # Une mission qui contient "budget" devrait router vers strategist
        # car weight=5 > weight=1
        result = RouterAgent._check_learned_rules(
            "budget discussion generique"
        )
        assert result == "strategist", (
            f"Attendu strategist (weight 5), obtenu {result}"
        )

    @pytest.mark.asyncio
    async def test_single_match_returns_its_agent(self, tmp_rules_file):
        """Une seule regle matche -> on retourne son agent meme si weight=1."""
        await RouterAgent.on_council_rule_learned(_event(
            "Refactor architectural profond.", ["architect"]
        ))
        result = RouterAgent._check_learned_rules(
            "refactor urgent du systeme"
        )
        assert result == "architect"

    @pytest.mark.asyncio
    async def test_no_match_returns_none(self, tmp_rules_file):
        await RouterAgent.on_council_rule_learned(_event(
            "Le budget critique reclame attention.", ["strategist"]
        ))
        result = RouterAgent._check_learned_rules(
            "mission completement hors sujet xyz"
        )
        assert result is None
