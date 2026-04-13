"""Tests unitaires pour experience_clock — Phase C Etape 2b.

Ces tests sont TOTALEMENT ISOLES de Promethee en cours d'execution. Ils
verifient la semantique de l'horloge metabolique (compteur RAM, persistance
differee, performance) sans depasser 100ms pour 10k ticks.
"""

import json
import os
import tempfile
import time
import pytest
from unittest.mock import patch

from core.experience_clock import ExperienceClock


class TestSingletonBehavior:
    def setup_method(self):
        ExperienceClock.reset_singleton()

    def teardown_method(self):
        ExperienceClock.reset_singleton()

    def test_singleton_returns_same_instance(self):
        with patch("core.experience_clock.EXPERIENCE_CLOCK_FILE", "/nonexistent/x.json"):
            c1 = ExperienceClock()
            c2 = ExperienceClock()
            assert c1 is c2

    def test_initial_cycle_zero_if_no_file(self):
        with patch("core.experience_clock.EXPERIENCE_CLOCK_FILE", "/nonexistent/x.json"):
            ExperienceClock.reset_singleton()
            clock = ExperienceClock()
            assert clock.current() == 0


class TestTickSemantics:
    def setup_method(self):
        ExperienceClock.reset_singleton()

    def teardown_method(self):
        ExperienceClock.reset_singleton()

    def test_tick_increments_by_one_by_default(self):
        with patch("core.experience_clock.EXPERIENCE_CLOCK_FILE", "/nonexistent/x.json"):
            ExperienceClock.reset_singleton()
            clock = ExperienceClock()
            before = clock.current()
            clock.tick()
            assert clock.current() == before + 1

    def test_tick_accepts_n_cycles(self):
        with patch("core.experience_clock.EXPERIENCE_CLOCK_FILE", "/nonexistent/x.json"):
            ExperienceClock.reset_singleton()
            clock = ExperienceClock()
            before = clock.current()
            clock.tick(5)
            assert clock.current() == before + 5

    def test_tick_zero_or_negative_is_noop(self):
        with patch("core.experience_clock.EXPERIENCE_CLOCK_FILE", "/nonexistent/x.json"):
            ExperienceClock.reset_singleton()
            clock = ExperienceClock()
            before = clock.current()
            clock.tick(0)
            clock.tick(-5)
            assert clock.current() == before

    def test_current_is_pure_read(self):
        with patch("core.experience_clock.EXPERIENCE_CLOCK_FILE", "/nonexistent/x.json"):
            ExperienceClock.reset_singleton()
            clock = ExperienceClock()
            # 1000 reads ne doivent PAS incrementer
            for _ in range(1000):
                clock.current()
            assert clock.current() == 0


class TestDeferredPersistence:
    def setup_method(self):
        ExperienceClock.reset_singleton()

    def teardown_method(self):
        ExperienceClock.reset_singleton()

    def test_no_immediate_save_on_tick(self):
        """Garde-fou I/O : tick() ne doit pas ecrire sur disque."""
        with tempfile.TemporaryDirectory() as tmpdir:
            clock_file = os.path.join(tmpdir, "clock.json")
            with patch("core.experience_clock.EXPERIENCE_CLOCK_FILE", clock_file):
                with patch("core.experience_clock.PERSIST_INTERVAL_S", 9999.0):
                    ExperienceClock.reset_singleton()
                    clock = ExperienceClock()
                    clock.tick()
                    clock.tick()
                    clock.tick()
                    # Intervalle non atteint -> aucune ecriture
                    assert not os.path.exists(clock_file)

    def test_force_persist_writes_immediately(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            clock_file = os.path.join(tmpdir, "clock.json")
            with patch("core.experience_clock.EXPERIENCE_CLOCK_FILE", clock_file):
                ExperienceClock.reset_singleton()
                clock = ExperienceClock()
                clock.tick(42)
                clock.force_persist()
                assert os.path.exists(clock_file)
                with open(clock_file, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                assert data["cycle"] == 42
                assert "saved_at" in data

    def test_force_persist_noop_if_not_dirty(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            clock_file = os.path.join(tmpdir, "clock.json")
            with patch("core.experience_clock.EXPERIENCE_CLOCK_FILE", clock_file):
                ExperienceClock.reset_singleton()
                clock = ExperienceClock()
                # Jamais de tick -> pas dirty -> pas d'ecriture
                clock.force_persist()
                assert not os.path.exists(clock_file)

    def test_automatic_save_when_interval_reached(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            clock_file = os.path.join(tmpdir, "clock.json")
            with patch("core.experience_clock.EXPERIENCE_CLOCK_FILE", clock_file):
                with patch("core.experience_clock.PERSIST_INTERVAL_S", 0.0):
                    ExperienceClock.reset_singleton()
                    clock = ExperienceClock()
                    clock.tick(10)
                    # Avec interval=0, l'auto-save s'est declenche
                    assert os.path.exists(clock_file)

    def test_load_restores_previous_state(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            clock_file = os.path.join(tmpdir, "clock.json")
            with open(clock_file, "w", encoding="utf-8") as fh:
                json.dump({"cycle": 12345, "saved_at": time.time()}, fh)
            with patch("core.experience_clock.EXPERIENCE_CLOCK_FILE", clock_file):
                ExperienceClock.reset_singleton()
                clock = ExperienceClock()
                assert clock.current() == 12345

    def test_load_corrupted_file_starts_at_zero(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            clock_file = os.path.join(tmpdir, "clock.json")
            with open(clock_file, "w", encoding="utf-8") as fh:
                fh.write("not valid json{")
            with patch("core.experience_clock.EXPERIENCE_CLOCK_FILE", clock_file):
                ExperienceClock.reset_singleton()
                clock = ExperienceClock()
                assert clock.current() == 0


class TestPerformance:
    def setup_method(self):
        ExperienceClock.reset_singleton()

    def teardown_method(self):
        ExperienceClock.reset_singleton()

    def test_10k_ticks_under_100ms(self):
        """Garde-fou performance explicitement demande par Gemini."""
        with tempfile.TemporaryDirectory() as tmpdir:
            clock_file = os.path.join(tmpdir, "clock.json")
            with patch("core.experience_clock.EXPERIENCE_CLOCK_FILE", clock_file):
                with patch("core.experience_clock.PERSIST_INTERVAL_S", 9999.0):
                    ExperienceClock.reset_singleton()
                    clock = ExperienceClock()
                    start = time.perf_counter()
                    for _ in range(10000):
                        clock.tick()
                    elapsed = time.perf_counter() - start
                    assert elapsed < 0.1, (
                        f"10k ticks took {elapsed*1000:.1f}ms "
                        f"(budget 100ms) — I/O hit possible"
                    )
                    assert clock.current() == 10000
