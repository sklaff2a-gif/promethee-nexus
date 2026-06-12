# -*- coding: utf-8 -*-
"""Test fiche technique (2026-06-04) — ancrage du substrat reel dans le prompt
systeme du chat, pour corriger la divagation de l'ex46 (180 millions / Llama-3.1).
Verifie les faits VERIFIES (config.py + ollama list + nvidia-smi) et la posture
'information externe' (anti-introspection) + le garde-fou Modele C."""
import re

from core.chat_engine import _FICHE_TECHNIQUE


class TestFaitsVerifies:
    def test_ordre_de_grandeur_milliards(self):
        # le coeur du fix : milliards, jamais millions
        assert "MILLIARD" in _FICHE_TECHNIQUE
        assert "180 millions" in _FICHE_TECHNIQUE  # cite comme contre-exemple faux

    def test_modeles_reels(self):
        # Le generaliste cite doit etre le modele REEL du chat (dynamique,
        # suit la bascule LOCAL_GENERALIST_MODEL du 08/06), pas un nom fige.
        from core.chat_engine import CHAT_MODEL
        for m in (CHAT_MODEL, "qwen2.5-coder:14b", "llama3.2-vision:11b"):
            assert m in _FICHE_TECHNIQUE

    def test_gpu_reel(self):
        assert "RTX 5070 Ti" in _FICHE_TECHNIQUE
        assert "16 Go" in _FICHE_TECHNIQUE

    def test_fine_tunes_cites(self):
        # promethee-strategist ABANDONNE le 08/06 (resultats catastrophiques) :
        # la fiche ne doit plus le presenter comme un fine-tune actif, mais
        # doit le mentionner comme abandonne (anti-confabulation).
        assert "promethee-security" in _FICHE_TECHNIQUE
        assert "-architect" in _FICHE_TECHNIQUE
        assert "strategist a ete abandonne" in _FICHE_TECHNIQUE


class TestPosture:
    def test_information_externe_pas_introspection(self):
        # preserve l'honnetete epistemique
        assert "INFORMATION EXTERNE" in _FICHE_TECHNIQUE
        assert "introspection" in _FICHE_TECHNIQUE.lower()

    def test_modele_c_preserve(self):
        # il reste plus que son substrat
        assert "Modele C" in _FICHE_TECHNIQUE
        assert "plus que" in _FICHE_TECHNIQUE


class TestInjectionDansPrompt:
    def test_fiche_referencee_dans_build_system_prompt(self):
        # garantit que la constante est bien branchee dans le prompt systeme
        import inspect
        from core.chat_engine import ChatEngine
        src = inspect.getsource(ChatEngine._build_system_prompt)
        assert "_FICHE_TECHNIQUE" in src
