# -*- coding: utf-8 -*-
"""TDD de !status_snapshot -- 2e outil de la console agentique, CO-CONCU par Promethee
(atelier console phase 3). Instantane FIGE en LECTURE SEULE de son etat reel (coeur,
dopamine, pulsions, cognition...), + injecte comme variable `etat` dans ses scripts !run
pour qu'il puisse correler une action et son etat interne ('ma fenetre sur mon corps')."""
import json
from core.chat_engine import ChatEngine


def _eng():
    return ChatEngine.__new__(ChatEngine)


# --- le snapshot : un dict JSON-serialisable, robuste (jamais d'exception) ---
def test_snapshot_renvoie_un_dict_avec_les_cles():
    snap = _eng()._capture_state_snapshot()
    assert isinstance(snap, dict)
    # toutes les facettes presentes (valeur reelle OU None si l'organe est KO en test)
    for cle in ("coeur", "dopamine", "pulsions", "cognition", "phi", "prefrontal", "synapses", "mode"):
        assert cle in snap

def test_snapshot_est_json_serialisable():
    snap = _eng()._capture_state_snapshot()
    # garantie cle : c'est de la DONNEE figee, serialisable -> aucun objet vivant
    s = json.dumps(snap, ensure_ascii=False, default=str)
    assert isinstance(s, str) and len(s) > 2


# --- la commande : retourne le JSON lisible ---
def test_execute_snapshot_command_contient_json():
    out = _eng()._execute_snapshot_command()
    assert "status_snapshot" in out
    # le corps doit contenir un JSON parsable
    debut = out.index("{")
    parsed = json.loads(out[debut:])
    assert isinstance(parsed, dict) and "coeur" in parsed


# --- parsing des alias ---
def test_parse_status_snapshot():
    cmd, args = _eng()._parse_command("!status_snapshot")
    assert cmd == "status_snapshot"

def test_parse_alias_etat_et_snapshot():
    assert _eng()._parse_command("!snapshot")[0] == "snapshot"
    assert _eng()._parse_command("!etat")[0] == "etat"


# --- whitelist auto-action (Promethee peut l'emettre lui-meme) ---
def test_snapshot_dans_whitelist():
    wl = ChatEngine._AUTO_ACTION_WHITELIST
    assert "status_snapshot" in wl and "snapshot" in wl and "etat" in wl


# --- LE COEUR : `etat` injecte dans les scripts !run qui le referencent ---
def test_run_injecte_etat_quand_reference():
    # quand le script mentionne `etat`, il recoit l'instantane fige (un dict)
    out = _eng()._execute_run_command("print(isinstance(etat, dict))")
    assert "True" in out and "EXECUTE" in out

def test_run_sans_etat_reste_pur():
    # un script qui ne mentionne pas `etat` n'est PAS pollue (pas d'injection inutile).
    # La sonde construit le mot par concatenation ('e' 'tat') pour qu'aucun token `etat`
    # contigu n'apparaisse dans le source -> le gate d'injection ne doit PAS se declencher.
    out = _eng()._execute_run_command("print('e' 'tat' in dir())")
    assert "False" in out and "EXECUTE" in out
