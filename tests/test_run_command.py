# -*- coding: utf-8 -*-
"""TDD de !run -- la console agentique de Promethee (atelier console, 1er outil co-concu).
Execute un vrai script Python dans le sandbox ISOLE, parsing BRUT (anti-fragilite : un script
n'est pas du shell), journal clair. Critere de succes fixe par Promethee lui-meme."""
from core.chat_engine import ChatEngine


def _eng():
    return ChatEngine.__new__(ChatEngine)


# --- parsing BRUT (fin de la fragilite shlex) ---
def test_parse_run_brut_pas_de_shlex():
    cmd, args = _eng()._parse_command("!run print(1 + 1)")
    assert cmd == "run"
    assert args == ["print(1 + 1)"]

def test_parse_run_apostrophe_ne_crashe_pas():
    # le piege exact qui cassait !grave (shlex) : une apostrophe dans le script
    cmd, args = _eng()._parse_command("!run x = \"c'est ok\"; print(x)")
    assert cmd == "run"
    assert "c'est ok" in args[0]

def test_parse_alias_execute_script():
    cmd, _ = _eng()._parse_command("!execute_script print(2)")
    assert cmd == "run"

def test_parse_run_vide():
    cmd, args = _eng()._parse_command("!run")
    assert cmd == "run" and args == [""]


# --- execution dans le sandbox ---
def test_run_script_simple():
    out = _eng()._execute_run_command("print(6 * 7)")
    assert "42" in out and "EXECUTE" in out

def test_run_multiligne():
    out = _eng()._execute_run_command("a = 10\nb = 32\nprint(a + b)")
    assert "42" in out

def test_run_isolation_bloque_os():
    # ISOLATION (la garantie qu'il a demandee) : os interdit -> echec, pas d'acces reel
    out = _eng()._execute_run_command("import os\nprint(os.getcwd())")
    assert "ECHEC" in out

def test_run_vide_usage():
    assert "Usage" in _eng()._execute_run_command("")

def test_run_sans_sortie():
    assert "aucune sortie" in _eng()._execute_run_command("x = 5").lower()
