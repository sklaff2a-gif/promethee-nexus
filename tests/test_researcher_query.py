"""Tests extraction query WebSurfer — Fix Robuste 2026-05-20 (Phase 3).

Couvre la cascade _extract_search_query a 3 niveaux :
  1. SCOLAIRE  : balise SUJET DU JOUR (PRIORITE ABSOLUE)
  2. NON-SCOLAIRE : fallback premiere ligne non-vide nettoyee
  3. GARDE-FOU : troncature a 200 chars + cas mission vide

Regression visee : incident RESEARCH 2026-05-20 01h07 (prompt scolaire 2000 chars
envoye comme query -> SERP vide -> hallucination par carence).
"""
import pytest
from unittest.mock import patch

from Agents.researcher_agent import DivineResearcher, _MAX_QUERY_CHARS


@pytest.fixture
def researcher():
    # On instancie sans declencher d'I/O reseau (WebSurfer init est leger).
    return DivineResearcher()


# --- Niveau 1 : extraction scolaire ---

class TestExtractionScolaire:

    def test_sujet_du_jour_research(self, researcher):
        """Le format SCHOOL_SLOT RESEARCH : extrait le sujet entre balise et separateur."""
        mission = (
            "[SCHOOL_SLOT: RESEARCH]\n"
            "PROTOCOLE_SCOLAIRE\n"
            "COURS : Recherche et veille technique\n\n"
            "=============================================\n"
            "SUJET DU JOUR (PRIORITE ABSOLUE) :\n"
            "Evaluation automatique de la qualite du code : metriques, linting, scoring\n"
            "=============================================\n\n"
            "NIVEAU DE DIFFICULTE : 3.0/3.0\n"
            "Redige une note de synthese structuree EXCLUSIVEMENT sur ce sujet :\n"
            "1. Definition et concepts cles\n"
        )
        q = researcher._extract_search_query(mission)
        assert q == "Evaluation automatique de la qualite du code : metriques, linting, scoring"
        # Le prompt parasite (PROTOCOLE, NIVEAU, plan) ne doit PAS etre dans la query
        assert "PROTOCOLE_SCOLAIRE" not in q
        assert "NIVEAU DE DIFFICULTE" not in q
        assert len(q) < _MAX_QUERY_CHARS

    def test_sujet_du_jour_code_review(self, researcher):
        """Format CODE_REVIEW : extrait la consigne de revue."""
        mission = (
            "COURS : Revue de code\n\n"
            "=============================================\n"
            "SUJET DU JOUR (PRIORITE ABSOLUE) :\n"
            "Tu dois faire la REVUE DE CODE du fichier : core/prefrontal.py\n"
            "=============================================\n\n"
            "CONTENU REEL DU FICHIER (extrait) :\n"
        )
        q = researcher._extract_search_query(mission)
        assert "REVUE DE CODE" in q
        assert "core/prefrontal.py" in q
        assert "CONTENU REEL" not in q


# --- Niveau 2 : fallback non-scolaire ---

class TestFallbackNonScolaire:

    def test_mission_libre_simple(self, researcher):
        """Mission libre sans balise : premiere ligne non-vide."""
        mission = "Les dernieres avancees en quantification de modeles LLM"
        q = researcher._extract_search_query(mission)
        assert q == "Les dernieres avancees en quantification de modeles LLM"

    def test_prefixe_researcher_retire(self, researcher):
        """Le prefixe d'instruction connu est retire."""
        mission = "Researcher: les transformers a contexte long"
        q = researcher._extract_search_query(mission)
        assert q == "les transformers a contexte long"
        assert not q.lower().startswith("researcher")

    def test_prefixe_scanne_le_web(self, researcher):
        mission = "Scanne le web pour les nouveautes Ollama"
        q = researcher._extract_search_query(mission)
        assert q == "les nouveautes Ollama"

    def test_multiligne_garde_premiere_ligne(self, researcher):
        """Mission multi-lignes sans balise : garde la 1ere ligne non-vide."""
        mission = "\n\nVeille sur le RAG cross-file\nDetails secondaires ignores\nEncore du bruit"
        q = researcher._extract_search_query(mission)
        assert q == "le RAG cross-file"


# --- Niveau 3 : garde-fou terminal + cas limites ---

class TestGardeFou:

    def test_query_too_long_tronquee(self, researcher):
        """Une query > 200 chars (sans balise) est tronquee + tracee."""
        mission = "x" * 500  # une seule ligne de 500 chars
        with patch("Agents.researcher_agent.log_decision") as mock_log:
            q = researcher._extract_search_query(mission)
            assert len(q) <= _MAX_QUERY_CHARS
            mock_log.assert_any_call(
                module="researcher_agent",
                function="_extract_search_query",
                reason="query_too_long",
                context={"original_len": 500, "truncated_to": _MAX_QUERY_CHARS},
            )

    def test_mission_vide_trace_et_retourne_vide(self, researcher):
        with patch("Agents.researcher_agent.log_decision") as mock_log:
            q = researcher._extract_search_query("")
            assert q == ""
            mock_log.assert_any_call(
                module="researcher_agent",
                function="_extract_search_query",
                reason="query_empty",
                context={"cause": "mission_vide"},
            )

    def test_mission_blancs_seulement(self, researcher):
        q = researcher._extract_search_query("   \n  \n ")
        assert q == ""

    def test_regression_incident_0107(self, researcher):
        """Le prompt exact de l'incident : ne doit JAMAIS partir entier au moteur."""
        mission = (
            "[SCHOOL_SLOT: RESEARCH]\nPROTOCOLE_SCOLAIRE\n"
            "Emploi du temps: RESEARCH (1h-3h)\n"
            "Sujet: Evaluation automatique de la qualite du code\n"
            "DERNIER BULLETIN (auto-evaluation): " + ("bla " * 100) + "\n"
            "=============================================\n"
            "SUJET DU JOUR (PRIORITE ABSOLUE) :\n"
            "Evaluation automatique de la qualite du code : metriques, linting, scoring\n"
            "=============================================\n"
        )
        q = researcher._extract_search_query(mission)
        # La balise prioritaire gagne, le bulletin parasite est exclu
        assert q == "Evaluation automatique de la qualite du code : metriques, linting, scoring"
        assert "BULLETIN" not in q
        assert len(q) < _MAX_QUERY_CHARS
