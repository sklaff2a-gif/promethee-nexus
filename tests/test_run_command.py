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


# --- boucle agentique : Promethee emet !run LUI-MEME (auto-action) ---
def test_run_dans_whitelist_auto_action():
    # sans ca, le scanner ignore le !run que Promethee ecrit dans sa reponse
    wl = ChatEngine._AUTO_ACTION_WHITELIST
    assert "run" in wl and "execute_script" in wl and "run_code" in wl

def test_collapse_run_multiligne():
    # un script est multi-ligne par nature : la capture mono-ligne du scanner le
    # tronquerait -> SyntaxError. Le collapse doit replier !run comme il replie !calc.
    resp = "Je teste :\n!run a = 10\nb = 32\nprint(a + b)\n\nVoila."
    collapsed = _eng()._collapse_multiline_calc(resp)
    line = [l for l in collapsed.splitlines() if l.startswith("!run")][0]
    assert "a = 10\\nb = 32\\nprint(a + b)" in line

def test_collapse_run_n_avale_pas_la_suite():
    # le bloc s'arrete a la prochaine commande / ligne vide
    resp = "!run print(1)\n!status"
    collapsed = _eng()._collapse_multiline_calc(resp)
    assert "!run print(1)" in collapsed and "!status" in collapsed
