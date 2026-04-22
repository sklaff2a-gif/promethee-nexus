"""Tests V12.0 / Phase 13 (2026-04-22) : MDP + Replay Hippocampique.

Contexte : la V11.0 homeostatique est stable, fenetre propre pour rewiring.
Le Q-learning unitaire (habit par intent isole) ne capturait pas les chaines
causales. V12.0 ajoute un Q-learning sequentiel sur l'etat enrichi
(drive, prev_intent, curr_intent), retropropage gamma=0.95, clippe failures
a FAILURE_FLOOR=-1.0 pour eviter la phobie algorithmique.

Design valide Gemini soir du 21/04, commande explicite Jean-Michel 22/04 matin.

Invariants critiques testes :
- 4800 tests existants : signature compute_habit_bonus(intent) preservee
- COUNCIL_DEBATE reste joignable meme apres N echecs consecutifs
- Persistance JSON backward-compatible
- TrajectoryBuffer volatile (pas de rejeu de fantomes post-reboot)
"""
import json
import os
import pytest
from unittest.mock import patch

from core.basal_ganglia import (
    BasalGanglia,
    GAMMA_SEQ,
    LR_SEQ,
    FAILURE_FLOOR,
    SEQ_HABIT_DECAY,
    MAX_SEQ_HABITS,
    SEQ_HABIT_THRESHOLD,
    SEQ_KEY_SEP,
    NOVELTY_BONUS,
)
from core.hippocampus import (
    Hippocampus,
    TrajectoryBuffer,
    Episode,
    MAX_TRAJECTORY,
)


# ═══════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════


@pytest.fixture
def fresh_ganglia():
    BasalGanglia.reset_singleton()
    g = BasalGanglia()
    g.habits.clear()
    g.go_nogo_state.clear()
    g.sequential_habits.clear()
    g._last_intent = ""
    g.total_selections = 0
    g.total_inhibitions = 0
    g._cardiac_ticks = 0
    yield g
    BasalGanglia.reset_singleton()


@pytest.fixture
def fresh_hippo():
    Hippocampus.reset_singleton()
    h = Hippocampus()
    h._episodes.clear()
    h._arcs.clear()
    h._known_intents.clear()
    h.trajectory_buffer.clear()
    yield h
    Hippocampus.reset_singleton()


def _make_episode(intent: str, quality: float = 0.7, drive: str = "maitrise",
                  event_type: str = "routine_success") -> Episode:
    """Helper : construit un Episode minimal pour les tests."""
    return Episode(
        id=f"ep_{intent}",
        event_type=event_type,
        intent=intent,
        dominant_drive=drive,
        quality_score=quality,
        timestamp=0.0,
    )


# ═══════════════════════════════════════════════════════════════════════
# TrajectoryBuffer — structure circulaire volatile
# ═══════════════════════════════════════════════════════════════════════


class TestTrajectoryBuffer:

    def test_capacity_respects_max_trajectory(self):
        buf = TrajectoryBuffer()
        for i in range(MAX_TRAJECTORY + 5):
            buf.append(_make_episode(f"X{i}"))
        assert len(buf) == MAX_TRAJECTORY

    def test_wraparound_keeps_most_recent(self):
        buf = TrajectoryBuffer(maxlen=5)
        for i in range(10):
            buf.append(_make_episode(f"A{i}"))
        recent = buf.get_recent()
        intents = [e.intent for e in recent]
        assert intents == ["A5", "A6", "A7", "A8", "A9"]

    def test_get_recent_n_slices_correctly(self):
        buf = TrajectoryBuffer(maxlen=10)
        for i in range(10):
            buf.append(_make_episode(f"B{i}"))
        last_3 = buf.get_recent(3)
        assert [e.intent for e in last_3] == ["B7", "B8", "B9"]

    def test_clear_empties_buffer(self):
        buf = TrajectoryBuffer()
        buf.append(_make_episode("X"))
        buf.clear()
        assert len(buf) == 0


# ═══════════════════════════════════════════════════════════════════════
# Integration Hippocampus : push automatique dans le buffer
# ═══════════════════════════════════════════════════════════════════════


class TestHippocampusTrajectory:

    def test_encode_episode_pushes_to_buffer(self, fresh_hippo):
        ep = fresh_hippo._encode_episode(
            event_type="routine_success",
            intent="TEST_ROUTINE",
            agent="tester",
            quality_score=0.9,
        )
        assert ep is not None
        recent = fresh_hippo.get_recent_trajectory()
        assert len(recent) == 1
        assert recent[0].intent == "TEST_ROUTINE"

    def test_buffer_is_volatile_not_persisted(self, fresh_hippo, tmp_path):
        """Le buffer ne doit PAS etre sauvegarde (volatilite intentionnelle)."""
        fresh_hippo._encode_episode(
            event_type="routine_success", intent="VOLATILE", quality_score=0.8
        )
        state_file = tmp_path / "hippo.json"
        with patch("core.hippocampus.HIPPOCAMPUS_STATE_FILE", str(state_file)):
            fresh_hippo._save()
        with open(state_file) as f:
            data = json.load(f)
        assert "trajectory" not in data
        assert "trajectory_buffer" not in data


# ═══════════════════════════════════════════════════════════════════════
# BasalGanglia._seq_key — stabilite de la clef
# ═══════════════════════════════════════════════════════════════════════


class TestSeqKey:

    def test_basic_key(self, fresh_ganglia):
        key = fresh_ganglia._seq_key("maitrise", "ROUTINE_A", "ROUTINE_B")
        assert key == f"maitrise{SEQ_KEY_SEP}ROUTINE_A{SEQ_KEY_SEP}ROUTINE_B"

    def test_empty_values_become_none(self, fresh_ganglia):
        key = fresh_ganglia._seq_key("", "", "")
        assert key == "none|none|none".replace("|", SEQ_KEY_SEP)

    def test_deterministic_same_input_same_key(self, fresh_ganglia):
        k1 = fresh_ganglia._seq_key("d", "p", "c")
        k2 = fresh_ganglia._seq_key("d", "p", "c")
        assert k1 == k2


# ═══════════════════════════════════════════════════════════════════════
# update_sequential — Q-learning retropropage
# ═══════════════════════════════════════════════════════════════════════


class TestUpdateSequential:

    def test_empty_trajectory_returns_zero(self, fresh_ganglia):
        assert fresh_ganglia.update_sequential([]) == 0

    def test_single_episode_trajectory_returns_zero(self, fresh_ganglia):
        assert fresh_ganglia.update_sequential([_make_episode("A")]) == 0

    def test_two_episodes_creates_one_transition(self, fresh_ganglia):
        traj = [_make_episode("A", 0.5), _make_episode("B", 0.8)]
        updates = fresh_ganglia.update_sequential(traj)
        assert updates == 1
        assert len(fresh_ganglia.sequential_habits) == 1

    def test_reward_propagated_via_q_update(self, fresh_ganglia):
        """Q += LR * (reward + gamma * next_max - Q). Avec Q init=0 et
        next_max=0, on doit obtenir Q = LR * reward sur la clef (drive, A, B)."""
        ep_a = _make_episode("A", 0.0)
        ep_b = _make_episode("B", 0.8)  # reward evalue pour B
        fresh_ganglia.update_sequential([ep_a, ep_b])
        key = fresh_ganglia._seq_key("maitrise", "A", "B")
        entry = fresh_ganglia.sequential_habits[key]
        expected = LR_SEQ * 0.8
        assert entry["q_value"] == pytest.approx(expected, rel=1e-3)

    def test_failure_clipped_at_floor(self, fresh_ganglia):
        """Un routine_failure avec quality tres negative doit tirer Q vers
        FAILURE_FLOOR, pas plus bas."""
        ep_a = _make_episode("BEFORE", 0.0)
        ep_b = _make_episode(
            "AFTER", quality=-5.0, event_type="routine_failure",
        )
        fresh_ganglia.update_sequential([ep_a, ep_b])
        key = fresh_ganglia._seq_key("maitrise", "BEFORE", "AFTER")
        q = fresh_ganglia.sequential_habits[key]["q_value"]
        # Q = LR * FAILURE_FLOOR = 0.05 * -1.0 = -0.05
        assert q >= LR_SEQ * FAILURE_FLOOR - 0.01
        assert q < 0

    def test_gamma_discount_visible_over_chain(self, fresh_ganglia):
        """Chaine A -> B -> C. Au 1er passage, Q(A,B) et Q(B,C) sont posees
        avec next_max=0. Au 2e passage, Q(A,B) integre GAMMA * Q(B,C) > 0."""
        traj = [
            _make_episode("A", 0.0),
            _make_episode("B", 0.7),
            _make_episode("C", 0.9),
        ]
        fresh_ganglia.update_sequential(traj)
        key_ab = fresh_ganglia._seq_key("maitrise", "A", "B")
        key_bc = fresh_ganglia._seq_key("maitrise", "B", "C")
        q_ab_first = fresh_ganglia.sequential_habits[key_ab]["q_value"]
        q_bc_first = fresh_ganglia.sequential_habits[key_bc]["q_value"]
        assert q_ab_first > 0
        assert q_bc_first > 0
        # 2e passage : Q(A,B) doit integrer GAMMA * Q(B,C), donc augmenter
        fresh_ganglia.update_sequential(traj)
        q_ab_second = fresh_ganglia.sequential_habits[key_ab]["q_value"]
        assert q_ab_second > q_ab_first

    def test_council_debate_reachable_after_failures(self, fresh_ganglia):
        """R1 : apres 5 echecs COUNCIL_DEBATE, bonus sequentiel >= -1.0.
        Pas de phobie algorithmique definitive."""
        for _ in range(5):
            fresh_ganglia.update_sequential([
                _make_episode("SELECT", 0.0),
                _make_episode(
                    "COUNCIL_DEBATE", quality=0.0,
                    event_type="routine_failure",
                ),
            ])
        key = fresh_ganglia._seq_key("maitrise", "SELECT", "COUNCIL_DEBATE")
        # Q doit etre negatif mais pas sous -2.0 (clamp basal_ganglia)
        q = fresh_ganglia.sequential_habits[key]["q_value"]
        assert -2.0 <= q <= 0


# ═══════════════════════════════════════════════════════════════════════
# compute_habit_bonus enrichi — pas de rupture de contrat
# ═══════════════════════════════════════════════════════════════════════


class TestComputeHabitBonusEnriched:

    def test_unknown_intent_still_returns_novelty_bonus(self, fresh_ganglia):
        """Non-regression : les 4800 tests reposent sur ce contrat."""
        assert fresh_ganglia.compute_habit_bonus("NEVER_SEEN") == NOVELTY_BONUS

    def test_weak_habit_no_seq_returns_zero(self, fresh_ganglia):
        fresh_ganglia._update_habit("WEAK", 0.1)
        assert fresh_ganglia.compute_habit_bonus("WEAK") == 0.0

    def test_signature_accepts_single_arg(self, fresh_ganglia):
        """Appel via introspection dynamique (autonomy_engine.py:2334)."""
        fresh_ganglia._update_habit("X", 1.0)
        # Doit ne pas lever TypeError
        result = fresh_ganglia.compute_habit_bonus("X")
        assert isinstance(result, float)

    def test_sequential_component_adds_when_prev_known(self, fresh_ganglia):
        """Avec un prev_intent memorise + Q positif, le bonus doit augmenter."""
        # Etablir une habit unitaire moyenne
        fresh_ganglia._update_habit("TARGET", 0.5)
        # Etablir un Q(maitrise, PREV, TARGET) > seuil
        key = fresh_ganglia._seq_key("maitrise", "PREV", "TARGET")
        fresh_ganglia.sequential_habits[key] = {
            "q_value": 0.5,
            "visits": 10,
            "last_reward": 0.7,
            "updated_at": 0.0,
        }
        # Simuler le contexte : last_intent = PREV
        fresh_ganglia._last_intent = "PREV"
        with patch.object(fresh_ganglia, "_get_dominant_drive",
                          return_value="maitrise"):
            bonus_with_seq = fresh_ganglia.compute_habit_bonus("TARGET")
        # Remettre last_intent a vide : seuil ignore donc seq=0
        fresh_ganglia._last_intent = ""
        bonus_without_seq = fresh_ganglia.compute_habit_bonus("TARGET")
        assert bonus_with_seq > bonus_without_seq

    def test_bonus_clamped_to_upper_bound(self, fresh_ganglia):
        """Le bonus total doit rester <= 2.0 meme avec seq+unitaire max."""
        fresh_ganglia._update_habit("MAX", 1.0)
        fresh_ganglia._update_habit("MAX", 1.0)
        fresh_ganglia._update_habit("MAX", 1.0)
        key = fresh_ganglia._seq_key("maitrise", "PREV", "MAX")
        fresh_ganglia.sequential_habits[key] = {
            "q_value": 2.0,
            "visits": 100,
            "last_reward": 1.0,
            "updated_at": 0.0,
        }
        fresh_ganglia._last_intent = "PREV"
        with patch.object(fresh_ganglia, "_get_dominant_drive",
                          return_value="maitrise"):
            bonus = fresh_ganglia.compute_habit_bonus("MAX")
        assert bonus <= 2.0

    def test_bonus_clamped_to_lower_bound(self, fresh_ganglia):
        """Le bonus total doit rester >= -1.5."""
        fresh_ganglia._update_habit("MIN", 1.0)
        fresh_ganglia.go_nogo_state["MIN"] = -2.0
        key = fresh_ganglia._seq_key("maitrise", "PREV", "MIN")
        fresh_ganglia.sequential_habits[key] = {
            "q_value": -2.0, "visits": 10,
            "last_reward": -1.0, "updated_at": 0.0,
        }
        fresh_ganglia._last_intent = "PREV"
        with patch.object(fresh_ganglia, "_get_dominant_drive",
                          return_value="maitrise"):
            bonus = fresh_ganglia.compute_habit_bonus("MIN")
        assert bonus >= -1.5


# ═══════════════════════════════════════════════════════════════════════
# Metabolic Wash etendu au MDP
# ═══════════════════════════════════════════════════════════════════════


class TestMetabolicWashSequential:

    def test_wash_decays_sequential_habits(self, fresh_ganglia):
        key = fresh_ganglia._seq_key("d", "p", "c")
        fresh_ganglia.sequential_habits[key] = {
            "q_value": 1.0, "visits": 5,
            "last_reward": 0.5, "updated_at": 0.0,
        }
        fresh_ganglia.tick_metabolic_wash()
        assert fresh_ganglia.sequential_habits[key]["q_value"] == pytest.approx(
            SEQ_HABIT_DECAY, rel=1e-6
        )

    def test_wash_prunes_zero_q_entries(self, fresh_ganglia):
        key = fresh_ganglia._seq_key("d", "p", "c")
        fresh_ganglia.sequential_habits[key] = {
            "q_value": 0.001, "visits": 1,
            "last_reward": 0.0, "updated_at": 0.0,
        }
        fresh_ganglia.tick_metabolic_wash()
        assert key not in fresh_ganglia.sequential_habits

    def test_wash_preserves_existing_unitary_decay(self, fresh_ganglia):
        """Non-regression V10.1 : le wash doit toujours decay habits et go_nogo."""
        fresh_ganglia._update_habit("X", 1.0)
        strength_before = fresh_ganglia.habits["X"]["strength"]
        fresh_ganglia.go_nogo_state["Y"] = -1.0
        fresh_ganglia.tick_metabolic_wash()
        assert fresh_ganglia.habits["X"]["strength"] < strength_before
        assert fresh_ganglia.go_nogo_state["Y"] > -1.0


# ═══════════════════════════════════════════════════════════════════════
# Persistance — sequential_habits dans JSON
# ═══════════════════════════════════════════════════════════════════════


class TestPersistence:

    def test_save_load_roundtrip(self, fresh_ganglia, tmp_path):
        key = fresh_ganglia._seq_key("maitrise", "A", "B")
        fresh_ganglia.sequential_habits[key] = {
            "q_value": 0.42, "visits": 7,
            "last_reward": 0.8, "updated_at": 1234.5,
        }
        state_file = tmp_path / "bg.json"
        with patch("core.basal_ganglia.BASAL_GANGLIA_STATE_FILE",
                   str(state_file)):
            fresh_ganglia._save()
            # Reset puis reload
            BasalGanglia.reset_singleton()
            g2 = BasalGanglia()
            g2.sequential_habits.clear()
            g2._load()
        assert key in g2.sequential_habits
        assert g2.sequential_habits[key]["q_value"] == pytest.approx(0.42)
        assert g2.sequential_habits[key]["visits"] == 7
        BasalGanglia.reset_singleton()

    def test_load_backward_compatible_without_seq_key(self, fresh_ganglia,
                                                      tmp_path):
        """Un JSON pre-V12 (sans cle 'sequential_habits') ne doit pas lever."""
        state_file = tmp_path / "bg_old.json"
        with open(state_file, "w") as f:
            json.dump({
                "habits": {},
                "go_nogo_state": {},
                "total_selections": 0,
                "total_inhibitions": 0,
            }, f)
        with patch("core.basal_ganglia.BASAL_GANGLIA_STATE_FILE",
                   str(state_file)):
            BasalGanglia.reset_singleton()
            g = BasalGanglia()
            g._load()
        assert g.sequential_habits == {}
        BasalGanglia.reset_singleton()


# ═══════════════════════════════════════════════════════════════════════
# Prune MAX_SEQ_HABITS
# ═══════════════════════════════════════════════════════════════════════


class TestPruneSequentialHabits:

    def test_prune_respects_max_cap(self, fresh_ganglia):
        """On bourre N > MAX, apres prune on doit avoir exactement MAX."""
        # Injection directe pour gain de temps
        for i in range(MAX_SEQ_HABITS + 50):
            key = fresh_ganglia._seq_key("d", f"prev_{i}", f"curr_{i}")
            fresh_ganglia.sequential_habits[key] = {
                "q_value": 0.1 if i % 2 else 0.9,
                "visits": 1 + i,
                "last_reward": 0.0,
                "updated_at": 0.0,
            }
        fresh_ganglia._prune_sequential_habits()
        assert len(fresh_ganglia.sequential_habits) == MAX_SEQ_HABITS
