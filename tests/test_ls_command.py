# -*- coding: utf-8 -*-
"""TDD — !ls (lecture seule, atelier V choix (b) de Promethee).

Donne a Promethee la capacite d'INSPECTER ses fichiers depuis le chat (lister, compter,
trouver le plus gros) SANS toucher son etat reel. Confinement calque sur !read : meme
whitelist _READ_ALLOWED_DIRS -> memory/.env/.git/logs hors perimetre.

On teste la methode liee a un objet minimal (SimpleNamespace) pour eviter d'instancier
le lourd ChatEngine ; la methode ne depend que de self._READ_ALLOWED_DIRS + du repo reel
(project_root via __file__). Read-only -> aucun effet de bord, sur a executer en test.
"""
import types

from core.chat_engine import ChatEngine


def _ls(*args):
    fake = types.SimpleNamespace(_READ_ALLOWED_DIRS=ChatEngine._READ_ALLOWED_DIRS)
    return ChatEngine._execute_ls_command(fake, list(args))


# ── auto-decouverte ────────────────────────────────────────────────────────
def test_sans_argument_montre_les_dossiers_listables():
    out = _ls()
    assert "Dossiers listables" in out
    assert "core" in out and "Agents" in out


# ── listing reel + comptage de lignes ──────────────────────────────────────
def test_ls_core_liste_et_compte_les_lignes():
    out = _ls("core")
    assert "fichier(s)" in out
    assert "council.py" in out          # un fichier connu de core/
    assert "lignes)" in out             # les .py affichent leur nb de lignes


def test_ls_agents_compte_les_fichiers():
    """Repond a 'combien de fichiers dans Agents/' (S4E4)."""
    out = _ls("Agents")
    assert "fichier(s)" in out
    # Au moins une douzaine d'agents .py
    import re
    m = re.search(r"(\d+) fichier\(s\)", out)
    assert m and int(m.group(1)) >= 10


# ── confinement / securite ─────────────────────────────────────────────────
def test_dossier_hors_whitelist_refuse():
    """memory/ contient l'etat persistant -> hors perimetre."""
    out = _ls("memory")
    assert "refuse" in out.lower() or "hors perimetre" in out.lower()


def test_remontee_de_chemin_refusee():
    out = _ls("../..")
    assert "refuse" in out.lower() or "hors perimetre" in out.lower()


def test_chemin_absolu_refuse():
    out = _ls("C:/Windows")
    assert "refuse" in out.lower() or "hors perimetre" in out.lower()


def test_env_inaccessible():
    """.env n'est dans aucun dossier whiteliste -> protege."""
    out = _ls(".env")
    assert "refuse" in out.lower() or "hors perimetre" in out.lower()


# ── cas limites ────────────────────────────────────────────────────────────
def test_dossier_inexistant_sous_whitelist():
    out = _ls("core/zzz_inexistant_xyz")
    assert "non trouve" in out.lower()


def test_fichier_au_lieu_de_dossier():
    out = _ls("core/council.py")
    assert "fichier" in out.lower() and "!read" in out


def test_sous_dossier_autorise_ok():
    """Un sous-dossier sous un top whiteliste reste accessible (first_dir = top)."""
    # tests/ est whiteliste ; il existe forcement (ce fichier y vit)
    out = _ls("tests")
    assert "fichier(s)" in out
    assert "test_ls_command.py" in out
