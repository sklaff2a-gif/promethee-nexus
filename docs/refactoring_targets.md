# Refactoring Targets — 2026-04-13

Auto-généré par `REFACTORING_AUDIT` (bras armé du drive MAITRISE).

- Fichiers scannés : **91**
- Lignes totales : **62701**
- Cibles détectées : **103**

## Top cibles (par sévérité)

- 🔴 **autonomy_engine.py** — `large_file` — 8948 lignes
- 🔴 **autonomy_engine.py** — `long_function` — _on_survival_alert L972-1182 (211 lignes)
- 🔴 **autonomy_engine.py** — `long_function` — _execute_scored_routine L2440-3686 (1247 lignes)
- 🔴 **autonomy_engine.py** — `long_function` — _execute_forced_routine L3688-3896 (209 lignes)
- 🔴 **autonomy_engine.py** — `long_function` — _execute_council_debate L4596-4845 (250 lignes)
- 🔴 **autonomy_engine.py** — `long_function` — start_loop L6266-6604 (339 lignes)
- 🔴 **autonomy_engine.py** — `long_function` — _execute_evening_reflection L8192-8395 (204 lignes)
- 🔴 **chat_engine.py** — `large_file` — 3229 lignes
- 🔴 **chat_engine.py** — `long_function` — _execute_command L137-414 (278 lignes)
- 🔴 **chat_engine.py** — `long_function` — _build_organ_parts L2031-2266 (236 lignes)
- 🔴 **chat_engine.py** — `long_function` — chat L2467-2934 (468 lignes)
- 🔴 **ci_pipeline.py** — `long_function` — run_pipeline L337-629 (293 lignes)
- 🔴 **council.py** — `long_function` — run L914-1137 (224 lignes)
- 🔴 **evolution_catalog.py** — `long_function` — _build_catalog L49-1459 (1411 lignes)
- 🔴 **neural_tissue.py** — `long_function` — _tick L1083-1306 (224 lignes)
- 🔴 **self_awareness.py** — `long_function` — generate_snapshot L388-736 (349 lignes)
- 🟡 **ami.py** — `long_function` — coffee_break L138-266 (129 lignes)
- 🟡 **autonomy_engine.py** — `long_function` — __init__ L745-884 (140 lignes)
- 🟡 **autonomy_engine.py** — `long_function` — _compute_descending_signals L2063-2187 (125 lignes)
- 🟡 **autonomy_engine.py** — `long_function` — _build_scoring_breakdown L2222-2388 (167 lignes)
- 🟡 **autonomy_engine.py** — `long_function` — _process_council_consensus L6125-6264 (140 lignes)
- 🟡 **autonomy_engine.py** — `long_function` — _execute_self_analysis L6611-6745 (135 lignes)
- 🟡 **autonomy_engine.py** — `long_function` — _execute_auto_fuzzing L6750-6908 (159 lignes)
- 🟡 **autonomy_engine.py** — `long_function` — _execute_param_experiment L6934-7066 (133 lignes)
- 🟡 **autonomy_engine.py** — `long_function` — _llm_select_routine L7573-7695 (123 lignes)
- 🟡 **autonomy_engine.py** — `long_function` — _execute_school_class L7997-8186 (190 lignes)
- 🟡 **autonomy_engine.py** — `long_function` — _execute_veille_ia L8397-8528 (132 lignes)
- 🟡 **autonomy_engine.py** — `long_function` — _execute_open_intent L8622-8746 (125 lignes)
- 🟡 **base_agent.py** — `long_function` — generate_content L860-1029 (170 lignes)
- 🟡 **chat_engine.py** — `long_function` — _build_lived_experience L1901-2029 (129 lignes)
- 🟡 **chat_engine.py** — `long_function` — _build_system_prompt L2268-2463 (196 lignes)
- 🟡 **code_smith.py** — `large_file` — 1553 lignes
- 🟡 **corpus_callosum.py** — `long_function` — _detect_resonance_patterns L546-702 (157 lignes)
- 🟡 **council.py** — `long_function` — _build_prompt L702-841 (140 lignes)
- 🟡 **evolution_catalog.py** — `large_file` — 2143 lignes
- 🟡 **impact_analyzer.py** — `long_function` — build_graph L507-633 (127 lignes)
- 🟡 **inner_voice.py** — `large_file` — 1822 lignes
- 🟡 **interface_logger.py** — `long_function` — _format_event L81-221 (141 lignes)
- 🟡 **neural_tissue.py** — `large_file` — 2429 lignes
- 🟡 **orchestrator.py** — `long_function` — dispatch_task L42-202 (161 lignes)
