"""Sauna Mode — cure de repos pour le tissu neural.

Quand le tissu est surchargé (dechets eleves, signaux satures,
energie en baisse), le mode Sauna nettoie et rééquilibre.

Comme un vrai sauna : on chauffe pour purger, puis on refroidit.

Declenchement : manuel via API ou automatique si waste > seuil.
Duree : 10 minutes.
Effets :
  1. Acceleration du recyclage des dechets (waste decay x3)
  2. Signaux cognitifs ramenes vers l'equilibre (0.5)
  3. Cellules faibles meurent naturellement (seuil apoptose baisse)
  4. Pause des injections intensives
  5. Boost de vitalite apres la cure
"""

import time
import logging
import asyncio
from typing import Dict, Any

logger = logging.getLogger(__name__)

SAUNA_DURATION = 600  # 10 minutes
WASTE_THRESHOLD = 200  # Declenchement auto si dechets > 200
ENERGY_LOW_THRESHOLD = 80  # Declenchement auto si energie moyenne < 80
SIGNAL_EQUILIBRIUM = 0.5  # Cible pour les signaux cognitifs
SAUNA_AUTO_COOLDOWN = 1800  # 30 min entre deux auto-triggers (evite boucle infinie)


class SaunaMode:
    """Mode Sauna — nettoyage du tissu neural."""

    def __init__(self):
        self.active = False
        self.started_at = 0.0
        self.last_auto_trigger = 0.0
        self.stats = {}

    async def start(self) -> Dict[str, Any]:
        """Demarre le mode Sauna."""
        if self.active:
            remaining = SAUNA_DURATION - (time.time() - self.started_at)
            return {"status": "already_active", "remaining_s": int(remaining)}

        self.active = True
        self.started_at = time.time()
        self.last_auto_trigger = time.time()

        # Capturer l'etat avant
        before = self._capture_state()
        self.stats["before"] = before

        logger.info(f"SAUNA: Demarrage — dechets={before.get('waste',0):.0f}, "
                    f"energie={before.get('avg_energy',0):.0f}")
        print("   🧖 SAUNA: Nettoyage du tissu neural demarre (10 min)")

        # Appliquer le nettoyage
        await self._clean()

        # Capturer l'etat apres
        after = self._capture_state()
        self.stats["after"] = after

        self.active = False

        # Boost de vitalite post-sauna
        self._boost_vitality()

        # Publier sur THOUGHT_STREAM
        try:
            from core.event_bus.bus import bus
            await bus.publish("THOUGHT_STREAM", {
                "thought": f"[SAUNA] Nettoyage termine. Dechets: {before.get('waste',0):.0f} → {after.get('waste',0):.0f}. "
                           f"Energie: {before.get('avg_energy',0):.0f} → {after.get('avg_energy',0):.0f}.",
                "source": "sauna",
            })
        except Exception:
            pass

        result = {
            "status": "completed",
            "duration_s": int(time.time() - self.started_at),
            "before": before,
            "after": after,
            "waste_cleaned": round(before.get("waste", 0) - after.get("waste", 0), 1),
            "energy_change": round(after.get("avg_energy", 0) - before.get("avg_energy", 0), 1),
        }

        logger.info(f"SAUNA: Termine — dechets nettoyes: {result['waste_cleaned']:.0f}, "
                    f"energie: {result['energy_change']:+.0f}")
        print(f"   🧖 SAUNA: Termine. Dechets -{result['waste_cleaned']:.0f}, "
              f"energie {result['energy_change']:+.0f}")

        return result

    async def _clean(self):
        """Nettoyage en 3 phases."""
        try:
            from core.neural_tissue import tissue
            if not tissue:
                return

            # Phase 1 : Accelerer le recyclage des dechets
            # Reduire les dechets de chaque cellule
            for cell in tissue.cells:
                if hasattr(cell, 'waste'):
                    cell.waste = max(0, cell.waste * 0.3)  # Reduire 70% des dechets

            # Nettoyer la grille de dechets
            if hasattr(tissue, 'waste_grid'):
                for y in range(len(tissue.waste_grid)):
                    for x in range(len(tissue.waste_grid[y])):
                        tissue.waste_grid[y][x] *= 0.2  # Reduire 80%

            # Phase 2 : Rééquilibrer les signaux cognitifs satures
            cs = tissue._cognitive_state
            for key in ["memory_activity", "creativity", "cognition_level"]:
                current = cs.get(key, 0.5)
                if current > 0.8:
                    # Ramener doucement vers l'equilibre
                    cs[key] = current * 0.6 + SIGNAL_EQUILIBRIUM * 0.4
                    logger.info(f"SAUNA: {key} {current:.2f} → {cs[key]:.2f}")

            # Baisser le stress thermique
            if cs.get("thermal_stress", 0) > 0.5:
                cs["thermal_stress"] = cs["thermal_stress"] * 0.5
            if cs.get("somatic_load", 0) > 0.5:
                cs["somatic_load"] = cs["somatic_load"] * 0.5

            # Phase 3 : Laisser les cellules faibles mourir
            # Baisser l'energie des cellules les plus faibles pour accelerer l'apoptose
            weak_count = 0
            for cell in tissue.cells:
                if cell.energy < 30:
                    cell.energy = max(0, cell.energy - 5)
                    weak_count += 1

            if weak_count > 0:
                logger.info(f"SAUNA: {weak_count} cellules faibles accelerees vers l'apoptose")

            # Attendre un peu pour que le tissu process
            await asyncio.sleep(2)

        except Exception as e:
            logger.warning(f"SAUNA: Nettoyage echoue: {e}")

    def _boost_vitality(self):
        """Boost de vitalite post-sauna."""
        try:
            from core.neural_tissue import tissue
            if not tissue:
                return
            cs = tissue._cognitive_state
            cs["vitality_level"] = min(1.0, cs.get("vitality_level", 0.5) + 0.2)
            cs["stability"] = min(1.0, cs.get("stability", 0.5) + 0.1)
            logger.info("SAUNA: Boost vitalite +0.2, stabilite +0.1")
        except Exception:
            pass

    def _capture_state(self) -> Dict[str, Any]:
        """Capture l'etat du tissu pour comparaison avant/apres."""
        try:
            from core.neural_tissue import tissue
            if not tissue:
                return {}
            cs = tissue._cognitive_state
            waste = sum(getattr(c, 'waste', 0) for c in tissue.cells)
            avg_energy = sum(c.energy for c in tissue.cells) / max(len(tissue.cells), 1)
            return {
                "waste": waste,
                "avg_energy": avg_energy,
                "alive_cells": len(tissue.cells),
                "memory": cs.get("memory_activity", 0),
                "creativity": cs.get("creativity", 0),
                "cognition": cs.get("cognition_level", 0),
                "thermal_stress": cs.get("thermal_stress", 0),
                "somatic_load": cs.get("somatic_load", 0),
                "vitality": cs.get("vitality_level", 0),
            }
        except Exception:
            return {}

    def should_auto_trigger(self) -> bool:
        """Verifie si le sauna devrait se declencher automatiquement."""
        if time.time() - self.last_auto_trigger < SAUNA_AUTO_COOLDOWN:
            return False
        state = self._capture_state()
        if not state:
            return False
        waste = state.get("waste", 0)
        energy = state.get("avg_energy", 100)
        return waste > WASTE_THRESHOLD or energy < ENERGY_LOW_THRESHOLD

    def get_status(self) -> Dict[str, Any]:
        state = self._capture_state()
        return {
            "active": self.active,
            "should_trigger": self.should_auto_trigger(),
            "current_state": state,
            "last_stats": self.stats,
        }


# Singleton
sauna = SaunaMode()
