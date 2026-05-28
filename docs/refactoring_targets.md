# Refactoring Targets — 2026-05-28

Auto-généré par `REFACTORING_AUDIT` (bras armé du drive MAITRISE).

- Fichiers scannés : **114**
- Lignes totales : **80429**
- Cibles détectées : **142**

## Top cibles (par sévérité)

- 🔴 **autonomy_engine.py** — `large_file` — 12321 lignes
- 🔴 **autonomy_engine.py** — `long_function` — _on_survival_alert L1177-1412 (236 lignes)
- 🔴 **autonomy_engine.py** — `long_function` — _execute_scored_routine L3009-4416 (1408 lignes)
- 🔴 **autonomy_engine.py** — `long_function` — _execute_forced_routine L4418-4708 (291 lignes)
- 🔴 **autonomy_engine.py** — `long_function` — _execute_council_debate L5466-5715 (250 lignes)
- 🔴 **autonomy_engine.py** — `long_function` — start_loop L7297-7685 (389 lignes)
- 🔴 **autonomy_engine.py** — `long_function` — _execute_feature_building L9020-9255 (236 lignes)
- 🔴 **autonomy_engine.py** — `long_function` — _code_review_map_reduce L9927-10351 (425 lignes)
- 🔴 **autonomy_engine.py** — `long_function` — _execute_school_class L10414-10951 (538 lignes)
- 🔴 **autonomy_engine.py** — `long_function` — _execute_evening_reflection L11508-11711 (204 lignes)
- 🔴 **base_agent.py** — `long_function` — generate_content L886-1177 (292 lignes)
- 🔴 **chat_engine.py** — `large_file` — 4174 lignes
- 🔴 **chat_engine.py** — `long_function` — _execute_command L254-546 (293 lignes)
- 🔴 **chat_engine.py** — `long_function` — _build_organ_parts L2480-2715 (236 lignes)
- 🔴 **chat_engine.py** — `long_function` — chat L2965-3681 (717 lignes)
- 🔴 **ci_pipeline.py** — `long_function` — run_pipeline L376-668 (293 lignes)
- 🔴 **council.py** — `long_function` — run L1093-1359 (267 lignes)
- 🔴 **evolution_catalog.py** — `long_function` — _build_catalog L49-1459 (1411 lignes)
- 🔴 **neural_tissue.py** — `long_function` — _tick L1083-1306 (224 lignes)
- 🔴 **school_schedule.py** — `long_function` — get_slot_prompt L498-734 (237 lignes)
- 🔴 **self_awareness.py** — `long_function` — generate_snapshot L404-752 (349 lignes)
- 🔴 **synaptic_network.py** — `long_function` — _learn_from_epistemic_closure L1533-1736 (204 lignes)
- 🟡 **ami.py** — `long_function` — coffee_break L138-314 (177 lignes)
- 🟡 **ami.py** — `long_function` — _find_recent_text L421-548 (128 lignes)
- 🟡 **autonomy_engine.py** — `long_function` — __init__ L928-1089 (162 lignes)
- 🟡 **autonomy_engine.py** — `long_function` — _compute_descending_signals L2498-2622 (125 lignes)
- 🟡 **autonomy_engine.py** — `long_function` — _build_scoring_breakdown L2657-2823 (167 lignes)
- 🟡 **autonomy_engine.py** — `long_function` — _execute_dream_routine L6255-6395 (141 lignes)
- 🟡 **autonomy_engine.py** — `long_function` — _execute_audit_survie L6812-6950 (139 lignes)
- 🟡 **autonomy_engine.py** — `long_function` — _process_council_consensus L7156-7295 (140 lignes)
- 🟡 **autonomy_engine.py** — `long_function` — _execute_self_analysis L7692-7826 (135 lignes)
- 🟡 **autonomy_engine.py** — `long_function` — _execute_auto_fuzzing L7831-7989 (159 lignes)
- 🟡 **autonomy_engine.py** — `long_function` — _execute_param_experiment L8015-8147 (133 lignes)
- 🟡 **autonomy_engine.py** — `long_function` — _llm_select_routine L8654-8776 (123 lignes)
- 🟡 **autonomy_engine.py** — `long_function` — _build_v15_school_context L9434-9614 (181 lignes)
- 🟡 **autonomy_engine.py** — `long_function` — _build_v31_dependency_context L9617-9774 (158 lignes)
- 🟡 **autonomy_engine.py** — `long_function` — _sandbox_correction_loop L9776-9925 (150 lignes)
- 🟡 **autonomy_engine.py** — `long_function` — _self_healing_hook L10973-11151 (179 lignes)
- 🟡 **autonomy_engine.py** — `long_function` — _execute_veille_ia L11771-11902 (132 lignes)
- 🟡 **autonomy_engine.py** — `long_function` — _execute_open_intent L11996-12120 (125 lignes)
