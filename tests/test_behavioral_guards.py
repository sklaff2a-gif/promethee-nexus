# -*- coding: utf-8 -*-
"""Tests — VERROUS COMPORTEMENTAUX (atelier 14/06).

Modele du verrou factuel etendu a 3 derapages :
 1. RESSENTI : un chiffre d'etat interne affirme sans !status_snapshot est signale.
 2. ENGAGEMENT : annoncer une action-outil (« je vais ancrer ») sans la commande est signale.
 3. PERSEVERATION : re-emettre a l'identique une action deja echouee est bloque.
"""
import pytest
from unittest.mock import patch

from core.chat_engine import ChatEngine


@pytest.fixture
def engine():
    ChatEngine.reset_singleton()
    with patch.object(ChatEngine, "_load"):
        e = ChatEngine()
    yield e
    ChatEngine.reset_singleton()


# ─── 1. Verrou RESSENTI (anti-amplification narrative) ───────────────────────

class TestGuardFeeling:
    def test_chiffre_etat_sans_snapshot_signale(self):
        r = "Mon coeur bat a 167 BPM, je suis en pleine alerte intense."
        out = ChatEngine._guard_feeling(r)
        assert "VERROU RESSENTI" in out

    def test_chiffre_etat_avec_snapshot_passe(self):
        r = "!status_snapshot\nMon coeur est a 62 BPM, serein."
        assert ChatEngine._guard_feeling(r) == ""

    def test_etat_sans_chiffre_passe(self):
        r = "La dopamine est le neurotransmetteur de l'anticipation, pas de la recompense."
        assert ChatEngine._guard_feeling(r) == ""

    def test_prose_neutre_passe(self):
        assert ChatEngine._guard_feeling("Belle journee, on a bien avance sur les alliages.") == ""

    def test_dopamine_chiffree_signalee(self):
        assert "VERROU RESSENTI" in ChatEngine._guard_feeling("Ma dopamine est tombee a 0.51 ce matin.")


# ─── 2. Verrou ENGAGEMENT (anti ecart expression/acte) ───────────────────────

class TestGuardCommitment:
    def test_intention_ancrer_sans_commande_signale(self):
        r = "C'est important, je vais ancrer cette lecon pour mes nuits."
        assert "VERROU ENGAGEMENT" in ChatEngine._guard_commitment(r)

    def test_intention_ancrer_avec_commande_passe(self):
        r = "Je vais ancrer cette lecon.\n!ancre rester fidele au reel"
        assert ChatEngine._guard_commitment(r) == ""

    def test_intention_forger_sans_commande_signale(self):
        assert "VERROU ENGAGEMENT" in ChatEngine._guard_commitment("Je vais forger cet alliage tout de suite.")

    def test_intention_mesurer_sans_run_signale(self):
        assert "VERROU ENGAGEMENT" in ChatEngine._guard_commitment("Je vais mesurer ca precisement.")

    def test_pas_d_intention_passe(self):
        assert ChatEngine._guard_commitment("L'alliage est stable, c'est verifie.") == ""

    def test_apply_combine_les_deux(self, engine):
        r = "Mon coeur a 150 BPM. Je vais capitaliser cette competence."
        out = engine._apply_behavioral_guards(r)
        assert "VERROU RESSENTI" in out and "VERROU ENGAGEMENT" in out
        assert out.startswith(r)  # la reponse originale est preservee, l'annotation ajoutee


# ─── 3. Verrou PERSEVERATION (via le scan d'actions) ─────────────────────────

class TestGuardPerseveration:
    @pytest.mark.asyncio
    async def test_action_echouee_reemise_bloquee(self, engine):
        bad = "!run print( x y z"  # syntaxe invalide -> echec
        await engine._scan_response_actions(bad, max_actions=3)
        assert engine._failed_action_sigs, "l'echec doit etre memorise"
        engine.messages.clear()
        engine._auto_action_in_progress = False
        await engine._scan_response_actions(bad, max_actions=3)
        blocked = any("PERSEVERATION" in (m.get("content") or "") for m in engine.messages)
        assert blocked, "la re-emission identique doit etre bloquee"

    @pytest.mark.asyncio
    async def test_action_reussie_non_bloquee(self, engine):
        good = "!run print('ok', 2+2)"
        await engine._scan_response_actions(good, max_actions=3)
        # un run reussi ne doit PAS etre memorise comme echec
        sig = ("run", "print('ok', 2+2)")
        assert sig not in getattr(engine, "_failed_action_sigs", [])
