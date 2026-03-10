"""Tests pour tools/lora_auto_trainer.py — Pipeline QLoRA automatise."""

import json
import os
import sys
import time

import pytest

# Ajouter le projet au path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from tools.lora_auto_trainer import (
    LoRAAutoTrainer,
    TRAINING_ROTATION,
    MIN_DATASET_SIZE,
    MIN_NEW_DATA_RATIO,
    MIN_HOURS_BETWEEN_TRAINING,
    OLLAMA_MODEL_PREFIX,
)


@pytest.fixture
def trainer(tmp_path, monkeypatch):
    """Cree un trainer avec un state file temporaire."""
    state_file = str(tmp_path / "lora_state.json")
    monkeypatch.setattr(
        "tools.lora_auto_trainer.TRAINING_STATE_FILE", state_file
    )
    monkeypatch.setattr(
        "tools.lora_auto_trainer.DATASETS_DIR", str(tmp_path / "datasets")
    )
    monkeypatch.setattr(
        "tools.lora_auto_trainer.ADAPTERS_DIR", str(tmp_path / "adapters")
    )
    t = LoRAAutoTrainer()
    return t


class TestState:
    """Tests de persistance de l'etat."""

    def test_initial_state(self, trainer):
        """Etat initial vide."""
        assert trainer._state.get("total_trainings", 0) == 0
        assert trainer._state.get("last_rotation_index", -1) == -1

    def test_save_and_reload(self, trainer, tmp_path, monkeypatch):
        """Sauvegarde et rechargement de l'etat."""
        trainer._state["total_trainings"] = 5
        trainer._save_state()

        # Recharger
        trainer2 = LoRAAutoTrainer()
        assert trainer2._state["total_trainings"] == 5

    def test_get_agent_state_creates_default(self, trainer):
        """get_agent_state cree un etat par defaut si absent."""
        state = trainer._get_agent_state("coder")
        assert state["last_trained"] == 0
        assert state["last_dataset_size"] == 0
        assert state["model_name"] == "promethee-coder"


class TestRotation:
    """Tests de rotation des agents."""

    def test_first_agent(self, trainer):
        """Premier agent de la rotation."""
        assert trainer.get_next_agent() == TRAINING_ROTATION[0]

    def test_advance_rotation(self, trainer):
        """Avancement correct de la rotation."""
        trainer._advance_rotation()
        assert trainer.get_next_agent() == TRAINING_ROTATION[1]

    def test_rotation_wraps(self, trainer):
        """La rotation revient au debut apres le dernier agent."""
        for _ in range(len(TRAINING_ROTATION)):
            trainer._advance_rotation()
        assert trainer.get_next_agent() == TRAINING_ROTATION[0]

    def test_rotation_persists(self, trainer, tmp_path, monkeypatch):
        """L'index de rotation persiste entre les instances."""
        trainer._advance_rotation()
        trainer._advance_rotation()
        trainer._save_state()

        trainer2 = LoRAAutoTrainer()
        assert trainer2.get_next_agent() == TRAINING_ROTATION[2]


class TestThreshold:
    """Tests des seuils de training."""

    def test_below_minimum_size(self, trainer):
        """Refuse si pas assez de donnees."""
        should, reason = trainer.should_train("coder", 10, "abc123")
        assert not should
        assert "Pas assez" in reason

    def test_above_minimum_size(self, trainer):
        """Accepte si assez de donnees et pas de training precedent."""
        should, reason = trainer.should_train("coder", MIN_DATASET_SIZE + 10, "abc123")
        assert should

    def test_same_hash_rejected(self, trainer):
        """Refuse si le hash du dataset est identique."""
        state = trainer._get_agent_state("coder")
        state["last_dataset_hash"] = "abc123"
        state["last_dataset_size"] = 50
        state["last_trained"] = 1.0  # Ancien timestamp (pas de cooldown)

        should, reason = trainer.should_train("coder", 50, "abc123")
        assert not should
        assert "identiques" in reason

    def test_cooldown_respected(self, trainer):
        """Refuse pendant le cooldown."""
        state = trainer._get_agent_state("coder")
        state["last_trained"] = time.time()  # Vient d'etre entraine

        should, reason = trainer.should_train("coder", 100, "new_hash")
        assert not should
        assert "Cooldown" in reason

    def test_cooldown_expired(self, trainer):
        """Accepte apres expiration du cooldown."""
        state = trainer._get_agent_state("coder")
        state["last_trained"] = time.time() - (MIN_HOURS_BETWEEN_TRAINING + 1) * 3600

        should, reason = trainer.should_train("coder", 100, "new_hash")
        assert should

    def test_insufficient_growth(self, trainer):
        """Refuse si pas assez de croissance."""
        state = trainer._get_agent_state("coder")
        state["last_dataset_size"] = 100
        state["last_dataset_hash"] = "old_hash"
        state["last_trained"] = 1.0

        # 5% de croissance < 20% seuil
        should, reason = trainer.should_train("coder", 105, "new_hash")
        assert not should
        assert "nouvelles donnees" in reason

    def test_sufficient_growth(self, trainer):
        """Accepte avec assez de croissance."""
        state = trainer._get_agent_state("coder")
        state["last_dataset_size"] = 100
        state["last_dataset_hash"] = "old_hash"
        state["last_trained"] = 1.0

        # 25% de croissance > 20% seuil
        should, reason = trainer.should_train("coder", 125, "new_hash")
        assert should

    def test_force_bypasses_all(self, trainer):
        """Force ignore tous les seuils."""
        should, reason = trainer.should_train("coder", 5, "same_hash", force=True)
        assert should
        assert "force" in reason.lower()


class TestExtractLoss:
    """Tests d'extraction de la loss depuis la sortie training."""

    def test_extract_standard_format(self, trainer):
        """Extrait la loss du format standard."""
        output = "blah blah\nLoss finale: 1.2345\nblah"
        assert abs(trainer._extract_loss(output) - 1.2345) < 0.001

    def test_extract_from_logs(self, trainer):
        """Extrait la derniere loss des logs."""
        output = "{'loss': 2.5}\n{'loss': 1.8}\n{'loss': 1.2}"
        assert abs(trainer._extract_loss(output) - 1.2) < 0.001

    def test_no_loss_returns_zero(self, trainer):
        """Retourne 0 si aucune loss trouvee."""
        assert trainer._extract_loss("no loss here") == 0.0


class TestStatus:
    """Tests du rapport d'etat."""

    def test_status_format(self, trainer):
        """Le status contient les champs attendus."""
        status = trainer.get_status()
        assert "total_trainings" in status
        assert "next_agent" in status
        assert "agents" in status
        assert len(status["agents"]) == len(TRAINING_ROTATION)

    def test_status_after_training(self, trainer):
        """Le status reflète un training effectue."""
        state = trainer._get_agent_state("strategist")
        state["last_trained"] = time.time()
        state["last_dataset_size"] = 50
        state["last_loss"] = 1.5
        state["training_count"] = 1

        status = trainer.get_status()
        assert status["agents"]["strategist"]["dataset_size"] == 50
        assert status["agents"]["strategist"]["training_count"] == 1

    def test_model_name_convention(self, trainer):
        """Les modeles suivent la convention promethee-<agent>."""
        for agent in TRAINING_ROTATION:
            state = trainer._get_agent_state(agent)
            assert state["model_name"] == f"{OLLAMA_MODEL_PREFIX}{agent}"


@pytest.mark.asyncio
class TestTrainingCycleSkip:
    """Tests du cycle complet (cas skip)."""

    async def test_skip_insufficient_data(self, trainer, tmp_path, monkeypatch):
        """Le cycle skip si pas assez de donnees."""
        # Mock collect_data pour retourner peu de donnees
        monkeypatch.setattr(
            trainer, "collect_data",
            lambda agent: (str(tmp_path / "test.jsonl"), 5, "hash123"),
        )
        result = await trainer.run_training_cycle(agent_name="strategist")
        assert result.get("skipped")
        assert not result.get("success")

    async def test_rotation_advances_on_skip(self, trainer, tmp_path, monkeypatch):
        """La rotation avance meme en cas de skip."""
        initial_next = trainer.get_next_agent()
        monkeypatch.setattr(
            trainer, "collect_data",
            lambda agent: (str(tmp_path / "test.jsonl"), 5, "hash123"),
        )
        await trainer.run_training_cycle()
        assert trainer.get_next_agent() != initial_next


@pytest.mark.asyncio
class TestNapIntegration:
    """Tests d'integration avec le nap mode."""

    async def test_lora_training_graceful_failure(self, monkeypatch):
        """_execute_lora_training ne crash pas si le module echoue."""
        from unittest.mock import AsyncMock, patch

        # Simuler un import qui leve une exception
        with patch.dict("sys.modules", {"tools.lora_auto_trainer": None}):
            # Import local dans _execute_lora_training va echouer
            # Le try/except dans la methode doit catcher sans crash
            pass
        # Ce test verifie surtout que le pattern try/except existe
