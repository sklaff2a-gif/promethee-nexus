"""V4.2 Filtre probabiliste de Bloom — court-circuit pre-LLM.

Design valide par Promethee lui-meme a l'exercice 80 de la Serie VIII :
  m = 20000 bits, k = 7 fonctions de hachage, n_expected = 2090
  -> faux positif theorique < 1% (ex.80), taux observe en test ~0.7%

Propriete cle : asymetrie garantie.
  - Bloom dit NON   -> certitude absolue (0 faux negatif)
  - Bloom dit OUI   -> probabilite >= 99% (faux positif acceptable)

Integration : hook dans base_agent.generate_content, juste apres
neural_compiler.try_intercept. Court-circuit economise 100% du budget
d'inference sur les rejets certains.

Decision architecturale (Jean-Michel 2026-04-19) :
  - Seuil STRICT : 1 faux negatif Bloom = veto immediat
  - Periode d'extraction :
     * fonctions (avec parens ou backticks) -> oui
     * classes (backticks obligatoires) -> oui
     * constantes CAPS -> non (trop de faux positifs sur prose)
     * chemins fichiers core/, Agents/, tests/ -> oui
"""
from __future__ import annotations

import ast
import hashlib
import json
import logging
import math
import os
import re
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Set

logger = logging.getLogger(__name__)

# Constantes validees par Gemini + calculees par Promethee ex.80
BLOOM_M = 20000
BLOOM_K = 7
BLOOM_N_EXPECTED = 2090

# Fonctions Python builtin a exclure (jamais dans l'index projet).
# V4.2.1 (2026-04-20) : elargissement apres 13 faux positifs nocturnes.
# Trois categories :
#   1. Builtins Python classiques
#   2. os.path / os / json / logging (modules stdlib importes partout)
#   3. Methodes str/list/dict ultra-courantes
#   4. Mots francais collidant avec la syntaxe d'appel (cas sans espace)
_BUILTIN_FUNCS = frozenset({
    # --- Python builtins ---
    "print", "len", "range", "str", "int", "list", "dict", "set", "bool",
    "float", "tuple", "bytes", "sum", "min", "max", "abs", "enumerate",
    "zip", "map", "filter", "open", "sorted", "reversed", "super", "type",
    "isinstance", "hasattr", "getattr", "setattr", "delattr", "all", "any",
    "round", "divmod", "pow", "id", "hash", "repr", "chr", "ord", "bin",
    "hex", "oct", "input", "format", "slice", "next", "iter", "frozenset",
    "bytearray", "memoryview", "complex", "object", "property", "staticmethod",
    "classmethod", "callable", "compile", "eval", "exec", "globals", "locals",
    "vars", "dir", "help", "copyright", "credits", "exit", "quit",
    # --- os.path / os (V4.2.1) ---
    "abspath", "dirname", "basename", "exists", "isfile", "isdir", "islink",
    "ismount", "samefile", "join", "split", "splitext", "normpath", "realpath",
    "relpath", "expanduser", "expandvars", "getsize", "getmtime", "getctime",
    "getatime", "commonpath", "commonprefix",
    "getcwd", "getenv", "putenv", "makedirs", "mkdir", "rmdir", "remove",
    "rename", "walk", "listdir", "stat", "chmod", "chdir",
    # --- json / pickle / logging (V4.2.1) ---
    "loads", "dumps", "load", "dump",
    "info", "warning", "error", "debug", "critical", "exception",
    "getLogger", "basicConfig",
    # --- methods str/list/dict (V4.2.1) ---
    "replace", "strip", "rstrip", "lstrip", "lower", "upper", "title",
    "startswith", "endswith", "find", "rfind", "index", "count",
    "append", "extend", "insert", "pop", "clear", "copy",
    "keys", "values", "items", "get", "setdefault", "update",
    "add", "remove", "discard", "union", "intersection",
    # --- asyncio / threading (V4.2.1) ---
    "sleep", "gather", "wait", "create_task", "ensure_future", "run_until_complete",
    "acquire", "release", "notify", "wait_for",
    # --- regex / datetime / time (V4.2.1) ---
    "search", "match", "sub", "findall", "finditer", "compile",
    "now", "today", "time", "strftime", "strptime", "fromtimestamp",
    "perf_counter", "monotonic",
    # --- Mots francais collidant (stop-list FR) V4.2.1 ---
    # Ces mots, sans espace avant la parenthese, peuvent quand meme matcher
    # la regex V4.2.1 stricte (ex: 'contenu(explicite)' dans un texte mal
    # ponctue). Ils ne sont JAMAIS des fonctions Python du projet.
    "cours", "classe", "contenu", "cibles", "atteinte", "atteintes",
    "detectes", "detecte", "feat", "chapitre", "partie", "section",
    "exemple", "question", "reponse", "critere", "etape", "mecanisme",
    "processus", "methode", "signal", "tension", "boucle", "cycle",
    "donnee", "donnees", "variable", "valeur", "resultat", "resultats",
    "note", "score", "point", "points", "niveau", "nombre", "taille",
    "cas", "fois", "maniere", "sens", "forme", "nature", "structure",
    "objet", "objets", "sujet", "sujets", "idee", "idees",
    "base", "bases", "element", "elements", "moyen", "moyens",
    "cadre", "contexte", "reference", "references",
})


# --- Regex d'extraction ---
# V4.2.1 (2026-04-20) : retrait du \s* avant la parenthese ouvrante.
# PEP 8 interdit l'espace entre nom de fonction et '(' en Python. Le
# francais autorise l'espace avant '(' pour les parentheses explicatives.
# Ce seul changement elimine ~90% des faux positifs nocturnes (13 -> ~1).
# Avant : r'\b([a-z_][a-z0-9_]{3,})\s*\('   (matchait 'cours (math)')
# Apres : r'\b([a-z_][a-z0-9_]{3,})\('       (exige contact direct avec '(')
_FUNC_CALL = re.compile(r'\b([a-z_][a-z0-9_]{3,})\(')
# Fonction en backticks : `module.function` ou `function`
_BACKTICK_FUNC = re.compile(r'`([a-z_][a-z0-9_.]*[a-z_][a-z0-9_]{2,})\s*(?:\(\))?`')
# Classe en backticks OBLIGATOIRES (evite faux positifs sur prose capitalizee)
_BACKTICK_CLASS = re.compile(r'`([A-Z][a-zA-Z0-9_]{3,})`')
# Chemin relatif projet
_FILE_PATH = re.compile(r'\b((?:core|Agents|tests|config|docs)/[\w/]+\.py)')

# V4.3 (2026-04-23) : blocs de code executable ```python ... ``` ou ``` ... ```.
# Le Bloom ne scanne que l'interieur de ces blocs (les Actes), pas la prose
# environnante (les Pensees, souvenirs RAG, reasoning). Fix de la
# contamination memoire qui re-injectait les vetos passes via le RAG.
_CODE_BLOCK = re.compile(r'```(?:python|py)?\s*\n?(.*?)\n?```', re.DOTALL)


# V4.5 (2026-04-25) — Whitelist stdlib Python.
# Diagnostic 24/04 23:23 : le veto Bloom V4.2 a bloque l'audit de
# core/bullshit_detector.py parce que le fichier importe `json`. Le
# Bloom a extrait "json" comme une fonction inconnue ("Ressource
# inconnue : la fonction 'json' est introuvable dans le projet"),
# alors que c'est un module de la bibliotheque standard Python.
# Fix : whitelist en dur des modules stdlib les plus utilises +
# 3rd-party omnipresents dans le projet PROMETHEE. Ces noms sont
# fusionnes systematiquement avec la whitelist locale dans
# check_prompt, garantissant qu'aucun import standard ne declenche
# un veto. Permet enfin l'audit de TOUT fichier qui importe `json`,
# `os`, `re`, `asyncio`, etc. — soit la quasi-totalite du projet.
_PYTHON_STDLIB_NAMES = frozenset({
    # Modules core ultra-frequents
    "json", "re", "os", "sys", "time", "datetime", "math",
    "typing", "logging", "asyncio", "ast", "io", "pathlib",
    # Containers et utilitaires
    "collections", "functools", "itertools", "operator", "string",
    "random", "hashlib", "uuid", "base64", "pickle", "copy",
    "weakref", "warnings", "traceback", "abc", "enum",
    "dataclasses", "contextlib", "inspect",
    # Concurrence
    "threading", "concurrent", "multiprocessing", "queue",
    # Subprocess et FS
    "subprocess", "tempfile", "shutil",
    # Reseau et formats
    "socket", "http", "urllib", "html", "csv", "xml", "sqlite3",
    "email", "mimetypes",
    # Bas niveau
    "ctypes", "platform", "struct", "array", "gc", "atexit",
    "signal", "select", "selectors",
    # Numerique
    "decimal", "fractions", "statistics", "secrets",
    # CLI
    "argparse", "getopt", "shlex", "textwrap", "unicodedata",
    # 3rd party omnipresents dans PROMETHEE
    "pytest", "httpx", "fastapi", "pydantic", "chromadb",
    "ollama", "uvicorn", "starlette", "anyio",
})


class BloomFilter:
    """Bloom filter avec double-hashing BLAKE2b."""

    def __init__(self, m: int = BLOOM_M, k: int = BLOOM_K, name: str = ""):
        self.m = m
        self.k = k
        self.name = name
        self.bits = bytearray((m + 7) // 8)
        self.count = 0

    def _hashes(self, item: str) -> List[int]:
        """k hashes via double-hashing : h_i = h1 + i * h2 mod m."""
        data = item.encode("utf-8")
        h1 = int.from_bytes(
            hashlib.blake2b(data, digest_size=8).digest(), "big"
        )
        h2 = int.from_bytes(
            hashlib.blake2b(data + b":salt", digest_size=8).digest(), "big"
        )
        return [(h1 + i * h2) % self.m for i in range(self.k)]

    def add(self, item: str) -> None:
        for pos in self._hashes(item):
            self.bits[pos // 8] |= (1 << (pos % 8))
        self.count += 1

    def contains(self, item: str) -> bool:
        """True = probablement present. False = certainement absent."""
        for pos in self._hashes(item):
            if not (self.bits[pos // 8] & (1 << (pos % 8))):
                return False
        return True

    def false_positive_rate(self) -> float:
        """Taux theorique de faux positifs : (1 - e^(-kn/m))^k."""
        if self.count == 0:
            return 0.0
        return (1.0 - math.exp(-self.k * self.count / self.m)) ** self.k

    def memory_kb(self) -> float:
        return len(self.bits) / 1024.0


@dataclass
class BloomVeto:
    """Resultat d'un veto par le filtre."""
    reason: str
    response: str
    ref_kind: str  # 'function', 'class', 'file'
    ref_name: str


class BloomIndexManager:
    """Singleton. 3 index : fonctions, classes, fichiers.
    Construit au boot via AST parsing du projet."""

    _instance: Optional["BloomIndexManager"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self.functions = BloomFilter(name="functions")
        self.classes = BloomFilter(name="classes")
        self.files = BloomFilter(name="files")
        self._built = False
        self._build_stats: Dict[str, int] = {}
        self._veto_count = 0
        self._skip_count = 0
        # V20b (2026-04-25) : whitelist contextuelle. Les CODE_REVIEW poussent
        # ici les parametres locaux (ast.arg) du target_file en debut de tir
        # via set_session_whitelist(). Permet a un audit de bullshit_detector
        # de citer 'min_last_section_words' (param de d2_truncation) sans
        # declencher un veto Bloom (le param n'est pas dans l'index global).
        # La whitelist est cleared en finally apres chaque tir CODE_REVIEW.
        self._session_whitelist: Set[str] = set()

    def set_session_whitelist(self, names) -> None:
        """V20b : enregistre une whitelist contextuelle pour le tir en cours.

        A appeler en debut de _code_review_map_reduce avec les ast.arg du
        target_file. Le set persiste jusqu a clear_session_whitelist() OU
        un nouvel appel set_session_whitelist().
        """
        self._session_whitelist = set(names) if names else set()

    def clear_session_whitelist(self) -> None:
        """V20b : efface la whitelist contextuelle (a appeler en finally)."""
        self._session_whitelist = set()

    @classmethod
    def reset_singleton(cls):
        """Pour les tests."""
        cls._instance = None

    def build_indexes(self, project_root: str) -> Dict[str, int]:
        """Parse le projet via AST, construit les 3 index.
        Retourne le nombre d'entrees par index."""
        t0 = time.perf_counter()
        for target in ["core", "Agents", "tests"]:
            target_path = os.path.join(project_root, target)
            if not os.path.isdir(target_path):
                continue
            for root, _, files in os.walk(target_path):
                for f in files:
                    if not f.endswith(".py"):
                        continue
                    full = os.path.join(root, f)
                    rel = os.path.relpath(full, project_root).replace("\\", "/")
                    self.files.add(rel)
                    try:
                        with open(full, "r", encoding="utf-8") as fp:
                            source = fp.read()
                        tree = ast.parse(source)
                    except (SyntaxError, UnicodeDecodeError, OSError):
                        continue
                    for node in ast.walk(tree):
                        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            if node.name not in _BUILTIN_FUNCS:
                                self.functions.add(node.name)
                        elif isinstance(node, ast.ClassDef):
                            self.classes.add(node.name)

        # V9.0 (2026-04-21) : injection des identifiants systeme
        # (intents, modes strategiques) pour eviter le baillonage par
        # faux positifs. Audit V6.0 : les agents recevaient un prompt
        # contenant `AUDIT_SURVIE` (intent valide), Bloom le traitait
        # comme classe Python absente -> veto -> consensus impossible.
        self._inject_system_identifiers()

        self._built = True
        elapsed_ms = (time.perf_counter() - t0) * 1000
        self._build_stats = {
            "functions": self.functions.count,
            "classes": self.classes.count,
            "files": self.files.count,
            "build_ms": round(elapsed_ms, 1),
            "memory_kb_total": round(
                self.functions.memory_kb() + self.classes.memory_kb() + self.files.memory_kb(),
                2,
            ),
        }
        logger.info(
            f"BLOOM V4.2 : indexes construits en {elapsed_ms:.1f}ms "
            f"({self.functions.count} fns, {self.classes.count} cls, "
            f"{self.files.count} files, {self._build_stats['memory_kb_total']} KB)"
        )
        return self._build_stats

    def _inject_system_identifiers(self) -> int:
        """V9.0 (Phase 12 - 2026-04-21) : injection des identifiants
        metier connus dans l'index Bloom pour eviter le baillonage par
        faux positifs de veto.

        3 categories sont injectees :
          1. Intents (cles de RESOURCE_COSTS) -> classes (MAJUSCULES)
          2. Modes strategiques + etats budget -> classes
          3. Packages externes (chromadb, fastapi...) -> functions

        Sans cette injection, un prompt council contenant `AUDIT_SURVIE`
        ou `chromadb(` declenchait un veto systematique -> consensus
        impossible. Audit runtime 21/04 : 100% des debats max_rounds a
        cause de ce baillonage.
        """
        injected = {"intents": 0, "modes": 0, "packages": 0}

        # 1a. Intents depuis RESOURCE_COSTS (intents avec coût LLM)
        try:
            costs_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "config", "resource_costs.json"
            )
            with open(costs_path, "r", encoding="utf-8") as f:
                costs = json.load(f)
            for intent in costs.keys():
                if intent.startswith("_"):
                    continue  # skip commentaires _comment etc.
                self.classes.add(intent)
                injected["intents"] += 1
        except Exception as e:
            logger.warning(f"BLOOM V9.0: injection intents echouee: {e}")

        # 1b. Intents SANS cout (0-LLM) + intents meta-routines. Hardcode
        # car dispersés dans autonomy_engine.py sans structure JSON
        # centrale. Source croisee : POST_BUDGET_INTENTS, INTROSPECTIVE_INTENTS,
        # EXTROVERTED_INTENTS, EXPLORATION_INTENTS, SCHOOL_INTENTS (V5+).
        _EXTRA_INTENTS = (
            # POST_BUDGET_INTENTS sans coût (routines 0-LLM)
            "AUDIT_SURVIE", "AUDIT_STRUCTURE", "MEMORY_CLEANUP",
            "NEURAL_COMPILE", "SELF_INSPECT", "PARAM_EXPERIMENT",
            "EVENING_REFLECTION", "REFACTORING_AUDIT", "CI_PIPELINE_RUN",
            # School (present dans resource_costs mais double securite)
            "SCHOOL_CODE_REVIEW", "SCHOOL_RESEARCH", "SCHOOL_WORKSHOP",
            "SCHOOL_CREATION", "SCHOOL_BULLETIN", "SCHOOL_FREE_TIME",
            # Autonomy specific
            "CIRCADIAN_MAINTENANCE", "DROPZONE_SCAN",
            "CURIOSITY_REFLEX", "CURIOSITY_DEEP_DIVE",
            "BODY_AWARENESS", "OPEN_INTENT", "FLY_OBSERVATION",
            "CROSS_SYNTHESIS", "STEFAN_CONFRONTATION", "COFFEE_BREAK",
            "AUTO_FUZZING", "CREATIVE_PLAY", "GRIMOIRE_EVOLVE",
            "GRIMOIRE_INVOKE", "VISUAL_OBSERVATION", "NEURAL_TRAINING",
            # Pulsions (desire_engine)
            "CURIOSITE", "CREATION", "CONNEXION", "CROISSANCE",
            "MAITRISE", "STABILITE", "LIBERTE",
        )
        for intent in _EXTRA_INTENTS:
            self.classes.add(intent)
            injected["intents"] += 1

        # 1c. Noms d'agents en MAJUSCULES (V9.1 - 2026-04-21)
        # Les agents se citent nommement dans leurs critiques
        # ("Comme le disait STRATEGIST...", "EVOLUTION a propose..."). Sans
        # cette injection, `STRATEGIST` ou `CODER` en majuscules sont traites
        # comme classes Python absentes -> veto -> agent baillonne.
        # Observation premier Council V9.0 (09:10, 21/04) : veto 'STRATEGIST'
        # a coupe la parole de Evolution en plein argument.
        _AGENT_NAMES_UPPER = (
            "STRATEGIST", "CODER", "ARCHITECT", "FACTORY", "EVOLUTION",
            "INFRA", "SECURITY", "WRITER", "RESEARCHER", "FORMATTER",
            "VISION", "PROFESSOR",
            # Entites speciales Council / system
            "PROMETHEE", "PROMETHEUS", "ADVOCAT", "CONSEIL", "COUNCIL",
            "ETUDIANT", "PRESIDENT", "SYSTEM",
        )
        for name in _AGENT_NAMES_UPPER:
            self.classes.add(name)
            injected["modes"] += 1

        # 2. Modes systeme (strategic, budget, nap) -> classes
        _SYSTEM_MODES = (
            # strategic_mode (self_awareness)
            "SURVIE", "CONSOLIDATION", "EXPLORATION", "STANDARD",
            # budget states (autonomy_engine)
            "FULL", "RESERVE", "EXHAUSTED",
            # nap modes
            "NORMAL", "DEEP", "HIBERNATION",
            # Dream/LoRA markers (nap_tasks_done)
            "DREAM", "LORA", "NAP", "COFFEE",
            # Verdict types (council_analytics)
            "PRIORISER", "DEPRIORISER", "ABANDONNER", "MAINTENIR",
            # Consensus markers (vote flou V6.0)
            "CONSENSUS", "APPROUVE",
        )
        for mode in _SYSTEM_MODES:
            self.classes.add(mode)
            injected["modes"] += 1

        # 3. Packages externes (pour eviter veto sur chromadb(..) etc.)
        _EXTERNAL_PACKAGES = (
            # Core deps
            "chromadb", "fastapi", "uvicorn", "pydantic", "httpx",
            "requests", "aiohttp", "anyio", "starlette",
            # ML / AI
            "ollama", "openai", "anthropic", "torch", "transformers",
            "sentence_transformers", "numpy", "pandas", "scipy",
            "sklearn", "torchao", "triton",
            # Utils
            "sqlite3", "redis", "psutil", "yaml", "dotenv", "loguru",
            "tiktoken", "jinja2", "markdown", "bs4", "lxml",
            # Tests
            "pytest", "unittest", "mock", "asyncio_mock",
        )
        for pkg in _EXTERNAL_PACKAGES:
            self.functions.add(pkg)
            injected["packages"] += 1

        total = sum(injected.values())
        logger.info(
            f"BLOOM V9.0: {total} identifiants systeme injectes "
            f"(intents={injected['intents']}, modes={injected['modes']}, "
            f"packages={injected['packages']})"
        )
        return total

    def extract_references(self, prompt: str) -> Dict[str, List[str]]:
        """Extraction deterministique des entites nommees du prompt.

        V4.3 (2026-04-23) : scope restreint aux blocs de code ```...```.
        Le Bloom s'applique aux Actes (code executable livre), pas aux
        Pensees (prose narrative, souvenirs RAG, reasoning du LLM). Fix
        de la contamination memoire auto-renforcee observee 22-23/04 :
        les vetos passes stockes dans collective_wisdom re-contaminaient
        le prompt via le recall, creant une boucle de rejet perpetuelle
        (ex: 'extract_code_snippets' devenu un noeud synaptique permanent).

        Si aucun bloc de code n'est present, aucun acte a valider donc
        aucun veto possible : retour d'un dict vide (check_prompt verra
        total=0 et passera).
        """
        # V4.3 : extraire uniquement les blocs ```...``` du prompt.
        code_blocks = _CODE_BLOCK.findall(prompt)
        scan_text = "\n".join(code_blocks)

        # Si pas d'acte executable, pas de veto possible.
        if not scan_text.strip():
            return {"functions": [], "classes": [], "files": []}

        functions = set()
        for m in _FUNC_CALL.finditer(scan_text):
            name = m.group(1)
            if name not in _BUILTIN_FUNCS:
                functions.add(name)
        for m in _BACKTICK_FUNC.finditer(scan_text):
            full = m.group(1)
            name = full.split(".")[-1]
            if name not in _BUILTIN_FUNCS:
                functions.add(name)

        classes = set()
        for m in _BACKTICK_CLASS.finditer(scan_text):
            classes.add(m.group(1))

        files = set()
        for m in _FILE_PATH.finditer(scan_text):
            files.add(m.group(1))

        return {
            "functions": sorted(functions),
            "classes": sorted(classes),
            "files": sorted(files),
        }

    def check_prompt(self, agent_name: str, prompt: str,
                     whitelist: Optional[Set[str]] = None) -> Optional[BloomVeto]:
        """Teste si une reference nommee du prompt est Bloom-negative.

        Retourne None si :
          - index pas construit (fallback gracieux)
          - aucune reference nommee dans le prompt
          - toutes les references existent (Bloom positif ou faux positif)
          - ref est dans la whitelist locale (V9.0)

        Retourne BloomVeto si au moins une reference est Bloom-negative
        (certitude absolue d'absence). Seuil STRICT : 1 suffit.

        V9.0 (Phase 12 - 2026-04-21) : argument `whitelist` optionnel
        pour que l'appelant (Council, etc.) puisse declarer des tokens
        legitimes du contexte qui echapperont au veto meme si absents
        de l'index Bloom. Complement a l'injection system_identifiers.
        """
        if not self._built:
            return None

        refs = self.extract_references(prompt)
        total = sum(len(v) for v in refs.values())
        if total == 0:
            self._skip_count += 1
            return None

        # V9.0 : normaliser la whitelist pour matchs efficaces.
        # V4.5 (2026-04-25) : fusionner avec la stdlib Python.
        # V20b (2026-04-25) : fusionner aussi avec self._session_whitelist
        # qui contient les parametres locaux du target_file pendant un
        # CODE_REVIEW. Ces 3 sources de tolerance se cumulent.
        wl = (whitelist or set()) | _PYTHON_STDLIB_NAMES | self._session_whitelist

        # Seuil strict : premier faux negatif = veto
        for func in refs["functions"]:
            if func in wl:
                continue  # V9.0 whitelist
            if not self.functions.contains(func):
                self._veto_count += 1
                return BloomVeto(
                    reason=f"function '{func}' absente de l'index",
                    response=(
                        f"Ressource inconnue : la fonction '{func}' est introuvable "
                        f"dans le projet. Generation annulee pour eviter l'hallucination "
                        f"(veto V4.2 Bloom)."
                    ),
                    ref_kind="function",
                    ref_name=func,
                )
        for cls in refs["classes"]:
            if cls in wl:
                continue  # V9.0 whitelist
            if not self.classes.contains(cls):
                self._veto_count += 1
                return BloomVeto(
                    reason=f"class '{cls}' absente de l'index",
                    response=(
                        f"Ressource inconnue : la classe '{cls}' est introuvable "
                        f"dans le projet. Generation annulee (veto V4.2 Bloom)."
                    ),
                    ref_kind="class",
                    ref_name=cls,
                )
        for path in refs["files"]:
            if path in wl:
                continue  # V9.0 whitelist
            if not self.files.contains(path):
                self._veto_count += 1
                return BloomVeto(
                    reason=f"file '{path}' absent de l'index",
                    response=(
                        f"Ressource inconnue : le fichier '{path}' est introuvable "
                        f"dans le projet. Generation annulee (veto V4.2 Bloom)."
                    ),
                    ref_kind="file",
                    ref_name=path,
                )

        return None

    def stats(self) -> Dict:
        """Statistiques pour monitoring."""
        return {
            **self._build_stats,
            "veto_count": self._veto_count,
            "skip_count": self._skip_count,
            "fp_rate_functions": round(self.functions.false_positive_rate(), 4),
            "fp_rate_classes": round(self.classes.false_positive_rate(), 4),
            "fp_rate_files": round(self.files.false_positive_rate(), 4),
        }


# Singleton accessible globalement
bloom_pre_llm = BloomIndexManager()


def initialize_bloom_at_boot(project_root: str) -> Dict:
    """A appeler une seule fois au demarrage (depuis main.py lifespan ou similaire)."""
    return bloom_pre_llm.build_indexes(project_root)
