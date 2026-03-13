"""Tests pour core/visual_cortex.py — Cortex Visuel."""
import json
import os
import pytest
from unittest.mock import patch, AsyncMock, MagicMock
import sys

# Mock core.base_agent avant import pour eviter les dependances lourdes
_mock_base_agent = sys.modules.get("core.base_agent")
if _mock_base_agent is None:
    mock_base = MagicMock()
    mock_base.BaseAgent = type("BaseAgent", (), {
        "__init__": lambda self, **kw: None,
        "generate_content": AsyncMock(return_value=""),
        "name": "test",
    })
    mock_base.gpu_scheduler = MagicMock()
    mock_base.gpu_scheduler.access = MagicMock(return_value=MagicMock(
        __aenter__=AsyncMock(), __aexit__=AsyncMock()
    ))
    sys.modules.setdefault("core.base_agent", mock_base)

import core.visual_cortex as mod
from core.visual_cortex import VisualCortex, EMOTION_DRIVE_MAP, IMAGE_EXTENSIONS


@pytest.fixture(autouse=True)
def reset_singleton():
    """Reset le singleton entre chaque test."""
    VisualCortex.reset_singleton()
    yield
    VisualCortex.reset_singleton()


@pytest.fixture
def cortex(tmp_path, monkeypatch):
    """Cree un cortex visuel isole."""
    photos_dir = tmp_path / "photos"
    photos_dir.mkdir()
    state_file = str(tmp_path / "visual_state.json")
    obs_dir = str(tmp_path / "observations")

    monkeypatch.setattr(mod, "PHOTOS_DIR_DEFAULT", str(photos_dir))
    monkeypatch.setattr(mod, "VISUAL_STATE_FILE", state_file)
    monkeypatch.setattr(mod, "OBSERVATIONS_DIR", obs_dir)

    vc = VisualCortex()
    vc._photos_dir = str(photos_dir)
    vc._state_file = state_file
    vc._observations_dir = obs_dir
    return vc


def _create_test_image(path, content=b"fake_image_data_12345"):
    """Cree un fichier image factice."""
    with open(path, "wb") as f:
        f.write(content)


# ── Scan ─────────────────────────────────────────────────────────────────

class TestScanPhotos:
    def test_empty_dir(self, cortex):
        stats = cortex.scan_photos()
        assert stats["total"] == 0
        assert stats["unseen"] == 0

    def test_with_photos(self, cortex):
        _create_test_image(os.path.join(cortex._photos_dir, "photo1.jpg"))
        _create_test_image(os.path.join(cortex._photos_dir, "photo2.png"))
        stats = cortex.scan_photos()
        assert stats["total"] == 2
        assert stats["unseen"] == 2

    def test_ignores_non_images(self, cortex):
        with open(os.path.join(cortex._photos_dir, "readme.txt"), "w") as f:
            f.write("not an image")
        stats = cortex.scan_photos()
        assert stats["total"] == 0

    def test_subdirectory_scan(self, cortex):
        sub = os.path.join(cortex._photos_dir, "famille")
        os.makedirs(sub)
        _create_test_image(os.path.join(sub, "photo.jpg"))
        stats = cortex.scan_photos()
        assert stats["total"] == 1

    def test_all_extensions(self, cortex):
        for ext in IMAGE_EXTENSIONS:
            _create_test_image(os.path.join(cortex._photos_dir, f"test{ext}"))
        stats = cortex.scan_photos()
        assert stats["total"] == len(IMAGE_EXTENSIONS)

    def test_seen_vs_unseen(self, cortex):
        path = os.path.join(cortex._photos_dir, "seen.jpg")
        _create_test_image(path)
        sha = cortex._hash_file(path)
        cortex._seen_photos[sha] = {"path": "seen.jpg", "last_seen": 0, "times_seen": 1}
        stats = cortex.scan_photos()
        assert stats["total"] == 1
        assert stats["unseen"] == 0
        assert stats["seen"] == 1


# ── Photo count ──────────────────────────────────────────────────────────

class TestPhotoCount:
    def test_no_photos(self, cortex):
        assert cortex.get_photo_count() == 0

    def test_counts_unseen_only(self, cortex):
        path1 = os.path.join(cortex._photos_dir, "a.jpg")
        path2 = os.path.join(cortex._photos_dir, "b.png")
        _create_test_image(path1)
        _create_test_image(path2, content=b"different_content")
        sha1 = cortex._hash_file(path1)
        cortex._seen_photos[sha1] = {"path": "a.jpg", "last_seen": 0, "times_seen": 1}
        assert cortex.get_photo_count() == 1

    def test_nonexistent_dir(self, cortex):
        cortex._photos_dir = "/nonexistent/path"
        assert cortex.get_photo_count() == 0


# ── Pick photo ───────────────────────────────────────────────────────────

class TestPickPhoto:
    def test_picks_unseen(self, cortex):
        _create_test_image(os.path.join(cortex._photos_dir, "new.jpg"))
        picked = cortex._pick_photo()
        assert picked is not None
        assert "new.jpg" in picked

    def test_empty_returns_none(self, cortex):
        assert cortex._pick_photo() is None

    def test_all_seen_recently_returns_none(self, cortex):
        import time
        path = os.path.join(cortex._photos_dir, "seen.jpg")
        _create_test_image(path)
        sha = cortex._hash_file(path)
        cortex._seen_photos[sha] = {
            "path": "seen.jpg",
            "last_seen": time.time(),  # Vue maintenant
            "times_seen": 1,
        }
        assert cortex._pick_photo() is None

    def test_revisit_after_cooldown(self, cortex):
        import time
        path = os.path.join(cortex._photos_dir, "old.jpg")
        _create_test_image(path)
        sha = cortex._hash_file(path)
        # Vue il y a longtemps
        cortex._seen_photos[sha] = {
            "path": "old.jpg",
            "last_seen": time.time() - (80 * 3600),  # 80h ago > 72h cooldown
            "times_seen": 1,
        }
        picked = cortex._pick_photo()
        assert picked is not None


# ── Hash ─────────────────────────────────────────────────────────────────

class TestHash:
    def test_consistent(self, cortex):
        path = os.path.join(cortex._photos_dir, "test.jpg")
        _create_test_image(path)
        assert cortex._hash_file(path) == cortex._hash_file(path)

    def test_different_content(self, cortex):
        path1 = os.path.join(cortex._photos_dir, "a.jpg")
        path2 = os.path.join(cortex._photos_dir, "b.jpg")
        _create_test_image(path1, content=b"content_a")
        _create_test_image(path2, content=b"content_b")
        assert cortex._hash_file(path1) != cortex._hash_file(path2)

    def test_nonexistent_file(self, cortex):
        assert cortex._hash_file("/nonexistent") == "error"


# ── Emotion detection ────────────────────────────────────────────────────

class TestEmotionDetection:
    def test_detects_joie(self, cortex):
        text = "Cette image m'inspire une grande joie. Les visages sont joyeux."
        assert cortex._detect_emotion(text) == "joie"

    def test_detects_serenite(self, cortex):
        text = "**EMOTION**: serenite. Le paysage est paisible."
        assert cortex._detect_emotion(text) == "serenite"

    def test_default_curiosite(self, cortex):
        text = "Une scene quelconque sans emotion particuliere."
        assert cortex._detect_emotion(text) == "curiosite"

    def test_multiple_emotions_picks_strongest(self, cortex):
        text = "Je ressens de la nostalgie nostalgie nostalgie et un peu de joie."
        assert cortex._detect_emotion(text) == "nostalgie"

    def test_emotion_section_bonus(self, cortex):
        text = "Scene banale.\n**EMOTION**: tendresse absolue.\nFin."
        assert cortex._detect_emotion(text) == "tendresse"


# ── Observe ──────────────────────────────────────────────────────────────

class TestObserve:
    @pytest.mark.asyncio
    async def test_observe_new_photo(self, cortex):
        _create_test_image(os.path.join(cortex._photos_dir, "test.jpg"))

        mock_response = (
            "## Observation\n\n"
            "**SCENE**: Une famille reunie autour d'une table.\n"
            "**PERSONNES**: Trois personnes souriantes.\n"
            "**DETAILS**: Lumiere chaude, nappe blanche.\n"
            "**EMOTION**: joie\n"
            "**CONNEXION**: La joie partagee est universelle."
        )

        with patch.object(cortex, "_call_ollama_vision", new=AsyncMock(return_value=mock_response)):
            with patch.object(cortex, "_memorize_observation"):
                with patch("core.visual_cortex.bus.publish", new=AsyncMock()):
                    result = await cortex.observe()

        assert result is not None
        assert result["emotion"] == "joie"
        assert result["is_revisit"] is False
        assert result["times_seen"] == 1
        assert cortex._total_observations == 1

    @pytest.mark.asyncio
    async def test_observe_no_photos(self, cortex):
        result = await cortex.observe()
        assert result is None

    @pytest.mark.asyncio
    async def test_observe_session_limit(self, cortex):
        cortex._session_observations = 3  # MAX_OBSERVATIONS_PER_SESSION
        _create_test_image(os.path.join(cortex._photos_dir, "test.jpg"))
        result = await cortex.observe()
        assert result is None

    @pytest.mark.asyncio
    async def test_observe_empty_response(self, cortex):
        _create_test_image(os.path.join(cortex._photos_dir, "test.jpg"))
        with patch.object(cortex, "_call_ollama_vision", new=AsyncMock(return_value="court")):
            result = await cortex.observe()
        assert result is None

    @pytest.mark.asyncio
    async def test_observe_revisit(self, cortex):
        import time
        path = os.path.join(cortex._photos_dir, "old.jpg")
        _create_test_image(path)
        sha = cortex._hash_file(path)
        cortex._seen_photos[sha] = {
            "path": "old.jpg",
            "first_seen": time.time() - 300000,
            "last_seen": time.time() - 300000,  # 83h > 72h cooldown
            "times_seen": 1,
            "emotion": "curiosite",
            "observation": "Premiere observation de cette scene.",
            "favorite": False,
        }

        mock_response = (
            "En revoyant cette image, je remarque des details que j'avais manques. "
            "La lumiere du soir cree une ambiance de nostalgie que je n'avais pas perçue initialement."
        )

        with patch.object(cortex, "_call_ollama_vision", new=AsyncMock(return_value=mock_response)):
            with patch.object(cortex, "_memorize_observation"):
                with patch("core.visual_cortex.bus.publish", new=AsyncMock()):
                    result = await cortex.observe()

        assert result is not None
        assert result["is_revisit"] is True
        assert result["times_seen"] == 2

    @pytest.mark.asyncio
    async def test_observe_updates_state(self, cortex):
        _create_test_image(os.path.join(cortex._photos_dir, "test.jpg"))

        mock_response = "Scene magnifique avec une lumiere doree. **EMOTION**: emerveillement. Les couleurs sont extraordinaires et le moment capture est parfait."

        with patch.object(cortex, "_call_ollama_vision", new=AsyncMock(return_value=mock_response)):
            with patch.object(cortex, "_memorize_observation"):
                with patch("core.visual_cortex.bus.publish", new=AsyncMock()):
                    await cortex.observe()

        assert len(cortex._seen_photos) == 1
        assert len(cortex._emotion_history) == 1
        assert cortex._emotion_history[0]["emotion"] == "emerveillement"

    @pytest.mark.asyncio
    async def test_observe_llm_error(self, cortex):
        _create_test_image(os.path.join(cortex._photos_dir, "test.jpg"))
        with patch.object(cortex, "_call_ollama_vision", new=AsyncMock(side_effect=Exception("GPU busy"))):
            result = await cortex.observe()
        assert result is None


# ── Image encoding ───────────────────────────────────────────────────────

class TestEncodeImage:
    def test_encode(self, cortex):
        path = os.path.join(cortex._photos_dir, "test.jpg")
        _create_test_image(path, content=b"hello")
        import base64
        result = cortex._encode_image(path)
        assert result == base64.b64encode(b"hello").decode("utf-8")

    def test_encode_nonexistent(self, cortex):
        assert cortex._encode_image("/nonexistent") is None


# ── Strip thinking ───────────────────────────────────────────────────────

class TestStripThinking:
    def test_no_tags(self):
        assert VisualCortex._strip_thinking("Normal text") == "Normal text"

    def test_strips_tags(self):
        text = "<think>internal reasoning</think>Visible output"
        assert VisualCortex._strip_thinking(text) == "Visible output"

    def test_empty(self):
        assert VisualCortex._strip_thinking("") == ""

    def test_none(self):
        assert VisualCortex._strip_thinking(None) is None


# ── Persistance ──────────────────────────────────────────────────────────

class TestPersistence:
    def test_save_load(self, cortex):
        cortex._seen_photos["abc123"] = {
            "path": "test.jpg",
            "first_seen": 1000,
            "last_seen": 2000,
            "times_seen": 3,
            "emotion": "joie",
            "observation": "Belle image",
            "favorite": True,
        }
        cortex._total_observations = 5
        cortex._emotion_history = [{"emotion": "joie", "timestamp": 1000, "photo": "test.jpg"}]
        cortex._save()

        # Reset et reload
        cortex._seen_photos = {}
        cortex._total_observations = 0
        cortex._emotion_history = []
        cortex._load()

        assert len(cortex._seen_photos) == 1
        assert cortex._total_observations == 5
        assert len(cortex._emotion_history) == 1

    def test_load_missing_file(self, cortex):
        cortex._state_file = "/nonexistent/visual_state.json"
        cortex._load()  # Should not crash
        assert cortex._seen_photos == {}


# ── Visual summary ───────────────────────────────────────────────────────

class TestVisualSummary:
    def test_empty_summary(self, cortex):
        summary = cortex.get_visual_summary()
        assert summary["total_observations"] == 0
        assert summary["photos_connues"] == 0

    def test_with_data(self, cortex):
        cortex._total_observations = 10
        cortex._seen_photos = {"a": {}, "b": {}, "c": {}}
        cortex._emotion_history = [
            {"emotion": "joie", "timestamp": 1},
            {"emotion": "joie", "timestamp": 2},
            {"emotion": "curiosite", "timestamp": 3},
        ]
        summary = cortex.get_visual_summary()
        assert summary["total_observations"] == 10
        assert summary["photos_connues"] == 3
        assert summary["emotions_dominantes"]["joie"] == 2


# ── Narrative ────────────────────────────────────────────────────────────

class TestNarrative:
    def test_no_observations(self, cortex):
        assert cortex.get_narrative() == ""

    def test_few_observations(self, cortex):
        cortex._total_observations = 3
        narrative = cortex.get_narrative()
        assert "decouvre" in narrative

    def test_dominant_emotion(self, cortex):
        cortex._total_observations = 10
        cortex._emotion_history = [
            {"emotion": "tendresse", "timestamp": i} for i in range(10)
        ]
        narrative = cortex.get_narrative()
        assert "tendresse" in narrative


# ── Reset session ────────────────────────────────────────────────────────

class TestResetSession:
    def test_reset(self, cortex):
        cortex._session_observations = 3
        cortex.reset_session()
        assert cortex._session_observations == 0


# ── Observation file save ────────────────────────────────────────────────

class TestObservationFile:
    def test_saves_markdown(self, cortex):
        obs = {
            "photo_path": "famille/reunion.jpg",
            "sha256": "abc123",
            "timestamp": "2026-03-13T15:00:00",
            "is_revisit": False,
            "observation": "Une belle scene de famille.",
            "emotion": "tendresse",
            "times_seen": 1,
        }
        cortex._save_observation_file(obs)
        assert os.path.exists(cortex._observations_dir)
        files = os.listdir(cortex._observations_dir)
        assert len(files) == 1
        assert files[0].endswith("_tendresse.md")

        content = open(os.path.join(cortex._observations_dir, files[0]), encoding="utf-8").read()
        assert "tendresse" in content.lower()
        assert "reunion.jpg" in content


# ── Emotion drive mapping ────────────────────────────────────────────────

class TestEmotionDriveMap:
    def test_all_emotions_have_drives(self):
        for emotion, drives in EMOTION_DRIVE_MAP.items():
            assert isinstance(drives, dict)
            assert len(drives) > 0

    def test_known_emotions(self):
        expected = {"joie", "serenite", "nostalgie", "curiosite",
                    "melancolie", "emerveillement", "tendresse", "mystere"}
        assert set(EMOTION_DRIVE_MAP.keys()) == expected


# ── Big image ignored ────────────────────────────────────────────────────

class TestBigImage:
    def test_big_image_skipped(self, cortex):
        path = os.path.join(cortex._photos_dir, "huge.jpg")
        # Creer un fichier > MAX_IMAGE_SIZE_MB (simuler avec monkeypatch)
        _create_test_image(path, content=b"x" * 100)
        # Monkey-patch la limite
        import core.visual_cortex as vc_mod
        old_max = vc_mod.MAX_IMAGE_SIZE_MB
        vc_mod.MAX_IMAGE_SIZE_MB = 0.0001 / 1024  # Tres petit
        try:
            stats = cortex.scan_photos()
            assert stats["total"] == 0
        finally:
            vc_mod.MAX_IMAGE_SIZE_MB = old_max
