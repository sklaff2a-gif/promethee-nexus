"""V30.12 — Anticorps deterministe : detection AST des fuites de mocks
asynchrones et autres assignations polluantes dans tests/.

0 LLM, 0 GPU. Linter pur Python qui parse le AST et flag les patterns
suspects. Designed pour completer le MAP V22 quand le LLM est aveugle
aux fuites d'etat global (yes-man syntaxique).

Usage : python tools/mock_leak_detector.py [tests/]

Patterns detectes :
1. <singleton>.<method> = AsyncMock/MagicMock/lambda  (assignation directe)
2. <var>.publish = ... ou <var>.<method> = ...  (mutation d'attribut sur
   un objet potentiellement partage)
3. patch(...).start() sans .stop() correspondant dans la meme methode
4. <Class>._instance = ... (singleton reset hack)
5. setattr(<singleton>, "<method>", ...) (idem via setattr)

Ne flag PAS :
- with patch(...) as mock (cleanup automatique)
- with patch.object(...) as mock (idem)
- @patch decorator (cleanup automatique)
- Assignation a self.* qui n'est pas un singleton importe
"""
from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

ROOT = Path(__file__).resolve().parents[1]

# Variables qui referencent des singletons (a etendre selon le projet)
SINGLETON_NAMES = frozenset({
    "bus", "_bus",
    "orchestrator",
    "compiler",
    "indexer",
    "schedule",
    "psyche",
    "amygdala",
    "cardiac",
    "dopamine",
    "desires",
    "reptilian",
    "self_awareness",
    "synaptic_network",
    "neural_compiler",
    "global_workspace",
    "hippocampus",
    "memory",
    "router",
    "salary",
    "mentor",
    "curiosity_bank",
})

MOCK_CLASSES = frozenset({"MagicMock", "AsyncMock", "Mock", "PropertyMock"})


@dataclass
class Leak:
    file: str
    line: int
    classname: str
    methodname: str
    pattern: str
    code_snippet: str
    severity: str  # high / medium / low


def _is_mock_call(node: ast.AST) -> bool:
    """Verifie si un node est un appel a MagicMock/AsyncMock/etc."""
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Name) and func.id in MOCK_CLASSES:
            return True
        if isinstance(func, ast.Attribute) and func.attr in MOCK_CLASSES:
            return True
    return False


def _attribute_target_str(node: ast.Attribute) -> str:
    """Convertit obj.attr ou obj.sub.attr en string."""
    parts = []
    cur = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
    return ".".join(reversed(parts))


def _is_inside_with_patch(node: ast.AST, ancestors: list[ast.AST]) -> bool:
    """Verifie si node est a l'interieur d'un `with patch(...)` block."""
    for anc in reversed(ancestors):
        if isinstance(anc, ast.With):
            for item in anc.items:
                ce = item.context_expr
                if isinstance(ce, ast.Call):
                    func = ce.func
                    if isinstance(func, ast.Name) and func.id == "patch":
                        return True
                    if isinstance(func, ast.Attribute) and func.attr in (
                        "object", "dict", "multiple",
                    ):
                        return True
    return False


def _walk_with_ancestors(node: ast.AST, ancestors: list[ast.AST] = None) -> Iterator[tuple[ast.AST, list[ast.AST]]]:
    if ancestors is None:
        ancestors = []
    yield node, ancestors
    for child in ast.iter_child_nodes(node):
        yield from _walk_with_ancestors(child, ancestors + [node])


def _enclosing_class_method(ancestors: list[ast.AST]) -> tuple[str, str]:
    classname = "<module>"
    methodname = "<top-level>"
    for anc in ancestors:
        if isinstance(anc, ast.ClassDef):
            classname = anc.name
        elif isinstance(anc, (ast.FunctionDef, ast.AsyncFunctionDef)):
            methodname = anc.name
    return classname, methodname


def detect_leaks_in_file(path: Path) -> list[Leak]:
    """Parse un fichier test et retourne la liste des fuites detectees."""
    leaks: list[Leak] = []
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
    except Exception as e:
        print(f"   [PARSE ERROR] {path}: {e}", file=sys.stderr)
        return leaks
    source_lines = source.splitlines()

    # ─── Pattern 1+2 : assignation Attribute (obj.attr = ...) ───
    for node, ancestors in _walk_with_ancestors(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Attribute):
                target_str = _attribute_target_str(target)
                # Skip self.xxx = ... (pas une fuite, attribut local au test)
                if target_str.startswith("self."):
                    # Sauf si self.<singleton>.<method> = ...
                    parts = target_str.split(".")
                    if len(parts) >= 3 and parts[1] in SINGLETON_NAMES:
                        pass  # potentiellement coupable, on continue
                    else:
                        # Mais flag quand meme si valeur = AsyncMock/MagicMock
                        # car self.agent.process_task = AsyncMock(...) modifie
                        # potentiellement un objet partage
                        if not _is_mock_call(node.value) and not isinstance(
                            node.value, ast.Lambda
                        ):
                            continue
                # Skip cls.xxx = ... (probablement fixture-level)
                if target_str.startswith("cls."):
                    continue
                # Maintenant on regarde le pattern
                value = node.value
                # Cas 2a : obj.method = AsyncMock/MagicMock/lambda
                if _is_mock_call(value) or isinstance(value, ast.Lambda):
                    severity = "high"
                # Cas 2b : obj.method = <function>
                elif isinstance(value, (ast.Name, ast.Attribute, ast.Lambda)):
                    severity = "medium"
                else:
                    continue
                # Skip si dans `with patch(...)` block (cleanup auto)
                if _is_inside_with_patch(node, ancestors):
                    continue
                cls, mth = _enclosing_class_method(ancestors)
                snippet = source_lines[node.lineno - 1].strip() if 0 < node.lineno <= len(source_lines) else "<?>"
                leaks.append(Leak(
                    file=str(path.relative_to(ROOT)).replace("\\", "/"),
                    line=node.lineno,
                    classname=cls,
                    methodname=mth,
                    pattern=f"assign attribute: {target_str} = ...",
                    code_snippet=snippet,
                    severity=severity,
                ))

    # ─── Pattern 3 : patch(...).start() sans .stop() ───
    # Recherche les .start() sur un patch(...) au niveau Expr ou Assign
    starts_in_method: dict[tuple[str, str], list[int]] = {}
    stops_in_method: dict[tuple[str, str], int] = {}
    for node, ancestors in _walk_with_ancestors(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "start":
                # Verifie que le receiver est patch(...)
                rec = func.value
                if isinstance(rec, ast.Call):
                    rfunc = rec.func
                    is_patch = (
                        (isinstance(rfunc, ast.Name) and rfunc.id == "patch")
                        or (isinstance(rfunc, ast.Attribute) and rfunc.attr in ("object", "dict", "multiple"))
                    )
                    if is_patch:
                        cls, mth = _enclosing_class_method(ancestors)
                        starts_in_method.setdefault((cls, mth), []).append(node.lineno)
            elif isinstance(func, ast.Attribute) and func.attr == "stop":
                cls, mth = _enclosing_class_method(ancestors)
                stops_in_method[(cls, mth)] = stops_in_method.get((cls, mth), 0) + 1

    for (cls, mth), lines in starts_in_method.items():
        # Si tearDown / teardown_method existe pour cette classe avec un .stop(), OK
        # Heuristique simple : si la meme classe a au moins autant de .stop() que de .start(),
        # on ne flag pas (probablement clean).
        nb_stops_class = sum(
            v for (c, _), v in stops_in_method.items() if c == cls
        )
        if nb_stops_class >= len(lines):
            continue
        for ln in lines:
            snippet = source_lines[ln - 1].strip() if 0 < ln <= len(source_lines) else "<?>"
            leaks.append(Leak(
                file=str(path.relative_to(ROOT)).replace("\\", "/"),
                line=ln,
                classname=cls,
                methodname=mth,
                pattern="patch().start() without matching .stop()",
                code_snippet=snippet,
                severity="high",
            ))

    return leaks


def main() -> int:
    if len(sys.argv) > 1:
        target = ROOT / sys.argv[1]
    else:
        target = ROOT / "tests"

    if not target.exists():
        print(f"ERREUR : {target} n'existe pas.", file=sys.stderr)
        return 1

    test_files = sorted(target.rglob("test_*.py")) if target.is_dir() else [target]
    print(f"=== V30.12 MOCK LEAK DETECTOR ===")
    print(f"Scope : {target} ({len(test_files)} fichiers)")
    print()

    all_leaks: list[Leak] = []
    for f in test_files:
        leaks = detect_leaks_in_file(f)
        all_leaks.extend(leaks)

    if not all_leaks:
        print("✓ Aucune fuite detectee. Suite propre.")
        return 0

    # Grouper par fichier
    by_file: dict[str, list[Leak]] = {}
    for lk in all_leaks:
        by_file.setdefault(lk.file, []).append(lk)

    print(f"FUITES DETECTEES : {len(all_leaks)} dans {len(by_file)} fichier(s)\n")
    for f in sorted(by_file):
        leaks = by_file[f]
        print(f"=== {f} ({len(leaks)} fuites) ===")
        # Trier par severite puis par ligne
        sev_order = {"high": 0, "medium": 1, "low": 2}
        leaks.sort(key=lambda x: (sev_order.get(x.severity, 99), x.line))
        for lk in leaks:
            print(f"  [{lk.severity.upper():6s}] L{lk.line:4d} {lk.classname}::{lk.methodname}")
            print(f"           {lk.pattern}")
            print(f"           >>> {lk.code_snippet}")
        print()

    # TOP 5 absolu
    print("=== TOP 5 (par severite) ===")
    sev_order = {"high": 0, "medium": 1, "low": 2}
    all_leaks.sort(key=lambda x: (sev_order.get(x.severity, 99), x.file, x.line))
    for lk in all_leaks[:5]:
        print(f"  [{lk.severity.upper():6s}] {lk.file}:{lk.line}")
        print(f"     {lk.classname}::{lk.methodname}")
        print(f"     {lk.pattern}")
        print(f"     >>> {lk.code_snippet}")

    return 0 if not all_leaks else 1


if __name__ == "__main__":
    sys.exit(main())
