# tests/test_log_rotation.py — Tests pour _SafeTimedRotatingFileHandler
import logging
from unittest.mock import patch, MagicMock


class TestSafeTimedRotatingFileHandler:
    """Vérifie que _SafeTimedRotatingFileHandler survive à PermissionError."""

    def test_doRollover_permission_error_is_caught(self):
        """Si le fichier est verrouillé (Windows), doRollover skip sans crash."""
        from main import _SafeTimedRotatingFileHandler

        handler = _SafeTimedRotatingFileHandler.__new__(_SafeTimedRotatingFileHandler)
        handler.stream = None
        handler.baseFilename = "fake.log"

        with patch.object(
            logging.handlers.TimedRotatingFileHandler,
            "doRollover",
            side_effect=PermissionError("locked by _TeeStream"),
        ):
            # Ne doit PAS lever d'exception
            handler.doRollover()

    def test_doRollover_success_passthrough(self):
        """Si le fichier n'est pas verrouillé, la rotation fonctionne normalement."""
        from main import _SafeTimedRotatingFileHandler

        handler = _SafeTimedRotatingFileHandler.__new__(_SafeTimedRotatingFileHandler)
        handler.stream = None
        handler.baseFilename = "fake.log"

        with patch.object(
            logging.handlers.TimedRotatingFileHandler,
            "doRollover",
        ) as mock_rollover:
            handler.doRollover()
            mock_rollover.assert_called_once()

    def test_emit_continues_after_failed_rollover(self):
        """Après un PermissionError dans doRollover, emit() continue à fonctionner."""
        from main import _SafeTimedRotatingFileHandler

        handler = _SafeTimedRotatingFileHandler.__new__(_SafeTimedRotatingFileHandler)
        handler.stream = None
        handler.baseFilename = "fake.log"

        with patch.object(
            logging.handlers.TimedRotatingFileHandler,
            "doRollover",
            side_effect=PermissionError("locked"),
        ):
            handler.doRollover()

        # emit() via le parent devrait fonctionner ensuite
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="test après rollover échoué", args=(), exc_info=None,
        )
        with patch.object(
            logging.handlers.TimedRotatingFileHandler,
            "emit",
        ) as mock_emit:
            handler.emit(record)
            mock_emit.assert_called_once_with(record)
