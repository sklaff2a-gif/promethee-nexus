"""Tests prediction_memory (chantier 26/05 — repare l'asymetrie hippocampe).

Couvre :
- context_signature : tolerance micro-variations + distinction contextes differents
- record_success : creation, reinforcement, cristallisation a 3 succes
- record_failure : decristallisation a confidence<0.2
- apply_decay_all : decay temporel anti-superstition
- suggest_prediction : bypass deliberation pour patterns cristallises
- persistance JSON sans BOM
- cap MAX_PREDICTION_STRATEGIES FIFO non-cristallisees
"""
import json
import os
import sys
import time
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.prediction_memory import (
    PredictionMemory,
    PredictionStrategy,
    context_signature_for_prediction,
    PREDICTION_CRYSTALLIZE_THRESHOLD,
    PREDICTION_DECRYSTALLIZE_THRESHOLD,
    PREDICTION_DECAY_PER_DAY,
    PREDICTION_INITIAL_CONFIDENCE,
    PREDICTION_CONFIDENCE_BOOST,
    MAX_PREDICTION_STRATEGIES,
)


@pytest.fixture
def mem(tmp_path):
    """Memoire fraiche avec fichier tmp."""
    PredictionMemory.reset_singleton()
    m = PredictionMemory()
    m.set_file_path(str(tmp_path / "prediction_memory.json"))
    yield m
    PredictionMemory.reset_singleton()


# ============================================================================
# context_signature_for_prediction
# ============================================================================


def test_signature_empty_returns_constant():
    sig = context_signature_for_prediction()
    assert sig == "empty_context"


def test_signature_concepts_only():
    sig = context_signature_for_prediction(concepts=["alpha", "beta"])
    assert sig != "empty_context"
    assert len(sig) == 16  # md5 truncated


def test_signature_order_independent():
    """L'ordre des concepts ne doit pas changer la signature."""
    sig1 = context_signature_for_prediction(concepts=["alpha", "beta", "gamma"])
    sig2 = context_signature_for_prediction(concepts=["gamma", "alpha", "beta"])
    assert sig1 == sig2


def test_signature_case_insensitive():
    sig1 = context_signature_for_prediction(emotion="CURIOSITE")
    sig2 = context_signature_for_prediction(emotion="curiosite")
    assert sig1 == sig2


def test_signature_duplicates_collapsed():
    """set() doit dedup les doublons."""
    sig1 = context_signature_for_prediction(concepts=["alpha", "beta", "alpha"])
    sig2 = context_signature_for_prediction(concepts=["alpha", "beta"])
    assert sig1 == sig2


def test_signature_top_concepts_limit():
    """Au-dela de top_concepts, les concepts mineurs sont ignores."""
    big = [f"concept_{i}" for i in range(20)]
    sig1 = context_signature_for_prediction(concepts=big, top_concepts=5)
    # Garder seulement les 5 premiers
    sig2 = context_signature_for_prediction(concepts=big[:5], top_concepts=5)
    assert sig1 == sig2


def test_signature_different_concepts_distinct():
    """Concepts radicalement differents = hash different."""
    sig1 = context_signature_for_prediction(concepts=["math", "logique"])
    sig2 = context_signature_for_prediction(concepts=["cuisine", "recette"])
    assert sig1 != sig2


def test_signature_3_dimensions_orchestrated():
    """Les 3 dimensions contribuent distinctement."""
    sig_concepts_only = context_signature_for_prediction(concepts=["alpha"])
    sig_with_goal = context_signature_for_prediction(concepts=["alpha"], goals=["beta"])
    sig_with_emotion = context_signature_for_prediction(concepts=["alpha"], emotion="curieux")
    sig_all = context_signature_for_prediction(
        concepts=["alpha"], goals=["beta"], emotion="curieux"
    )
    # 4 signatures distinctes pour 4 contextes distincts
    assert len({sig_concepts_only, sig_with_goal, sig_with_emotion, sig_all}) == 4


def test_signature_truncation_at_12_chars():
    """Suffixes au-dela du 12eme char sont ignores (norm[:12])."""
    # 12 chars communs, puis differences au-dela -> meme bucket
    sig1 = context_signature_for_prediction(concepts=["consolidatio_strategie_v1"])
    sig2 = context_signature_for_prediction(concepts=["consolidatio_strategie_v2"])
    # Les 12 premiers chars 'consolidatio' sont identiques apres norm[:12]
    assert sig1 == sig2

    # Difference DANS les 12 premiers chars -> hash different
    sig3 = context_signature_for_prediction(concepts=["consolidatio"])
    sig4 = context_signature_for_prediction(concepts=["consolidatxx"])
    assert sig3 != sig4


# ============================================================================
# record_success — creation + reinforcement + cristallisation
# ============================================================================


def test_record_success_creates_new_strategy(mem):
    result = mem.record_success("sig_001", "predicted_pattern_A")
    assert result["status"] == "created"
    assert result["successes"] == 1
    assert result["crystallized"] is False
    assert len(mem.list_strategies()) == 1


def test_record_success_reinforces_existing(mem):
    mem.record_success("sig_001", "pattern_A")
    result = mem.record_success("sig_001", "pattern_A")
    assert result["status"] == "reinforced"
    assert result["successes"] == 2
    # Confidence boost
    assert result["confidence"] > PREDICTION_INITIAL_CONFIDENCE


def test_record_success_crystallizes_at_threshold(mem):
    """Cristallisation a PREDICTION_CRYSTALLIZE_THRESHOLD = 3 succes."""
    for i in range(PREDICTION_CRYSTALLIZE_THRESHOLD - 1):
        result = mem.record_success("sig_X", "pattern_X")
        assert result["crystallized"] is False
    # 3eme succes -> cristallisation
    result = mem.record_success("sig_X", "pattern_X")
    assert result["successes"] == PREDICTION_CRYSTALLIZE_THRESHOLD
    assert result["crystallized"] is True
    assert result["newly_crystallized"] is True


def test_record_success_different_signatures_independent(mem):
    """2 contextes differents -> 2 strategies independantes."""
    mem.record_success("sig_A", "pattern_X")
    mem.record_success("sig_B", "pattern_X")
    strategies = mem.list_strategies()
    assert len(strategies) == 2


def test_record_success_different_patterns_independent(mem):
    """Meme signature mais patterns differents -> 2 strategies."""
    mem.record_success("sig_A", "pattern_X")
    mem.record_success("sig_A", "pattern_Y")
    assert len(mem.list_strategies()) == 2


# ============================================================================
# record_failure — decristallisation
# ============================================================================


def test_record_failure_decrystallizes_below_threshold(mem):
    """Cristallise + echecs cumules -> decristallisation a confidence<0.2."""
    # Cristalliser d'abord
    for _ in range(PREDICTION_CRYSTALLIZE_THRESHOLD):
        mem.record_success("sig", "pat")
    s = mem.list_strategies()[0]
    initial_conf = s["confidence"]
    assert s["crystallized"] is True

    # Faire echouer plusieurs fois (jusqu'a passer sous le seuil)
    for _ in range(10):
        result = mem.record_failure("sig", "pat")
        if result and result.get("decrystallized"):
            assert result["confidence"] < PREDICTION_DECRYSTALLIZE_THRESHOLD
            return
    # Si on arrive ici sans decristallisation, c'est un bug
    pytest.fail("Decristallisation n'a pas eu lieu apres 10 echecs")


def test_record_failure_unknown_returns_none(mem):
    """Pas de strategy pour ce (sig, pat) -> retourne None."""
    result = mem.record_failure("inconnue", "inconnue")
    assert result is None


# ============================================================================
# apply_decay_all — anti-superstition popperienne
# ============================================================================


def test_decay_reduces_confidence(mem):
    """Decay temporel reduit la confidence."""
    mem.record_success("sig", "pat")
    s = mem._strategies[0]
    initial_conf = s.confidence
    # Forcer le dernier decay a hier
    s.last_decay_applied = time.time() - 86400.0
    result = mem.apply_decay_all()
    assert result["total_decay_applied"] > 0
    assert mem._strategies[0].confidence < initial_conf


def test_decay_amount_per_day(mem):
    """Verifie que la baisse de confidence est ~PREDICTION_DECAY_PER_DAY par jour."""
    mem.record_success("sig", "pat")
    s = mem._strategies[0]
    s.confidence = 0.8
    s.last_decay_applied = time.time() - 86400.0  # 1 jour exactement
    mem.apply_decay_all()
    # confidence devrait avoir baisse de ~PREDICTION_DECAY_PER_DAY
    expected = 0.8 - PREDICTION_DECAY_PER_DAY
    assert abs(mem._strategies[0].confidence - expected) < 0.01


def test_decay_garbage_collects_dead_strategies(mem):
    """Strategies non-cristallisees a confidence<=0.05 sont retirees."""
    mem.record_success("sig", "pat")
    s = mem._strategies[0]
    s.confidence = 0.05  # quasi morte
    s.last_decay_applied = time.time() - 86400.0
    result = mem.apply_decay_all()
    assert result["removed_count"] == 1
    assert len(mem._strategies) == 0


def test_decay_decrystallizes_without_removing_yet(mem):
    """Strategy cristallisee qui passe sous seuil decrist mais reste au-dessus
    de 0.05 doit etre decristallisee SANS etre removed dans le meme tour."""
    for _ in range(PREDICTION_CRYSTALLIZE_THRESHOLD):
        mem.record_success("sig", "pat")
    s = mem._strategies[0]
    # confidence assez haute pour rester au-dessus de 0.05 apres 1 jour de decay
    s.confidence = 0.25  # > seuil decrist 0.20 + 0.05 garbage
    s.last_decay_applied = time.time() - 86400.0 * 1  # 1 jour -> decay 0.1
    result = mem.apply_decay_all()
    # Apres : confidence ~= 0.15 (sous seuil decrist 0.20) -> decristallise
    # mais > 0.05 donc pas removed
    assert result["decrystallized_count"] >= 1
    assert result["removed_count"] == 0
    assert len(mem._strategies) == 1
    assert mem._strategies[0].crystallized is False
    assert mem._strategies[0].confidence > 0.05


def test_decay_removes_when_confidence_fully_collapsed(mem):
    """Une fois decristallisee, si decay continue d'eroder sous 0.05 -> removed."""
    mem.record_success("sig", "pat")  # non cristallisee
    s = mem._strategies[0]
    s.confidence = 0.03  # deja sous le garbage threshold
    s.last_decay_applied = time.time() - 86400.0
    result = mem.apply_decay_all()
    assert result["removed_count"] == 1
    assert len(mem._strategies) == 0


# ============================================================================
# suggest_prediction — bypass deliberation
# ============================================================================


def test_suggest_returns_crystallized_only(mem):
    """suggest_prediction retourne seulement les cristallisees."""
    # Non-cristallisee
    mem.record_success("sig_A", "pat_A")
    suggestion = mem.suggest_prediction("sig_A")
    assert suggestion is None  # pas encore cristallisee

    # Cristalliser
    mem.record_success("sig_A", "pat_A")
    mem.record_success("sig_A", "pat_A")  # 3eme succes
    suggestion = mem.suggest_prediction("sig_A")
    assert suggestion is not None
    assert suggestion["predicted_pattern"] == "pat_A"


def test_suggest_returns_most_confident(mem):
    """Si plusieurs cristallisees pour meme sig, retourne la + confiante."""
    # 2 patterns sur meme sig, 1 confidence haute, 1 confidence basse
    for _ in range(PREDICTION_CRYSTALLIZE_THRESHOLD):
        mem.record_success("sig", "pat_low")
    for _ in range(PREDICTION_CRYSTALLIZE_THRESHOLD + 5):
        mem.record_success("sig", "pat_high")  # plus de succes
    suggestion = mem.suggest_prediction("sig")
    assert suggestion["predicted_pattern"] == "pat_high"


def test_suggest_unknown_signature_returns_none(mem):
    suggestion = mem.suggest_prediction("signature_qui_n_existe_pas")
    assert suggestion is None


# ============================================================================
# Persistance JSON sans BOM
# ============================================================================


def test_persistence_no_bom(mem, tmp_path):
    mem.record_success("sig_persist", "pat_persist")
    file_path = tmp_path / "prediction_memory.json"
    raw_bytes = file_path.read_bytes()
    # Pas de BOM (EF BB BF)
    assert raw_bytes[:3] != b"\xef\xbb\xbf"
    # JSON valide
    parsed = json.loads(raw_bytes.decode("utf-8"))
    assert isinstance(parsed, list)
    assert len(parsed) == 1


def test_load_existing_file(tmp_path):
    """Engine reinitialise charge depuis fichier existant."""
    file_path = tmp_path / "prediction_memory.json"
    file_path.write_text(json.dumps([{
        "id": "abc123",
        "context_signature": "test_sig",
        "predicted_pattern": "test_pat",
        "successes": 5,
        "failures": 0,
        "confidence": 0.8,
        "crystallized": True,
        "last_confirmed": time.time(),
        "last_decay_applied": time.time(),
        "created_at": time.time(),
    }]), encoding="utf-8")

    PredictionMemory.reset_singleton()
    m = PredictionMemory()
    m.set_file_path(str(file_path))
    strategies = m.list_strategies()
    assert len(strategies) == 1
    assert strategies[0]["id"] == "abc123"
    assert strategies[0]["crystallized"] is True
    PredictionMemory.reset_singleton()


# ============================================================================
# Cap MAX_PREDICTION_STRATEGIES
# ============================================================================


def test_cap_evicts_oldest_non_crystallized(mem):
    """Au-dela de MAX, evicte la plus ancienne non-cristallisee."""
    # On force une petite limite pour le test (sans modifier la constante globale)
    # Plutot, on cree MAX+5 strategies non-cristallisees
    for i in range(MAX_PREDICTION_STRATEGIES + 5):
        mem.record_success(f"sig_{i:04d}", f"pat_{i:04d}")
    # Apres MAX_PREDICTION_STRATEGIES insertions, le cap doit avoir agi
    assert len(mem._strategies) <= MAX_PREDICTION_STRATEGIES


# ============================================================================
# Stats
# ============================================================================


def test_get_stats_counts_correctly(mem):
    mem.record_success("sig_A", "pat_A")
    for _ in range(PREDICTION_CRYSTALLIZE_THRESHOLD):
        mem.record_success("sig_B", "pat_B")  # va cristalliser
    stats = mem.get_stats()
    assert stats["total_strategies"] == 2
    assert stats["crystallized"] == 1
