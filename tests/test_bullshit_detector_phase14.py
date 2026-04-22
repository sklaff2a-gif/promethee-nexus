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
    d1_completeness,
    d2_truncation,
    d3_target_drift,
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

    def test_clamp_above_three(self):
        assert grade_multiplier(5) == 0.1

    def test_clamp_below_zero(self):
        assert grade_multiplier(-1) == 1.0


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
