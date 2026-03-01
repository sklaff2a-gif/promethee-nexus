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
TICK_INTERVAL = 2.0
SAVE_EVERY_N_TICKS = 50
FOOD_SPAWN_PER_ZONE = 2
PATTERN_TRACK_SIZE = 20
EXTINCTION_THRESHOLD = 5       # Repeuplement si < 5 cellules

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

    def tick(self, grid, neighbors):
        """Exécute un cycle du génome."""
        if self.energy <= 0 or not self.alive:
            self.alive = False
            return None

        instruction = self.genome[self.pointer]
        child = self._execute(instruction, grid, neighbors)

        self.energy -= MAINTENANCE_COST
        if self.energy <= 0:
            self.alive = False
        self.pointer = (self.pointer + 1) % len(self.genome)
        self.age += 1

        return child

    def _execute(self, instruction, grid, neighbors):
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
                self.energy += CAPTURE_REWARD * min(signal, 2.0)
                grid[self.y][self.x] *= 0.5
            else:
                self.register = 0.0

        elif instruction == 'G':
            # Generate — produire un pattern (récompensé si signal capté)
            if self.register > 0.1:
                self.output_count += 1
                self.energy += GENERATE_REWARD
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
        child_genome = mutate(self.genome)
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

def mutate(genome: str) -> str:
    """Mutation d'un génome : substitution, insertion, délétion."""
    result = list(genome)

    for i in range(len(result)):
        if random.random() < MUTATION_RATE:
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
        }

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
            self._subscribed = True
            logger.info("TISSUE: Substrat cellulaire neuronal actif.")
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
        # 1. Injecter les signaux cognitifs
        self._inject_signals()

        # 2. Exécuter chaque cellule
        new_cells = []
        for cell in self.cells:
            if not cell.alive:
                continue
            neighbors = self._get_neighbors(cell)
            child = cell.tick(self.grid, neighbors)
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

        # 6. Decay des signaux
        for row in self.grid:
            for x in range(len(row)):
                row[x] *= SIGNAL_DECAY

        # 7. Mettre à jour les patterns dominants
        self._update_dominant_patterns()

        # 8. Publier si pattern émergent significatif
        self._check_emergence()

        self.tick_count += 1

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
        }

        for zone_name, (x1, y1, x2, y2) in SIGNAL_ZONES.items():
            intensity = zone_intensities.get(zone_name, 0.3)
            for _ in range(FOOD_SPAWN_PER_ZONE):
                sx = random.randint(x1, min(x2 - 1, GRID_SIZE - 1))
                sy = random.randint(y1, min(y2 - 1, GRID_SIZE - 1))
                self.grid[sy][sx] += intensity * random.uniform(0.5, 1.5)

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
        if phase == "sommeil_profond":
            self._cognitive_state["stability"] = 0.9
        elif phase == "eveil":
            self._cognitive_state["stability"] = 0.5

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

        return (
            f"Substrat cellulaire: {alive} cellules, gen {gen_max}, "
            f"pattern dominant [{genome}] ({freq:.0%}) = {desc}"
        )

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
