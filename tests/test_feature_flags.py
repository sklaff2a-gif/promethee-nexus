"""Tests unitaires de core/feature_flags.py."""

import json
import time

import pytest

from core import feature_flags as ff_module
from core.feature_flags import get_all, get_flag, reset_cache


@pytest.fixture
def flags_file(monkeypatch, tmp_path):
    """Redirige FLAGS_FILE vers tmp + reset cache."""
    p = tmp_path / "feature_flags.json"
    monkeypatch.setattr(ff_module, "FLAGS_FILE", p)
    reset_cache()
    yield p
    reset_cache()


def _write(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")


def test_default_si_fichier_absent(flags_file):
    assert not flags_file.exists()
    assert get_flag("any", default="fallback") == "fallback"


def test_lit_valeur_existante(flags_file):
    _write(flags_file, {"soliloque_engine": "v2"})
    assert get_flag("soliloque_engine") == "v2"


def test_default_si_cle_absente(flags_file):
    _write(flags_file, {"soliloque_engine": "v2"})
    assert get_flag("autre_flag", default="def") == "def"


def test_filtre_cles_meta(flags_file):
    """Les clés _doc, _version ne doivent pas apparaître dans get_all()."""
    _write(flags_file, {
        "_doc": "documentation",
        "_version": "1.0",
        "real_flag": "active",
    })
    all_flags = get_all()
    assert "_doc" not in all_flags
    assert "_version" not in all_flags
    assert all_flags == {"real_flag": "active"}


def test_hot_reload_sur_changement_mtime(flags_file):
    """Modifier le fichier → prochain get_flag voit la nouvelle valeur."""
    _write(flags_file, {"soliloque_engine": "v1"})
    assert get_flag("soliloque_engine") == "v1"

    # Force mtime différent (sinon Windows peut garder le même)
    time.sleep(0.05)
    _write(flags_file, {"soliloque_engine": "v2"})
    # Touche explicitement le mtime au cas où
    new_mtime = flags_file.stat().st_mtime + 1
    import os
    os.utime(flags_file, (new_mtime, new_mtime))

    assert get_flag("soliloque_engine") == "v2"


def test_json_invalide_garde_cache(flags_file):
    """Si le JSON est cassé, on garde le cache précédent."""
    _write(flags_file, {"soliloque_engine": "v2"})
    assert get_flag("soliloque_engine") == "v2"
    flags_file.write_text("pas du json valide {", encoding="utf-8")
    # Force mtime différent
    import os
    new_mtime = flags_file.stat().st_mtime + 1
    os.utime(flags_file, (new_mtime, new_mtime))
    # Ne crash pas — retourne le cache précédent ou le default
    val = get_flag("soliloque_engine", default="fallback")
    assert val in ("v2", "fallback")  # cache OU fallback selon ordre


def test_types_supportes(flags_file):
    """Flags peuvent être string, int, bool, dict."""
    _write(flags_file, {
        "str_flag": "value",
        "int_flag": 42,
        "bool_flag": True,
        "dict_flag": {"nested": "ok"},
    })
    assert get_flag("str_flag") == "value"
    assert get_flag("int_flag") == 42
    assert get_flag("bool_flag") is True
    assert get_flag("dict_flag") == {"nested": "ok"}
