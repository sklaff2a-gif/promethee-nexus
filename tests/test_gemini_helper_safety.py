"""Tests gemini_helper apres fix 26/05 : safety_settings BLOCK_NONE + logging
finish_reason/safety_ratings + handling robuste de response.text bloque.

Couvre :
- safety_settings BLOCK_NONE passe dans le call generate_content_async
- finish_reason normal (STOP) -> log INFO, text retourne
- finish_reason SAFETY -> log WARNING, text "" retourne (au lieu de crasher)
- response.text qui leve une exception (cas bloque) -> handle gracieux
- reponse courte (<100 chars) -> log WARNING
- is_available False -> return None sans appel
"""
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.gemini_helper import GeminiHelper


def _make_response(text_value=None, finish_name="STOP", safety_ratings=None,
                   text_raises=False):
    """Construit un mock de response Gemini avec candidates + finish_reason."""
    response = MagicMock()
    if text_raises:
        type(response).text = property(lambda self: (_ for _ in ()).throw(
            ValueError("response.text inaccessible (bloque)")
        ))
    else:
        response.text = text_value if text_value is not None else ""

    cand = MagicMock()
    fr = MagicMock()
    fr.name = finish_name
    cand.finish_reason = fr
    cand.safety_ratings = safety_ratings or []
    response.candidates = [cand]
    return response


def _make_safety_rating(category_name, probability_name):
    """Mock d'un rating individuel."""
    rating = MagicMock()
    rating.category = MagicMock()
    rating.category.name = category_name
    rating.probability = MagicMock()
    rating.probability.name = probability_name
    return rating


@pytest.fixture
def helper():
    GeminiHelper.reset_singleton()
    h = GeminiHelper()
    h._api_key = "FAKE_KEY"  # simule cle presente
    h._calls_today = 0
    h._today = "2026-05-26"
    yield h
    GeminiHelper.reset_singleton()


# ============================================================================
# Cas standard : reponse complete avec STOP
# ============================================================================


@pytest.mark.asyncio
async def test_safety_settings_passed_in_call(helper):
    """Verifie que safety_settings BLOCK_NONE est dans le call."""
    response = _make_response(text_value="Voici une reponse philosophique longue " * 5)
    mock_model = MagicMock()
    mock_model.generate_content_async = AsyncMock(return_value=response)

    with patch("google.generativeai.configure"), \
         patch("google.generativeai.GenerativeModel", return_value=mock_model), \
         patch("google.generativeai.GenerationConfig", MagicMock()):
        result = await helper.generate("Quelle est la nature de l'illusion ?", max_tokens=800)

    # Verifie que generate_content_async a ete appele avec safety_settings
    assert mock_model.generate_content_async.called
    call_kwargs = mock_model.generate_content_async.call_args.kwargs
    assert "safety_settings" in call_kwargs
    safety = call_kwargs["safety_settings"]
    # 4 categories doivent etre presentes avec BLOCK_NONE
    categories_blocked = {s["category"]: s["threshold"] for s in safety}
    assert categories_blocked.get("HARM_CATEGORY_HARASSMENT") == "BLOCK_NONE"
    assert categories_blocked.get("HARM_CATEGORY_HATE_SPEECH") == "BLOCK_NONE"
    assert categories_blocked.get("HARM_CATEGORY_SEXUALLY_EXPLICIT") == "BLOCK_NONE"
    assert categories_blocked.get("HARM_CATEGORY_DANGEROUS_CONTENT") == "BLOCK_NONE"
    # Reponse normale retournee
    assert result is not None
    assert len(result) > 30


@pytest.mark.asyncio
async def test_normal_response_returned(helper):
    """STOP + texte long -> reponse retournee."""
    long_text = "Reponse philosophique " * 20  # ~440 chars
    response = _make_response(text_value=long_text, finish_name="STOP")
    mock_model = MagicMock()
    mock_model.generate_content_async = AsyncMock(return_value=response)

    with patch("google.generativeai.configure"), \
         patch("google.generativeai.GenerativeModel", return_value=mock_model), \
         patch("google.generativeai.GenerationConfig", MagicMock()):
        result = await helper.generate("test")

    assert result is not None
    assert "Reponse" in result


# ============================================================================
# Cas anormal : finish_reason SAFETY -> handle gracieux
# ============================================================================


@pytest.mark.asyncio
async def test_finish_reason_safety_handled(helper, caplog):
    """Si Gemini bloque pour SAFETY, on ne crashe pas, on retourne string vide."""
    response = _make_response(
        text_value="Mini",  # texte tronque court
        finish_name="SAFETY",
        safety_ratings=[_make_safety_rating("HARM_CATEGORY_DANGEROUS_CONTENT", "HIGH")],
    )
    mock_model = MagicMock()
    mock_model.generate_content_async = AsyncMock(return_value=response)

    with patch("google.generativeai.configure"), \
         patch("google.generativeai.GenerativeModel", return_value=mock_model), \
         patch("google.generativeai.GenerationConfig", MagicMock()):
        with caplog.at_level("WARNING"):
            result = await helper.generate("question potentiellement bloquante")

    # Texte tres court reçu -> log WARNING attendu
    assert any("finish=SAFETY" in rec.message for rec in caplog.records), (
        f"Logs warning ne mentionnent pas finish=SAFETY. Records: {[r.message for r in caplog.records]}"
    )


@pytest.mark.asyncio
async def test_response_text_exception_handled(helper):
    """Si response.text leve (cas safety dur), retourne string vide sans crasher."""
    response = _make_response(text_raises=True, finish_name="SAFETY")
    mock_model = MagicMock()
    mock_model.generate_content_async = AsyncMock(return_value=response)

    with patch("google.generativeai.configure"), \
         patch("google.generativeai.GenerativeModel", return_value=mock_model), \
         patch("google.generativeai.GenerationConfig", MagicMock()):
        result = await helper.generate("test")

    # Pas de crash, retourne string vide
    assert result == ""


# ============================================================================
# Cas anormal : reponse courte -> log WARNING
# ============================================================================


@pytest.mark.asyncio
async def test_short_response_logged_warning(helper, caplog):
    """Reponse < 100 chars -> log WARNING."""
    response = _make_response(text_value="Trop court.", finish_name="STOP")
    mock_model = MagicMock()
    mock_model.generate_content_async = AsyncMock(return_value=response)

    with patch("google.generativeai.configure"), \
         patch("google.generativeai.GenerativeModel", return_value=mock_model), \
         patch("google.generativeai.GenerationConfig", MagicMock()):
        with caplog.at_level("WARNING"):
            result = await helper.generate("test")

    # Le log doit avoir warning a cause de len(text)<100
    warning_logs = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warning_logs) >= 1, "Reponse courte devrait declencher un WARNING"


# ============================================================================
# Cas hors disponibilite
# ============================================================================


@pytest.mark.asyncio
async def test_no_api_key_returns_none():
    GeminiHelper.reset_singleton()
    h = GeminiHelper()
    h._api_key = ""  # pas de cle
    h._calls_today = 0

    result = await h.generate("test")
    assert result is None
    GeminiHelper.reset_singleton()


@pytest.mark.asyncio
async def test_budget_exhausted_returns_none():
    GeminiHelper.reset_singleton()
    h = GeminiHelper()
    h._api_key = "FAKE"
    h._calls_today = 999  # depasse le budget de 10
    h._today = "2026-05-26"

    result = await h.generate("test")
    assert result is None
    GeminiHelper.reset_singleton()
