"""Tests unitaires SynapticNetwork.batch_mutations (chantier 25/05).

Couverture :
- Pendant le with, _seeding=True
- A la sortie, _seeding restaure + save() appelee une fois
- Exception dans le bloc : save quand meme (finally)
- Reentrance : with imbrique, save uniquement par le batch racine
- Preservation du flag _seeding externe (si deja True avant batch_mutations)
"""
import os
import sys
from unittest.mock import MagicMock

import pytest

# Pour pouvoir importer core.synaptic_network
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.synaptic_network import SynapticNetwork


@pytest.fixture
def cortex():
    """Cortex singleton avec save() mocke pour ne pas toucher le disque."""
    c = SynapticNetwork()
    # On garde la vraie save mais on la mock pour observer les appels
    c.save = MagicMock()
    c._mutations_since_save = 0
    c._seeding = False
    yield c
    # Cleanup
    c.save = MagicMock()  # reset


def test_seeding_is_True_during_block(cortex):
    states_observed = []
    with cortex.batch_mutations():
        states_observed.append(cortex._seeding)
    states_observed.append(cortex._seeding)
    assert states_observed == [True, False]


def test_save_called_once_at_exit(cortex):
    cortex.save.reset_mock()
    cortex._mutations_since_save = 5
    with cortex.batch_mutations():
        pass
    assert cortex.save.call_count == 1
    assert cortex._mutations_since_save == 0


def test_save_called_even_on_exception(cortex):
    """Si exception dans le with, save doit quand meme s'executer (finally)."""
    cortex.save.reset_mock()
    with pytest.raises(RuntimeError):
        with cortex.batch_mutations():
            raise RuntimeError("simulated mutation failure")
    assert cortex.save.call_count == 1
    assert cortex._seeding is False


def test_reentrant_inner_does_not_save(cortex):
    """With imbrique : seul le batch racine save a la fin."""
    cortex.save.reset_mock()
    with cortex.batch_mutations():
        # Inner batch_mutations imbrique
        with cortex.batch_mutations():
            pass  # inner exit
        # Inner ne doit PAS avoir save (le outer le fera)
        assert cortex.save.call_count == 0
    # Outer save a la sortie
    assert cortex.save.call_count == 1


def test_preserves_external_seeding_True(cortex):
    """Si _seeding etait deja True avant le with (par autre code),
    le context manager preserve cet etat et ne save pas (parent gere)."""
    cortex._seeding = True
    cortex.save.reset_mock()
    with cortex.batch_mutations():
        # Pendant le with, _seeding reste True (normal)
        assert cortex._seeding is True
    # A la sortie, _seeding doit etre RESTE True (preserve etat externe)
    assert cortex._seeding is True
    # save() ne doit PAS avoir ete appele (parent gere la persistance)
    assert cortex.save.call_count == 0


def test_seeding_reset_to_False_normally(cortex):
    """Cas standard : _seeding=False avant -> True pendant -> False apres."""
    cortex._seeding = False
    with cortex.batch_mutations():
        assert cortex._seeding is True
    assert cortex._seeding is False


def test_mutations_counter_reset_on_normal_exit(cortex):
    """A la sortie d'un batch racine, _mutations_since_save = 0."""
    cortex.save.reset_mock()
    cortex._mutations_since_save = 42  # simule des mutations accumulees
    with cortex.batch_mutations():
        cortex._mutations_since_save = 99  # autres mutations pendant le batch
    assert cortex._mutations_since_save == 0


def test_mutations_counter_NOT_reset_when_reentrant(cortex):
    """Reentrance : le inner ne touche pas _mutations_since_save (parent gere)."""
    cortex.save.reset_mock()
    cortex._seeding = True  # simule un batch parent en cours
    cortex._mutations_since_save = 5
    with cortex.batch_mutations():
        cortex._mutations_since_save = 17
    # Reentrant -> ne reset PAS, garde la valeur pour que parent reset a sa sortie
    assert cortex._mutations_since_save == 17
    assert cortex.save.call_count == 0


def test_auto_save_skipped_during_batch(cortex):
    """Le throttle _auto_save doit SKIPPER pendant un batch."""
    cortex.save.reset_mock()
    with cortex.batch_mutations():
        cortex._mutations_since_save = 100  # depasse largement le seuil 10
        cortex._auto_save()  # devrait skipper a cause de _seeding=True
        cortex._auto_save()
        cortex._auto_save()
        # Aucun save ne doit etre appele pendant le batch
        assert cortex.save.call_count == 0
    # save() une fois a la sortie (batch root)
    assert cortex.save.call_count == 1
