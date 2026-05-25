"""Tests unitaires _parse_command apres fix shlex (25/05).

Couverture :
- Sans guillemets (cas legacy)
- Guillemets equilibres preserves -> pattern multi-mots
- Guillemets desequilibres -> pseudo-cmd __invalid_command__ (pas de crash)
- Messages non-bang -> None
- Edge cases (bang seul, espaces multiples)
"""
import os
import sys
import pytest

# Permet l'import de core.chat_engine sans demarrer le serveur
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class _FakeChatEngine:
    """Wrapper minimaliste pour tester _parse_command en isolation.

    On evite d'importer ChatEngine en entier car il instancie des organes
    lourds au module-load. On copie juste la methode a tester.
    """
    def _parse_command(self, message):
        # Copie litterale de la methode patchee (chat_engine.py:184+)
        import shlex
        from typing import Optional, Tuple, List
        stripped = message.strip()
        if not stripped.startswith("!"):
            return None
        try:
            parts = shlex.split(stripped, posix=True)
        except ValueError as e:
            return ("__invalid_command__", [f"Syntaxe invalide (guillemets desequilibres ?) : {e}"])
        if not parts or not parts[0].startswith("!"):
            return None
        cmd = parts[0][1:].lower()
        args = parts[1:]
        return (cmd, args)


@pytest.fixture
def engine():
    return _FakeChatEngine()


# ============================================================================
# Cas non-bang
# ============================================================================

def test_message_non_bang_returns_none(engine):
    assert engine._parse_command("bonjour Promethee") is None


def test_empty_message_returns_none(engine):
    assert engine._parse_command("") is None


def test_only_whitespace_returns_none(engine):
    assert engine._parse_command("   ") is None


# ============================================================================
# Cas legacy : sans guillemets (doit continuer a fonctionner)
# ============================================================================

def test_simple_bang_no_args(engine):
    assert engine._parse_command("!aide") == ("aide", [])


def test_simple_bang_one_arg(engine):
    assert engine._parse_command("!grep pattern") == ("grep", ["pattern"])


def test_simple_bang_multi_args_no_quotes(engine):
    assert engine._parse_command("!grep pattern core/file.py") == (
        "grep", ["pattern", "core/file.py"]
    )


def test_bang_case_insensitive(engine):
    assert engine._parse_command("!GREP pattern")[0] == "grep"


def test_bang_with_dash_in_command(engine):
    assert engine._parse_command("!seed-ok phrase") == ("seed-ok", ["phrase"])


# ============================================================================
# Cas guillemets equilibres : le fix lui-meme
# ============================================================================

def test_quoted_pattern_with_spaces_kept_together(engine):
    """Le bug d'origine : !grep "def extract_concepts" core/syn.py"""
    result = engine._parse_command('!grep "def extract_concepts" core/syn.py')
    assert result == ("grep", ["def extract_concepts", "core/syn.py"])


def test_quoted_pattern_quotes_are_stripped(engine):
    """Les guillemets sont retires du contenu (pas litterale dans le pattern)."""
    cmd, args = engine._parse_command('!grep "factuality_verifier"')
    # args[0] doit etre "factuality_verifier" sans les quotes
    assert args[0] == "factuality_verifier"
    assert '"' not in args[0]


def test_single_quotes_also_work(engine):
    """shlex POSIX gere aussi les single quotes."""
    result = engine._parse_command("!grep 'pattern espace' file.py")
    assert result == ("grep", ["pattern espace", "file.py"])


def test_mixed_quotes_in_multiple_args(engine):
    result = engine._parse_command('!craft mon_outil "Description avec espaces"')
    assert result == ("craft", ["mon_outil", "Description avec espaces"])


# ============================================================================
# Cas guillemets desequilibres : rejet semantique (pas crash)
# ============================================================================

def test_unbalanced_quote_returns_invalid_command(engine):
    result = engine._parse_command('!grep "pattern sans fermer')
    assert result is not None
    cmd, args = result
    assert cmd == "__invalid_command__"
    assert "Syntaxe invalide" in args[0]
    assert "guillemets" in args[0].lower()


def test_only_opening_single_quote(engine):
    result = engine._parse_command("!grep 'pattern sans fermer")
    assert result[0] == "__invalid_command__"


def test_unbalanced_at_end_after_valid_args(engine):
    result = engine._parse_command('!grep pattern "fichier sans fermer')
    assert result[0] == "__invalid_command__"


# ============================================================================
# Edge cases
# ============================================================================

def test_bang_alone_returns_none_safe(engine):
    """`!` tout seul ne doit pas crasher."""
    result = engine._parse_command("!")
    # shlex.split("!") -> ["!"], parts[0]="!" startswith "!", cmd=""
    assert result == ("", [])


def test_extra_spaces_collapsed(engine):
    """Espaces multiples doivent etre traites comme un seul separateur."""
    assert engine._parse_command("!grep    pattern    file.py") == (
        "grep", ["pattern", "file.py"]
    )


def test_empty_quoted_string(engine):
    """!grep '' file.py -> pattern vide, fichier garde."""
    result = engine._parse_command("!grep '' file.py")
    assert result == ("grep", ["", "file.py"])


# ============================================================================
# Regression : le bug initial est resolu
# ============================================================================

def test_REGRESSION_bug_quotes_in_pattern_FIXED(engine):
    """Reproduction du bug observe 4× pendant la session 4 debats 25/05.

    Avant fix : `!grep "factuality_verifier"` -> args[0] = '"factuality_verifier"'
        -> regex compile la chaine LITTERALE avec quotes -> "Aucun resultat"
    Apres fix : args[0] = 'factuality_verifier' -> regex correcte
    """
    cmd, args = engine._parse_command('!grep "factuality_verifier" core/autonomy_engine.py')
    assert cmd == "grep"
    assert args[0] == "factuality_verifier", (
        f"REGRESSION : args[0]={args[0]!r}, devrait etre sans guillemets"
    )
    assert args[1] == "core/autonomy_engine.py"
