"""
Tests pour PercentileTracker — suivi adaptatif par percentiles glissants.

~25 tests en 5 classes :
- TestBasic : état initial, record, is_ready
- TestComputation : percentiles, distribution, cas limites
- TestCircularBuffer : éviction, synchro sorted, capacité
- TestPersistence : to_dict, from_dict, roundtrip
- TestEdgeCases : valeurs identiques, performance
"""

import pytest

from core.percentile_tracker import (
    DEFAULT_CAPACITY,
    DEFAULT_MIN_SAMPLES,
    PercentileTracker,
)


# ============================================================
# 1. Basique
# ============================================================

class TestBasic:

    def test_empty_not_ready(self):
        """Un tracker vide n'est pas prêt."""
        t = PercentileTracker()
        assert not t.is_ready()
        assert t.count() == 0

    def test_record_increments_count(self):
        """Chaque record incrémente le compteur."""
        t = PercentileTracker()
        t.record(42.0)
        assert t.count() == 1
        t.record(43.0)
        assert t.count() == 2

    def test_ready_at_min_samples(self):
        """Prêt exactement à min_samples."""
        t = PercentileTracker(min_samples=5)
        for i in range(4):
            t.record(float(i))
        assert not t.is_ready()
        t.record(4.0)
        assert t.is_ready()

    def test_custom_capacity_and_min_samples(self):
        """Paramètres personnalisés respectés."""
        t = PercentileTracker(capacity=100, min_samples=10)
        assert t._capacity == 100
        assert t._min_samples == 10

    def test_min_capacity_clamped(self):
        """Capacité et min_samples ne peuvent pas être < 1."""
        t = PercentileTracker(capacity=0, min_samples=0)
        assert t._capacity == 1
        assert t._min_samples == 1

    def test_repr(self):
        """__repr__ affiche count/capacity et état."""
        t = PercentileTracker(capacity=100, min_samples=5)
        assert "cold" in repr(t)
        for i in range(5):
            t.record(float(i))
        assert "ready" in repr(t)


# ============================================================
# 2. Calcul de percentiles
# ============================================================

class TestComputation:

    def test_uniform_distribution_p50(self):
        """P50 d'une distribution uniforme 0-99 ≈ 49-50."""
        t = PercentileTracker(min_samples=10)
        for i in range(100):
            t.record(float(i))
        p50 = t.get_value_at(0.5)
        assert 49 <= p50 <= 50

    def test_p0_returns_min(self):
        """P0 retourne la valeur minimale."""
        t = PercentileTracker(min_samples=1)
        for v in [10, 20, 30, 40, 50]:
            t.record(float(v))
        assert t.get_value_at(0.0) == 10.0

    def test_p100_returns_max(self):
        """P100 retourne la valeur maximale."""
        t = PercentileTracker(min_samples=1)
        for v in [10, 20, 30, 40, 50]:
            t.record(float(v))
        assert t.get_value_at(1.0) == 50.0

    def test_percentile_clamped_below_zero(self):
        """Percentile négatif → clampé à 0."""
        t = PercentileTracker(min_samples=1)
        for v in [1, 2, 3]:
            t.record(float(v))
        assert t.get_value_at(-0.5) == 1.0

    def test_percentile_clamped_above_one(self):
        """Percentile > 1 → clampé à 1."""
        t = PercentileTracker(min_samples=1)
        for v in [1, 2, 3]:
            t.record(float(v))
        assert t.get_value_at(1.5) == 3.0

    def test_get_percentile_of_middle(self):
        """get_percentile_of d'une valeur médiane ≈ 0.5."""
        t = PercentileTracker(min_samples=1)
        for i in range(100):
            t.record(float(i))
        pct = t.get_percentile_of(49.0)
        assert 0.45 <= pct <= 0.55

    def test_get_percentile_of_below_all(self):
        """Valeur inférieure à tout → percentile ≈ 0."""
        t = PercentileTracker(min_samples=1)
        for v in [50, 60, 70]:
            t.record(float(v))
        assert t.get_percentile_of(10.0) == 0.0

    def test_get_percentile_of_above_all(self):
        """Valeur supérieure à tout → percentile = 1.0."""
        t = PercentileTracker(min_samples=1)
        for v in [10, 20, 30]:
            t.record(float(v))
        assert t.get_percentile_of(100.0) == 1.0

    def test_duplicates_handled(self):
        """Les doublons sont correctement comptés."""
        t = PercentileTracker(min_samples=1)
        for _ in range(10):
            t.record(50.0)
        assert t.count() == 10
        assert t.get_percentile_of(50.0) == 1.0
        assert t.get_value_at(0.5) == 50.0

    def test_empty_get_percentile_of(self):
        """Tracker vide → percentile = 0.0."""
        t = PercentileTracker()
        assert t.get_percentile_of(42.0) == 0.0

    def test_empty_get_value_at(self):
        """Tracker vide → valeur = 0.0."""
        t = PercentileTracker()
        assert t.get_value_at(0.5) == 0.0


# ============================================================
# 3. Buffer circulaire
# ============================================================

class TestCircularBuffer:

    def test_eviction_oldest(self):
        """Au-delà de la capacité, les valeurs les plus anciennes sont évincées."""
        t = PercentileTracker(capacity=5, min_samples=1)
        for v in [1, 2, 3, 4, 5]:
            t.record(float(v))
        assert t.count() == 5
        # Ajouter un 6e → le 1 est évincé
        t.record(6.0)
        assert t.count() == 5
        assert t.get_value_at(0.0) == 2.0  # Min = 2, pas 1

    def test_sorted_sync_after_eviction(self):
        """La liste triée reste synchronisée après éviction."""
        t = PercentileTracker(capacity=3, min_samples=1)
        t.record(10.0)
        t.record(20.0)
        t.record(30.0)
        # Buffer plein : [10, 20, 30], sorted: [10, 20, 30]
        t.record(5.0)
        # 10 évincé → buffer: [20, 30, 5], sorted: [5, 20, 30]
        assert t.count() == 3
        assert len(t._sorted) == 3
        assert t.get_value_at(0.0) == 5.0
        assert t.get_value_at(1.0) == 30.0

    def test_capacity_one(self):
        """Capacité 1 : seule la dernière valeur est gardée."""
        t = PercentileTracker(capacity=1, min_samples=1)
        t.record(100.0)
        t.record(200.0)
        assert t.count() == 1
        assert t.get_value_at(0.5) == 200.0

    def test_small_buffer_full_cycle(self):
        """Cycle complet d'un petit buffer."""
        t = PercentileTracker(capacity=3, min_samples=1)
        # Remplir
        t.record(1.0)
        t.record(2.0)
        t.record(3.0)
        # Évincer tout le premier contenu
        t.record(4.0)
        t.record(5.0)
        t.record(6.0)
        assert t.count() == 3
        assert t.get_value_at(0.0) == 4.0
        assert t.get_value_at(1.0) == 6.0


# ============================================================
# 4. Persistance
# ============================================================

class TestPersistence:

    def test_to_dict_structure(self):
        """to_dict contient capacity, min_samples, values."""
        t = PercentileTracker(capacity=100, min_samples=10)
        t.record(1.0)
        t.record(2.0)
        d = t.to_dict()
        assert d["capacity"] == 100
        assert d["min_samples"] == 10
        assert d["values"] == [1.0, 2.0]

    def test_roundtrip(self):
        """to_dict → from_dict restaure les données et percentiles."""
        t = PercentileTracker(capacity=50, min_samples=5)
        for i in range(20):
            t.record(float(i))

        d = t.to_dict()
        t2 = PercentileTracker.from_dict(d)

        assert t2.count() == t.count()
        assert t2.is_ready() == t.is_ready()
        assert t2._capacity == t._capacity
        assert t2._min_samples == t._min_samples
        assert t2.get_value_at(0.5) == t.get_value_at(0.5)

    def test_empty_roundtrip(self):
        """Roundtrip d'un tracker vide."""
        t = PercentileTracker()
        d = t.to_dict()
        t2 = PercentileTracker.from_dict(d)
        assert t2.count() == 0
        assert not t2.is_ready()

    def test_from_dict_bad_data(self):
        """from_dict avec données manquantes → tracker vide avec defaults."""
        t = PercentileTracker.from_dict({})
        assert t.count() == 0
        assert t._capacity == DEFAULT_CAPACITY
        assert t._min_samples == DEFAULT_MIN_SAMPLES

    def test_from_dict_values_converted_to_float(self):
        """from_dict convertit les valeurs en float."""
        t = PercentileTracker.from_dict({
            "capacity": 10,
            "min_samples": 2,
            "values": [1, 2, 3],  # ints, pas floats
        })
        assert t.count() == 3
        assert t.get_value_at(0.0) == 1.0


# ============================================================
# 5. Cas limites
# ============================================================

class TestEdgeCases:

    def test_identical_values(self):
        """Toutes les valeurs identiques → percentile_of = 1.0 pour cette valeur."""
        t = PercentileTracker(min_samples=1)
        for _ in range(50):
            t.record(42.0)
        assert t.get_percentile_of(42.0) == 1.0
        assert t.get_percentile_of(41.9) == 0.0
        assert t.get_value_at(0.5) == 42.0

    def test_performance_default_capacity(self):
        """Insertion de 8640 valeurs en temps raisonnable."""
        t = PercentileTracker(min_samples=100)
        for i in range(DEFAULT_CAPACITY):
            t.record(float(i % 100))
        assert t.count() == DEFAULT_CAPACITY
        assert t.is_ready()
        # Vérifier que les lookups fonctionnent encore
        p50 = t.get_value_at(0.5)
        assert 0 <= p50 <= 100

    def test_negative_values(self):
        """Les valeurs négatives sont supportées."""
        t = PercentileTracker(min_samples=1)
        for v in [-10, -5, 0, 5, 10]:
            t.record(float(v))
        assert t.get_value_at(0.0) == -10.0
        assert t.get_value_at(1.0) == 10.0

    def test_float_precision(self):
        """Les valeurs flottantes proches sont distinguées."""
        t = PercentileTracker(min_samples=1)
        t.record(1.001)
        t.record(1.002)
        t.record(1.003)
        assert t.get_value_at(0.0) == 1.001
        assert t.get_value_at(1.0) == 1.003
