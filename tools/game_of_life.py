#!/usr/bin/env python3
"""
Simulateur Jeu de la Vie de Conway - Promethee Edition V4.0

Premier exercice de production de code autonome de Promethee.
Itere 5 fois (D+ → A-) grace au protocole de reflexion structuree :
dessiner, verifier, implementer, relire.

Lecons apprises :
- Dessiner le pattern ASCII AVANT de coder les offsets
- Convention explicite (dr, dc) → ny = cy + dr, nx = cx + dc
- Implementer les algorithmes difficiles au lieu de commenter
- Iterer sans ego sur chaque feedback

Usage : python tools/game_of_life.py
"""

import random
import time
from typing import List, Tuple, Dict, Optional


class GameOfLife:
    def __init__(self, width: int = 60, height: int = 25):
        self.width = width
        self.height = height
        self.grid = [[False for _ in range(width)] for _ in range(height)]
        self.hash_history: Dict[int, int] = {}

    def set_random_state(self, density: float = 0.3):
        """Remplit la grille avec un etat aleatoire."""
        self.grid = [
            [random.random() < density for _ in range(self.width)]
            for _ in range(self.height)
        ]

    def get_neighbors_count(self, x: int, y: int) -> int:
        """Compte les voisins vivants (bordure dure)."""
        count = 0
        for dy in [-1, 0, 1]:
            for dx in [-1, 0, 1]:
                if dx == 0 and dy == 0:
                    continue
                nx, ny = x + dx, y + dy
                if 0 <= nx < self.width and 0 <= ny < self.height:
                    if self.grid[ny][nx]:
                        count += 1
        return count

    def update(self) -> bool:
        """Applique une generation. Retourne True si changement."""
        new_grid = [row[:] for row in self.grid]
        changed = False

        for y in range(self.height):
            for x in range(self.width):
                neighbors = self.get_neighbors_count(x, y)
                alive = self.grid[y][x]

                if alive:
                    if not (neighbors == 2 or neighbors == 3):
                        new_grid[y][x] = False
                        changed = True
                else:
                    if neighbors == 3:
                        new_grid[y][x] = True
                        changed = True

        self.grid = new_grid
        return changed

    def add_pattern(self, pattern_name: str):
        """Ajoute des motifs avec offsets (dr, dc) stricts.

        Convention : dr = delta row, dc = delta col
        Application : ny = cy + dr, nx = cx + dc, grid[ny][nx] = True
        """
        cy, cx = self.height // 2 - 1, self.width // 2

        if pattern_name == "glider":
            # Glider : .O. / ..O / OOO (5 cellules)
            #   .O.  → (0,1)
            #   ..O  → (1,2)
            #   OOO  → (2,0), (2,1), (2,2)
            offsets = [(0, 1), (1, 2), (2, 0), (2, 1), (2, 2)]
        elif pattern_name == "blinker":
            # Blinker horizontal : OOO (3 cellules)
            offsets = [(0, -1), (0, 0), (0, 1)]
        elif pattern_name == "beacon":
            # Beacon : OO.. / O... / ...O / ..OO (6 cellules)
            #   OO..  → (0,0), (0,1)
            #   O...  → (1,0)
            #   ...O  → (2,3)
            #   ..OO  → (3,2), (3,3)
            offsets = [(0, 0), (0, 1), (1, 0), (2, 3), (3, 2), (3, 3)]
        elif pattern_name == "random":
            self.set_random_state(density=0.3)
            return
        else:
            print(f"Warning : Pattern '{pattern_name}' ignore.")
            return

        for dr, dc in offsets:
            ny, nx = cy + dr, cx + dc
            if 0 <= nx < self.width and 0 <= ny < self.height:
                self.grid[ny][nx] = True

    def get_grid_hash(self) -> int:
        """Calcule un hash immuable de la grille actuelle."""
        grid_tuple = tuple(tuple(row) for row in self.grid)
        return hash(grid_tuple)

    def run(self, generations: int, show_grid: bool = True) -> Tuple[List[Tuple[int, int]], str]:
        """Execute la simulation avec animation et detection de cycles fusionnee.

        Retourne (historique_population, etat_final).
        """
        history: List[Tuple[int, int]] = []
        self.hash_history.clear()

        for gen in range(generations):
            current_hash = self.get_grid_hash()

            if show_grid and (gen == 0 or gen % 5 == 0):
                print(f"\n--- Generation {gen} ---")
                self._print_grid()

            population = sum(sum(row) for row in self.grid)
            history.append((gen, population))

            # Detection de cycle par hash
            if current_hash in self.hash_history:
                period = gen - self.hash_history[current_hash]
                state = f"Oscillateur (periode {period})" if period > 1 else "Stable"
                print(f"\n>>> CYCLE DETECTE ! {state} (generation {gen})")
                return history, state

            self.hash_history[current_hash] = gen

            if population == 0:
                print(f"\n>>> Extinction a la generation {gen}.")
                return history, "Extinction"

            self.update()
            time.sleep(0.3)

        return history, "Evolution active (fin des generations)"

    def _print_grid(self):
        """Affiche la grille en ASCII."""
        for row in self.grid:
            print("".join("O" if cell else "." for cell in row))


def main():
    """Point d'entree du script."""
    print("=== Jeu de la Vie de Conway — Promethee Edition ===\n")

    game = GameOfLife(width=60, height=25)

    print("Patterns disponibles : glider, blinker, beacon, random")
    choice = input("Choisir un motif : ").strip().lower()
    if choice not in ("glider", "blinker", "beacon", "random"):
        choice = "random"

    game.add_pattern(choice)
    print(f"Motif '{choice}' ajoute. Simulation (50 generations)...\n")

    stats, state = game.run(generations=50)

    print(f"\n=== Resultats ===")
    print(f"Etat final : {state}")
    if stats:
        avg_pop = sum(h[1] for h in stats) / len(stats)
        print(f"Generations : {len(stats)}")
        print(f"Population moyenne : {avg_pop:.1f}")
        print(f"Population finale : {stats[-1][1]}")


if __name__ == "__main__":
    main()
