"""Tests unitaires SeedsEngine (chantier 25/05).

Couverture :
- Two-Key Turn (propose, validate, TTL expire, anti-doublon)
- Persistance UTF-8 sans BOM
- Recall (activation + mesure + ladder)
- Hard Cap J+7 (jamais depasse)
- Plafonnement energie (anti-saturation)
- Trim history (50 derniers)

Tous les appels P16 sont mocques -> tests isoles du runtime.
"""
import json
import time
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from core.seeds_engine import (
    SeedsEngine,
    INTERVAL_LADDER_DAYS,
    ENERGY_THRESHOLD,
    MAX_ENERGY_CAP,
    PROPOSAL_TTL_SECONDS,
    MAX_RECALL_HISTORY,
)


@pytest.fixture
def engine(tmp_path):
    """Engine frais (singleton reset) avec seeds.json dans tmp_path."""
    SeedsEngine.reset_singleton()
    e = SeedsEngine()
    e.set_seeds_file(str(tmp_path / "seeds.json"))
    yield e
    SeedsEngine.reset_singleton()


# ============================================================================
# Two-Key Turn
# ============================================================================


def test_validate_via_phrase_creates_seed(engine):
    seed = engine.validate(phrase="La verite par l'acte.", source_debat="dialogue_test")
    assert seed["phrase"] == "La verite par l'acte."
    assert seed["source_debat"] == "dialogue_test"
    assert seed["ladder_index"] == 0
    assert seed["stability_score"] == 1.0
    assert seed["recall_history"] == []
    assert seed["id"].startswith("seed_")
    # next_recall = J+3 (premier intervalle)
    next_recall = datetime.fromisoformat(seed["next_recall"])
    creation = datetime.fromisoformat(seed["creation_date"])
    delta_days = (next_recall - creation).total_seconds() / 86400
    assert 2.99 < delta_days < 3.01


def test_propose_then_validate_via_proposal_id(engine):
    pid = engine.propose("Je ne sais pas, donc je tends la main.", source_debat="d1")
    assert pid in [p["proposal_id"] for p in engine.get_pending()]
    seed = engine.validate(proposal_id=pid)
    assert seed["phrase"] == "Je ne sais pas, donc je tends la main."
    assert seed["source_debat"] == "d1"
    # Le pending doit avoir ete consomme
    assert pid not in [p["proposal_id"] for p in engine.get_pending()]


def test_validate_picks_latest_pending_when_no_args(engine):
    engine.propose("Premiere graine.")
    time.sleep(0.01)
    engine.propose("Deuxieme graine.")
    seed = engine.validate()  # sans arg -> dernier pending
    assert seed["phrase"] == "Deuxieme graine."


def test_validate_no_pending_no_phrase_raises(engine):
    with pytest.raises(ValueError, match="Aucune proposition pending"):
        engine.validate()


def test_validate_unknown_proposal_id_raises(engine):
    with pytest.raises(ValueError, match="proposal_id inconnu ou expire"):
        engine.validate(proposal_id="ffffffffffff")


def test_propose_ttl_expires(engine):
    """Apres TTL (15 min), la proposition disparaît silencieusement."""
    pid = engine.propose("Graine expirable.")
    # Avance le timestamp interne de TTL+1s
    engine._pending[pid]["ts"] = time.time() - PROPOSAL_TTL_SECONDS - 1
    engine._cleanup_pending()
    assert pid not in engine._pending
    # Tentative de validate -> erreur explicite (proposal_id expire)
    with pytest.raises(ValueError, match="proposal_id inconnu ou expire"):
        engine.validate(proposal_id=pid)


def test_proposal_ttl_fallback_phrase_works(engine):
    """Si TTL expire mais JM redonne la phrase exacte, ca marche (fallback)."""
    pid = engine.propose("Phrase oubliee.")
    engine._pending[pid]["ts"] = time.time() - PROPOSAL_TTL_SECONDS - 1
    engine._cleanup_pending()
    seed = engine.validate(phrase="Phrase oubliee.")
    assert seed["phrase"] == "Phrase oubliee."


def test_validate_anti_doublon_raises(engine):
    engine.validate(phrase="Une fois.")
    with pytest.raises(ValueError, match="deja presente"):
        engine.validate(phrase="Une fois.")


def test_validate_empty_phrase_raises(engine):
    with pytest.raises(ValueError, match="phrase vide"):
        engine.validate(phrase="   ")


# ============================================================================
# Persistance + listing
# ============================================================================


def test_seeds_persisted_without_bom(engine, tmp_path):
    engine.validate(phrase="Test BOM.")
    seeds_file = tmp_path / "seeds.json"
    raw_bytes = seeds_file.read_bytes()
    # Pas de BOM en tete (EF BB BF)
    assert raw_bytes[:3] != b"\xef\xbb\xbf"
    # Le fichier doit etre du JSON valide
    parsed = json.loads(raw_bytes.decode("utf-8"))
    assert isinstance(parsed, list)
    assert parsed[0]["phrase"] == "Test BOM."


def test_load_existing_seeds_file(tmp_path):
    """Engine reinitialise charge bien depuis fichier existant."""
    seeds_file = tmp_path / "seeds.json"
    seeds_file.write_text(json.dumps([{
        "id": "seed_abc",
        "phrase": "Persistante.",
        "creation_date": "2026-05-25T00:00:00",
        "ladder_index": 1,
        "stability_score": 1.5,
        "next_recall": "2026-05-30T00:00:00",
        "source_debat": None,
        "recall_history": [],
    }]), encoding="utf-8")

    SeedsEngine.reset_singleton()
    e = SeedsEngine()
    e.set_seeds_file(str(seeds_file))
    seeds = e.list_seeds()
    assert len(seeds) == 1
    assert seeds[0]["id"] == "seed_abc"
    SeedsEngine.reset_singleton()


def test_list_seeds_returns_copies(engine):
    """list_seeds() doit retourner des copies, pas les references internes."""
    engine.validate(phrase="Immutable.")
    snapshot = engine.list_seeds()
    snapshot[0]["phrase"] = "Hack."
    # L'interne ne doit pas avoir bouge
    assert engine.list_seeds()[0]["phrase"] == "Immutable."


# ============================================================================
# Remove
# ============================================================================


def test_remove_existing_seed(engine):
    seed = engine.validate(phrase="A retirer.")
    assert engine.remove(seed["id"]) is True
    assert engine.list_seeds() == []


def test_remove_unknown_seed_returns_false(engine):
    assert engine.remove("seed_doesnotexist") is False


# ============================================================================
# get_due (selection des graines a rappeler)
# ============================================================================


def test_get_due_filters_future_seeds(engine):
    """Une graine fraichement creee (next_recall=J+3) n'est PAS due maintenant."""
    engine.validate(phrase="Future.")
    assert engine.get_due() == []


def test_get_due_returns_past_seeds(engine):
    """Si on force next_recall dans le passe, get_due la retourne."""
    seed = engine.validate(phrase="En retard.")
    # Force next_recall a hier
    past = datetime.now() - timedelta(days=1)
    engine._seeds[0]["next_recall"] = past.isoformat()
    due = engine.get_due()
    assert len(due) == 1
    assert due[0]["id"] == seed["id"]


def test_get_due_sorted_by_oldest_first(engine):
    a = engine.validate(phrase="A.")
    b = engine.validate(phrase="B.")
    # Force A plus en retard que B
    engine._seeds[0]["next_recall"] = (datetime.now() - timedelta(days=5)).isoformat()
    engine._seeds[1]["next_recall"] = (datetime.now() - timedelta(days=1)).isoformat()
    due = engine.get_due()
    assert due[0]["id"] == a["id"]
    assert due[1]["id"] == b["id"]


# ============================================================================
# Recall — coeur du systeme
# ============================================================================


def test_recall_unknown_seed_raises(engine):
    with pytest.raises(ValueError, match="Graine inconnue"):
        engine.recall("seed_nonexistent")


def test_recall_no_concepts_extracted(engine):
    """Si extract_concepts ne renvoie rien, recall log un error sans crash."""
    seed = engine.validate(phrase="zzz.")
    with patch("core.spreading_activation.extract_concepts", return_value=[]):
        result = engine.recall(seed["id"])
    assert result["energy"] == 0.0
    assert result["success"] is False
    assert result["error"] == "no_concepts_extracted"
    # Le ladder doit etre descendu (ou rester a 0)
    assert result["ladder_index"] == 0


def test_recall_success_ladder_climbs(engine):
    """Succes (energy >= seuil) -> ladder monte 0 -> 1 -> 2 et reste a 2."""
    seed = engine.validate(phrase="La verite par l'acte renforce.")

    # Mock : haute resonance
    fake_concepts = [("verite", 0.8), ("acte", 0.7), ("renforce", 0.6)]
    fake_assocs = [("voisin1", 0.9), ("voisin2", 0.8), ("voisin3", 0.7)]

    with patch("core.spreading_activation.extract_concepts", return_value=fake_concepts), \
         patch("core.synaptic_network.cortex") as mock_cortex:
        mock_cortex.query_associations.return_value = fake_assocs
        mock_cortex.activate_concept.return_value = "node_xyz"

        # Recall 1 : idx 0 -> 1 (J+3 -> J+5)
        r1 = engine.recall(seed["id"])
        assert r1["success"] is True
        assert r1["ladder_index"] == 1
        assert r1["interval_days"] == 5

        # Recall 2 : idx 1 -> 2 (J+5 -> J+7)
        r2 = engine.recall(seed["id"])
        assert r2["ladder_index"] == 2
        assert r2["interval_days"] == 7

        # Recall 3 : idx 2 -> 2 (HARD CAP, ne depasse pas)
        r3 = engine.recall(seed["id"])
        assert r3["ladder_index"] == 2
        assert r3["interval_days"] == 7  # toujours J+7, jamais J+10+


def test_recall_failure_ladder_descends(engine):
    """Echec (energy < seuil) -> ladder descend mais reste >= 0."""
    seed = engine.validate(phrase="Resonance trop faible.")
    # Force ladder a 2 (J+7) pour observer la descente
    engine._seeds[0]["ladder_index"] = 2

    with patch("core.spreading_activation.extract_concepts",
               return_value=[("resonance", 0.5)]), \
         patch("core.synaptic_network.cortex") as mock_cortex:
        # Energie sous le seuil
        mock_cortex.query_associations.return_value = [("voisin", 0.05)]
        mock_cortex.activate_concept.return_value = "node_xyz"

        r1 = engine.recall(seed["id"])
        assert r1["success"] is False
        assert r1["ladder_index"] == 1  # descendu de 2 -> 1
        assert r1["interval_days"] == 5

        r2 = engine.recall(seed["id"])
        assert r2["ladder_index"] == 0  # 1 -> 0
        assert r2["interval_days"] == 3

        r3 = engine.recall(seed["id"])
        assert r3["ladder_index"] == 0  # plancher, reste a 0
        assert r3["interval_days"] == 3


def test_recall_hard_cap_never_exceeds_J7(engine):
    """Quelles que soient les conditions, l'intervalle ne depasse JAMAIS J+7."""
    seed = engine.validate(phrase="Test cap.")
    with patch("core.spreading_activation.extract_concepts",
               return_value=[("test", 0.5)]), \
         patch("core.synaptic_network.cortex") as mock_cortex:
        mock_cortex.query_associations.return_value = [("voisin", 100.0)]  # max
        mock_cortex.activate_concept.return_value = "node_xyz"

        for _ in range(20):  # nombreux rappels successifs
            r = engine.recall(seed["id"])
            assert r["interval_days"] <= 7, f"Hard cap viole : J+{r['interval_days']}"
            assert r["ladder_index"] < len(INTERVAL_LADDER_DAYS)


def test_recall_energy_capped_at_max(engine):
    """Si la somme depasse MAX_ENERGY_CAP, plafonnee (garde-fou anti-saturation)."""
    seed = engine.validate(phrase="Saturation.")
    huge_assocs = [(f"v{i}", 100.0) for i in range(10)]  # somme = 1000

    with patch("core.spreading_activation.extract_concepts",
               return_value=[("saturation", 0.5)]), \
         patch("core.synaptic_network.cortex") as mock_cortex:
        mock_cortex.query_associations.return_value = huge_assocs
        mock_cortex.activate_concept.return_value = "node_xyz"

        r = engine.recall(seed["id"])
        assert r["energy"] == MAX_ENERGY_CAP


def test_recall_calls_activate_concept_for_each_seed(engine):
    """Verifie que cortex.activate_concept est appele pour chaque concept extrait."""
    seed = engine.validate(phrase="Trois concepts ici.")
    fake_concepts = [("alpha", 0.6), ("beta", 0.5), ("gamma", 0.4)]

    with patch("core.spreading_activation.extract_concepts", return_value=fake_concepts), \
         patch("core.synaptic_network.cortex") as mock_cortex:
        mock_cortex.query_associations.return_value = [("v", 1.0)]
        mock_cortex.activate_concept.return_value = "node_xyz"

        engine.recall(seed["id"])

        # 3 appels (un par concept extrait)
        assert mock_cortex.activate_concept.call_count == 3
        activated = [call.args[0] for call in mock_cortex.activate_concept.call_args_list]
        assert set(activated) == {"alpha", "beta", "gamma"}


def test_recall_history_trimmed_at_max(engine):
    """recall_history ne depasse jamais MAX_RECALL_HISTORY entrees."""
    seed = engine.validate(phrase="History limit.")

    with patch("core.spreading_activation.extract_concepts",
               return_value=[("history", 0.5)]), \
         patch("core.synaptic_network.cortex") as mock_cortex:
        mock_cortex.query_associations.return_value = [("v", 0.6)]
        mock_cortex.activate_concept.return_value = "node_xyz"

        for _ in range(MAX_RECALL_HISTORY + 10):
            engine.recall(seed["id"])

        seed_state = engine.list_seeds()[0]
        assert len(seed_state["recall_history"]) == MAX_RECALL_HISTORY


def test_recall_persists_after_each_call(engine, tmp_path):
    """Apres recall, le fichier seeds.json doit refleter le nouveau state."""
    seed = engine.validate(phrase="Persistance recall.")

    with patch("core.spreading_activation.extract_concepts",
               return_value=[("persist", 0.6)]), \
         patch("core.synaptic_network.cortex") as mock_cortex:
        mock_cortex.query_associations.return_value = [("v", 0.8)]
        mock_cortex.activate_concept.return_value = "node_xyz"

        engine.recall(seed["id"])

        # Re-lit depuis disque
        saved = json.loads((tmp_path / "seeds.json").read_text(encoding="utf-8"))
        assert saved[0]["ladder_index"] == 1
        assert len(saved[0]["recall_history"]) == 1


def test_recall_exception_in_extract_does_not_crash(engine):
    """Si extract_concepts leve, recall traite comme no_concepts."""
    seed = engine.validate(phrase="Crash extract.")
    with patch("core.spreading_activation.extract_concepts",
               side_effect=RuntimeError("boom")):
        result = engine.recall(seed["id"])
    assert result["error"] == "no_concepts_extracted"
    assert result["energy"] == 0.0
