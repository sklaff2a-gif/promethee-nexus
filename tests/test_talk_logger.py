import pytest
import asyncio
import os
import tempfile
from unittest.mock import patch
from datetime import datetime

from core.event_bus.bus import bus
from core import talk_logger


@pytest.fixture
def talks_tmpdir(tmp_path):
    """Crée un dossier temporaire et redirige le talk_logger dessus."""
    talks_dir = str(tmp_path / "talks")
    with patch.object(talk_logger, "TALKS_DIR", talks_dir):
        talk_logger.start(talks_dir)
        yield talks_dir
        talk_logger.stop()


def _read_today_file(talks_dir):
    """Lit le contenu du fichier du jour dans le dossier donné."""
    today = datetime.now().strftime("%Y-%m-%d")
    filepath = os.path.join(talks_dir, f"talk_{today}.txt")
    if not os.path.exists(filepath):
        return ""
    with open(filepath, "r", encoding="utf-8") as f:
        return f.read()


class TestTalkLoggerCreationDossier:

    def test_start_cree_le_dossier(self, tmp_path):
        """start() doit créer le dossier s'il n'existe pas."""
        talks_dir = str(tmp_path / "nouveau_dossier" / "talks")
        assert not os.path.exists(talks_dir)
        with patch.object(talk_logger, "TALKS_DIR", talks_dir):
            talk_logger.start(talks_dir)
            talk_logger.stop()
        assert os.path.isdir(talks_dir)


class TestTalkLoggerUserCommand:

    @pytest.mark.asyncio
    async def test_user_command_archivee(self, talks_tmpdir):
        """Un USER_COMMAND doit apparaître dans le fichier du jour."""
        await bus.publish("USER_COMMAND", {"mission": "Coder: crée hello.py"})

        content = _read_today_file(talks_tmpdir)
        assert "USER >" in content
        assert "Coder: crée hello.py" in content


class TestTalkLoggerAgentResponse:

    @pytest.mark.asyncio
    async def test_agent_response_archivee_integralement(self, talks_tmpdir):
        """La réponse d'un agent doit être archivée sans troncature, code inclus."""
        code_block = '```python\nfrom flask import Flask\napp = Flask(__name__)\n\n@app.route("/")\ndef hello():\n    return "Hello World"\n\nif __name__ == "__main__":\n    app.run()\n```'
        await bus.publish("AGENT_RESPONSE", {
            "agent": "coder",
            "content": f"Voici le serveur Flask demandé :\n{code_block}",
        })

        content = _read_today_file(talks_tmpdir)
        assert "CODER >" in content
        assert "from flask import Flask" in content
        assert 'app = Flask(__name__)' in content
        assert "--- FIN CODER ---" in content


class TestTalkLoggerCodePreservation:

    @pytest.mark.asyncio
    async def test_code_long_non_tronque(self, talks_tmpdir):
        """Un bloc de code de 200 lignes doit être archivé intégralement."""
        lines = [f"    print('ligne {i}')" for i in range(200)]
        long_code = "def mega_function():\n" + "\n".join(lines)
        await bus.publish("AGENT_RESPONSE", {
            "agent": "coder",
            "content": f"```python\n{long_code}\n```",
        })

        content = _read_today_file(talks_tmpdir)
        assert "ligne 0" in content
        assert "ligne 199" in content


class TestTalkLoggerCouncil:

    @pytest.mark.asyncio
    async def test_council_complet_archive(self, talks_tmpdir):
        """Les 3 phases du conseil (start, turn, end) doivent être archivées."""
        await bus.publish("COUNCIL_START", {
            "participants": ["coder", "architect"],
            "mission": "Créer une API REST",
            "max_rounds": 3,
        })
        await bus.publish("COUNCIL_TURN", {
            "agent": "coder",
            "round": 1,
            "max_rounds": 3,
            "content": "Je propose FastAPI avec SQLAlchemy.",
        })
        await bus.publish("COUNCIL_END", {
            "status": "consensus",
            "rounds_used": 1,
        })

        content = _read_today_file(talks_tmpdir)
        assert "CONSEIL OUVERT" in content
        assert "CODER, ARCHITECT" in content
        assert "Je propose FastAPI" in content
        assert "CONSEIL FERMÉ" in content
        assert "CONSENSUS" in content


class TestTalkLoggerStream:

    @pytest.mark.asyncio
    async def test_stream_start_et_end_archives(self, talks_tmpdir):
        """Les signaux start/end du stream doivent être archivés, pas les chunks."""
        await bus.publish("AGENT_STREAM", {
            "agent": "coder", "stream_id": "abc123",
            "chunk": "", "done": False, "status": "start",
        })
        # Chunk intermédiaire (ne doit PAS être archivé)
        await bus.publish("AGENT_STREAM", {
            "agent": "coder", "stream_id": "abc123",
            "chunk": "Hello", "done": False,
        })
        await bus.publish("AGENT_STREAM", {
            "agent": "coder", "stream_id": "abc123",
            "chunk": "", "done": True, "status": "end",
        })

        content = _read_today_file(talks_tmpdir)
        assert "STREAM START" in content
        assert "STREAM END" in content
        # Le chunk intermédiaire ne doit pas apparaître comme ligne séparée
        lines = [l for l in content.strip().split("\n") if l.strip()]
        assert len(lines) == 2  # start + end uniquement


class TestTalkLoggerFiltrage:

    @pytest.mark.asyncio
    async def test_evenement_non_suivi_ignore(self, talks_tmpdir):
        """Un événement hors TRACKED_EVENTS ne doit rien écrire."""
        await bus.publish("SYSTEM_OVERRIDE", {"active": True})

        content = _read_today_file(talks_tmpdir)
        assert content == ""


class TestTalkLoggerFichierQuotidien:

    def test_nom_fichier_contient_date_du_jour(self, talks_tmpdir):
        """Le nom de fichier doit correspondre à la date du jour."""
        today = datetime.now().strftime("%Y-%m-%d")
        filepath = talk_logger._get_today_file()
        assert today in os.path.basename(filepath)
        assert filepath.endswith(".txt")
