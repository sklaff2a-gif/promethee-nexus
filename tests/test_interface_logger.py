import pytest
import asyncio
import os
import time
from unittest.mock import patch
from datetime import datetime

from core.event_bus.bus import bus
from core import interface_logger


@pytest.fixture
def iface_tmpdir(tmp_path):
    """Crée un dossier temporaire et redirige l'interface_logger dessus."""
    iface_dir = str(tmp_path / "interface")
    with patch.object(interface_logger, "INTERFACE_DIR", iface_dir):
        # Reset état interne
        interface_logger._active_streams.clear()
        interface_logger._recently_streamed.clear()
        interface_logger.start(iface_dir)
        yield iface_dir
        interface_logger.stop()


def _read_today_file(iface_dir):
    today = datetime.now().strftime("%Y-%m-%d")
    filepath = os.path.join(iface_dir, f"interface_{today}.txt")
    if not os.path.exists(filepath):
        return ""
    with open(filepath, "r", encoding="utf-8") as f:
        return f.read()


class TestInterfaceLoggerCreation:

    def test_start_cree_le_dossier(self, tmp_path):
        iface_dir = str(tmp_path / "nouveau" / "interface")
        assert not os.path.exists(iface_dir)
        with patch.object(interface_logger, "INTERFACE_DIR", iface_dir):
            interface_logger.start(iface_dir)
            interface_logger.stop()
        assert os.path.isdir(iface_dir)

    def test_nom_fichier_contient_date(self, iface_tmpdir):
        today = datetime.now().strftime("%Y-%m-%d")
        filepath = interface_logger._get_today_file()
        assert today in os.path.basename(filepath)
        assert filepath.endswith(".txt")


class TestInterfaceLoggerUserCommand:

    @pytest.mark.asyncio
    async def test_user_command_dans_dialogue(self, iface_tmpdir):
        await bus.publish("USER_COMMAND", {"mission": "analyse ce fichier"})
        content = _read_today_file(iface_tmpdir)
        assert "[DIALOGUE]" in content
        assert "[COMMANDER]" in content
        assert "analyse ce fichier" in content


class TestInterfaceLoggerDispatch:

    @pytest.mark.asyncio
    async def test_dispatch_dans_log_sys(self, iface_tmpdir):
        await bus.publish("AGENT_TASK_DISPATCH", {"target": "coder"})
        content = _read_today_file(iface_tmpdir)
        assert "[LOG:sys]" in content
        assert "[ROUTER]" in content
        assert "CODER" in content


class TestInterfaceLoggerThoughtStream:

    @pytest.mark.asyncio
    async def test_thought_success_dans_dialogue(self, iface_tmpdir):
        await bus.publish("THOUGHT_STREAM", {
            "agent": "coder", "content": "Code généré", "type": "success"
        })
        content = _read_today_file(iface_tmpdir)
        assert "[DIALOGUE]" in content
        assert "[CODER]" in content
        assert "Code généré" in content

    @pytest.mark.asyncio
    async def test_thought_error_dans_log_err(self, iface_tmpdir):
        await bus.publish("THOUGHT_STREAM", {
            "agent": "coder", "content": "Erreur critique", "type": "error"
        })
        content = _read_today_file(iface_tmpdir)
        assert "[LOG:err]" in content
        assert "Erreur critique" in content

    @pytest.mark.asyncio
    async def test_thought_info_dans_log_info(self, iface_tmpdir):
        await bus.publish("THOUGHT_STREAM", {
            "agent": "coder", "content": "Réflexion en cours", "type": "thought"
        })
        content = _read_today_file(iface_tmpdir)
        assert "[LOG:info]" in content
        assert "Réflexion en cours" in content


class TestInterfaceLoggerAgentResponse:

    @pytest.mark.asyncio
    async def test_response_dans_dialogue_et_log(self, iface_tmpdir):
        await bus.publish("AGENT_RESPONSE", {
            "agent": "architect", "content": "Structure validée"
        })
        content = _read_today_file(iface_tmpdir)
        assert "[DIALOGUE]" in content
        assert "[ARCHITECT]" in content
        assert "Structure validée" in content
        assert "[LOG:success]" in content
        assert "Tâche terminée avec succès" in content


class TestInterfaceLoggerStreaming:

    @pytest.mark.asyncio
    async def test_stream_accumule_chunks(self, iface_tmpdir):
        """Les chunks sont accumulés et écrits d'un bloc à la fin du stream."""
        await bus.publish("AGENT_STREAM", {
            "agent": "coder", "stream_id": "s1", "status": "start"
        })
        await bus.publish("AGENT_STREAM", {
            "agent": "coder", "stream_id": "s1", "chunk": "Hello "
        })
        await bus.publish("AGENT_STREAM", {
            "agent": "coder", "stream_id": "s1", "chunk": "World"
        })
        # Pas encore écrit
        content = _read_today_file(iface_tmpdir)
        assert "Hello" not in content

        # Fin du stream
        await bus.publish("AGENT_STREAM", {
            "agent": "coder", "stream_id": "s1", "done": True
        })
        content = _read_today_file(iface_tmpdir)
        assert "[DIALOGUE]" in content
        assert "Hello World" in content

    @pytest.mark.asyncio
    async def test_anti_doublon_apres_stream(self, iface_tmpdir):
        """Après un stream, l'AGENT_RESPONSE ne re-crée pas le dialogue (anti-doublon)."""
        await bus.publish("AGENT_STREAM", {
            "agent": "coder", "stream_id": "s2", "status": "start"
        })
        await bus.publish("AGENT_STREAM", {
            "agent": "coder", "stream_id": "s2", "chunk": "Contenu streamé"
        })
        await bus.publish("AGENT_STREAM", {
            "agent": "coder", "stream_id": "s2", "done": True
        })

        # L'AGENT_RESPONSE arrive juste après
        await bus.publish("AGENT_RESPONSE", {
            "agent": "coder", "content": "Contenu streamé"
        })

        content = _read_today_file(iface_tmpdir)
        # Le dialogue ne doit contenir "Contenu streamé" qu'UNE seule fois
        dialogue_lines = [l for l in content.split("\n") if "[DIALOGUE]" in l and "Contenu streamé" in l]
        assert len(dialogue_lines) == 1
        # Mais le LOG:success doit quand même apparaître
        assert "[LOG:success]" in content


class TestInterfaceLoggerArtifact:

    @pytest.mark.asyncio
    async def test_artifact_dans_dialogue_et_log(self, iface_tmpdir):
        await bus.publish("ARTIFACT_CREATED", {
            "filename": "hello.py", "filepath": "/projet/hello.py"
        })
        content = _read_today_file(iface_tmpdir)
        assert "[DIALOGUE]" in content
        assert "NOUVEAU FICHIER CREE" in content
        assert "hello.py" in content
        assert "[LOG:success]" in content
        assert "Ecriture disque" in content


class TestInterfaceLoggerCouncil:

    @pytest.mark.asyncio
    async def test_council_complet(self, iface_tmpdir):
        await bus.publish("COUNCIL_START", {
            "participants": ["coder", "architect"],
            "mission": "Refactorer le routeur",
            "max_rounds": 3,
        })
        await bus.publish("COUNCIL_TURN", {
            "agent": "coder", "round": 1, "max_rounds": 3,
            "content": "Je propose une refonte complète."
        })
        await bus.publish("COUNCIL_END", {
            "status": "consensus", "rounds_used": 1,
        })

        content = _read_today_file(iface_tmpdir)
        assert "CONSEIL OUVERT" in content
        assert "CODER, ARCHITECT" in content
        assert "[CONSEIL Tour 1/3]" in content
        assert "refonte complète" in content
        assert "CONSEIL FERME" in content
        assert "CONSENSUS ATTEINT" in content

    @pytest.mark.asyncio
    async def test_council_limite_tours(self, iface_tmpdir):
        await bus.publish("COUNCIL_END", {
            "status": "max_rounds", "rounds_used": 5,
        })
        content = _read_today_file(iface_tmpdir)
        assert "LIMITE DE TOURS" in content


class TestInterfaceLoggerCIPipeline:

    @pytest.mark.asyncio
    async def test_ci_pipeline_complet(self, iface_tmpdir):
        await bus.publish("CI_PIPELINE_START", {"filename": "utils.py"})
        await bus.publish("CI_PIPELINE_STEP", {
            "filename": "utils.py", "step": "COMPILE",
            "status": "success", "detail": "OK"
        })
        await bus.publish("CI_PIPELINE_RESULT", {
            "filename": "utils.py", "success": True, "detail": "Tests passés"
        })

        content = _read_today_file(iface_tmpdir)
        assert "Pipeline démarré" in content
        assert "[COMPILE]" in content
        assert "DEPLOYE" in content

    @pytest.mark.asyncio
    async def test_ci_pipeline_echec(self, iface_tmpdir):
        await bus.publish("CI_PIPELINE_RESULT", {
            "filename": "bad.py", "success": False, "detail": "SyntaxError"
        })
        content = _read_today_file(iface_tmpdir)
        assert "ROLLBACK" in content
        assert "[LOG:err]" in content


class TestInterfaceLoggerFiltrage:

    @pytest.mark.asyncio
    async def test_evenement_non_suivi_ignore(self, iface_tmpdir):
        await bus.publish("SYSTEM_OVERRIDE", {"active": True})
        await bus.publish("PSYCHE_UPDATE", {"system_average": {}})
        await bus.publish("AUTONOMY_HEARTBEAT", {})

        content = _read_today_file(iface_tmpdir)
        assert content == ""
