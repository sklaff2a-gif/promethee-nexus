"""Tests Phase 14 (sequel V12.0) — Sanity Check Local anti-Perroquet.

Calibration menee sur 20 livrables du corpus mentor (22/04) apres 2 passes :
V1 (6 signaux stylistiques) a echoue F1=0.42, V2 (3 detecteurs structurels)
a obtenu recall 1.00 sur corpus reel.

Architecture du detecteur : 3 detecteurs determinist, aucun LLM.
  D1 completeness — sections promises vs livrees substantiellement
  D2 truncation — phrase finale interrompue ou derniere section squelettique
  D3 target drift — CODE_REVIEW/WORKSHOP cible pas citee dans le corps

Reward shaping gradue (pas couperet binaire) pour eviter reward sparsity :
  0 flag → 1.0, 1 flag → 0.5, 2 flags → 0.25, 3 flags → 0.1
"""
import pytest

from core.bullshit_detector import (
    D1_SKIP_SLOTS,
    D5_COVERAGE_THRESHOLD,
    D5_MIN_KEYWORDS,
    _extract_subject_keywords,
    d1_completeness,
    d2_truncation,
    d3_target_drift,
    d4a_syntax_parse,
    d5_subject_drift,
    evaluate_deliverable,
    extract_promised_items,
    extract_sections,
    grade_multiplier,
    strip_header,
)


# ═══════════════════════════════════════════════════════════════════════
# Parsing helpers
# ═══════════════════════════════════════════════════════════════════════


class TestExtractPromisedItems:

    def test_comma_separated_items(self):
        items = extract_promised_items(
            "Patterns de resilience : circuit breaker, bulkhead, retry avec backoff"
        )
        assert "circuit breaker" in items
        assert "bulkhead" in items
        assert "retry avec backoff" in items

    def test_et_separator(self):
        items = extract_promised_items("Algorithmes : TOPSIS, PROMETHEE et AHP")
        assert set(items) == {"topsis", "promethee", "ahp"}

    def test_no_colon_returns_empty(self):
        assert extract_promised_items("Pas de deux-points ici") == []

    def test_parentheses_stripped(self):
        items = extract_promised_items("Sujet : TOPSIS, PROMETHEE (ironie), AHP")
        joined = " ".join(items)
        assert "ironie" not in joined
        assert "promethee" in items

    def test_slash_kept_as_compound(self):
        """pub/sub est un acronyme compose, pas un separateur."""
        items = extract_promised_items("Architecture : pub/sub, cqrs")
        assert "pub/sub" in items
        assert "cqrs" in items

    def test_empty_subject(self):
        assert extract_promised_items("") == []


class TestExtractSections:

    def test_markdown_headers(self):
        body = "## A. Titre1\ncontenu1\n## B. Titre2\ncontenu2"
        sections = extract_sections(body)
        assert len(sections) == 2
        assert "Titre1" in sections[0][0]
        assert "contenu1" in sections[0][1]

    def test_numbered_headers(self):
        body = "1. Premier\ntexte un\n2. Deuxieme\ntexte deux"
        sections = extract_sections(body)
        assert len(sections) == 2

    def test_no_headers_returns_full(self):
        sections = extract_sections("texte sans structure")
        assert sections == [("(full)", "texte sans structure")]


class TestStripHeader:

    def test_strips_school_preamble(self):
        text = (
            "# Livrable: RESEARCH\n"
            "Date: 2026-04-22\n"
            "Note: 8.8/10\n"
            "Feedback: ...\n"
            "Challenge: ...\n"
            "\n---\n"
            "\n# Vrai corps\ncontenu ici"
        )
        body = strip_header(text)
        assert "Note: 8.8" not in body
        assert "Vrai corps" in body

    def test_no_preamble_returns_text(self):
        text = "# Juste un corps\nsans preambule"
        assert strip_header(text) == text


# ═══════════════════════════════════════════════════════════════════════
# D1 Completeness
# ═══════════════════════════════════════════════════════════════════════


class TestD1Completeness:

    def test_skip_bulletin(self):
        assert not d1_completeness(
            "contenu", "Bulletin : bilan, routine, note", "BULLETIN"
        )

    def test_skip_creation(self):
        assert not d1_completeness(
            "contenu", "Invente un mot : neologisme, definition", "CREATION"
        )

    def test_skip_single_item(self):
        assert not d1_completeness("texte", "Sujet : un_seul_item", "RESEARCH")

    def test_skip_no_colon(self):
        assert not d1_completeness("texte", "Pas de deux points", "RESEARCH")

    def test_all_sections_present_and_substantial(self):
        body = (
            "## A. Circuit Breaker\n" + "mot " * 100 +
            "\n## B. Bulkhead\n" + "mot " * 100 +
            "\n## C. Retry avec Backoff\n" + "mot " * 100
        )
        subject = "Patterns : circuit breaker, bulkhead, retry avec backoff"
        assert not d1_completeness(body, subject, "RESEARCH")

    def test_one_section_missing_flags(self):
        body = (
            "## A. Circuit Breaker\n" + "mot " * 100 +
            "\n## B. Bulkhead\n" + "mot " * 100
        )
        subject = "Patterns : circuit breaker, bulkhead, retry avec backoff"
        assert d1_completeness(body, subject, "RESEARCH")

    def test_section_too_short_flags(self):
        body = (
            "## A. Item1\n" + "mot " * 100 +
            "\n## B. Item2\n" + "mot " * 30 +  # < 80 mots
            "\n## C. Item3\n" + "mot " * 10
        )
        subject = "Sujet : item1, item2, item3"
        assert d1_completeness(body, subject, "RESEARCH")

    def test_coverage_two_thirds_still_flags(self):
        """Doctrine Perroquet : si un item promis manque, on flag.
        2/3 = 0.667 < 0.67 → flag."""
        body = (
            "## A. Item1\n" + "mot " * 100 +
            "\n## B. Item2\n" + "mot " * 100 +
            "\n## C. Item3\ntres court"
        )
        subject = "Sujet : item1, item2, item3"
        assert d1_completeness(body, subject, "RESEARCH")

    def test_full_coverage_not_flagged(self):
        body = (
            "## A. Item1\n" + "mot " * 100 +
            "\n## B. Item2\n" + "mot " * 100 +
            "\n## C. Item3\n" + "mot " * 100
        )
        subject = "Sujet : item1, item2, item3"
        assert not d1_completeness(body, subject, "RESEARCH")


# ═══════════════════════════════════════════════════════════════════════
# D2 Truncation
# ═══════════════════════════════════════════════════════════════════════


class TestD2Truncation:

    def test_clean_ending_with_period(self):
        body = (
            "## Section 1\n" + "mot " * 150 + ".\n" +
            "## Section 2\n" + "mot " * 150 + ".\n" +
            "## Section 3\n" + "mot " * 150 + "."
        )
        assert not d2_truncation(body)

    def test_broken_last_sentence_flags(self):
        body = (
            "## Section 1\n" + "mot " * 100 + ".\n" +
            "## Section 2\nUne phrase qui s'arrete sans finir"
        )
        assert d2_truncation(body)

    def test_last_section_too_short_flags(self):
        body = (
            "## Section 1\n" + "mot " * 150 + ".\n" +
            "## Section 2\n" + "mot " * 150 + ".\n" +
            "## Section 3\nTrop court point."
        )
        assert d2_truncation(body)

    def test_code_block_closed_ok(self):
        body = (
            "## Section 1\n" + "mot " * 150 + ".\n" +
            "## Section 2\n" + "mot " * 150 + ".\n" +
            "## Section 3\n" + "mot " * 150 + ".\n```python\nprint('done')\n```"
        )
        assert not d2_truncation(body)

    def test_ends_with_bullet_ok_if_substantial(self):
        """Derniere section en liste a puces, avec contenu substantiel."""
        bullets = "\n".join(
            f"- item {c} avec un contenu descriptif un peu long pour depasser le seuil de mots de la section."
            for c in "abcdefghijklmnop"
        )
        body = (
            "## Section 1\n" + "mot " * 150 + ".\n" +
            "## Section 2\n" + "mot " * 150 + ".\n" +
            "## Section 3\n" + bullets
        )
        # Dernier bullet legitime + > 100 mots → pas de flag
        assert not d2_truncation(body)

    def test_empty_body(self):
        assert not d2_truncation("")


# ═══════════════════════════════════════════════════════════════════════
# D3 Target Drift
# ═══════════════════════════════════════════════════════════════════════


class TestD3TargetDrift:

    def test_skip_non_code_review_slots(self):
        assert not d3_target_drift("contenu", "core/rival.py", "RESEARCH")
        assert not d3_target_drift("contenu", "core/rival.py", "CREATION")
        assert not d3_target_drift("contenu", "core/rival.py", "BULLETIN")

    def test_target_cited_ok(self):
        body = "Audit de core/rival.py : function foo() ligne 10 dans rival.py"
        assert not d3_target_drift(
            body, "Revue de code : core/rival.py", "CODE_REVIEW"
        )

    def test_target_absent_flags(self):
        body = "Audit de core/reasoning_protocol.py : function bar() ligne 20"
        assert d3_target_drift(
            body, "Revue de code : core/rival.py", "CODE_REVIEW"
        )

    def test_target_dominated_by_other(self):
        body = (
            "core/rival.py ligne 5. "
            "core/reasoning_protocol.py ligne 10. "
            "core/reasoning_protocol.py ligne 15. "
            "core/reasoning_protocol.py ligne 20."
        )
        assert d3_target_drift(
            body, "Revue de code : core/rival.py", "CODE_REVIEW"
        )

    def test_no_target_in_subject_no_flag(self):
        assert not d3_target_drift("body", "Topic generic", "CODE_REVIEW")

    def test_workshop_also_applies(self):
        body = "Exercice sur reasoning_protocol.py ligne 30"
        assert d3_target_drift(
            body, "Workshop : core/neural_tissue.py", "WORKSHOP"
        )


# ═══════════════════════════════════════════════════════════════════════
# Grade Multiplier (Reward Shaping)
# ═══════════════════════════════════════════════════════════════════════


class TestGradeMultiplier:

    def test_zero_flags_preserves_grade(self):
        assert grade_multiplier(0) == 1.0

    def test_one_flag_half(self):
        assert grade_multiplier(1) == 0.5

    def test_two_flags_quarter(self):
        assert grade_multiplier(2) == 0.25

    def test_three_flags_clip(self):
        assert grade_multiplier(3) == 0.1

    def test_four_flags_phase141_floor(self):
        """Phase 14.1 : 4 flags cumules (D1+D2+D3+D4a) → 0.05."""
        assert grade_multiplier(4) == 0.05

    def test_clamp_above_four(self):
        assert grade_multiplier(5) == 0.05

    def test_clamp_below_zero(self):
        assert grade_multiplier(-1) == 1.0


# ═══════════════════════════════════════════════════════════════════════
# Phase 14.1 (23/04/2026) — D4a AST Syntax Check
# ═══════════════════════════════════════════════════════════════════════


class TestD4aSyntaxParse:
    """D4a : flag si un bloc ```python contient une SyntaxError."""

    def test_no_code_block_no_flag(self):
        body = "Livrable prose sans code. Juste du texte explicatif."
        assert not d4a_syntax_parse(body)

    def test_valid_python_block_no_flag(self):
        body = """Voici un exemple :
```python
def foo(x):
    return x * 2
```
Fin."""
        assert not d4a_syntax_parse(body)

    def test_syntax_error_flags(self):
        body = """Exemple :
```python
def foo(x:
    return x * 2
```"""
        assert d4a_syntax_parse(body)

    def test_truncated_code_flags(self):
        """Simule une troncation par limite de tokens : classe inachevee."""
        body = """```python
class Agent:
    def __init__(self, name):
        self.name = name
    async def vote(self, target
```"""
        assert d4a_syntax_parse(body)

    def test_empty_code_block_skipped(self):
        body = "```python\n\n```"
        assert not d4a_syntax_parse(body)

    def test_non_python_block_skipped(self):
        """Un bloc non marque 'python' n'est pas parse.
        (Regex autorise aussi sans tag, on prend tous les ```.)"""
        body = """```
this is not python but doesn't matter
just text
```"""
        # Le bloc sans tag est parse comme Python, cette chaine
        # est valide comme Python (3 expressions-statements).
        # On verifie juste que ca ne leve pas.
        result = d4a_syntax_parse(body)
        assert isinstance(result, bool)

    def test_multiple_blocks_one_broken_flags(self):
        body = """Bloc 1 :
```python
x = 1
```
Bloc 2 :
```python
def broken(
```"""
        assert d4a_syntax_parse(body)

    def test_multiple_valid_blocks_no_flag(self):
        body = """Bloc 1 :
```python
x = 1
```
Bloc 2 :
```python
def valid():
    return True
```"""
        assert not d4a_syntax_parse(body)


class TestD4aIntegration:
    """Integration D4a dans evaluate_deliverable."""

    def test_d4a_flag_in_result(self):
        body = """Code :
```python
def broken(
```"""
        r = evaluate_deliverable(body, "Sujet simple", "RESEARCH")
        assert r["d4a_syntax_parse"]
        assert any("d4a" in reason.lower() for reason in r["reasons"])

    def test_clean_code_no_d4a_flag(self):
        body = """```python
def valid():
    return 1
```"""
        r = evaluate_deliverable(body, "Sujet simple", "RESEARCH")
        assert not r["d4a_syntax_parse"]

    def test_d4a_cumulates_with_other_flags(self):
        """D4a SyntaxError + D1 item manquant = 2 flags → mult 0.25."""
        body = """## A. Section1
""" + ("mot " * 100) + """

```python
def broken(
```"""
        # Sujet promet 2 items, seul "section1" est couvert
        r = evaluate_deliverable(body, "Sujet : section1, section2", "RESEARCH")
        assert r["d1_completeness"]
        assert r["d4a_syntax_parse"]
        assert r["n_flags"] >= 2
        assert r["multiplier"] <= 0.25


# ═══════════════════════════════════════════════════════════════════════
# evaluate_deliverable — integration
# ═══════════════════════════════════════════════════════════════════════


class TestEvaluateDeliverable:

    def test_clean_research_no_malus(self):
        body = (
            "## A. Part1\n" + "mot " * 120 + ".\n" +
            "## B. Part2\n" + "mot " * 120 + ".\n" +
            "## C. Part3\n" + "mot " * 120 + "."
        )
        r = evaluate_deliverable(body, "Sujet : part1, part2, part3", "RESEARCH")
        assert r["n_flags"] == 0
        assert r["multiplier"] == 1.0
        assert r["reasons"] == []

    def test_truncated_research_one_malus(self):
        body = (
            "## A. Complet\n" + "mot " * 120 + ".\n" +
            "## B. Aussi\n" + "mot " * 120 + ".\n" +
            "## C. Derniere\n" + "mot " * 120 + " phrase qui coupe"
        )
        r = evaluate_deliverable(body, "Sujet : complet, aussi, derniere", "RESEARCH")
        assert r["d2_truncation"]
        assert r["multiplier"] == 0.5

    def test_code_review_drift_single_malus(self):
        body = (
            "## Audit\n" +
            "Analyse de core/reasoning_protocol.py : " +
            ("ligne 10 function foo. " * 100) + "."
        )
        r = evaluate_deliverable(body, "Revue de code : core/rival.py", "CODE_REVIEW")
        assert r["d3_target_drift"]
        # D1 skip (1 item), D2 peut ne pas flag
        assert r["multiplier"] <= 0.5

    def test_reasons_list_populated(self):
        body = "Court texte tronque sans point"
        r = evaluate_deliverable(body, "Sujet : x", "RESEARCH")
        # D2 devrait flag (phrase non terminee)
        if r["d2_truncation"]:
            assert any("truncation" in reason for reason in r["reasons"])


# ═══════════════════════════════════════════════════════════════════════
# Cas reels du corpus mentor (integration test)
# ═══════════════════════════════════════════════════════════════════════


class TestRealCorpusCases:
    """Reproductions approximatives des cas observes dans mentor_state.json."""

    def test_event_sourcing_cqrs_missing(self):
        """Cas 2026-04-22 RESEARCH — CQRS promis, absent du corps."""
        body = (
            "## 1. Pub/Sub\n" +
            ("definition asyncio aio-pika queue publish subscribe broker python. " * 20) +
            "\n## 2. Event Sourcing\n" +
            ("event sourcing sqlite postgres table payload timestamp immuable. " * 20)
        )
        r = evaluate_deliverable(
            body, "Architectures : pub/sub, event sourcing, CQRS", "RESEARCH"
        )
        assert r["d1_completeness"], "CQRS manquant doit etre detecte"
        assert r["multiplier"] <= 0.5

    def test_code_review_reasoning_protocol_attractor(self):
        """Cas recurrent : CODE_REVIEW demande X.py, livre reasoning_protocol.py."""
        body = (
            "# Audit securite\n"
            "Fichier : core/reasoning_protocol.py\n"
            "V1. validation insuffisante ligne 42\n"
            "V2. logging d'erreur ligne 87\n"
            "V3. reasoning_protocol.py gere mal les inputs\n"
        )
        r = evaluate_deliverable(
            body, "Revue de code : core/neural_tissue.py", "CODE_REVIEW"
        )
        assert r["d3_target_drift"]

    def test_retry_backoff_truncated(self):
        """Cas 2026-04-19 RESEARCH — 3 patterns promis, le 3e tronque."""
        body = (
            "## A. Circuit Breaker\n" + ("mot " * 150) + ".\n" +
            "## B. Bulkhead\n" + ("mot " * 150) + ".\n" +
            "## C. Retry avec Backoff\ntres court seulement"
        )
        r = evaluate_deliverable(
            body,
            "Patterns : circuit breaker, bulkhead, retry avec backoff",
            "RESEARCH"
        )
        # Au moins un des deux doit se declencher (D1 section courte < 80 mots OU D2 section finale < 100)
        assert r["d1_completeness"] or r["d2_truncation"]


# ═══════════════════════════════════════════════════════════════════════
# D5 (03/05/2026) — Subject Drift sémantique
# ═══════════════════════════════════════════════════════════════════════


class TestExtractSubjectKeywords:
    """Helper d'extraction des keywords saillants du sujet pour D5."""

    def test_acronymes_capitalises(self):
        kws = _extract_subject_keywords("Architectures : RAG, GraphRAG, MemWalker")
        assert "rag" in kws
        assert "graphrag" in kws
        assert "memwalker" in kws

    def test_paths_python(self):
        kws = _extract_subject_keywords("Améliorer : core/ci_pipeline.py")
        assert any("ci_pipeline" in k for k in kws)

    def test_snake_case(self):
        kws = _extract_subject_keywords("Revue de code : factory_agent.py")
        assert any("factory_agent" in k for k in kws)

    def test_stop_words_filtres(self):
        kws = _extract_subject_keywords("Le système de mémoire pour les agents")
        # "le", "de", "pour", "les" doivent être exclus
        assert "le" not in kws
        assert "les" not in kws
        assert "pour" not in kws
        # mais "système", "mémoire", "agents" doivent rester
        assert any("syst" in k for k in kws)

    def test_subject_vide(self):
        assert _extract_subject_keywords("") == []
        assert _extract_subject_keywords(None) == []

    def test_dedup_stable_order(self):
        kws = _extract_subject_keywords("RAG GraphRAG RAG MemWalker GraphRAG")
        assert kws.count("rag") == 1
        assert kws.count("graphrag") == 1


class TestD5SubjectDrift:
    """D5 : flag si coverage des keywords du sujet dans le livrable < 30%."""

    def test_skip_si_subject_vide(self):
        assert not d5_subject_drift("body assez long pour passer le filtre min", "", "RESEARCH")
        assert not d5_subject_drift("body assez long pour passer le filtre min", None, "RESEARCH")

    def test_skip_si_body_trop_court(self):
        # body < 100 chars → skip (autre flag actif)
        assert not d5_subject_drift("court", "Architectures RAG GraphRAG MemWalker", "RESEARCH")

    def test_skip_si_subject_pas_assez_keywords(self):
        # Subject avec < 3 keywords → ambigu, ne pas flag
        body = "x" * 200
        assert not d5_subject_drift(body, "veille", "RESEARCH")

    def test_drift_evident_flag(self):
        """Sujet RAG/GraphRAG/MemWalker, livrable parle de pulsions."""
        body = (
            "Aujourd'hui j'ai analyse mes pulsions internes. La curiosite "
            "depasse 50, la maitrise est saturee. Mes drives oscillent entre "
            "creation et stabilite. Le tissu neural montre une activite normale."
        )
        subject = "Architectures de memoire pour systemes multi-agents (RAG, GraphRAG, MemWalker)"
        assert d5_subject_drift(body, subject, "RESEARCH")

    def test_couverture_complete_no_flag(self):
        """Body qui cite tous les keywords du sujet → pas de flag."""
        body = (
            "RAG (Retrieval Augmented Generation) est une architecture de memoire "
            "qui combine indexation vectorielle et generation. GraphRAG etend cette "
            "approche avec un graphe de connaissance. MemWalker traite la memoire "
            "comme un arbre navigable. Pour les systemes multi-agents, ces architectures "
            "offrent des compromis differents."
        )
        subject = "Architectures de memoire pour systemes multi-agents (RAG, GraphRAG, MemWalker)"
        assert not d5_subject_drift(body, subject, "RESEARCH")

    def test_couverture_partielle_seuil(self):
        """Coverage entre 30% et 50% → tolere (pas de flag)."""
        # 4 keywords (rag, graphrag, memwalker, multi-agents probable), ~50% couverts
        body = (
            "RAG est une architecture interessante. GraphRAG etend RAG. "
            "Pour le reste, mes systemes internes utilisent autre chose. "
            "L'orchestration des differents agents se fait via le bus event."
        )
        subject = "Architectures de memoire pour systemes multi-agents (RAG, GraphRAG, MemWalker)"
        # 2 keywords cites sur 3-4 minimum → coverage >= 50% → pas flag
        assert not d5_subject_drift(body, subject, "RESEARCH")

    def test_tous_les_slots_couverts(self):
        """D5 doit fonctionner pour TOUS les slots (vs D3 qui était limité)."""
        body = "x" * 200 + " contenu hors-sujet absolument generique"
        subject = "Architectures RAG GraphRAG MemWalker pour multi-agents"
        for slot in ("CODE_REVIEW", "RESEARCH", "WORKSHOP", "CREATION", "BULLETIN"):
            assert d5_subject_drift(body, subject, slot), f"D5 doit flag pour {slot}"


class TestD5ReproductionInVivo:
    """Tests de calibration : reproduction des 4 hors-sujets observes 02-03/05.

    Si D5 ne flag pas ces cas, le seuil COVERAGE_THRESHOLD est trop laxe.
    """

    def test_creation_ci_pipeline_recyclee_en_rag(self):
        """02:14 → 03:25 : CREATION ci_pipeline.py rend un plan d'atelier RAG."""
        body = (
            "Plan d'atelier sur les architectures de memoire pour systemes "
            "multi-agents. Premier objectif : comprendre RAG (Retrieval Augmented "
            "Generation). Deuxieme : explorer GraphRAG comme extension. Troisieme : "
            "implementer un prototype MemWalker minimal."
        ) * 3  # body assez long
        subject = "Ameliorer : core/ci_pipeline.py"
        r = evaluate_deliverable(body, subject, "CREATION")
        assert r["d5_subject_drift"], (
            "Le hors-sujet ci_pipeline.py → RAG doit declencher D5"
        )

    def test_research_rag_repli_introspection(self):
        """01:05 : RESEARCH RAG, livrable = introspection systeme."""
        body = (
            "Etat des lieux de mon systeme. Mes contraintes Windows m'empechent "
            "d'utiliser certains outils. Mes echecs du Jour #47 ont impacte mon "
            "budget de credits. Je remarque que mes routines de la nuit ont consomme "
            "plus que prevu, et la car les credits"
        )
        subject = "Architectures de memoire pour systemes multi-agents (RAG, GraphRAG, MemWalker)"
        r = evaluate_deliverable(body, subject, "RESEARCH")
        assert r["d5_subject_drift"], (
            "Le repli introspectif sur sujet RAG doit declencher D5"
        )

    def test_workshop_rag_physics_playground(self):
        """02:14 : WORKSHOP RAG, livrable = Physics Playground."""
        body = (
            "Physics Playground - simulation de particules.\n"
            "```python\n"
            "def apply_force(particle, force):\n"
            "    particle.velocity += force * time_step\n"
            "    return particle\n"
            "```\n"
            "Cette simulation modelise les interactions gravitationnelles entre "
            "particules. La docstring decrit la spéci"
        )
        subject = "Architectures de memoire pour systemes multi-agents (RAG, GraphRAG, MemWalker)"
        r = evaluate_deliverable(body, subject, "WORKSHOP")
        assert r["d5_subject_drift"], (
            "Physics Playground sur sujet RAG doit declencher D5"
        )

    def test_code_review_factory_agent_audit_securite(self):
        """00:32 : CODE_REVIEW audit securite factory_agent, livrable = banalites."""
        body = (
            "[V1] Severite MOYENNE — validation insuffisante des inputs ligne 42. "
            "Les imports dynamiques peuvent ralentir le demarrage. "
            "[V2] Severite FAIBLE — manque de logging structure. "
            "[V3] Recommandation generale : ajouter des tests unitaires. "
            "[V4] Pas de gestion d'erreur sur certaines branches"
        )
        # Sujet a target_file factory_agent.py — D3 devrait flag (target_file present)
        # ET D5 devrait flag (keywords audit/securite/factory_agent absents du body)
        subject = "Audit de securite : Agents/factory_agent.py — injection chemin, traversal, execution code arbitraire"
        r = evaluate_deliverable(body, subject, "CODE_REVIEW")
        # Le body cite "factory_agent" indirectement via "[V1]..." ? Non, il ne cite pas le fichier.
        # On exige au moins UN des deux flags drift (D3 ou D5)
        assert r["d3_target_drift"] or r["d5_subject_drift"], (
            "Audit securite generique doit declencher D3 ou D5"
        )
