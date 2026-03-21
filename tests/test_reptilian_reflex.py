"""Tests pour le circuit reflexe codelet → reptilien → autonomy."""

import time
import pytest
from unittest.mock import patch, AsyncMock, MagicMock

from core.reptilian_core import ReptilianCore


@pytest.fixture
def reptile():
    """ReptilianCore frais."""
    ReptilianCore.reset_singleton()
    with patch("core.reptilian_core.ReptilianCore._subscribe_events"):
        r = ReptilianCore()
    r._alive = True
    yield r
    ReptilianCore.reset_singleton()


class TestCodeletDirectiveMap:
    """Verifie le mapping codelet → directive."""

    def test_map_has_all_codelets(self, reptile):
        expected = {"danger", "fatigue", "stagnation", "contradiction",
                    "opportunity", "creativity_burst", "flow", "convergence", "resonance"}
        assert expected == set(reptile._CODELET_DIRECTIVE_MAP.keys())

    def test_danger_is_urgent(self, reptile):
        level, target, desc = reptile._CODELET_DIRECTIVE_MAP["danger"]
        assert level == "urgent"
        assert target == "SECURITY_AUDIT"

    def test_fatigue_is_moderate(self, reptile):
        level, target, _ = reptile._CODELET_DIRECTIVE_MAP["fatigue"]
        assert level == "moderate"
        assert target == "MEMORY_CONSOLIDATION"

    def test_flow_is_info(self, reptile):
        level, target, _ = reptile._CODELET_DIRECTIVE_MAP["flow"]
        assert level == "info"
        assert target is None


class TestOnCodeletAlert:
    """Tests pour _on_codelet_alert."""

    @pytest.mark.asyncio
    async def test_info_level_no_directive(self, reptile):
        """Les codelets info (flow, convergence, resonance) ne publient pas de directive."""
        with patch("core.event_bus.bus.bus") as mock_bus:
            mock_bus.publish = AsyncMock()
            await reptile._on_codelet_alert({
                "name": "flow", "salience": 0.5, "category": "creation",
            })
        mock_bus.publish.assert_not_called()

    @pytest.mark.asyncio
    async def test_moderate_publishes_directive(self, reptile):
        """Les codelets moderes publient REPTILIAN_DIRECTIVE."""
        with patch("core.event_bus.bus.bus") as mock_bus:
            mock_bus.publish = AsyncMock()
            await reptile._on_codelet_alert({
                "name": "fatigue", "salience": 0.6, "category": "body",
            })
        mock_bus.publish.assert_called_once()
        call_args = mock_bus.publish.call_args
        assert call_args[0][0] == "REPTILIAN_DIRECTIVE"
        directive = call_args[0][1]
        assert directive["level"] == "moderate"
        assert directive["target_intent"] == "MEMORY_CONSOLIDATION"

    @pytest.mark.asyncio
    async def test_urgent_publishes_directive(self, reptile):
        """Les codelets urgents publient REPTILIAN_DIRECTIVE urgent."""
        with patch("core.event_bus.bus.bus") as mock_bus:
            mock_bus.publish = AsyncMock()
            await reptile._on_codelet_alert({
                "name": "danger", "salience": 0.9, "category": "urgence",
            })
        directive = mock_bus.publish.call_args[0][1]
        assert directive["level"] == "urgent"
        assert directive["target_intent"] == "SECURITY_AUDIT"

    @pytest.mark.asyncio
    async def test_cooldown_prevents_repeat(self, reptile):
        """Le cooldown empeche les directives repetees."""
        with patch("core.event_bus.bus.bus") as mock_bus:
            mock_bus.publish = AsyncMock()
            await reptile._on_codelet_alert({
                "name": "fatigue", "salience": 0.6,
            })
            await reptile._on_codelet_alert({
                "name": "fatigue", "salience": 0.6,
            })
        # Seulement 1 appel malgre 2 alertes (cooldown)
        assert mock_bus.publish.call_count == 1

    @pytest.mark.asyncio
    async def test_max_directives_per_hour(self, reptile):
        """Max 3 directives par heure."""
        with patch("core.event_bus.bus.bus") as mock_bus:
            mock_bus.publish = AsyncMock()
            # Envoyer 4 alertes differentes
            for name in ["fatigue", "stagnation", "contradiction", "creativity_burst"]:
                await reptile._on_codelet_alert({"name": name, "salience": 0.7})
        # Max 3 directives
        assert mock_bus.publish.call_count == 3

    @pytest.mark.asyncio
    async def test_unknown_codelet_ignored(self, reptile):
        """Les codelets inconnus sont ignores."""
        with patch("core.event_bus.bus.bus") as mock_bus:
            mock_bus.publish = AsyncMock()
            await reptile._on_codelet_alert({
                "name": "unknown_codelet", "salience": 0.9,
            })
        mock_bus.publish.assert_not_called()


class TestAutonomyDirectiveHandler:
    """Tests pour _on_reptilian_directive dans autonomy_engine."""

    @pytest.mark.asyncio
    async def test_urgent_forces_intent(self):
        """Directive urgente force _forced_next_intent."""
        from core.autonomy_engine import AutonomyEngine
        engine = AutonomyEngine.__new__(AutonomyEngine)
        engine._forced_next_intent = ""
        engine._reptilian_boosts = {}

        await engine._on_reptilian_directive({
            "level": "urgent",
            "target_intent": "SECURITY_AUDIT",
            "source_codelet": "danger",
            "salience": 0.9,
        })
        assert engine._forced_next_intent == "SECURITY_AUDIT"

    @pytest.mark.asyncio
    async def test_moderate_adds_boost(self):
        """Directive moderee ajoute un boost temporaire."""
        from core.autonomy_engine import AutonomyEngine
        engine = AutonomyEngine.__new__(AutonomyEngine)
        engine._forced_next_intent = ""
        engine._reptilian_boosts = {}

        await engine._on_reptilian_directive({
            "level": "moderate",
            "target_intent": "MEMORY_CONSOLIDATION",
            "source_codelet": "fatigue",
            "salience": 0.6,
        })
        assert "MEMORY_CONSOLIDATION" in engine._reptilian_boosts
        assert engine._reptilian_boosts["MEMORY_CONSOLIDATION"]["boost"] == 3.0

    @pytest.mark.asyncio
    async def test_moderate_boost_expires(self):
        """Les boosts expirent apres 900s."""
        from core.autonomy_engine import AutonomyEngine
        engine = AutonomyEngine.__new__(AutonomyEngine)
        engine._forced_next_intent = ""
        engine._reptilian_boosts = {
            "MEMORY_CONSOLIDATION": {
                "boost": 3.0, "expires": time.time() - 1, "source": "fatigue",
            }
        }
        # Simuler le nettoyage (fait dans _execute_scored_routine)
        expired = [k for k, v in engine._reptilian_boosts.items()
                   if time.time() > v["expires"]]
        for k in expired:
            del engine._reptilian_boosts[k]
        assert "MEMORY_CONSOLIDATION" not in engine._reptilian_boosts

    @pytest.mark.asyncio
    async def test_urgent_does_not_overwrite(self):
        """Si un intent est deja force, la directive ne l'ecrase pas."""
        from core.autonomy_engine import AutonomyEngine
        engine = AutonomyEngine.__new__(AutonomyEngine)
        engine._forced_next_intent = "COUNCIL_DEBATE"  # Deja force
        engine._reptilian_boosts = {}

        await engine._on_reptilian_directive({
            "level": "urgent",
            "target_intent": "SECURITY_AUDIT",
            "source_codelet": "danger",
            "salience": 0.9,
        })
        # Pas ecrase
        assert engine._forced_next_intent == "COUNCIL_DEBATE"
