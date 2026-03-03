# core/neural_tissue.py
"""Substrat cellulaire neuronal — computation émergente massivement parallèle.

0 LLM, 100% déterministe. Chaque cellule exécute un micro-programme génomique.
Les patterns utiles survivent et se répliquent (sélection naturelle cognitive).

Alphabet cognitif :
  A = Activate  (propager signal vers un voisin)
  C = Capture   (lire le signal cognitif à cette position)
  G = Generate  (produire un pattern — récompensé si signal capté)
  T = Transform (amplifier/atténuer le signal interne)
  I = Inhibit   (supprimer le signal local)
  R = Replicate (division cellulaire si énergie suffisante)
"""

import json
import os
import time
import random
import asyncio
import logging
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from collections import Counter

logger = logging.getLogger("neural_tissue")

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TISSUE_STATE_FILE = os.path.join(PROJECT_ROOT, "memory", "neural_tissue_state.json")

# --- Constantes ---
ALPHABET = "ACGTIR"

GRID_SIZE = 16                  # 16x16 = 256 positions
MAX_CELLS = 500
INITIAL_CELLS = 30
INITIAL_ENERGY = 100.0
DIVISION_THRESHOLD = 180.0
MAINTENANCE_COST = 1.0
ACTION_COST = 0.5
CAPTURE_REWARD = 3.0
GENERATE_REWARD = 2.0
MUTATION_RATE = 0.02            # 2% par nucléotide
INSERTION_RATE = 0.005
DELETION_RATE = 0.003
MAX_GENOME_LENGTH = 24
MIN_GENOME_LENGTH = 2
SIGNAL_DECAY = 0.92
MAX_GRID_SIGNAL = 5.0              # Saturation : signal max par cellule de grille
TICK_INTERVAL = 2.0
SAVE_EVERY_N_TICKS = 50
FOOD_SPAWN_PER_ZONE = 2
PATTERN_TRACK_SIZE = 20
EXTINCTION_THRESHOLD = 5       # Repeuplement si < 5 cellules

# Seuils efférents (Sprint 5)
THRESHOLD_ZONE_OVERLOAD = 2.5       # Activité moyenne zone > seuil
THRESHOLD_ZONE_DESERT = 0.05        # Activité moyenne zone < seuil
THRESHOLD_EXTINCTION_RISK = 10      # Population < seuil
THRESHOLD_DIVERSITY_DROP = 3        # Génomes uniques < seuil
THRESHOLD_CREATIVITY_SPIKE = 1.5    # Activité zone creativity > seuil
PUBLISH_COOLDOWN_TICKS = 10         # Min 10 ticks (20s) entre publications

# Mapping goal → zone(s) bonusée(s) (Sprint 6 - Pression sélective)
# Mots-clés dans le titre du goal → zones qui reçoivent un bonus de nourriture
GOAL_ZONE_MAP = {
    "explor":     ["creativity", "desire"],
    "créati":     ["creativity"],
    "innov":      ["creativity", "cognition"],
    "sécuri":     ["threat", "stability"],
    "audit":      ["stability", "threat"],
    "refactor":   ["stability", "cognition"],
    "mémoire":    ["memory"],
    "memory":     ["memory"],
    "debug":      ["cognition", "threat"],
    "evolut":     ["creativity", "dopamine"],
    "apprend":    ["memory", "cognition"],
    "learn":      ["memory", "cognition"],
    "optimi":     ["cognition", "stability"],
    "comprendr":  ["cognition", "memory"],
}
GOAL_FOOD_BONUS = 2  # Food supplémentaire par zone bonusée par goal actif

# Zones de la grille où les signaux cognitifs sont injectés
SIGNAL_ZONES = {
    "emotion":    (0,  0,  4,  4),
    "threat":     (12, 0,  16, 4),
    "dopamine":   (0,  12, 4,  16),
    "goals":      (12, 12, 16, 16),
    "desire":     (6,  0,  10, 4),
    "memory":     (6,  12, 10, 16),
    "stability":  (0,  6,  4,  10),
    "creativity": (12, 6,  16, 10),
    "cognition":  (6,  6,  10, 10),   # Matrice grise — néocortex
}


# ─────────────────────────────────────────────
# Cellule Neurale
# ─────────────────────────────────────────────

@dataclass
class NeuralCell:
    """Une cellule neurale avec un génome cognitif."""
    genome: str
    x: int
    y: int
    energy: float = INITIAL_ENERGY
    pointer: int = 0
    alive: bool = True
    age: int = 0
    generation: int = 0
    register: float = 0.0
    output_count: int = 0

    def tick(self, grid, neighbors, capture_reward=None, generate_reward=None,
             mutation_rate=None):
        """Exécute un cycle du génome."""
        if self.energy <= 0 or not self.alive:
            self.alive = False
            return None

        self._eff_mutation_rate = mutation_rate
        instruction = self.genome[self.pointer]
        child = self._execute(
            instruction, grid, neighbors,
            capture_reward if capture_reward is not None else CAPTURE_REWARD,
            generate_reward if generate_reward is not None else GENERATE_REWARD,
        )

        self.energy -= MAINTENANCE_COST
        if self.energy <= 0:
            self.alive = False
        self.pointer = (self.pointer + 1) % len(self.genome)
        self.age += 1

        return child

    def _execute(self, instruction, grid, neighbors, eff_capture, eff_generate):
        """Exécute une instruction cognitive."""
        if instruction == 'A':
            # Activate — propager signal vers un voisin
            if neighbors:
                target = random.choice(neighbors)
                target.energy += self.register * 0.3
            self.energy -= ACTION_COST

        elif instruction == 'C':
            # Capture — lire signal à cette position
            signal = grid[self.y][self.x]
            if signal > 0.1:
                self.register = signal
                self.energy += eff_capture * min(signal, 2.0)
                grid[self.y][self.x] *= 0.5
            else:
                self.register = 0.0

        elif instruction == 'G':
            # Generate — produire un pattern (récompensé si signal capté)
            if self.register > 0.1:
                self.output_count += 1
                self.energy += eff_generate
            self.energy -= ACTION_COST

        elif instruction == 'T':
            # Transform — amplifier/atténuer le signal interne
            if self.register > 0.5:
                self.register = min(self.register * 1.5, 5.0)
            else:
                self.register *= 0.5
            self.energy -= ACTION_COST

        elif instruction == 'I':
            # Inhibit — supprimer le signal local
            grid[self.y][self.x] *= 0.3
            self.energy -= ACTION_COST

        elif instruction == 'R':
            # Replicate — division cellulaire
            if self.energy > DIVISION_THRESHOLD:
                return self._replicate()

        # Instruction inconnue → NOP (tolérance aux mutations)
        return None

    def _replicate(self):
        """Division cellulaire avec mutation."""
        self.energy /= 2
        eff_rate = getattr(self, '_eff_mutation_rate', None)
        child_genome = mutate(self.genome, mutation_rate=eff_rate)
        dx, dy = random.choice([(0, 1), (0, -1), (1, 0), (-1, 0)])
        cx = (self.x + dx) % GRID_SIZE
        cy = (self.y + dy) % GRID_SIZE
        return NeuralCell(
            genome=child_genome,
            x=cx, y=cy,
            energy=self.energy,
            generation=self.generation + 1,
        )


# ─────────────────────────────────────────────
# Mutation
# ─────────────────────────────────────────────

def mutate(genome: str, mutation_rate: float = None) -> str:
    """Mutation d'un génome : substitution, insertion, délétion."""
    rate = mutation_rate if mutation_rate is not None else MUTATION_RATE
    result = list(genome)

    for i in range(len(result)):
        if random.random() < rate:
            result[i] = random.choice(ALPHABET)

    if random.random() < INSERTION_RATE and len(result) < MAX_GENOME_LENGTH:
        pos = random.randint(0, len(result))
        result.insert(pos, random.choice(ALPHABET))

    if random.random() < DELETION_RATE and len(result) > MIN_GENOME_LENGTH:
        pos = random.randint(0, len(result) - 1)
        result.pop(pos)

    return "".join(result)


# ─────────────────────────────────────────────
# Substrat Tissulaire (Singleton)
# ─────────────────────────────────────────────

class NeuralTissue:
    """Singleton — substrat cellulaire pour computation émergente."""

    _instance: Optional["NeuralTissue"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True

        self.grid = [[0.0] * GRID_SIZE for _ in range(GRID_SIZE)]
        self.cells: List[NeuralCell] = []
        self.tick_count = 0
        self.total_births = 0
        self.total_deaths = 0
        self.dominant_patterns: List[Dict[str, Any]] = []
        self._task: Optional[asyncio.Task] = None
        self._subscribed = False
        self._running = False

        # Cache de l'état cognitif (mis à jour par les événements bus)
        self._cognitive_state = {
            "emotion_intensity": 0.5,
            "threat_level": 0.0,
            "dopamine_level": 0.5,
            "goal_count": 0,
            "desire_intensity": 50.0,
            "memory_activity": 0.3,
            "stability": 0.7,
            "creativity": 0.3,
            "cognition_level": 0.3,
        }

        self._last_tick_ms: float = 0.0
        self._zone_signals: Dict[str, Dict[str, float]] = {}
        # Cooldowns pour events haute fréquence (tick_count au dernier traitement)
        self._event_cooldowns: Dict[str, int] = {}
        # Cooldowns pour publications de seuil
        self._publish_cooldowns: Dict[str, int] = {}
        # Zones bonusées par les goals actifs (Sprint 6)
        self._goal_bonus_zones: Dict[str, int] = {}  # zone_name → nb bonus food
        # Phase circadienne courante (Sprint 6)
        self._circadian_phase: str = "eveil"
        # Boucles de rétroaction (Sprint 7) — suivi historique zone threat
        self._threat_was_high: bool = False  # zone threat était en surcharge

        self._load()

    @classmethod
    def reset_singleton(cls):
        cls._instance = None

    # --- Initialisation ---

    def init(self):
        """Souscrit aux événements bus et initialise la population."""
        if self._subscribed:
            return
        try:
            from core.event_bus.bus import bus
            bus.subscribe("CARDIAC_BEAT", self._on_cardiac_beat)
            bus.subscribe("REPTILIAN_ALERT", self._on_reptilian_alert)
            bus.subscribe("DOPAMINE_SURGE", self._on_dopamine_surge)
            bus.subscribe("DOPAMINE_DIP", self._on_dopamine_dip)
            bus.subscribe("CIRCADIAN_PHASE_CHANGE", self._on_circadian_change)
            # Sprint 2 — Grand Câblage : 8 nouvelles connexions
            bus.subscribe("PREFRONTAL_GOAL_CREATED", self._on_goal_created)
            bus.subscribe("PREFRONTAL_GOAL_COMPLETE", self._on_goal_complete)
            bus.subscribe("PREFRONTAL_GOAL_ABANDONED", self._on_goal_abandoned)
            bus.subscribe("CORPUS_CALLOSUM_STATE", self._on_corpus_callosum)
            bus.subscribe("INNER_VOICE_BROADCAST", self._on_inner_voice)
            bus.subscribe("HALLUCINATION_DETECTED", self._on_hallucination)
            bus.subscribe("AUTONOMY_ROUTINE_COMPLETE", self._on_routine_complete)
            bus.subscribe("KNOWLEDGE_GAP_DETECTED", self._on_knowledge_gap)
            # Afférences complètes — 7 canaux supplémentaires
            bus.subscribe("PREFRONTAL_THOUGHT", self._on_prefrontal_thought)
            bus.subscribe("PSYCHE_UPDATE", self._on_psyche_update)
            bus.subscribe("SYNAPTIC_UPDATE", self._on_synaptic_update)
            bus.subscribe("COUNCIL_END", self._on_council_end)
            bus.subscribe("EVOLUTION_FEEDBACK", self._on_evolution_feedback)
            bus.subscribe("EXPERIENCE_RECORDED", self._on_experience_recorded)
            bus.subscribe("SOLILOQUE_EXCHANGE", self._on_soliloque_exchange)
            self._subscribed = True
            logger.info("TISSUE: Substrat cellulaire neuronal actif (20 canaux).")
        except Exception as e:
            logger.warning(f"TISSUE: Erreur init bus: {e}")

        if not self.cells:
            self._seed_population()

    def start_loop(self):
        """Démarre la boucle de ticks async."""
        if self._running:
            return
        self._running = True
        try:
            loop = asyncio.get_running_loop()
            self._task = loop.create_task(self._tick_loop())
        except RuntimeError:
            pass

    def stop_loop(self):
        """Arrête la boucle."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()

    # --- Population ---

    def _seed_population(self):
        """Crée la population initiale avec des génomes aléatoires."""
        self.cells = []
        for _ in range(INITIAL_CELLS):
            genome_len = random.randint(3, 8)
            genome = "".join(random.choice(ALPHABET) for _ in range(genome_len))
            x = random.randint(0, GRID_SIZE - 1)
            y = random.randint(0, GRID_SIZE - 1)
            self.cells.append(NeuralCell(genome=genome, x=x, y=y))
        self.total_births += INITIAL_CELLS

    def _dawn_repopulate(self):
        """Aube après sommeil : injecter des cellules fraîches dans les zones désertées."""
        if not self._zone_signals:
            return
        desert_zones = [
            name for name, sig in self._zone_signals.items()
            if sig.get("density", 0) < 0.05
        ]
        spawned = 0
        for zone_name in desert_zones:
            bounds = SIGNAL_ZONES.get(zone_name)
            if not bounds:
                continue
            x1, y1, x2, y2 = bounds
            for _ in range(3):  # 3 cellules fraîches par zone désertée
                if len(self.cells) >= MAX_CELLS:
                    break
                genome_len = random.randint(3, 8)
                genome = "".join(random.choice(ALPHABET) for _ in range(genome_len))
                x = random.randint(x1, min(x2 - 1, GRID_SIZE - 1))
                y = random.randint(y1, min(y2 - 1, GRID_SIZE - 1))
                self.cells.append(NeuralCell(genome=genome, x=x, y=y))
                spawned += 1
        if spawned:
            self.total_births += spawned
            logger.info(f"TISSUE: Aube — {spawned} cellules fraîches dans {len(desert_zones)} zones désertées")

    def _get_effective_mutation_rate(self) -> float:
        """Taux de mutation modulé par la phase circadienne."""
        if self._circadian_phase == "crepuscule":
            return MUTATION_RATE * 2.0   # Exploration — mutation doublée
        if self._circadian_phase == "sommeil_profond":
            return MUTATION_RATE * 0.5   # Consolidation — mutation réduite
        return MUTATION_RATE             # Éveil/aube — normal

    # --- Boucle principale ---

    async def _tick_loop(self):
        """Boucle de ticks continue."""
        while self._running:
            try:
                self._tick()
                if self.tick_count % SAVE_EVERY_N_TICKS == 0:
                    self._save()
                await asyncio.sleep(TICK_INTERVAL)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"TISSUE: Erreur tick: {e}")
                await asyncio.sleep(5.0)

    def _tick(self):
        """Un cycle complet : injecter signaux, exécuter cellules, sélection."""
        _t0 = time.perf_counter()

        # 1. Injecter les signaux cognitifs
        self._inject_signals()

        # 1b. Calculer les rewards effectifs modulés par la dopamine
        dopamine = self._cognitive_state.get("dopamine_level", 0.5)
        eff_capture = CAPTURE_REWARD * (0.5 + dopamine)   # 1.5 à 4.5
        eff_generate = GENERATE_REWARD * (0.5 + dopamine)  # 1.0 à 3.0

        # 1c. Taux de mutation modulé par le circadien
        eff_mutation = self._get_effective_mutation_rate()

        # 2. Exécuter chaque cellule
        new_cells = []
        for cell in self.cells:
            if not cell.alive:
                continue
            neighbors = self._get_neighbors(cell)
            child = cell.tick(self.grid, neighbors, eff_capture, eff_generate,
                              eff_mutation)
            if child is not None:
                new_cells.append(child)
                self.total_births += 1

        # 3. Ajouter les enfants (si pas surpopulation)
        for child in new_cells:
            if len(self.cells) < MAX_CELLS:
                self.cells.append(child)

        # 4. Supprimer les morts
        before = len(self.cells)
        self.cells = [c for c in self.cells if c.alive and c.energy > 0]
        self.total_deaths += before - len(self.cells)

        # 5. Repeuplement d'urgence si extinction
        if len(self.cells) < EXTINCTION_THRESHOLD:
            self._seed_population()

        # 6. Decay des signaux + saturation (garde-fou anti-divergence)
        for row in self.grid:
            for x in range(len(row)):
                row[x] = min(row[x] * SIGNAL_DECAY, MAX_GRID_SIGNAL)

        # 7. Mettre à jour les patterns dominants
        self._update_dominant_patterns()

        # 8. Publier si pattern émergent significatif
        self._check_emergence()

        # 9. Mettre à jour les signaux de zone
        self._update_zone_signals()

        # 10. Publier les événements de seuil (efférences)
        self._check_thresholds()

        self.tick_count += 1
        self._last_tick_ms = (time.perf_counter() - _t0) * 1000.0
        if self._last_tick_ms > 500.0:
            logger.warning(f"TISSUE: tick lent {self._last_tick_ms:.1f}ms ({len(self.cells)} cellules)")

    def _inject_signals(self):
        """Injecte les signaux cognitifs sur la grille selon l'état des organes."""
        state = self._cognitive_state
        zone_intensities = {
            "emotion":    state["emotion_intensity"],
            "threat":     min(state["threat_level"] / 10.0, 1.0),
            "dopamine":   state["dopamine_level"],
            "goals":      min(state["goal_count"] / 3.0, 1.0),
            "desire":     min(state["desire_intensity"] / 100.0, 1.0),
            "memory":     state["memory_activity"],
            "stability":  state["stability"],
            "creativity": state["creativity"],
            "cognition":  state["cognition_level"],
        }

        # Phase circadienne : en sommeil, pas d'injection (sélection par mérite)
        if self._circadian_phase == "sommeil_profond":
            return

        for zone_name, (x1, y1, x2, y2) in SIGNAL_ZONES.items():
            intensity = zone_intensities.get(zone_name, 0.3)
            # Nombre de food spawns : base + bonus des goals actifs
            food_count = FOOD_SPAWN_PER_ZONE + self._goal_bonus_zones.get(zone_name, 0)
            for _ in range(food_count):
                sx = random.randint(x1, min(x2 - 1, GRID_SIZE - 1))
                sy = random.randint(y1, min(y2 - 1, GRID_SIZE - 1))
                self.grid[sy][sx] = min(
                    self.grid[sy][sx] + intensity * random.uniform(0.5, 1.5),
                    MAX_GRID_SIGNAL
                )

    def _get_neighbors(self, cell: NeuralCell) -> List[NeuralCell]:
        """Retourne les cellules adjacentes (rayon 2, wrap-around)."""
        neighbors = []
        for other in self.cells:
            if other is cell or not other.alive:
                continue
            dx = abs(other.x - cell.x)
            dy = abs(other.y - cell.y)
            dx = min(dx, GRID_SIZE - dx)
            dy = min(dy, GRID_SIZE - dy)
            if dx <= 2 and dy <= 2:
                neighbors.append(other)
        return neighbors

    def _update_dominant_patterns(self):
        """Identifie les génomes les plus fréquents."""
        if not self.cells:
            return

        genome_counter = Counter(c.genome for c in self.cells if c.alive)
        top = genome_counter.most_common(PATTERN_TRACK_SIZE)

        self.dominant_patterns = []
        for genome, count in top:
            cells_with = [c for c in self.cells if c.genome == genome and c.alive]
            avg_fitness = sum(
                c.output_count / max(c.age, 1) for c in cells_with
            ) / max(len(cells_with), 1)
            self.dominant_patterns.append({
                "genome": genome,
                "count": count,
                "frequency": round(count / len(self.cells), 4),
                "avg_fitness": round(avg_fitness, 4),
            })

    def _check_emergence(self):
        """Publie un événement si un pattern domine fortement."""
        if not self.dominant_patterns:
            return
        top = self.dominant_patterns[0]
        # Pattern significatif si > 30% de la population et tick > 100
        if top["frequency"] > 0.30 and self.tick_count > 100:
            if self.tick_count % 50 == 0:  # Pas de spam
                try:
                    from core.event_bus.bus import bus
                    import asyncio as _aio
                    loop = _aio.get_running_loop()
                    loop.create_task(bus.publish("TISSUE_PATTERN_EMERGED", {
                        "genome": top["genome"],
                        "frequency": top["frequency"],
                        "fitness": top["avg_fitness"],
                        "tick": self.tick_count,
                        "population": len(self.cells),
                    }))
                except Exception:
                    pass

    # --- Signaux de zone ---

    def _update_zone_signals(self):
        """Calcule 4 métriques par zone : activity, density, energy, diversity."""
        signals = {}
        for zone_name, (x1, y1, x2, y2) in SIGNAL_ZONES.items():
            # Activity : moyenne des signaux grille dans la zone
            total_signal = 0.0
            area = (x2 - x1) * (y2 - y1)
            for gy in range(y1, y2):
                for gx in range(x1, x2):
                    total_signal += self.grid[gy][gx]
            activity = total_signal / max(area, 1)

            # Cellules dans cette zone
            zone_cells = [
                c for c in self.cells
                if c.alive and x1 <= c.x < x2 and y1 <= c.y < y2
            ]
            nb_cells = len(zone_cells)

            # Density : cellules vivantes / surface zone
            density = nb_cells / max(area, 1)

            # Energy : énergie moyenne des cellules (0.0 si aucune)
            energy = (
                sum(c.energy for c in zone_cells) / nb_cells
                if nb_cells > 0 else 0.0
            )

            # Diversity : génomes uniques / nb cellules (0.0 si aucune)
            diversity = (
                len(set(c.genome for c in zone_cells)) / nb_cells
                if nb_cells > 0 else 0.0
            )

            signals[zone_name] = {
                "activity": round(activity, 4),
                "density": round(density, 4),
                "energy": round(energy, 1),
                "diversity": round(diversity, 4),
            }
        self._zone_signals = signals

    def get_zone_signals(self) -> Dict[str, Dict[str, float]]:
        """Retourne les signaux de zone (shallow copy)."""
        return dict(self._zone_signals)

    # --- Efférences de seuil (Sprint 5) ---

    def _publish_cooldown_ok(self, event_name: str) -> bool:
        """Vérifie le cooldown de publication (min PUBLISH_COOLDOWN_TICKS entre 2 pubs)."""
        last = self._publish_cooldowns.get(event_name, -PUBLISH_COOLDOWN_TICKS)
        if self.tick_count - last < PUBLISH_COOLDOWN_TICKS:
            return False
        self._publish_cooldowns[event_name] = self.tick_count
        return True

    def _try_publish(self, event_name: str, payload: dict):
        """Publie un event si le cooldown est OK (fire-and-forget async)."""
        if not self._publish_cooldown_ok(event_name):
            return
        try:
            from core.event_bus.bus import bus
            import asyncio as _aio
            loop = _aio.get_running_loop()
            loop.create_task(bus.publish(event_name, payload))
            logger.debug(f"TISSUE: {event_name} publié")
        except Exception:
            pass

    def _check_thresholds(self):
        """Vérifie les seuils et publie les events efférents."""
        if not self._zone_signals:
            return

        alive = [c for c in self.cells if c.alive]
        population = len(alive)

        # TISSUE_EXTINCTION_RISK — population dangereusement basse
        if population < THRESHOLD_EXTINCTION_RISK:
            self._try_publish("TISSUE_EXTINCTION_RISK", {
                "population": population,
                "tick": self.tick_count,
            })

        # TISSUE_DIVERSITY_DROP — trop peu de génomes uniques
        unique_genomes = len(set(c.genome for c in alive)) if alive else 0
        if 0 < unique_genomes < THRESHOLD_DIVERSITY_DROP and population > EXTINCTION_THRESHOLD:
            self._try_publish("TISSUE_DIVERSITY_DROP", {
                "unique_genomes": unique_genomes,
                "population": population,
                "tick": self.tick_count,
            })

        # Vérification par zone
        for zone_name, sig in self._zone_signals.items():
            activity = sig.get("activity", 0.0)

            # TISSUE_ZONE_OVERLOAD — zone surchargée
            if activity > THRESHOLD_ZONE_OVERLOAD:
                self._try_publish("TISSUE_ZONE_OVERLOAD", {
                    "zone": zone_name,
                    "activity": activity,
                    "tick": self.tick_count,
                })

            # TISSUE_ZONE_DESERT — zone déserte
            if activity < THRESHOLD_ZONE_DESERT and self.tick_count > 50:
                self._try_publish("TISSUE_ZONE_DESERT", {
                    "zone": zone_name,
                    "activity": activity,
                    "tick": self.tick_count,
                })

        # TISSUE_CREATIVITY_SPIKE — zone creativity en pointe
        creativity_sig = self._zone_signals.get("creativity", {})
        if creativity_sig.get("activity", 0.0) > THRESHOLD_CREATIVITY_SPIKE:
            self._try_publish("TISSUE_CREATIVITY_SPIKE", {
                "activity": creativity_sig["activity"],
                "density": creativity_sig.get("density", 0.0),
                "tick": self.tick_count,
            })

        # Boucle menace ↔ tissu (Sprint 7) — retour au calme
        threat_sig = self._zone_signals.get("threat", {})
        threat_activity = threat_sig.get("activity", 0.0)
        if threat_activity > THRESHOLD_ZONE_OVERLOAD:
            self._threat_was_high = True
        elif self._threat_was_high and threat_activity < 0.5:
            self._threat_was_high = False
            self._try_publish("TISSUE_THREAT_SUBSIDED", {
                "activity": threat_activity,
                "tick": self.tick_count,
            })

    # --- Handlers bus ---

    async def _on_cardiac_beat(self, event):
        data = event.get("data", event)
        self._cognitive_state["emotion_intensity"] = data.get("arousal", 0.5)

    async def _on_reptilian_alert(self, event):
        data = event.get("data", event)
        self._cognitive_state["threat_level"] = data.get("threat_level", 0.0)

    async def _on_dopamine_surge(self, event):
        self._cognitive_state["dopamine_level"] = min(
            self._cognitive_state["dopamine_level"] + 0.2, 1.0
        )

    async def _on_dopamine_dip(self, event):
        self._cognitive_state["dopamine_level"] = max(
            self._cognitive_state["dopamine_level"] - 0.2, 0.0
        )

    async def _on_circadian_change(self, event):
        data = event.get("data", event)
        phase = data.get("phase", "eveil")
        old_phase = self._circadian_phase
        self._circadian_phase = phase

        if phase == "sommeil_profond":
            self._cognitive_state["stability"] = 0.9
        elif phase == "eveil":
            self._cognitive_state["stability"] = 0.5
        elif phase == "aube" and old_phase == "sommeil_profond":
            # Aube après sommeil → repeuplement des zones désertées
            self._dawn_repopulate()

    # --- Handlers Sprint 2 — Grand Câblage ---

    async def _on_goal_created(self, event):
        """Prefrontal crée un goal → enrichir la zone goals + bonus zones."""
        self._cognitive_state["goal_count"] = min(
            self._cognitive_state["goal_count"] + 1, 10
        )
        data = event.get("data", event)
        title = data.get("title", "")
        self._update_goal_bonus_zones(title, add=True)

    async def _on_goal_complete(self, event):
        """Goal atteint → récompense zone goals + stabilité."""
        self._cognitive_state["goal_count"] = max(
            self._cognitive_state["goal_count"] - 1, 0
        )
        self._cognitive_state["stability"] = min(
            self._cognitive_state["stability"] + 0.1, 1.0
        )
        data = event.get("data", event)
        title = data.get("title", "")
        self._update_goal_bonus_zones(title, add=False)

    async def _on_goal_abandoned(self, event):
        """Goal abandonné → diminuer goals, baisser stabilité."""
        self._cognitive_state["goal_count"] = max(
            self._cognitive_state["goal_count"] - 1, 0
        )
        self._cognitive_state["stability"] = max(
            self._cognitive_state["stability"] - 0.05, 0.0
        )
        data = event.get("data", event)
        title = data.get("title", "")
        self._update_goal_bonus_zones(title, add=False)

    def _update_goal_bonus_zones(self, title: str, add: bool = True):
        """Met à jour les zones bonusées en fonction du titre du goal."""
        title_lower = title.lower()
        matched_zones = set()
        for keyword, zones in GOAL_ZONE_MAP.items():
            if keyword in title_lower:
                matched_zones.update(zones)
        if not matched_zones:
            matched_zones.add("goals")  # Fallback : zone goals par défaut
        for zone in matched_zones:
            if add:
                self._goal_bonus_zones[zone] = (
                    self._goal_bonus_zones.get(zone, 0) + GOAL_FOOD_BONUS
                )
            else:
                self._goal_bonus_zones[zone] = max(
                    self._goal_bonus_zones.get(zone, 0) - GOAL_FOOD_BONUS, 0
                )

    async def _on_corpus_callosum(self, event):
        """État cognitif global → moduler stabilité et créativité."""
        data = event.get("data", event)
        cog_state = data.get("cognitive_state", "")
        if cog_state == "flow":
            self._cognitive_state["stability"] = 0.9
            self._cognitive_state["creativity"] = 0.8
        elif cog_state == "creative_surge":
            self._cognitive_state["creativity"] = min(
                self._cognitive_state["creativity"] + 0.3, 1.0
            )
        elif cog_state == "crisis":
            self._cognitive_state["stability"] = max(
                self._cognitive_state["stability"] - 0.3, 0.0
            )
        elif cog_state == "stagnation":
            self._cognitive_state["creativity"] = max(
                self._cognitive_state["creativity"] - 0.2, 0.0
            )
        elif cog_state == "exploration":
            self._cognitive_state["creativity"] = min(
                self._cognitive_state["creativity"] + 0.15, 1.0
            )

    async def _on_inner_voice(self, event):
        """Voix intérieure → stimuler mémoire + créativité + cognition."""
        self._cognitive_state["memory_activity"] = min(
            self._cognitive_state["memory_activity"] + 0.15, 1.0
        )
        self._cognitive_state["creativity"] = min(
            self._cognitive_state["creativity"] + 0.1, 1.0
        )
        self._cognitive_state["cognition_level"] = min(
            self._cognitive_state["cognition_level"] + 0.1, 1.0
        )

    async def _on_hallucination(self, event):
        """Hallucination détectée → signal de menace."""
        self._cognitive_state["threat_level"] = min(
            self._cognitive_state["threat_level"] + 3.0, 10.0
        )

    async def _on_routine_complete(self, event):
        """Routine autonome terminée → moduler désir selon succès."""
        data = event.get("data", event)
        success = data.get("success", False)
        if success:
            self._cognitive_state["desire_intensity"] = max(
                self._cognitive_state["desire_intensity"] - 5.0, 0.0
            )
        else:
            self._cognitive_state["desire_intensity"] = min(
                self._cognitive_state["desire_intensity"] + 3.0, 100.0
            )

    async def _on_knowledge_gap(self, event):
        """Lacune détectée → stimuler créativité et désir."""
        self._cognitive_state["creativity"] = min(
            self._cognitive_state["creativity"] + 0.2, 1.0
        )
        self._cognitive_state["desire_intensity"] = min(
            self._cognitive_state["desire_intensity"] + 5.0, 100.0
        )

    # --- Afférences complètes (Guide Sprints 2-4) ---

    def _cooldown_ok(self, event_name: str, min_ticks: int = 1) -> bool:
        """Vérifie si le cooldown est écoulé pour un event haute fréquence."""
        last = self._event_cooldowns.get(event_name, -min_ticks)
        if self.tick_count - last < min_ticks:
            return False
        self._event_cooldowns[event_name] = self.tick_count
        return True

    async def _on_prefrontal_thought(self, event):
        """Pensée préfrontale → cognition + créativité (cooldown 1 tick)."""
        if not self._cooldown_ok("PREFRONTAL_THOUGHT"):
            return
        self._cognitive_state["cognition_level"] = min(
            self._cognitive_state["cognition_level"] + 0.15, 1.0
        )
        category = event.get("category", "")
        if category in ("hypothesis", "strategy", "observation"):
            self._cognitive_state["creativity"] = min(
                self._cognitive_state["creativity"] + 0.05, 1.0
            )

    async def _on_psyche_update(self, event):
        """Mise à jour PSYCHE → stabilité via system_average."""
        data = event.get("data", event)
        avg = data.get("system_average", {})
        if isinstance(avg, dict):
            coherence = avg.get("coherence", avg.get("stability", 0.5))
            self._cognitive_state["stability"] = (
                self._cognitive_state["stability"] * 0.7 + float(coherence) * 0.3
            )

    async def _on_synaptic_update(self, event):
        """Mise à jour synaptique → activité mémoire."""
        self._cognitive_state["memory_activity"] = min(
            self._cognitive_state["memory_activity"] + 0.1, 1.0
        )

    async def _on_council_end(self, event):
        """Fin de council → stabilité selon résultat."""
        data = event.get("data", event)
        status = data.get("status", "")
        if status == "consensus":
            self._cognitive_state["stability"] = min(
                self._cognitive_state["stability"] + 0.15, 1.0
            )
        else:
            self._cognitive_state["stability"] = max(
                self._cognitive_state["stability"] - 0.05, 0.0
            )

    async def _on_evolution_feedback(self, event):
        """Feedback évolution → créativité/désir selon verdict."""
        data = event.get("data", event)
        verdict = data.get("verdict", "")
        if "success" in verdict.lower() or "validated" in verdict.lower():
            self._cognitive_state["creativity"] = min(
                self._cognitive_state["creativity"] + 0.15, 1.0
            )
        else:
            self._cognitive_state["desire_intensity"] = min(
                self._cognitive_state["desire_intensity"] + 3.0, 100.0
            )

    async def _on_experience_recorded(self, event):
        """Expérience enregistrée → mémoire + cognition."""
        self._cognitive_state["memory_activity"] = min(
            self._cognitive_state["memory_activity"] + 0.1, 1.0
        )
        self._cognitive_state["cognition_level"] = min(
            self._cognitive_state["cognition_level"] + 0.05, 1.0
        )

    async def _on_soliloque_exchange(self, event):
        """Dialogue interne → cognition + mémoire."""
        self._cognitive_state["cognition_level"] = min(
            self._cognitive_state["cognition_level"] + 0.1, 1.0
        )
        self._cognitive_state["memory_activity"] = min(
            self._cognitive_state["memory_activity"] + 0.05, 1.0
        )

    # --- API publique ---

    def get_emergent_patterns(self, top_n: int = 5) -> List[Dict[str, Any]]:
        """Retourne les patterns émergents dominants."""
        return self.dominant_patterns[:top_n]

    def get_dominant_genome(self) -> Optional[str]:
        """Retourne le génome le plus fréquent."""
        if self.dominant_patterns:
            return self.dominant_patterns[0]["genome"]
        return None

    def compute_tissue_bonus(self, intent: str) -> float:
        """Bonus pour le scoring autonomy basé sur la vitalité du substrat."""
        if not self.dominant_patterns or not self.cells:
            return 0.0

        top = self.dominant_patterns[0]
        freq_bonus = top["frequency"] * 0.5
        fitness_bonus = min(top["avg_fitness"] * 0.3, 0.5)

        return round(freq_bonus + fitness_bonus, 3)

    def get_tissue_context(self) -> str:
        """Texte injectable dans le purpose_context."""
        if not self.cells or len(self.cells) < 10:
            return ""
        if not self.dominant_patterns:
            return ""

        alive = len(self.cells)
        top = self.dominant_patterns[0]
        genome = top["genome"]
        freq = top["frequency"]
        gen_max = max((c.generation for c in self.cells), default=0)

        # Décrire le pattern dominant en termes cognitifs
        instruction_names = {
            'A': "propagation", 'C': "perception", 'G': "création",
            'T': "transformation", 'I': "inhibition", 'R': "reproduction",
        }
        desc = "→".join(
            instruction_names.get(ch, "?") for ch in genome[:5]
        )

        ctx = (
            f"Substrat cellulaire: {alive} cellules, gen {gen_max}, "
            f"pattern dominant [{genome}] ({freq:.0%}) = {desc}"
        )

        # Ajouter les zones les plus actives
        if self._zone_signals:
            hot = sorted(
                self._zone_signals.items(),
                key=lambda kv: kv[1].get("activity", 0),
                reverse=True,
            )[:3]
            zones_desc = ", ".join(
                f"{name}={sig['activity']:.2f}" for name, sig in hot
            )
            ctx += f" | zones actives: {zones_desc}"

        return ctx

    def get_stats(self) -> Dict[str, Any]:
        """Statistiques complètes du substrat."""
        alive = [c for c in self.cells if c.alive]
        return {
            "alive_cells": len(alive),
            "max_cells": MAX_CELLS,
            "tick_count": self.tick_count,
            "total_births": self.total_births,
            "total_deaths": self.total_deaths,
            "dominant_genome": self.get_dominant_genome(),
            "dominant_patterns": self.dominant_patterns[:5],
            "max_generation": max((c.generation for c in alive), default=0),
            "avg_energy": round(
                sum(c.energy for c in alive) / max(len(alive), 1), 1
            ),
            "avg_age": round(
                sum(c.age for c in alive) / max(len(alive), 1), 1
            ),
            "genome_diversity": len(set(c.genome for c in alive)),
            "grid_size": GRID_SIZE,
            "cognitive_state": dict(self._cognitive_state),
            "tick_ms": round(self._last_tick_ms, 2),
        }

    # --- Persistance ---

    def _save(self):
        """Sauvegarde les stats et les meilleures cellules."""
        top_cells = sorted(
            [c for c in self.cells if c.alive],
            key=lambda c: c.output_count,
            reverse=True,
        )[:50]

        data = {
            "tick_count": self.tick_count,
            "total_births": self.total_births,
            "total_deaths": self.total_deaths,
            "dominant_patterns": self.dominant_patterns[:10],
            "cognitive_state": self._cognitive_state,
            "zone_signals": self._zone_signals,
            "top_cells": [
                {
                    "genome": c.genome, "x": c.x, "y": c.y,
                    "energy": round(c.energy, 1), "age": c.age,
                    "generation": c.generation, "output_count": c.output_count,
                }
                for c in top_cells
            ],
        }

        os.makedirs(os.path.dirname(TISSUE_STATE_FILE), exist_ok=True)
        tmp = TISSUE_STATE_FILE + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(tmp, TISSUE_STATE_FILE)
        except Exception as e:
            logger.warning(f"TISSUE: Erreur sauvegarde: {e}")

    def _load(self):
        """Charge l'état depuis le JSON."""
        try:
            with open(TISSUE_STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.tick_count = data.get("tick_count", 0)
            self.total_births = data.get("total_births", 0)
            self.total_deaths = data.get("total_deaths", 0)
            self.dominant_patterns = data.get("dominant_patterns", [])
            saved_state = data.get("cognitive_state", {})
            self._cognitive_state.update(saved_state)
            self._zone_signals = data.get("zone_signals", {})

            top_cells = data.get("top_cells", [])
            if top_cells:
                self.cells = []
                for cd in top_cells:
                    self.cells.append(NeuralCell(
                        genome=cd["genome"],
                        x=cd.get("x", random.randint(0, GRID_SIZE - 1)),
                        y=cd.get("y", random.randint(0, GRID_SIZE - 1)),
                        energy=cd.get("energy", INITIAL_ENERGY),
                        age=cd.get("age", 0),
                        generation=cd.get("generation", 0),
                        output_count=cd.get("output_count", 0),
                    ))
                # Compléter avec des mutants des survivants
                while len(self.cells) < INITIAL_CELLS:
                    parent = random.choice(self.cells)
                    self.cells.append(NeuralCell(
                        genome=mutate(parent.genome),
                        x=random.randint(0, GRID_SIZE - 1),
                        y=random.randint(0, GRID_SIZE - 1),
                        generation=parent.generation + 1,
                    ))
        except (FileNotFoundError, json.JSONDecodeError):
            pass


# Singleton
tissue = NeuralTissue()
