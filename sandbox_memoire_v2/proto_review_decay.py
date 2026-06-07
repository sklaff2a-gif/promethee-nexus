# -*- coding: utf-8 -*-
"""Raffinement 3 — Amortisseur temporel : registre asynchrone + decay d'influence + alerte.

Sandbox ISOLE. Pression homeostatique passive : un PREMIUM drapote garde son poids
Hebbian d'origine INTACT (reversibilite), mais un INFLUENCE_FACTOR cinetique decote a
chaque cycle tant que l'arbitrage humain n'a pas eu lieu -> le savoir suspect "s'endort"
sans disparaitre, et cesse de colorer les reponses si JM ne le valide pas.

Trois regles (V22.1_DECAY) :
  1. Registre premium_review.md : 1 ligne normalisee par flag (priorite derivee de la source).
  2. Decay passif : influence = max(0.10, influence - decay_rate(source)).  [uniforme d'abord]
  3. Alerte saturation : >= 10 entrees sous revue -> meta-instruction prioritaire.
"""
from datetime import datetime

DECAY_STEP = 0.05
INFLUENCE_FLOOR = 0.10
SATURATION_THRESHOLD = 10
PRIORITY = {"external_verification": "HAUTE", "internal_inference": "NORMALE"}


def decay_rate(source: str) -> float:
    """UNIFORME pour l'instant (valider la plomberie). Extension future : external =
    2*DECAY_STEP (preuve forte, decay rapide) ; internal = DECAY_STEP (faillible, lent)."""
    return DECAY_STEP


class ReviewQueue:
    def __init__(self, registry_path):
        self.registry_path = str(registry_path)
        self.influence = {}   # node_id -> influence_factor cinetique
        self.flagged = {}     # node_id -> {"source":..., "resolved":bool}
        self._hebbian = {}    # node_id -> poids Hebbian d'origine (jamais touche)

    def flag(self, node_id, conflit, source, hebbian_weight, date_str=None):
        date_str = date_str or datetime.now().strftime("%Y-%m-%d")
        prio = PRIORITY.get(source, "NORMALE")
        line = f"- [DATE: {date_str}][PRIORITE: {prio}][SRC: {source}] ID: {node_id} | Conflit: {conflit}\n"
        with open(self.registry_path, "a", encoding="utf-8") as f:
            f.write(line)
        self.influence[node_id] = 1.0
        self.flagged[node_id] = {"source": source, "resolved": False}
        self._hebbian[node_id] = hebbian_weight   # INTACT : on ne degrade jamais le poids

    def tick(self):
        """Un cycle de consolidation : decote l'influence des drapotes NON resolus."""
        for nid, info in self.flagged.items():
            if not info["resolved"]:
                self.influence[nid] = max(INFLUENCE_FLOOR,
                                          self.influence[nid] - decay_rate(info["source"]))

    def get_influence(self, node_id):
        return self.influence.get(node_id, 1.0)

    def get_hebbian(self, node_id):
        return self._hebbian.get(node_id)

    def resolve(self, node_id, confirmed: bool):
        """Arbitrage JM. confirmed=True -> drapeau faux, influence RESTAUREE (reversibilite
        totale). confirmed=False -> reste degrade (descente au tampon geree ailleurs)."""
        if node_id in self.flagged:
            self.flagged[node_id]["resolved"] = True
            if confirmed:
                self.influence[node_id] = 1.0

    def pending_count(self):
        return sum(1 for i in self.flagged.values() if not i["resolved"])

    def saturation_alert(self):
        n = self.pending_count()
        if n >= SATURATION_THRESHOLD:
            return f"[ALERTE] Systeme sous tension : {n} premiums en stase memorielle. Arbitrage requis."
        return None


if __name__ == "__main__":
    import os
    reg = os.path.join(os.path.dirname(__file__), "premium_review_demo.md")
    open(reg, "w", encoding="utf-8").close()  # registre neuf pour la demo
    q = ReviewQueue(reg)

    print("=" * 64)
    print("RAFFINEMENT 3 — amortisseur temporel (demo)")
    print("=" * 64)
    # un premium drapote (contradiction interne), poids Hebbian 1.0
    q.flag("p_compact", 'Conflit sur "compactage sans perte"', "internal_inference",
           hebbian_weight=1.0, date_str="2026-06-07")
    print("\nDecay d'influence sur 18 cycles (poids Hebbian = constante a cote) :")
    for c in range(1, 19):
        q.tick()
        if c <= 5 or c % 3 == 0 or q.get_influence("p_compact") == INFLUENCE_FLOOR:
            print(f"  cycle {c:2}: influence={q.get_influence('p_compact'):.2f}  | hebbian={q.get_hebbian('p_compact'):.2f} (intact)")
        if q.get_influence("p_compact") == INFLUENCE_FLOOR and c > 5:
            print(f"  >>> plancher {INFLUENCE_FLOOR} atteint au cycle {c} : le savoir s'est endormi, PAS efface.")
            break

    print("\nArbitrage JM : confirme le drapeau faux -> reversibilite :")
    q.resolve("p_compact", confirmed=True)
    print(f"  influence restauree = {q.get_influence('p_compact'):.2f} | hebbian = {q.get_hebbian('p_compact'):.2f}")

    print("\nAlerte de saturation : on drapote 10 premiums non traites :")
    for k in range(10):
        q.flag(f"node_{k:03}", "conflit simule", "internal_inference", 0.8, date_str="2026-06-07")
    print(f"  pending = {q.pending_count()} | {q.saturation_alert()}")
    print("=" * 64)
