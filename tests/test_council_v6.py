"""Tests V6.0 (2026-04-20) : Reforme du Parlement (Debate Engine V2).

Phase 7 - 3 reformes constitutionnelles apres audit Phase 6 :
 - Reforme 1 : Separation des pouvoirs (architect jamais participant)
 - Reforme 2 : Vote flou (regex robuste anti-faute de frappe LLM 8B)
 - Reforme 3 : Partial insight (capture valeur des max_rounds)
"""
from unittest.mock import MagicMock
import pytest

from core.council import (
    Council, PRESIDENT_AGENT_NAME,
    _is_consensus_v2, _CONSENSUS_REGEX_STRICT,
    _VOTE_LINE_FLEX, _VOTE_NEGATIVE,
    MIN_CONSENSUS_CONTENT_LENGTH,
)


# ═══════════════════════════════════════════════════════════════════════
# Reforme 1 - Separation des pouvoirs
# ═══════════════════════════════════════════════════════════════════════


class TestSeparationDesPouvoirs:
    """L'architect (PRESIDENT_AGENT_NAME) ne doit jamais etre participant.
    Il doit rester arbitre pour emettre REDIRECT / ABORT."""

    def _make_agents(self, names):
        """Helper : cree un dict d'agents mocks avec les noms fournis."""
        return {n: MagicMock() for n in names}

    def test_architect_filtered_out_of_participants(self):
        """Si architect est dans la liste initiale, il doit etre retire."""
        agents = self._make_agents(["strategist", "architect", "security",
                                    "evolution", "coder"])
        c = Council(
            agents=agents,
            participants=["strategist", "architect", "security"],
            mission="test",
        )
        assert PRESIDENT_AGENT_NAME not in c.participants, (
            f"Architect devrait etre retire, obtenu {c.participants}"
        )
        assert "strategist" in c.participants
        assert "security" in c.participants

    def test_fallback_preserves_participant_count(self):
        """Quand architect est retire, un fallback doit prendre sa place
        pour maintenir le nombre initial de participants (ici 3)."""
        agents = self._make_agents(["strategist", "architect", "security",
                                    "evolution", "coder", "writer"])
        c = Council(
            agents=agents,
            participants=["strategist", "architect", "security"],
            mission="test",
        )
        assert len(c.participants) == 3, (
            f"Attendu 3 participants apres filtre+fallback, "
            f"obtenu {len(c.participants)} : {c.participants}"
        )
        # Le fallback doit etre un agent dispo qui n'est pas deja dans la liste
        fallback_added = set(c.participants) - {"strategist", "security"}
        assert len(fallback_added) == 1
        added = fallback_added.pop()
        assert added in ("evolution", "coder", "writer")

    def test_fallback_skips_agents_not_in_pool(self):
        """Si un fallback n'existe pas dans self.agents, il est saute."""
        # Seuls strategist, architect, security existent comme agents
        # -> apres filtrage d'architect il reste 2, et aucun fallback dispo
        # (evolution/coder/writer/researcher/formatter absents du dict agents)
        # strategist est deja dans les participants, donc on ne le re-ajoute pas
        agents = self._make_agents(["strategist", "architect", "security"])
        c = Council(
            agents=agents,
            participants=["strategist", "architect", "security"],
            mission="test",
        )
        # architect retire, 2 restent, aucun fallback dispo -> accepte tel quel
        assert c.participants == ["strategist", "security"]
        assert PRESIDENT_AGENT_NAME not in c.participants

    def test_no_architect_means_no_change(self):
        """Si architect n'etait pas dans la liste, rien ne change."""
        agents = self._make_agents(["strategist", "coder", "evolution"])
        c = Council(
            agents=agents,
            participants=["strategist", "coder", "evolution"],
            mission="test",
        )
        assert c.participants == ["strategist", "coder", "evolution"]

    def test_minimum_2_participants_guaranteed(self):
        """Edge case : topic ne contenait qu'architect. Apres filtrage,
        il reste 0. Le patch doit completer jusqu'a >= 2."""
        agents = self._make_agents(["architect", "evolution", "coder",
                                    "writer", "strategist"])
        c = Council(
            agents=agents,
            participants=["architect"],  # 1 seul, et c'est le president
            mission="test",
        )
        assert PRESIDENT_AGENT_NAME not in c.participants
        assert len(c.participants) >= 2, (
            f"Garantie 2 participants violee : {c.participants}"
        )

    def test_architect_remains_as_president_agent(self):
        """Meme retire des participants, architect reste dans self.agents
        pour pouvoir etre convoque comme President par _evaluate_round."""
        agents = self._make_agents(["strategist", "architect", "security",
                                    "evolution"])
        c = Council(
            agents=agents,
            participants=["strategist", "architect", "security"],
            mission="test",
        )
        assert PRESIDENT_AGENT_NAME in c.agents, (
            "Architect doit rester dans self.agents pour officier comme "
            "President (seulement retire des participants)."
        )

    def test_participants_is_copied_not_mutated(self):
        """Le patch utilise list(participants), donc la liste passee par
        l'appelant ne doit PAS etre mutee."""
        agents = self._make_agents(["strategist", "architect", "security",
                                    "evolution"])
        original = ["strategist", "architect", "security"]
        c = Council(agents=agents, participants=original, mission="test")
        # La liste originale ne doit pas etre modifiee
        assert original == ["strategist", "architect", "security"]
        # Mais c.participants doit refleter le filtre
        assert PRESIDENT_AGENT_NAME not in c.participants


# ═══════════════════════════════════════════════════════════════════════
# Reforme 2 - Vote flou (_is_consensus_v2)
# ═══════════════════════════════════════════════════════════════════════


# Helper : construit un texte assez long pour passer le filtre
# MIN_CONSENSUS_CONTENT_LENGTH = 100
def _long(prefix: str) -> str:
    padding = " " + "La solution propose modifie core/council.py avec un patch cible et teste."
    while len(prefix) < MIN_CONSENSUS_CONTENT_LENGTH + 20:
        prefix += padding
    return prefix


class TestVoteFlouPositifs:
    """Doit matcher les marqueurs legitimes."""

    def test_consensus_canonique(self):
        assert _is_consensus_v2(_long("CONSENSUS: la solution est validee."))

    def test_consensu_tronque_8B(self):
        """Le LLM 8B tronque souvent CONSENSUS en CONSENU (47x observes)."""
        assert _is_consensus_v2(_long("CONSENU: verdict positif."))

    def test_consenus_typo(self):
        assert _is_consensus_v2(_long("CONSENUS - nous sommes d'accord."))

    def test_approuve_majuscule(self):
        assert _is_consensus_v2(_long("APPROUVE. La proposition est solide."))

    def test_approuve_accent(self):
        assert _is_consensus_v2(_long("APPROUVÉ: les 3 critiques sont adressees."))

    def test_accord_final(self):
        assert _is_consensus_v2(_long("ACCORD FINAL: implementation validee."))

    def test_vote_structure_pour(self):
        """Format commande VOTE: POUR (Reforme 2 couche 3)."""
        text = _long("VOTE: POUR\n\nLa solution adresse les critiques precedentes.")
        assert _is_consensus_v2(text)

    def test_verdict_structure_case_insensible(self):
        """Le format commande est case-insensible."""
        assert _is_consensus_v2(_long("verdict: pour\n\nJe valide."))

    def test_consensus_apres_nouvelle_ligne(self):
        """Le marqueur doit aussi matcher en debut de ligne interne."""
        text = _long("Tour 3 analyse.\nCONSENSUS: les critiques sont levees.")
        assert _is_consensus_v2(text)


class TestVoteFlouFauxPositifs:
    """Doit REJETER le vocabulaire courant qui contient les stems."""

    def test_consequence_rejete(self):
        """'En consequence' contient 'consens' mais n'est pas un vote."""
        text = _long("En consequence, nous procedons avec la proposition.")
        assert not _is_consensus_v2(text)

    def test_consentement_rejete(self):
        """'consentement' contient 'consent' mais n'est pas un vote."""
        text = _long("Mon consentement est acquis sur ce point.")
        assert not _is_consensus_v2(text)

    def test_accordeon_rejete(self):
        """'accordeon' contient 'accord' mais n'est pas un vote."""
        text = _long("L'accordeon est un instrument de musique complexe.")
        assert not _is_consensus_v2(text)

    def test_pas_d_accord_rejete(self):
        """'pas d'accord' en milieu de phrase doit pas etre matche
        (debut de ligne obligatoire pour couche 1)."""
        text = _long("Je ne suis pas d'accord avec cette proposition du tout.")
        assert not _is_consensus_v2(text)

    def test_vote_contre_rejete(self):
        """VOTE: CONTRE invalide meme si CONSENSUS apparait ailleurs."""
        text = _long("VOTE: CONTRE\n\nJe m'oppose bien que CONSENSUS soit le theme.")
        assert not _is_consensus_v2(text)

    def test_meta_discours_rejete(self):
        """'CONSENSUS est le mot-cle' : pas de terminateur ponctuation."""
        # Le mot CONSENSUS doit etre suivi d'un terminateur (. : , ! \n -)
        # sinon c'est considere comme du meta-discours.
        text = _long("CONSENSUS est le mot-cle que nous cherchons a definir ici.")
        assert not _is_consensus_v2(text)

    def test_texte_trop_court(self):
        """En dessous de MIN_CONSENSUS_CONTENT_LENGTH = 100, rejet."""
        assert not _is_consensus_v2("CONSENSUS.")

    def test_texte_vide(self):
        assert not _is_consensus_v2("")
        assert not _is_consensus_v2(None)

    def test_lowercase_rejete(self):
        """'consensus' en minuscules n'est pas un vote formel."""
        text = _long("consensus de la communaute scientifique sur ce sujet.")
        assert not _is_consensus_v2(text)


class TestVoteStructureRejetNegatif:
    """Le format commande VOTE: permet aussi d'exprimer un rejet clair."""

    def test_vote_contre_detecte_comme_negatif(self):
        assert _VOTE_NEGATIVE.search("VOTE: CONTRE\nJe refuse.")

    def test_verdict_non_detecte_comme_negatif(self):
        assert _VOTE_NEGATIVE.search("VERDICT: NON\nRaison: hors perimetre.")

    def test_position_oppose_detecte_comme_negatif(self):
        assert _VOTE_NEGATIVE.search("position: oppose\nArguments...")

    def test_vote_rejete_invalide_consensus_partout(self):
        """Un VOTE: CONTRE invalide meme si un marqueur positif existe ailleurs."""
        text = _long("VOTE: CONTRE\n\nBien que le mot CONSENSUS soit mentionne ici.")
        assert not _is_consensus_v2(text)


# ═══════════════════════════════════════════════════════════════════════
# Reforme 3 - Partial Insight
# ═══════════════════════════════════════════════════════════════════════


class TestPartialInsight:
    """Extraction de valeur d'un debat max_rounds."""

    def _make_council_with_transcript(self, transcript):
        """Helper : cree un Council et injecte un transcript."""
        agents = {n: MagicMock() for n in ["strategist", "coder", "evolution"]}
        c = Council(
            agents=agents,
            participants=["strategist", "coder", "evolution"],
            mission="Comment ameliorer le cortex prefrontal ?",
        )
        c.transcript = transcript
        return c

    def test_returns_none_if_less_than_2_entries(self):
        """Impossible d'extraire convergence avec 1 seul agent."""
        c = self._make_council_with_transcript([
            {"agent": "strategist", "round": 1, "content": "analyse seule",
             "score": 0.5},
        ])
        assert c._extract_partial_insight() is None

    def test_returns_none_if_single_agent_multiple_rounds(self):
        """Meme agent en 3 rounds ne produit pas de convergence multi-agent."""
        c = self._make_council_with_transcript([
            {"agent": "strategist", "round": 1, "content": "cortex prefrontal goals",
             "score": 0.5},
            {"agent": "strategist", "round": 2, "content": "cortex prefrontal delibration",
             "score": 0.4},
        ])
        # 1 seul agent unique -> len(agents_kw) < 2 -> None
        assert c._extract_partial_insight() is None

    def test_convergence_keywords_extracted(self):
        """Les mots-cles partages entre agents doivent apparaitre."""
        c = self._make_council_with_transcript([
            {"agent": "strategist", "round": 1,
             "content": "cortex prefrontal priorite goal stale accumulator",
             "score": 0.6},
            {"agent": "coder", "round": 1,
             "content": "accumulator priorite cortex fix base source",
             "score": 0.7},
            {"agent": "strategist", "round": 2,
             "content": "cortex prefrontal priorite recalcule base stable",
             "score": 0.65},
            {"agent": "coder", "round": 2,
             "content": "base priorite source cortex fix recalcul",
             "score": 0.72},
        ])
        insight = c._extract_partial_insight()
        assert insight is not None
        conv = insight["convergence_keywords"]
        # "cortex", "priorite", "base" apparaissent chez les 2 agents
        assert any(kw in conv for kw in ("cortex", "priorite", "base"))

    def test_divergence_by_agent(self):
        """Chaque agent a ses mots propres."""
        c = self._make_council_with_transcript([
            {"agent": "strategist", "round": 1,
             "content": "strategie planification objectifs horizon",
             "score": 0.5},
            {"agent": "coder", "round": 1,
             "content": "implementation python regex pattern",
             "score": 0.6},
        ])
        insight = c._extract_partial_insight()
        assert insight is not None
        div = insight["divergence_by_agent"]
        assert "strategist" in div
        assert "coder" in div
        # strategist a des mots que coder n'a pas
        assert len(div["strategist"]) > 0
        assert len(div["coder"]) > 0

    def test_best_argument_selected(self):
        """Le meilleur argument est celui avec le score le plus eleve."""
        c = self._make_council_with_transcript([
            {"agent": "strategist", "round": 1, "content": "faible argument",
             "score": 0.2},
            {"agent": "coder", "round": 1,
             "content": "excellent argument technique precis", "score": 0.85},
            {"agent": "evolution", "round": 1, "content": "moyen", "score": 0.5},
        ])
        insight = c._extract_partial_insight()
        assert insight is not None
        assert insight["best_argument"] is not None
        assert insight["best_argument"]["agent"] == "coder"
        assert insight["best_argument"]["score"] == 0.85

    def test_ignores_student_and_advocate_entries(self):
        """Les contributions de promethee-etudiant et avocat ne comptent pas."""
        c = self._make_council_with_transcript([
            {"agent": "promethee", "round": 1, "content": "question etudiant",
             "is_student": True, "score": 0},
            {"agent": "advocat", "round": 1, "content": "question avocat",
             "is_advocate": True, "score": 0},
            {"agent": "strategist", "round": 1, "content": "reponse strategist",
             "score": 0.5},
        ])
        # Seul strategist compte -> 1 agent unique -> None
        assert c._extract_partial_insight() is None
