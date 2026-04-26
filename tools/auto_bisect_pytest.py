"""V30.15 — Auto-bisecteur QA : trouve mathematiquement quel fichier de
test pollue un test cible.

Recherche dichotomique : log2(N) appels pytest. Sur 150 fichiers, ~8
iterations a ~30-60s chacune = 4-8 minutes pour identifier le pollueur
de maniere indiscutable. Pas d'intuition, pas de devinette, juste de
l'isolation mathematique.

Usage :
    python tools/auto_bisect_pytest.py <test_node_id>

Exemple :
    python tools/auto_bisect_pytest.py \\
        "tests/test_cloud_routing.py::TestOrchestratorForceLocal::test_internal_context_sets_flag"

Algorithme :
1. Sanity-check : le test cible passe en isolation (sinon ce n'est pas
   une pollution mais un vrai bug du test).
2. Confirmation : le test cible echoue en suite complete (sinon il n'y
   a rien a chasser).
3. Bisection sur la liste des fichiers tests SAUF le fichier du test cible :
   - Divise les candidats en 2 moitiees
   - Lance pytest sur [moitie_1, fichier_cible::test]
   - Si plante : pollueur dans moitie_1 -> recurse
   - Si vert  : pollueur dans moitie_2 -> recurse
4. Quand il reste 1 fichier candidat -> c'est le pollueur.

Output : nom du fichier pollueur + transcript des iterations.
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Quarantaines actuelles a propager dans chaque appel pytest pour rester
# coherent avec le sandbox MEDIC.
DESELECTS = (
    "tests/test_cardiac_engine.py::TestBusIntegration",
    "tests/test_cloud_routing.py::TestOrchestratorForceLocal",
)
IGNORES = (
    "tests/auto/test_resource_monitor.py",
)


def _run_pytest(files: list[str], target_node_id: str, timeout_s: int = 240) -> tuple[bool, str]:
    """Lance pytest sur (files + target_node_id) avec deselects/ignores.
    Retourne (passed, summary). passed=True si TOUS les tests passent."""
    cmd = [
        sys.executable, "-m", "pytest",
        "--tb=line", "--no-header", "-q", "-x",
    ]
    for ig in IGNORES:
        cmd.extend(["--ignore", ig])
    for ds in DESELECTS:
        # Skip le deselect si on cible explicitement ce test (sinon
        # il ne tournera pas et on ne saura jamais s'il passe).
        if target_node_id.startswith(ds):
            continue
        cmd.extend(["--deselect", ds])
    cmd.extend(files)
    cmd.append(target_node_id)

    try:
        proc = subprocess.run(
            cmd, cwd=str(ROOT), capture_output=True, text=True,
            timeout=timeout_s, encoding="utf-8", errors="replace",
        )
    except subprocess.TimeoutExpired:
        return False, "TIMEOUT"

    out = proc.stdout
    # Cherche la ligne "X failed, Y passed" ou "Y passed in Zs"
    last_lines = out.strip().splitlines()[-5:]
    summary = " | ".join(last_lines).replace("\n", " ")[:300]
    passed = (proc.returncode == 0)
    return passed, summary


def _all_test_files() -> list[str]:
    """Liste tous les fichiers tests/test_*.py et tests/auto/test_*.py
    sauf ceux dans IGNORES.
    Ordre : alphabetique (comme pytest collect).
    """
    files = []
    files.extend(sorted((ROOT / "tests" / "auto").rglob("test_*.py")))
    files.extend(sorted((ROOT / "tests").glob("test_*.py")))
    out = []
    for f in files:
        rel = str(f.relative_to(ROOT)).replace("\\", "/")
        if rel in IGNORES:
            continue
        out.append(rel)
    return out


def _file_of_node(node_id: str) -> str:
    """Extrait le chemin de fichier d'un node id pytest."""
    return node_id.split("::")[0]


def bisect(target_node_id: str) -> str | None:
    target_file = _file_of_node(target_node_id)
    print(f"=== AUTO-BISECT — Pollueur de {target_node_id} ===")
    print(f"Fichier du test cible : {target_file}\n")

    # 1. Sanity check : le test passe en isolation
    print(f"[SANITY 1] {target_node_id} en isolation...")
    t0 = time.time()
    ok, summary = _run_pytest([], target_node_id)
    print(f"  {'OK' if ok else 'FAIL'} ({time.time()-t0:.1f}s) : {summary}")
    if not ok:
        print("[ABANDON] Le test echoue en isolation. C'est un VRAI bug, pas de la pollution.")
        return None
    print()

    # 2. Confirmation : il echoue en suite complete
    candidates = [f for f in _all_test_files() if f != target_file]
    print(f"[SANITY 2] Suite complete + cible (N={len(candidates)} candidats)...")
    t0 = time.time()
    ok, summary = _run_pytest(candidates, target_node_id)
    print(f"  {'OK' if ok else 'FAIL'} ({time.time()-t0:.1f}s) : {summary}")
    if ok:
        print("[ABANDON] Le test passe en suite complete. Il n'y a rien a chasser.")
        return None
    print()

    # 3. Bisection
    pool = list(candidates)
    iter_n = 0
    while len(pool) > 1:
        iter_n += 1
        mid = len(pool) // 2
        first_half = pool[:mid]
        second_half = pool[mid:]
        print(f"[ITER {iter_n}] Pool size={len(pool)} -> moitie 1 ({len(first_half)} fichiers)")
        print(f"   Fichiers de [moitie 1] : {first_half[0]} .. {first_half[-1]}")
        t0 = time.time()
        ok, summary = _run_pytest(first_half, target_node_id)
        print(f"   {'OK' if ok else 'FAIL'} ({time.time()-t0:.1f}s) : {summary}")
        if not ok:
            # Pollueur dans first_half
            pool = first_half
            print(f"   -> pollueur dans moitie 1, on garde\n")
        else:
            pool = second_half
            print(f"   -> pollueur dans moitie 2, on garde\n")

    if len(pool) == 1:
        culprit = pool[0]
        print(f"=== POLLUEUR IDENTIFIE : {culprit} ===")
        # Verification finale
        print(f"[VERIF] Lance {culprit} + cible...")
        t0 = time.time()
        ok, summary = _run_pytest([culprit], target_node_id)
        print(f"  {'OK' if ok else 'FAIL'} ({time.time()-t0:.1f}s) : {summary}")
        if ok:
            print(f"  WARNING : {culprit} seul + cible passe ?! La pollution est multi-fichiers ou dependante de l'ordre.")
        else:
            print(f"  CONFIRME : {culprit} seul suffit a faire planter le test cible.")
        return culprit
    return None


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    target = sys.argv[1]
    culprit = bisect(target)
    if culprit:
        print(f"\nResultat final : {culprit}")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
