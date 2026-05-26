"""Tests Journal des Echecs Silencieux + sursis de curiosite (atelier 26/05).

Couvre :
- qualify_cause_de_mort : 3e voie hybride (physiologie > textuel)
- _jaccard : similarite des sets
- record_silent_failure : inscription + FIFO cap
- try_grant_amnesty : seuil 60%, anti-spam, TTL, cause emotionnel only
- is_under_amnesty + expiration 5 min
- Persistance JSON (round-trip)
"""
import json
import os
import sys
import time
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.silent_failures import (
    AMNESTY_DURATION_SECONDS,
    CAUSE_EMOTIONAL,
    CAUSE_PHYSIOLOGICAL,
    CAUSE_TECHNICAL,
    ENTRY_TTL_SECONDS,
    MAX_AMNESTIES_PER_ENTRY,
    MAX_ENTRIES,
    SIMILARITY_THRESHOLD,
    SilentFailureEntry,
    SilentFailuresJournal,
)


@pytest.fixture
def journal(tmp_path):
    SilentFailuresJournal.reset_singleton()
    j = SilentFailuresJournal()
    j._file = str(tmp_path / "silent_failures_test.json")
    j._entries = []
    j._active_amnesties = {}
    yield j
    SilentFailuresJournal.reset_singleton()


# ============================================================================
# 1. qualify_cause_de_mort (E6 — voie hybride)
# ============================================================================


def test_cause_emotional_if_bpm_high():
    """BPM > 80 -> emotionnel meme si reason technique."""
    cause = SilentFailuresJournal.qualify_cause_de_mort(
        reason="Distraction: focus sur 'consolidation P16'",
        physiology={"bpm": 95.0, "dopamine": 0.5},
    )
    assert cause == CAUSE_EMOTIONAL


def test_cause_emotional_if_dopamine_low():
    """Dopamine < 0.3 -> emotionnel."""
    cause = SilentFailuresJournal.qualify_cause_de_mort(
        reason="Goal secondaire différé",
        physiology={"bpm": 65.0, "dopamine": 0.25},
    )
    assert cause == CAUSE_EMOTIONAL


def test_cause_technical_when_reason_mentions_focus():
    """Physio calme + raison contient 'focus' -> technique."""
    cause = SilentFailuresJournal.qualify_cause_de_mort(
        reason="Goal secondaire différé: priorité au goal primaire",
        physiology={"bpm": 60.0, "dopamine": 0.5},
    )
    assert cause == CAUSE_TECHNICAL


def test_cause_emotional_when_reason_mentions_fatigue():
    """Physio calme + raison contient 'fatigue' -> emotionnel."""
    cause = SilentFailuresJournal.qualify_cause_de_mort(
        reason="Fatigue cognitive — report",
        physiology={"bpm": 65.0, "dopamine": 0.55},
    )
    assert cause == CAUSE_EMOTIONAL


def test_cause_physiological_fallback():
    """Physio calme + raison neutre -> physiologique."""
    cause = SilentFailuresJournal.qualify_cause_de_mort(
        reason="raison opaque",
        physiology={"bpm": 60.0, "dopamine": 0.5},
    )
    assert cause == CAUSE_PHYSIOLOGICAL


# ============================================================================
# 2. Jaccard
# ============================================================================


def test_jaccard_identical_sets():
    score = SilentFailuresJournal._jaccard(["a", "b", "c"], ["a", "b", "c"])
    assert score == 1.0


def test_jaccard_disjoint_sets():
    score = SilentFailuresJournal._jaccard(["a", "b"], ["c", "d"])
    assert score == 0.0


def test_jaccard_partial_overlap():
    # |A ∩ B| = 2, |A ∪ B| = 4 -> 0.5
    score = SilentFailuresJournal._jaccard(["a", "b", "c"], ["b", "c", "d"])
    assert score == pytest.approx(0.5)


def test_jaccard_empty_both():
    assert SilentFailuresJournal._jaccard([], []) == 0.0


# ============================================================================
# 3. record_silent_failure + FIFO cap
# ============================================================================


def test_record_entry_persists(journal):
    entry = journal.record_silent_failure(
        intent="EXPLORE_LLM_HALLUCINATIONS",
        reason="Distraction: focus sur 'consolidation'",
        context_signature_hash="abc123",
        concepts=["hallucination", "llm", "biais"],
        goals=["consolidation"],
        emotion="impatience",
        physiology={"bpm": 95.0, "dopamine": 0.4},
    )
    assert entry.cause_de_mort == CAUSE_EMOTIONAL  # bpm > 80
    assert len(journal._entries) == 1
    assert journal._entries[0].id == entry.id

    # Round-trip via persistance
    SilentFailuresJournal.reset_singleton()
    j2 = SilentFailuresJournal()
    j2._file = journal._file
    j2._entries = []
    j2._load()
    assert len(j2._entries) == 1
    assert j2._entries[0].intent == "EXPLORE_LLM_HALLUCINATIONS"


def test_fifo_cap_respected(journal):
    """Au-dela de MAX_ENTRIES, les plus vieilles sont evictees."""
    for i in range(MAX_ENTRIES + 50):
        journal.record_silent_failure(
            intent=f"intent_{i}", reason="r",
            context_signature_hash=f"sig{i}",
            concepts=[f"c{i}"], goals=[], emotion="",
            physiology={"bpm": 60.0, "dopamine": 0.5},
            cause_de_mort=CAUSE_PHYSIOLOGICAL,
        )
    assert len(journal._entries) == MAX_ENTRIES
    assert journal._entries[0].intent == f"intent_{50}"


# ============================================================================
# 4. try_grant_amnesty — coeur du sursis
# ============================================================================


def test_amnesty_granted_on_similar_emotional(journal):
    """Hypothese similaire tuee par veto emotionnel -> sursis accorde."""
    # Inscrit une entree emotionnelle
    journal.record_silent_failure(
        intent="EXPLORE_PARADOX_X",
        reason="Distraction: focus sur autre chose",
        context_signature_hash="hash1",
        concepts=["paradoxe", "verite", "logique"],
        goals=["consolidation"],
        emotion="impatience",
        physiology={"bpm": 95.0, "dopamine": 0.4},
        cause_de_mort=CAUSE_EMOTIONAL,
    )

    # Tente l'amnistie avec contexte similaire (3/3 concepts identiques, goal idem, emotion idem)
    reason, entry = journal.try_grant_amnesty(
        intent="EXPLORE_PARADOX_X",
        context_signature_hash="hash2",
        concepts=["paradoxe", "verite", "logique"],
        goals=["consolidation"],
        emotion="impatience",
        cause_de_mort=CAUSE_EMOTIONAL,
    )
    assert reason is not None, "Sursis devrait etre accorde sur contexte identique"
    assert entry is not None
    assert entry.sursis_granted_count == 1
    # Sursis actif
    assert journal.is_under_amnesty("EXPLORE_PARADOX_X")


def test_amnesty_refused_when_cause_technical(journal):
    """Cause technique (vraie contrainte de focus) -> jamais de sursis."""
    journal.record_silent_failure(
        intent="X", reason="r", context_signature_hash="h",
        concepts=["a", "b"], goals=["g"], emotion="serenite",
        physiology={"bpm": 60.0, "dopamine": 0.5},
        cause_de_mort=CAUSE_TECHNICAL,
    )
    reason, _ = journal.try_grant_amnesty(
        intent="X", context_signature_hash="h",
        concepts=["a", "b"], goals=["g"], emotion="serenite",
        cause_de_mort=CAUSE_TECHNICAL,
    )
    assert reason is None


def test_amnesty_refused_below_threshold(journal):
    """Similarite < 60% -> pas de sursis."""
    journal.record_silent_failure(
        intent="X", reason="r", context_signature_hash="h",
        concepts=["a", "b", "c", "d", "e"], goals=["g1"], emotion="impatience",
        physiology={"bpm": 95.0, "dopamine": 0.4},
        cause_de_mort=CAUSE_EMOTIONAL,
    )
    # Contexte tres different : 0 concept en commun, autre goal, autre emotion
    reason, _ = journal.try_grant_amnesty(
        intent="X", context_signature_hash="h2",
        concepts=["z", "y", "x", "w", "v"], goals=["g2"], emotion="serenite",
        cause_de_mort=CAUSE_EMOTIONAL,
    )
    assert reason is None


def test_amnesty_antispam_max_granted(journal):
    """Apres MAX_AMNESTIES_PER_ENTRY sursis, l'entree est saturee."""
    journal.record_silent_failure(
        intent="X", reason="r", context_signature_hash="h",
        concepts=["a", "b", "c"], goals=["g"], emotion="impatience",
        physiology={"bpm": 95.0, "dopamine": 0.4},
        cause_de_mort=CAUSE_EMOTIONAL,
    )
    # Decremente _active_amnesties entre chaque pour permettre relances
    for i in range(MAX_AMNESTIES_PER_ENTRY):
        journal._active_amnesties.clear()
        reason, _ = journal.try_grant_amnesty(
            intent=f"X_{i}", context_signature_hash="h",
            concepts=["a", "b", "c"], goals=["g"], emotion="impatience",
            cause_de_mort=CAUSE_EMOTIONAL,
        )
        assert reason is not None, f"sursis {i} devrait passer"

    # Le suivant doit echouer
    journal._active_amnesties.clear()
    reason, _ = journal.try_grant_amnesty(
        intent="X_FINAL", context_signature_hash="h",
        concepts=["a", "b", "c"], goals=["g"], emotion="impatience",
        cause_de_mort=CAUSE_EMOTIONAL,
    )
    assert reason is None, "antispam doit bloquer apres MAX_AMNESTIES_PER_ENTRY"


def test_amnesty_dry_run_does_not_persist(journal):
    """Dry-run renvoie reason mais ne pose pas le sursis ni n'incremente."""
    journal.record_silent_failure(
        intent="X", reason="r", context_signature_hash="h",
        concepts=["a", "b", "c"], goals=["g"], emotion="impatience",
        physiology={"bpm": 95.0, "dopamine": 0.4},
        cause_de_mort=CAUSE_EMOTIONAL,
    )
    reason, entry = journal.try_grant_amnesty(
        intent="Y", context_signature_hash="h",
        concepts=["a", "b", "c"], goals=["g"], emotion="impatience",
        cause_de_mort=CAUSE_EMOTIONAL,
        dry_run=True,
    )
    assert reason is not None  # aurait accorde
    assert entry.sursis_granted_count == 0  # pas incremente
    assert not journal.is_under_amnesty("Y")  # pas pose


# ============================================================================
# 5. is_under_amnesty + expiration
# ============================================================================


def test_amnesty_expires_after_duration(journal):
    journal.record_silent_failure(
        intent="X", reason="r", context_signature_hash="h",
        concepts=["a", "b"], goals=["g"], emotion="impatience",
        physiology={"bpm": 95.0, "dopamine": 0.4},
        cause_de_mort=CAUSE_EMOTIONAL,
    )
    reason, _ = journal.try_grant_amnesty(
        intent="Y", context_signature_hash="h",
        concepts=["a", "b"], goals=["g"], emotion="impatience",
        cause_de_mort=CAUSE_EMOTIONAL,
    )
    assert reason is not None
    assert journal.is_under_amnesty("Y")

    # Force expiration en manipulant le timestamp
    journal._active_amnesties["Y"] = time.time() - 1.0
    assert not journal.is_under_amnesty("Y")
    # Et l'entree a ete nettoyee
    assert "Y" not in journal._active_amnesties


def test_amnesty_ttl_expired_entry_ignored(journal):
    """Entree au-dela du TTL n'est plus candidate au matching."""
    entry = journal.record_silent_failure(
        intent="X", reason="r", context_signature_hash="h",
        concepts=["a", "b"], goals=["g"], emotion="impatience",
        physiology={"bpm": 95.0, "dopamine": 0.4},
        cause_de_mort=CAUSE_EMOTIONAL,
    )
    # Force le killed_at dans le tres lointain passe
    entry.killed_at = time.time() - ENTRY_TTL_SECONDS - 100

    reason, _ = journal.try_grant_amnesty(
        intent="Y", context_signature_hash="h",
        concepts=["a", "b"], goals=["g"], emotion="impatience",
        cause_de_mort=CAUSE_EMOTIONAL,
    )
    assert reason is None


# ============================================================================
# 6. Stats
# ============================================================================


def test_get_stats(journal):
    journal.record_silent_failure(
        intent="X1", reason="r", context_signature_hash="h",
        concepts=["a"], goals=[], emotion="",
        physiology={"bpm": 95.0, "dopamine": 0.5},
        cause_de_mort=CAUSE_EMOTIONAL,
    )
    journal.record_silent_failure(
        intent="X2", reason="r", context_signature_hash="h",
        concepts=["a"], goals=[], emotion="",
        physiology={"bpm": 60.0, "dopamine": 0.5},
        cause_de_mort=CAUSE_TECHNICAL,
    )
    stats = journal.get_stats()
    assert stats["total_entries"] == 2
    assert stats["by_cause"][CAUSE_EMOTIONAL] == 1
    assert stats["by_cause"][CAUSE_TECHNICAL] == 1
