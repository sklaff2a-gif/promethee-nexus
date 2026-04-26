"""V30.13 — Eradication de la fuite de mock global dans test_divine_infra.

Les 6 occurrences de @patch('core.event_bus.bus.bus', new_callable=MagicMock)
causent une pollution cross-tests : combo @patch + @pytest.mark.asyncio sur
async function, le restore peut foirer et un MagicMock standard reste
referencé. Quand Orchestrator.dispatch_task fait `await bus.publish(...)`,
le mock pollue retourne un MagicMock standard (non awaitable) -> erreur.

Fix : new_callable=AsyncMock. Si la fuite arrive, bus.publish retourne
un coroutine awaitable et pas un MagicMock standard.

Ce script :
1. Charge le source de tests/auto/test_divine_infra.py
2. Construit un audit cible
3. Appelle SURGEON V30 (qwen2.5-coder:14b LOCAL) avec instruction
   d'utiliser action='replace_line_all'
4. Parse + apply en preview
5. Lance MEDIC sandbox (py_compile + pytest --deselect)
6. Verifie que test_internal_context_sets_flag passe au vert
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from Agents.scrub_nurse_agent import ScrubNurseAgent
from Agents.surgeon_agent import SurgeonAgent
from core.capabilities.code_sandbox import (
    parse_v30_patch,
    apply_v30_patch,
    CodeSandbox,
)

TARGET_FILE = "tests/auto/test_divine_infra.py"

AUDIT_REPORT = """[FUITE DE MOCK GLOBAL — Pollution Cross-Tests]

Localisation : tests/auto/test_divine_infra.py.

Description :
Le fichier contient 6 decorateurs @pytest.mark.asyncio combines avec
@patch('core.event_bus.bus.bus', new_callable=MagicMock). Le combo
@patch + asyncio sur des async function declenche un bug de cleanup :
le restore du module-level attribute 'bus' dans 'core.event_bus.bus'
peut ne pas operer parfaitement, laissant un MagicMock standard reference
quand un test ulterieur (TestOrchestratorForceLocal) importe `bus` et
appelle `await bus.publish(...)` -> erreur :
  TypeError: object MagicMock can't be used in 'await' expression

Le bug a ete identifie par le mock_leak_detector (V30.12) : 6 lignes
IDENTIQUES, motif `@patch('core.event_bus.bus.bus', new_callable=MagicMock)`.

[CORRECTION DEMANDEE — UNE SEULE PASSE V30.13]

Remplacer EN UNE PASSE les 6 occurrences identiques. Le sandbox V30
supporte desormais l'action 'replace_line_all' qui applique le patch
a TOUTES les occurrences correspondantes en une seule operation.

Tu DOIS produire un JSON V30 unique avec :
- anchor_line : "@patch('core.event_bus.bus.bus', new_callable=MagicMock)"
  (verbatim, sans indentation prefixe — ces decorateurs sont au niveau 4
  espaces, _v30_compute_indent extraira le bon indent depuis le source)
- action : "replace_line_all" (NOUVELLE action V30.13)
- new_code : "@patch('core.event_bus.bus.bus', new_callable=AsyncMock)"
  (juste swap MagicMock -> AsyncMock, le reste identique)

PAS d'anchor_function. PAS de tableau patches[]. UNE seule action atomique
qui touche les 6 lignes.

[ANCRAGE VERBATIM — NON NEGOCIABLE]

La ligne EXISTE textuellement (verifiee par detecteur AST) :

```
    @patch('core.event_bus.bus.bus', new_callable=MagicMock)
```

Avec 4 espaces d'indentation initiale (decorateur de methode dans une
classe). Reproduis EXACTEMENT cette anchor_line, apostrophes simples
incluses (le source utilise des quotes simples Python natives).

[INTERDICTION ABSOLUE]

N'ECRIS PAS [PATCH_IMPOSSIBLE]. La ligne EXISTE 6 fois. Le patch EST
possible. Tu DOIS produire un JSON V30 conforme avec
action='replace_line_all', RIEN d'autre.

JSON pur, pas de markdown, pas de narration.
"""


async def main() -> None:
    target_path = ROOT / TARGET_FILE
    if not target_path.exists():
        print(f"ERREUR : {target_path} introuvable", file=sys.stderr)
        return
    source = target_path.read_text(encoding="utf-8")
    occurrences = source.count("@patch('core.event_bus.bus.bus', new_callable=MagicMock)")
    print(f"=== V30.13 PATCH MOCK LEAK — {TARGET_FILE} ===")
    print(f"Source : {len(source)}c, {source.count(chr(10))+1} lignes")
    print(f"Occurrences detectees du pattern coupable : {occurrences}\n")

    print("=== NURSE (V29) ===")
    nurse = ScrubNurseAgent()
    t0 = time.time()
    checklist = await nurse.prepare_checklist(AUDIT_REPORT, source)
    print(f"Checklist (dur={time.time()-t0:.1f}s) :")
    print(f"  fallback={checklist.get('fallback')}")
    print(f"  lines_to_preserve={len(checklist.get('lines_to_preserve', []))}\n")

    print("=== SURGEON V30 ===")
    surgeon = SurgeonAgent()
    t0 = time.time()
    try:
        raw_patch = await surgeon.generate_patch(
            audit_report=AUDIT_REPORT,
            target_source=source,
            checklist=checklist,
        )
    except Exception as e:
        print(f"ERREUR SURGEON : {type(e).__name__}: {e}")
        return
    print(f"Raw patch ({len(raw_patch)}c, dur={time.time()-t0:.1f}s) :")
    print(raw_patch[:1500])
    print()

    print("=== PARSE + APPLY (preview en memoire) ===")
    try:
        patch = parse_v30_patch(raw_patch)
        print(f"  is_multi  : {patch.get('is_multi', False)}")
        if patch.get("is_multi"):
            print(f"  patches   : {len(patch['patches'])} sous-patches")
        else:
            print(f"  action       : {patch['action']}")
            print(f"  anchor_func  : {patch.get('anchor_function')}")
            print(f"  anchor_line  : {patch.get('anchor_line')!r}")
            print(f"  new_code     : {patch.get('new_code')!r}")
        new_source = apply_v30_patch(source, patch)
        print(f"  Nouveau source : {len(new_source)}c (delta {len(new_source)-len(source):+d})")
        # Compter les remplacements effectifs
        new_count = new_source.count("@patch('core.event_bus.bus.bus', new_callable=AsyncMock)")
        old_count = new_source.count("@patch('core.event_bus.bus.bus', new_callable=MagicMock)")
        print(f"  AsyncMock occurrences : {new_count}")
        print(f"  MagicMock restantes   : {old_count}")
    except Exception as e:
        print(f"  ERREUR parse/apply : {type(e).__name__}: {e}")
        return
    print()

    print("=== MEDIC SANDBOX (py_compile + pytest full_suite) ===")
    sandbox = CodeSandbox()
    t0 = time.time()
    result = sandbox.apply_patch_in_sandbox(
        surgeon_output=raw_patch,
        target_file=TARGET_FILE,
        iteration=0,
        checklist=checklist,
    )
    print(f"Sandbox (dur={time.time()-t0:.1f}s) :")
    print(f"  status            : {result.status}")
    print(f"  blocks_applied    : {result.blocks_applied}")
    print(f"  test_strategy     : {result.test_strategy}")
    print(f"  tests_passed      : {result.tests_passed}")
    print(f"  tests_failed      : {result.tests_failed}")
    if result.test_failures:
        print(f"  test_failures     :")
        for f in result.test_failures[:5]:
            print(f"    - {f}")
    if result.compile_stderr:
        print(f"  compile_stderr    : {result.compile_stderr[:500]}")
    if result.error_message:
        print(f"  error_message     : {result.error_message[:300]}")

    out_dir = ROOT / "memory" / "auto_patches" / "preview"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y-%m-%d_%H-%M-%S")
    if result.unified_diff:
        out_dir.joinpath(f"mock_leak_{ts}.diff").write_text(
            result.unified_diff, encoding="utf-8",
        )
        print(f"\n=== DIFF (saved memory/auto_patches/preview/) ===")
        print(result.unified_diff[:3000])
    print()
    print(f"=== VERDICT : {result.status.upper()} ===")


if __name__ == "__main__":
    asyncio.run(main())
