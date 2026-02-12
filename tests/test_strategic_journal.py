# tests/test_strategic_journal.py
import os
import pytest
from core.strategic_journal import StrategicJournal, JOURNAL_FILE, HEADER, MAX_ENTRIES


class TestInit:
    def setup_method(self):
        StrategicJournal.reset_singleton()
        if os.path.exists(JOURNAL_FILE):
            os.remove(JOURNAL_FILE)

    def teardown_method(self):
        StrategicJournal.reset_singleton()
        if os.path.exists(JOURNAL_FILE):
            os.remove(JOURNAL_FILE)

    def test_singleton_same_instance(self):
        j1 = StrategicJournal()
        j2 = StrategicJournal()
        assert j1 is j2

    def test_reset_creates_new_instance(self):
        j1 = StrategicJournal()
        StrategicJournal.reset_singleton()
        j2 = StrategicJournal()
        assert j1 is not j2

    def test_init_no_file(self):
        j = StrategicJournal()
        assert j.entry_count() == 0

    def test_file_created_on_first_write(self):
        j = StrategicJournal()
        assert not os.path.exists(JOURNAL_FILE)
        j.append_research_entry("test", "findings", "test")
        assert os.path.exists(JOURNAL_FILE)


class TestAppendCouncil:
    def setup_method(self):
        StrategicJournal.reset_singleton()
        if os.path.exists(JOURNAL_FILE):
            os.remove(JOURNAL_FILE)

    def teardown_method(self):
        StrategicJournal.reset_singleton()
        if os.path.exists(JOURNAL_FILE):
            os.remove(JOURNAL_FILE)

    def test_append_council_basic(self):
        j = StrategicJournal()
        j.append_council_entry(
            participants=["strategist", "coder"],
            subject="Test sujet",
            status="CONSENSUS",
            conclusion="On fait ça.",
        )
        assert j.entry_count() == 1

    def test_council_fields_present(self):
        j = StrategicJournal()
        j.append_council_entry(
            participants=["researcher", "evolution"],
            subject="Architecture",
            status="DISSENSUS",
            conclusion="Pas d'accord.",
            research_context="Le researcher a trouvé des patterns.",
        )
        full = j.get_full_journal()
        assert "**Participants**: researcher, evolution" in full
        assert "**Sujet**: Architecture" in full
        assert "**Verdict**: DISSENSUS" in full
        assert "**Conclusion**: Pas d'accord." in full
        assert "**Recherche**: Le researcher a trouvé" in full

    def test_council_markdown_valid(self):
        j = StrategicJournal()
        j.append_council_entry(
            participants=["a", "b", "c"],
            subject="Test",
            status="CONSENSUS",
            conclusion="OK",
        )
        full = j.get_full_journal()
        assert full.startswith(HEADER)
        assert "### " in full
        assert "— Council" in full

    def test_council_no_research(self):
        j = StrategicJournal()
        j.append_council_entry(
            participants=["a"],
            subject="Test",
            status="CONSENSUS",
            conclusion="OK",
        )
        full = j.get_full_journal()
        assert "**Recherche**" not in full


class TestAppendResearch:
    def setup_method(self):
        StrategicJournal.reset_singleton()
        if os.path.exists(JOURNAL_FILE):
            os.remove(JOURNAL_FILE)

    def teardown_method(self):
        StrategicJournal.reset_singleton()
        if os.path.exists(JOURNAL_FILE):
            os.remove(JOURNAL_FILE)

    def test_append_research_youtube(self):
        j = StrategicJournal()
        j.append_research_entry(
            topic="LLMs locaux",
            findings="Technique GGUF intéressante.",
            source="YouTube",
        )
        assert j.entry_count() == 1
        full = j.get_full_journal()
        assert "Veille YouTube" in full
        assert "**Sujet**: LLMs locaux" in full
        assert "**Découvertes**: Technique GGUF" in full

    def test_append_research_default_source(self):
        j = StrategicJournal()
        j.append_research_entry(topic="Test", findings="Résultats")
        full = j.get_full_journal()
        assert "Veille web" in full


class TestGetRecentContext:
    def setup_method(self):
        StrategicJournal.reset_singleton()
        if os.path.exists(JOURNAL_FILE):
            os.remove(JOURNAL_FILE)

    def teardown_method(self):
        StrategicJournal.reset_singleton()
        if os.path.exists(JOURNAL_FILE):
            os.remove(JOURNAL_FILE)

    def test_empty_journal(self):
        j = StrategicJournal()
        assert j.get_recent_context() == ""

    def test_one_entry(self):
        j = StrategicJournal()
        j.append_research_entry("Test", "Findings")
        ctx = j.get_recent_context(n_entries=3)
        assert "Test" in ctx
        assert "Findings" in ctx

    def test_n_entries_cap(self):
        j = StrategicJournal()
        for i in range(5):
            j.append_research_entry(f"Topic_{i}", f"Finding_{i}")
        ctx = j.get_recent_context(n_entries=2)
        assert "Topic_3" in ctx
        assert "Topic_4" in ctx
        assert "Topic_0" not in ctx

    def test_max_chars_truncation(self):
        j = StrategicJournal()
        j.append_research_entry("Long", "A" * 3000)
        ctx = j.get_recent_context(n_entries=1, max_chars=200)
        assert len(ctx) <= 220  # 200 + "[...tronqué]"
        assert "[...tronqué]" in ctx


class TestTrim:
    def setup_method(self):
        StrategicJournal.reset_singleton()
        if os.path.exists(JOURNAL_FILE):
            os.remove(JOURNAL_FILE)

    def teardown_method(self):
        StrategicJournal.reset_singleton()
        if os.path.exists(JOURNAL_FILE):
            os.remove(JOURNAL_FILE)

    def test_trim_at_max(self):
        j = StrategicJournal()
        for i in range(MAX_ENTRIES + 10):
            j.append_research_entry(f"Topic_{i}", f"F_{i}")
        assert j.entry_count() == MAX_ENTRIES
        # Les plus anciennes sont supprimées
        full = j.get_full_journal()
        assert "Topic_0" not in full
        assert f"Topic_{MAX_ENTRIES + 9}" in full


class TestPersistence:
    def setup_method(self):
        StrategicJournal.reset_singleton()
        if os.path.exists(JOURNAL_FILE):
            os.remove(JOURNAL_FILE)

    def teardown_method(self):
        StrategicJournal.reset_singleton()
        if os.path.exists(JOURNAL_FILE):
            os.remove(JOURNAL_FILE)

    def test_reload_from_file(self):
        j = StrategicJournal()
        j.append_council_entry(["a", "b"], "Sujet persisté", "CONSENSUS", "Conclusion OK")
        j.append_research_entry("Veille persistée", "Trouvaille")
        assert j.entry_count() == 2

        # Reset et recréer — doit recharger depuis le fichier
        StrategicJournal.reset_singleton()
        j2 = StrategicJournal()
        assert j2.entry_count() == 2
        full = j2.get_full_journal()
        assert "Sujet persisté" in full
        assert "Veille persistée" in full


class TestIntegrationFormat:
    def setup_method(self):
        StrategicJournal.reset_singleton()
        if os.path.exists(JOURNAL_FILE):
            os.remove(JOURNAL_FILE)

    def teardown_method(self):
        StrategicJournal.reset_singleton()
        if os.path.exists(JOURNAL_FILE):
            os.remove(JOURNAL_FILE)

    def test_entries_separated_by_separator(self):
        j = StrategicJournal()
        j.append_council_entry(["a"], "S1", "CONSENSUS", "C1")
        j.append_research_entry("S2", "F2")
        full = j.get_full_journal()
        # Le header est en premier, puis les entrées séparées par ---
        assert full.startswith(HEADER)
        parts = full.split("\n---\n")
        # header + 2 entries = au moins 3 parts
        assert len(parts) >= 3

    def test_header_always_present(self):
        j = StrategicJournal()
        assert j.get_full_journal().startswith(HEADER)
        j.append_research_entry("X", "Y")
        assert j.get_full_journal().startswith(HEADER)
