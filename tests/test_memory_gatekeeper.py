"""Tests pour core/memory_gatekeeper.py — filtre qualite deterministe."""
import pytest
from core.memory_gatekeeper import (
    evaluate,
    _unique_words_ratio,
    _is_noise,
    _info_density,
    _bigram_overlap,
)


class TestUniqueWordsRatio:
    def test_all_unique(self):
        assert _unique_words_ratio("un deux trois quatre cinq") == 1.0

    def test_all_same(self):
        ratio = _unique_words_ratio("test test test test test test")
        assert ratio < 0.2

    def test_mixed(self):
        ratio = _unique_words_ratio("le chat le chien le lapin la souris")
        assert 0.5 < ratio < 0.9

    def test_short_text(self):
        # < 5 mots -> retourne 1.0
        assert _unique_words_ratio("un deux") == 1.0

    def test_empty(self):
        assert _unique_words_ratio("") == 1.0


class TestIsNoise:
    def test_ras(self):
        assert _is_noise("R.A.S")
        assert _is_noise("R.A.S.")
        assert _is_noise("  R.A.S  ")

    def test_aucune_vulnerabilite(self):
        assert _is_noise("Aucune vulnérabilité détectée dans ce fichier.")

    def test_rien_a_signaler(self):
        assert _is_noise("Rien à signaler pour ce module.")

    def test_tout_en_ordre(self):
        assert _is_noise("Tout est en ordre dans le système.")

    def test_short_text(self):
        assert _is_noise("ok")
        assert _is_noise("OK.")

    def test_real_content(self):
        text = (
            "Le module orchestrator.py utilise un pattern singleton pour le dispatch "
            "multi-agents. La methode dispatch_task importe localement le Summoner "
            "pour eviter les imports circulaires."
        )
        assert not _is_noise(text)


class TestInfoDensity:
    def test_technical_text(self):
        text = "python async agent orchestrator bus event pipeline chromadb ollama"
        density = _info_density(text)
        assert density > 0.5

    def test_non_technical_text(self):
        text = "le la les de du des un une et en est que qui dans pour pas"
        density = _info_density(text)
        assert density < 0.05

    def test_mixed_text(self):
        text = "le module python utilise un bus async pour les agents du pipeline"
        density = _info_density(text)
        assert 0.1 < density < 0.8

    def test_empty(self):
        assert _info_density("") == 0.0


class TestBigramOverlap:
    def test_identical(self):
        text = "le chat mange la souris dans le jardin"
        overlap = _bigram_overlap(text, text)
        assert overlap == 1.0

    def test_completely_different(self):
        text_a = "le chat mange la souris"
        text_b = "python async agent orchestrator pipeline"
        overlap = _bigram_overlap(text_a, text_b)
        assert overlap < 0.1

    def test_partial_overlap(self):
        text_a = "le module python agent utilise un bus"
        text_b = "le module python config utilise un cache"
        overlap = _bigram_overlap(text_a, text_b)
        assert 0.2 < overlap < 0.8

    def test_empty(self):
        assert _bigram_overlap("", "test") == 0.0
        assert _bigram_overlap("test", "") == 0.0

    def test_single_word(self):
        assert _bigram_overlap("un", "deux") == 0.0


class TestEvaluate:
    def test_accept_good_text(self):
        text = (
            "Le module orchestrator.py utilise le pattern singleton pour gerer "
            "le dispatch multi-agents dans le pipeline de Promethee. La methode "
            "dispatch_task importe localement le Summoner pour eviter les imports circulaires. "
            "Cela permet un routage intelligent des missions vers les agents specialises."
        )
        result = evaluate(text)
        assert result["accept"] is True
        assert result["quality_score"] > 0.0

    def test_reject_empty(self):
        result = evaluate("")
        assert result["accept"] is False
        assert "vide" in result["reason"]

    def test_reject_repetition(self):
        # Texte tres repetitif -> ratio mots uniques < 0.30
        text = " ".join(["test erreur debug"] * 20)
        result = evaluate(text)
        assert result["accept"] is False
        assert "repetition" in result["reason"]

    def test_reject_noise_pattern(self):
        text = "R.A.S. Rien a signaler pour le moment dans ce module du projet."
        result = evaluate(text)
        assert result["accept"] is False
        assert "bruit" in result["reason"]

    def test_reject_bigram_doublon(self):
        text = (
            "Le module orchestrator utilise un pattern singleton pour le dispatch "
            "multi-agents avec un bus evenementiel asynchrone en Python."
        )
        existing = (
            "Le module orchestrator utilise un pattern singleton pour le dispatch "
            "multi-agents avec un bus evenementiel asynchrone en Python version 3.11."
        )
        result = evaluate(text, existing_text=existing, existing_distance=0.05)
        assert result["accept"] is False
        assert "doublon" in result["reason"]

    def test_accept_when_existing_far(self):
        text = (
            "Le module orchestrator.py utilise le pattern singleton pour gerer "
            "le dispatch multi-agents dans le pipeline de Promethee."
        )
        existing = "Complement different texte sur securite agent ChromaDB memoire vectorielle."
        result = evaluate(text, existing_text=existing, existing_distance=0.8)
        assert result["accept"] is True

    def test_accept_no_existing(self):
        text = (
            "Le module orchestrator.py utilise le pattern singleton pour gerer "
            "le dispatch multi-agents dans le pipeline de Promethee."
        )
        result = evaluate(text, existing_text=None, existing_distance=1.0)
        assert result["accept"] is True

    def test_reject_low_density_long_text(self):
        # Long texte sans aucun mot technique
        text = " ".join(["le la les de du des un une et en est que qui"] * 5)
        result = evaluate(text)
        # Peut etre rejete par repetition OU densite basse
        assert result["accept"] is False
