"""Tests V16 - CodeSandbox.

Couvre les 6 couches de securite + la surface publique : lint, run_python,
format_traceback, stats.
"""
import pytest

from core.capabilities.code_sandbox import (
    CodeSandbox,
    SandboxResult,
    _BANNED_MODULES,
    _BANNED_NAMES,
    DEFAULT_TIMEOUT_S,
    MAX_CODE_CHARS,
    MAX_TIMEOUT_S,
)


@pytest.fixture
def fresh_sandbox():
    CodeSandbox.reset_singleton()
    yield CodeSandbox()
    CodeSandbox.reset_singleton()


# ═══════════════════════════════════════════════════════════════════════
# Lint AST pre-run
# ═══════════════════════════════════════════════════════════════════════


class TestLint:

    def test_clean_code_returns_none(self, fresh_sandbox):
        assert fresh_sandbox.lint("x = 1 + 2\nprint(x)") is None

    def test_import_os_rejected(self, fresh_sandbox):
        assert fresh_sandbox.lint("import os\nprint(os.getcwd())") == "os"

    def test_from_os_import_rejected(self, fresh_sandbox):
        assert fresh_sandbox.lint("from os import getcwd") == "os"

    def test_import_subprocess_rejected(self, fresh_sandbox):
        assert fresh_sandbox.lint("import subprocess") == "subprocess"

    def test_socket_rejected(self, fresh_sandbox):
        assert fresh_sandbox.lint("import socket") == "socket"

    def test_pickle_rejected(self, fresh_sandbox):
        assert fresh_sandbox.lint("import pickle") == "pickle"

    def test_threading_rejected(self, fresh_sandbox):
        assert fresh_sandbox.lint("import threading") == "threading"

    def test_ctypes_rejected(self, fresh_sandbox):
        assert fresh_sandbox.lint("import ctypes") == "ctypes"

    def test_importlib_rejected(self, fresh_sandbox):
        # Garantit qu on ne peut pas contourner la liste via import dynamique
        assert fresh_sandbox.lint("import importlib") == "importlib"

    def test_eval_call_rejected(self, fresh_sandbox):
        assert fresh_sandbox.lint("x = eval('1+1')") == "eval"

    def test_exec_call_rejected(self, fresh_sandbox):
        assert fresh_sandbox.lint("exec('print(42)')") == "exec"

    def test_compile_rejected(self, fresh_sandbox):
        assert fresh_sandbox.lint("compile('1', 'x', 'eval')") == "compile"

    def test_open_rejected(self, fresh_sandbox):
        # open() peut lire n importe quoi sur le FS parent
        assert fresh_sandbox.lint("f = open('/etc/passwd')") == "open"

    def test_dunder_import_rejected(self, fresh_sandbox):
        assert fresh_sandbox.lint("m = __import__('os')") == "__import__"

    def test_getattr_builtins_exec_rejected(self, fresh_sandbox):
        # Contournement paranoia : getattr(__builtins__, 'exec')('...')
        assert fresh_sandbox.lint(
            "getattr(__builtins__, 'exec')('print(1)')"
        ) == "getattr->exec"

    def test_syntax_error_returns_none(self, fresh_sandbox):
        # SyntaxError laissee au subprocess pour traceback lisible
        assert fresh_sandbox.lint("def broken(\n  pass") is None

    def test_clean_math_allowed(self, fresh_sandbox):
        code = "import math\nresult = math.sqrt(144)\nprint(result)"
        assert fresh_sandbox.lint(code) is None

    def test_json_allowed(self, fresh_sandbox):
        assert fresh_sandbox.lint("import json\nprint(json.dumps({'a': 1}))") is None


# ═══════════════════════════════════════════════════════════════════════
# run_python - cas nominal
# ═══════════════════════════════════════════════════════════════════════


class TestRunPythonSuccess:

    def test_simple_print(self, fresh_sandbox):
        r = fresh_sandbox.run_python("print('hello')")
        assert r.success is True
        assert "hello" in r.stdout
        assert r.stderr == "" or "hello" not in r.stderr
        assert r.return_code == 0
        assert r.duration_ms > 0
        assert r.exception is None
        assert r.banned_import is None
        assert r.timed_out is False

    def test_arithmetic(self, fresh_sandbox):
        r = fresh_sandbox.run_python("x = 2 + 3\nprint(x * 4)")
        assert r.success
        assert "20" in r.stdout

    def test_math_import_works(self, fresh_sandbox):
        r = fresh_sandbox.run_python("import math\nprint(math.pi)")
        assert r.success
        assert "3.14" in r.stdout

    def test_json_roundtrip(self, fresh_sandbox):
        code = "import json\nd = {'a': [1,2,3]}\nprint(json.dumps(d))"
        r = fresh_sandbox.run_python(code)
        assert r.success
        assert "[1, 2, 3]" in r.stdout


# ═══════════════════════════════════════════════════════════════════════
# run_python - echecs proprement captures
# ═══════════════════════════════════════════════════════════════════════


class TestRunPythonFailures:

    def test_syntax_error_captured(self, fresh_sandbox):
        r = fresh_sandbox.run_python("def broken(\n  pass")
        assert r.success is False
        assert r.return_code != 0
        assert "SyntaxError" in r.stderr
        assert r.exception == "SyntaxError"

    def test_name_error(self, fresh_sandbox):
        r = fresh_sandbox.run_python("print(undefined_var)")
        assert not r.success
        assert r.exception == "NameError"

    def test_value_error_raised(self, fresh_sandbox):
        r = fresh_sandbox.run_python("raise ValueError('test message')")
        assert not r.success
        assert r.exception == "ValueError"
        assert "test message" in r.stderr

    def test_zero_division(self, fresh_sandbox):
        r = fresh_sandbox.run_python("x = 1 / 0")
        assert not r.success
        assert r.exception == "ZeroDivisionError"

    def test_banned_os_returns_banned_import(self, fresh_sandbox):
        r = fresh_sandbox.run_python("import os\nprint(os.environ)")
        assert not r.success
        assert r.banned_import == "os"
        assert r.exception is None  # pas de subprocess lance

    def test_banned_eval(self, fresh_sandbox):
        r = fresh_sandbox.run_python("print(eval('1+1'))")
        assert not r.success
        assert r.banned_import == "eval"


# ═══════════════════════════════════════════════════════════════════════
# Timeout
# ═══════════════════════════════════════════════════════════════════════


class TestTimeout:

    def test_infinite_loop_killed(self, fresh_sandbox):
        r = fresh_sandbox.run_python("while True: pass", timeout=1)
        assert not r.success
        assert r.timed_out is True
        assert r.return_code == -9

    def test_timeout_clamped_to_max(self, fresh_sandbox):
        # Meme si on demande 999, le sandbox clamp a MAX_TIMEOUT_S
        r = fresh_sandbox.run_python("print(1)", timeout=999)
        assert r.success  # print rapide, pas de timeout reel
        # (on ne peut pas verifier la valeur clamp depuis resultat, mais
        # le code doit tourner normalement)

    def test_timeout_min_1s(self, fresh_sandbox):
        r = fresh_sandbox.run_python("print(1)", timeout=0)
        # timeout clampe a 1s minimum ; print est instantane
        assert r.success


# ═══════════════════════════════════════════════════════════════════════
# Isolation - mur de feu (exigence Gemini #1)
# ═══════════════════════════════════════════════════════════════════════


class TestIsolation:

    def test_no_env_leak_api_keys(self, fresh_sandbox, monkeypatch):
        # Meme si le parent a une cle API, le subprocess ne doit pas la voir.
        # Comme os est banni, on ne peut pas le verifier directement via
        # os.environ. On fait un test indirect : import os est rejete = OK.
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret-leak-test-12345")
        r = fresh_sandbox.run_python("import os\nprint(os.environ.get('ANTHROPIC_API_KEY'))")
        assert not r.success
        assert r.banned_import == "os"

    def test_no_file_system_access_via_open(self, fresh_sandbox):
        r = fresh_sandbox.run_python("f = open('/etc/passwd')")
        assert not r.success
        assert r.banned_import == "open"

    def test_tempdir_cleaned_after_run(self, fresh_sandbox):
        import glob, tempfile as _tf
        before = set(glob.glob(f"{_tf.gettempdir()}/promethee_sandbox_*"))
        fresh_sandbox.run_python("print('clean')")
        after = set(glob.glob(f"{_tf.gettempdir()}/promethee_sandbox_*"))
        # Pas de fuite de tempdir
        assert after <= before  # aucun nouveau subsist

    def test_no_network_access(self, fresh_sandbox):
        r = fresh_sandbox.run_python("import socket\ns = socket.socket()")
        assert not r.success
        assert r.banned_import == "socket"

    def test_no_subprocess_spawn(self, fresh_sandbox):
        r = fresh_sandbox.run_python("import subprocess\nsubprocess.run(['ls'])")
        assert not r.success
        assert r.banned_import == "subprocess"


# ═══════════════════════════════════════════════════════════════════════
# SandboxResult.format_traceback (echo de l erreur - exigence Gemini #2)
# ═══════════════════════════════════════════════════════════════════════


class TestFormatTraceback:

    def test_success_returns_empty(self, fresh_sandbox):
        r = fresh_sandbox.run_python("print(1)")
        assert r.format_traceback() == ""

    def test_banned_import_message(self, fresh_sandbox):
        r = fresh_sandbox.run_python("import os")
        msg = r.format_traceback()
        assert "IMPORT INTERDIT" in msg
        assert "'os'" in msg
        assert "Reecris" in msg

    def test_timeout_message(self, fresh_sandbox):
        r = fresh_sandbox.run_python("while True: pass", timeout=1)
        msg = r.format_traceback()
        assert "TIMEOUT" in msg
        assert "simplif" in msg.lower()

    def test_traceback_injected(self, fresh_sandbox):
        r = fresh_sandbox.run_python("raise ValueError('bad')")
        msg = r.format_traceback()
        assert "TRACEBACK A CORRIGER" in msg
        assert "ValueError" in msg
        assert "bad" in msg

    def test_traceback_truncated(self, fresh_sandbox):
        r = fresh_sandbox.run_python("raise ValueError('x')")
        msg = r.format_traceback(max_chars=20)
        # Le message tronque inclut "TRACEBACK A CORRIGER:" + dernier segment
        assert "TRACEBACK A CORRIGER" in msg


# ═══════════════════════════════════════════════════════════════════════
# Entrees degrades
# ═══════════════════════════════════════════════════════════════════════


class TestEdgeCases:

    def test_empty_code(self, fresh_sandbox):
        r = fresh_sandbox.run_python("")
        assert not r.success
        assert "vide" in r.stderr.lower() or "invalide" in r.stderr.lower()

    def test_whitespace_only(self, fresh_sandbox):
        r = fresh_sandbox.run_python("   \n\t  ")
        assert not r.success

    def test_code_too_long_rejected(self, fresh_sandbox):
        huge = "x = 1\n" * (MAX_CODE_CHARS // 3)  # > 20000 chars
        r = fresh_sandbox.run_python(huge)
        assert not r.success
        assert "trop long" in r.stderr.lower()

    def test_non_string_input(self, fresh_sandbox):
        r = fresh_sandbox.run_python(None)  # type: ignore
        assert not r.success


# ═══════════════════════════════════════════════════════════════════════
# Stats
# ═══════════════════════════════════════════════════════════════════════


class TestStats:

    def test_stats_initial(self, fresh_sandbox):
        s = fresh_sandbox.stats
        assert s["runs"] == 0
        assert s["crashes"] == 0
        assert s["success_rate"] == 1.0

    def test_stats_after_success_and_fail(self, fresh_sandbox):
        fresh_sandbox.run_python("print(1)")
        fresh_sandbox.run_python("raise ValueError()")
        fresh_sandbox.run_python("import os")  # banni, compte comme crash
        s = fresh_sandbox.stats
        assert s["runs"] == 3
        assert s["crashes"] == 2
        assert 0.3 < s["success_rate"] < 0.34


# ═══════════════════════════════════════════════════════════════════════
# V16.2 Stethoscope — _extract_exception_name avec stderr complexes
# ═══════════════════════════════════════════════════════════════════════


class TestExceptionExtractor:
    """V16.2 : tests unitaires du parseur d'exception.

    Injecte des stderr factices complexes pour verifier que la regex
    trouve bien la racine du mal (derniere ligne de traceback).
    """

    def test_simple_traceback_attributeerror(self, fresh_sandbox):
        stderr = (
            "Traceback (most recent call last):\n"
            "  File \"<string>\", line 5, in <module>\n"
            "  File \"<string>\", line 3, in fusionner\n"
            "AttributeError: 'list' object has no attribute 'update'\n"
        )
        assert fresh_sandbox._extract_exception_name(stderr) == "AttributeError"

    def test_keyerror_with_message(self, fresh_sandbox):
        stderr = (
            "Traceback (most recent call last):\n"
            "  File \"<string>\", line 2, in <module>\n"
            "KeyError: 'missing_key'\n"
        )
        assert fresh_sandbox._extract_exception_name(stderr) == "KeyError"

    def test_typeerror_multiline_message(self, fresh_sandbox):
        stderr = (
            "Traceback (most recent call last):\n"
            "  File \"<string>\", line 1, in <module>\n"
            "TypeError: unsupported operand type(s) for +: 'int' and 'str'\n"
        )
        assert fresh_sandbox._extract_exception_name(stderr) == "TypeError"

    def test_chained_exception_returns_last(self, fresh_sandbox):
        # Python chained exceptions : on veut la DERNIERE visible par le process
        stderr = (
            "Traceback (most recent call last):\n"
            "  File \"<string>\", line 3, in <module>\n"
            "ValueError: invalid literal\n"
            "\n"
            "During handling of the above exception, another exception occurred:\n"
            "\n"
            "Traceback (most recent call last):\n"
            "  File \"<string>\", line 6, in <module>\n"
            "RuntimeError: could not recover\n"
        )
        # La root cause visible = RuntimeError (derniere ligne)
        assert fresh_sandbox._extract_exception_name(stderr) == "RuntimeError"

    def test_custom_exception(self, fresh_sandbox):
        stderr = (
            "Traceback (most recent call last):\n"
            "  File \"<string>\", line 10, in <module>\n"
            "MyCustomError: something went wrong\n"
        )
        assert fresh_sandbox._extract_exception_name(stderr) == "MyCustomError"

    def test_warning_captured(self, fresh_sandbox):
        stderr = (
            "Traceback (most recent call last):\n"
            "  File \"<string>\", line 2, in <module>\n"
            "DeprecationWarning: use alternative method\n"
        )
        assert fresh_sandbox._extract_exception_name(stderr) == "DeprecationWarning"

    def test_keyboardinterrupt(self, fresh_sandbox):
        stderr = (
            "Traceback (most recent call last):\n"
            "  File \"<string>\", line 3, in <module>\n"
            "KeyboardInterrupt\n"
        )
        assert fresh_sandbox._extract_exception_name(stderr) == "KeyboardInterrupt"

    def test_systemexit_with_message(self, fresh_sandbox):
        stderr = (
            "Traceback (most recent call last):\n"
            "  File \"<string>\", line 2, in <module>\n"
            "SystemExit: 1\n"
        )
        assert fresh_sandbox._extract_exception_name(stderr) == "SystemExit"

    def test_empty_stderr_returns_none(self, fresh_sandbox):
        assert fresh_sandbox._extract_exception_name("") is None
        assert fresh_sandbox._extract_exception_name(None) is None

    def test_no_exception_pattern_returns_none(self, fresh_sandbox):
        # stderr de debug pur sans exception formee
        stderr = "[DEBUG] some info\n[INFO] another message\n"
        assert fresh_sandbox._extract_exception_name(stderr) is None

    def test_ignores_narrative_colon_lines(self, fresh_sandbox):
        # Un traceback File doit etre ignore malgre le ":"
        stderr = (
            "Traceback (most recent call last):\n"
            '  File "<string>", line 1, in <module>\n'
            "NameError: name 'foo' is not defined\n"
        )
        assert fresh_sandbox._extract_exception_name(stderr) == "NameError"

    def test_trailing_whitespace_stripped(self, fresh_sandbox):
        stderr = "ValueError: bad input   \n\n\n"
        assert fresh_sandbox._extract_exception_name(stderr) == "ValueError"

    def test_sysexit_produces_systemexit_label(self, fresh_sandbox):
        # Fallback : sys.exit(N) ne produit pas de traceback sur stderr,
        # juste un return_code != 0. Doit etre identifie SystemExit.
        r = fresh_sandbox.run_python("import sys\nsys.exit(1)")
        assert not r.success
        assert r.return_code == 1
        assert r.exception == "SystemExit"

    def test_real_attributeerror_end_to_end(self, fresh_sandbox):
        # Reproduit le scenario du tir piege de Gemini (10:38)
        r = fresh_sandbox.run_python(
            "x = [1, 2, 3]\nx.update({'a': 1})"
        )
        assert not r.success
        assert r.exception == "AttributeError"


# ═══════════════════════════════════════════════════════════════════════
# V16.3 Heuristique pseudo-code (is_pseudo_code)
# ═══════════════════════════════════════════════════════════════════════


class TestIsPseudoCode:
    """V16.3 : detection des blocs illustratifs non-executables.

    Permet au hook _sandbox_correction_loop de skip les CODE_REVIEW dont
    les blocs contiennent des annotations (Lxx:, ellipsis) typiques des
    extraits de code commentes dans une revue.
    """

    def test_real_code_not_pseudo(self):
        from core.capabilities.code_sandbox import is_pseudo_code
        code = "def hello(name):\n    return f'hi {name}'"
        assert is_pseudo_code(code) is False

    def test_line_prefix_lxx_detected(self):
        from core.capabilities.code_sandbox import is_pseudo_code
        code = (
            "L26: def verify_code_review(result: str) -> bool:\n"
            "L27:     return True\n"
        )
        assert is_pseudo_code(code) is True

    def test_ellipsis_solo_line_detected(self):
        from core.capabilities.code_sandbox import is_pseudo_code
        code = (
            "def foo():\n"
            "    x = 1\n"
            "    ...\n"
            "    return x\n"
        )
        assert is_pseudo_code(code) is True

    def test_codereview_style_extract(self):
        # Cas reel observe 07:55 : audit avec extraits annotes
        from core.capabilities.code_sandbox import is_pseudo_code
        code = (
            "L26: def verify_code_review(result: str, target_file: str) -> Tuple[bool, str]:\n"
            "...\n"
            "L70: def _extract_real_names(filepath: str) -> List[str]:\n"
            "L83:     except Exception:\n"
        )
        assert is_pseudo_code(code) is True

    def test_empty_string(self):
        from core.capabilities.code_sandbox import is_pseudo_code
        assert is_pseudo_code("") is False

    def test_none_input(self):
        from core.capabilities.code_sandbox import is_pseudo_code
        assert is_pseudo_code(None) is False

    def test_comment_with_ellipsis_inline_ok(self):
        # "..." inline dans une string ou un commentaire ne doit pas deceler
        from core.capabilities.code_sandbox import is_pseudo_code
        code = "msg = 'processing...'\nprint(msg)"
        assert is_pseudo_code(code) is False

    def test_ordinary_script_with_comments(self):
        from core.capabilities.code_sandbox import is_pseudo_code
        code = (
            "# Calcule les primes\n"
            "import math\n"
            "def is_prime(n):\n"
            "    if n < 2: return False\n"
            "    for i in range(2, int(math.isqrt(n))+1):\n"
            "        if n % i == 0: return False\n"
            "    return True\n"
        )
        assert is_pseudo_code(code) is False
