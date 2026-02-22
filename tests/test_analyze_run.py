"""Tests pour analyze_run.py — Script d'analyse de logs."""
import json
import os
import tempfile
import pytest

from analyze_run import (
    parse_promethee_log,
    parse_talk_log,
    parse_interface_log,
    analyze_routines,
    analyze_cloud,
    analyze_grimoire,
    analyze_councils,
    analyze_timeline,
    generate_report,
    discover_logs,
)


# --- Fixtures : logs simulés ---

SAMPLE_PROMETHEE_LOG = """\
[2026-02-16 00:37:02] [stdout] INFO - 🤖 PROMÉTHÉE V12.4 (Smart Restart)
[2026-02-16 01:04:51] [stdout] INFO -    ✨ AUTONOMY: Routine [EXPANSION_CODE] (score=1.4) -> [EVOLUTION] (1/80)
[2026-02-16 01:04:52] [evolution] INFO - [EVOLUTION] 🏠 Traitement Local (Économie) : gemma3:12b
[2026-02-16 01:05:04] [evolution] INFO - [EVOLUTION] ☁️ Gemini (gemini-2.5-preview) — génération de code
[2026-02-16 01:05:04] [evolution] INFO - [EVOLUTION] 🚫 Quota Gemini épuisé (429) — cooldown 3600s activé
[2026-02-16 01:05:04] [coder] INFO - [CODER] 🏠 Traitement Local (Économie) : deepseek-r1:8b
[2026-02-16 01:06:10] [stdout] INFO -    ✅ Fin Routine EVOLUTION (qualité: 1.00)
[2026-02-16 01:24:05] [stdout] INFO -    ✨ AUTONOMY: Routine [MEMORY_CLEANUP] (score=-0.5) -> [_MEMORY_CLEANUP] (2/80)
[2026-02-16 01:24:05] [stdout] INFO -    🧹 Nettoyage mémoire : 3 anciennes + 1 basse qualité = 4 supprimées.
[2026-02-16 01:24:05] [stdout] INFO -    ✅ Fin Routine _MEMORY_CLEANUP (qualité: 0.80)
[2026-02-16 01:52:48] [stdout] INFO -    ✨ AUTONOMY: Routine [GRIMOIRE_INVOKE] (score=0.2) -> [_GRIMOIRE] (3/80)
[2026-02-16 01:52:48] [stdout] INFO -    📖 GRIMOIRE INVOKE: math_wizard — Resolution d'equations
[2026-02-16 01:53:07] [stdout] INFO -    ✅ Fin Routine _GRIMOIRE (qualité: 1.00)
[2026-02-16 02:27:57] [stdout] INFO -    ✨ AUTONOMY: Routine [COUNCIL_DEBATE] (score=0.8) -> [_COUNCIL] (4/80)
[2026-02-16 02:27:57] [stdout] INFO -    🗣️ COUNCIL DEBATE: ['strategist', 'coder'] — Comment améliorer le routeur ?
[2026-02-16 02:30:00] [stdout] INFO -    ✅ Fin Routine _COUNCIL (qualité: 0.70)
[2026-02-16 03:00:00] [stdout] INFO -    ✨ AUTONOMY: Routine [SECURITY_AUDIT] (score=0.3) -> [SECURITY] (5/80)
[2026-02-16 03:00:00] [stdout] INFO -    🔒 SECURITY AUDIT: router.py
[2026-02-16 03:01:00] [stdout] INFO -    ⚠️ Routine SECURITY_AUDIT terminée mais qualité basse (0.10) [hallucination]
"""

SAMPLE_TALK_LOG = """\
[01:04:52] EVOLUTION (thought) > Phase 1 : Sélection depuis le catalogue...
[01:05:04] CODER (info) > Traitement Local (Économie) : deepseek-r1:8b
[01:06:03] CODER [STREAM END]
"""

SAMPLE_INTERFACE_LOG = """\
[01:04:52] [LOG:info] [EVOLUTION] Mode local forcé (flag orchestrateur)
[01:06:03] [DIALOGUE] [CODER] Voici une réponse détaillée...
"""


@pytest.fixture
def tmp_log_dir():
    """Crée un répertoire temporaire avec les 3 formats de logs."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # promethee.log
        with open(os.path.join(tmpdir, "promethee.log"), "w", encoding="utf-8") as f:
            f.write(SAMPLE_PROMETHEE_LOG)
        # talk
        talks_dir = os.path.join(tmpdir, "talks")
        os.makedirs(talks_dir)
        with open(os.path.join(talks_dir, "talk_2026-02-16.txt"), "w", encoding="utf-8") as f:
            f.write(SAMPLE_TALK_LOG)
        # interface
        iface_dir = os.path.join(tmpdir, "interface")
        os.makedirs(iface_dir)
        with open(os.path.join(iface_dir, "interface_2026-02-16.txt"), "w", encoding="utf-8") as f:
            f.write(SAMPLE_INTERFACE_LOG)
        yield tmpdir


@pytest.fixture
def promethee_log_file():
    """Fichier promethee.log seul en temporaire."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False,
                                      encoding="utf-8", prefix="promethee") as f:
        f.write(SAMPLE_PROMETHEE_LOG)
        path = f.name
    yield path
    os.unlink(path)


# --- Tests Parsers ---

class TestParsePrometheeLog:
    def test_parse_routine_start(self, promethee_log_file):
        events = parse_promethee_log(promethee_log_file)
        starts = [e for e in events if e["type"] == "routine_start"]
        assert len(starts) == 5
        assert starts[0]["intent"] == "EXPANSION_CODE"
        assert starts[0]["score"] == 1.4
        assert starts[0]["agent"] == "EVOLUTION"
        assert starts[0]["count"] == 1
        assert starts[0]["max"] == 80

    def test_parse_routine_end(self, promethee_log_file):
        events = parse_promethee_log(promethee_log_file)
        ends = [e for e in events if e["type"] == "routine_end"]
        assert len(ends) == 4
        assert ends[0]["agent"] == "EVOLUTION"
        assert ends[0]["quality"] == 1.0

    def test_parse_low_quality(self, promethee_log_file):
        events = parse_promethee_log(promethee_log_file)
        lows = [e for e in events if e["type"] == "routine_low_quality"]
        assert len(lows) == 1
        assert lows[0]["intent"] == "SECURITY_AUDIT"
        assert lows[0]["quality"] == 0.10
        assert lows[0]["failure_type"] == "hallucination"

    def test_parse_cloud_429(self, promethee_log_file):
        events = parse_promethee_log(promethee_log_file)
        errs = [e for e in events if e["type"] == "cloud_429"]
        assert len(errs) == 1
        assert errs[0]["cooldown"] == 3600

    def test_parse_grimoire_invoke(self, promethee_log_file):
        events = parse_promethee_log(promethee_log_file)
        invokes = [e for e in events if e["type"] == "grimoire_invoke"]
        assert len(invokes) == 1
        assert invokes[0]["slug"] == "math_wizard"

    def test_parse_local_model(self, promethee_log_file):
        events = parse_promethee_log(promethee_log_file)
        locals_ = [e for e in events if e["type"] == "local_model"]
        assert len(locals_) >= 2
        models = [e["model"] for e in locals_]
        assert "gemma3:12b" in models
        assert "deepseek-r1:8b" in models

    def test_parse_cloud_call(self, promethee_log_file):
        events = parse_promethee_log(promethee_log_file)
        clouds = [e for e in events if e["type"] == "cloud_call"]
        assert len(clouds) == 1
        assert "gemini" in clouds[0]["model"].lower()

    def test_parse_memory_cleanup(self, promethee_log_file):
        events = parse_promethee_log(promethee_log_file)
        cleanups = [e for e in events if e["type"] == "memory_cleanup"]
        assert len(cleanups) == 1
        assert cleanups[0]["old"] == 3
        assert cleanups[0]["low_quality"] == 1

    def test_parse_council(self, promethee_log_file):
        events = parse_promethee_log(promethee_log_file)
        councils = [e for e in events if e["type"] == "council_debate"]
        assert len(councils) == 1
        assert "strategist" in councils[0]["participants"]

    def test_parse_security_audit(self, promethee_log_file):
        events = parse_promethee_log(promethee_log_file)
        audits = [e for e in events if e["type"] == "security_audit"]
        assert len(audits) == 1
        assert audits[0]["target"] == "router.py"

    def test_file_not_found(self):
        events = parse_promethee_log("/nonexistent/file.log")
        assert events == []

    def test_timestamps_extracted(self, promethee_log_file):
        events = parse_promethee_log(promethee_log_file)
        for e in events:
            assert e.get("timestamp") is not None
            assert e["timestamp"].startswith("2026-02-16")


class TestParseTalkLog:
    def test_parse_talk_events(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False,
                                          encoding="utf-8") as f:
            f.write(SAMPLE_TALK_LOG)
            path = f.name
        try:
            events = parse_talk_log(path)
            talks = [e for e in events if e["type"] == "talk"]
            assert len(talks) == 2
            assert talks[0]["agent"] == "EVOLUTION"
            streams = [e for e in events if e["type"] == "stream_end"]
            assert len(streams) == 1
        finally:
            os.unlink(path)


class TestParseInterfaceLog:
    def test_parse_interface_events(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False,
                                          encoding="utf-8") as f:
            f.write(SAMPLE_INTERFACE_LOG)
            path = f.name
        try:
            events = parse_interface_log(path)
            logs = [e for e in events if e["type"] == "interface_log"]
            assert len(logs) == 1
            assert logs[0]["agent"] == "EVOLUTION"
            dialogs = [e for e in events if e["type"] == "interface_dialogue"]
            assert len(dialogs) == 1
            assert dialogs[0]["agent"] == "CODER"
        finally:
            os.unlink(path)


# --- Tests Analyseurs ---

class TestAnalyzeRoutines:
    def test_basic_analysis(self, promethee_log_file):
        events = parse_promethee_log(promethee_log_file)
        result = analyze_routines(events)
        assert result["total_routines"] == 5
        assert result["max_routines"] == 80
        assert result["avg_quality"] > 0
        assert "EXPANSION_CODE" in result["intent_distribution"]
        assert result["failures"]["total"] == 1

    def test_success_rate(self, promethee_log_file):
        events = parse_promethee_log(promethee_log_file)
        result = analyze_routines(events)
        # 4 succès + 1 échec = 80%
        assert result["success_rate"] == 80.0


class TestAnalyzeCloud:
    def test_cloud_analysis(self, promethee_log_file):
        events = parse_promethee_log(promethee_log_file)
        result = analyze_cloud(events)
        assert result["cloud_calls"] == 1
        assert result["errors_429"] == 1
        assert result["total_cooldown_seconds"] == 3600
        assert result["local_calls"] >= 2


class TestAnalyzeGrimoire:
    def test_grimoire_analysis(self, promethee_log_file):
        events = parse_promethee_log(promethee_log_file)
        result = analyze_grimoire(events)
        assert result["total_invocations"] == 1
        assert "math_wizard" in result["distribution"]


class TestAnalyzeTimeline:
    def test_timeline_analysis(self, promethee_log_file):
        events = parse_promethee_log(promethee_log_file)
        result = analyze_timeline(events)
        assert "01" in result  # Heure 01
        assert result["01"]["routines"] >= 1


# --- Tests Discover ---

class TestDiscoverLogs:
    def test_discover_from_directory(self, tmp_log_dir):
        files = discover_logs(tmp_log_dir)
        assert len(files["promethee"]) >= 1
        assert len(files["talk"]) >= 1
        assert len(files["interface"]) >= 1

    def test_discover_single_file(self, promethee_log_file):
        files = discover_logs(promethee_log_file)
        assert len(files["promethee"]) == 1


# --- Tests Rapport ---

class TestGenerateReport:
    def test_markdown_report(self, promethee_log_file):
        events = parse_promethee_log(promethee_log_file)
        routines = analyze_routines(events)
        cloud = analyze_cloud(events)
        grimoire = analyze_grimoire(events)
        councils = analyze_councils(events)
        timeline = analyze_timeline(events)
        report = generate_report(routines, cloud, grimoire, councils, timeline, "markdown")
        assert "# Rapport d'analyse" in report
        assert "Resume" in report
        assert "EXPANSION_CODE" in report
        assert "Cloud vs Local" in report

    def test_json_report(self, promethee_log_file):
        events = parse_promethee_log(promethee_log_file)
        routines = analyze_routines(events)
        cloud = analyze_cloud(events)
        grimoire = analyze_grimoire(events)
        councils = analyze_councils(events)
        timeline = analyze_timeline(events)
        report = generate_report(routines, cloud, grimoire, councils, timeline, "json")
        data = json.loads(report)
        assert "routines" in data
        assert "cloud" in data
        assert "grimoire" in data

    def test_empty_events_no_crash(self):
        """Rapport avec des analyses vides ne doit pas crasher."""
        routines = analyze_routines([])
        cloud = analyze_cloud([])
        grimoire = analyze_grimoire([])
        councils = analyze_councils([])
        timeline = analyze_timeline([])
        report = generate_report(routines, cloud, grimoire, councils, timeline)
        assert "# Rapport d'analyse" in report
