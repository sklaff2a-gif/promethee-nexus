# -*- coding: utf-8 -*-
"""Tableau de bord d'arbitrage du Gardien — review_cli.py.

Sandbox ISOLE. L'arme d'arbitrage : parcourir les premiums drapotes en stase,
voir leur erosion d'influence, et CONSACRER (promote) ou EVINCER (purge) par lots.
Architecture : logique pure (`ReviewBoard`, testable) + double facade argparse/REPL.

Statuts : PREMIUM_FLAGGED (en stase, drapote) -> promote=PREMIUM | purge=CHURN.
"""
import argparse
import json
import os

# on reutilise les invariants de l'amortisseur temporel (coherence)
from proto_review_decay import ReviewQueue, INFLUENCE_FLOOR, PRIORITY

PRIO_RANK = {"HAUTE": 0, "NORMALE": 1}


class ReviewBoard:
    """Moteur d'arbitrage : opere sur un etat JSON de drapeaux. Sans aucune I/O console."""
    def __init__(self, state_path):
        self.state_path = str(state_path)
        self.flags = []
        if os.path.exists(self.state_path):
            self.flags = json.load(open(self.state_path, encoding="utf-8")).get("flags", [])

    def save(self):
        with open(self.state_path, "w", encoding="utf-8") as f:
            json.dump({"flags": self.flags}, f, ensure_ascii=False, indent=2)

    def _get(self, node_id):
        return next((f for f in self.flags if f["node_id"] == node_id), None)

    def pending(self):
        return [f for f in self.flags if f["status"] == "PREMIUM_FLAGGED"]

    def list_sorted(self):
        """File triee : priorite (HAUTE d'abord) puis influence CROISSANTE
        (les plus erodes en tete -> a traiter avant qu'ils ne s'endorment)."""
        return sorted(self.pending(),
                      key=lambda f: (PRIO_RANK.get(f["priority"], 9), f["influence"]))

    def promote(self, node_id):
        """Consecration : flag nettoye, influence remontee a 1.0, statut PREMIUM definitif."""
        f = self._get(node_id)
        if not f or f["status"] != "PREMIUM_FLAGGED":
            return False
        f["status"] = "PREMIUM"; f["influence"] = 1.0; f["is_flagged"] = False
        return True

    def purge(self, node_id):
        """Eviction : perte d'immunite, rejet dans le CHURN."""
        f = self._get(node_id)
        if not f or f["status"] != "PREMIUM_FLAGGED":
            return False
        f["status"] = "CHURN"; f["is_flagged"] = False
        return True

    def purge_all_decayed(self, threshold=0.3):
        """Eviction par lot de tous les drapotes deja endormis (influence < seuil)."""
        n = 0
        for f in self.pending():
            if f["influence"] < threshold:
                f["status"] = "CHURN"; f["is_flagged"] = False; n += 1
        return n

    def diff(self, node_id):
        """Conflit semantique brut : origine de la contradiction vs donnee stockee."""
        f = self._get(node_id)
        if not f:
            return None
        return {"node_id": node_id, "source": f["source"], "conflit": f.get("conflit", ""),
                "stored": f.get("stored", ""), "influence": f["influence"]}


# --- rendu console -----------------------------------------------------------
def _bar(influence):
    full = int(round(influence * 10))
    return "#" * full + "." * (10 - full)


def render_status(board):
    rows = board.list_sorted()
    if not rows:
        return "File de revision VIDE — aucun premium en stase."
    out = [f"FILE DE REVISION — {len(rows)} premium(s) en stase (tri: priorite, puis erosion)",
           "-" * 72]
    for f in rows:
        out.append(f"  [{f['priority']:7}] {f['node_id']:10} influence {f['influence']:.2f} "
                   f"[{_bar(f['influence'])}]  src={f['source']}")
        out.append(f"             conflit: {f.get('conflit','')[:60]}")
    return "\n".join(out)


# --- generateur de faux conflits (via l'amortisseur proto_review_decay) -------
def seed_fake_conflicts(registry_md, state_path):
    """Injecte 4 faux conflits (dont 2 HAUTE prio external) avec erosions variees,
    ecrit le registre .md (via ReviewQueue) ET l'etat JSON du CLI."""
    q = ReviewQueue(registry_md)
    fakes = [
        ("node_042", "Refute par !calc : la formule etait fausse", "external_verification", 0.85,
         "ChromaDB v2 supporte le sharding natif"),
        ("node_077", "Source web contredit la donnee stockee", "external_verification", 0.15,
         "Le throttle GPU se declenche a 90 degres"),
        ("node_108", "Contradiction interne detectee", "internal_inference", 0.25,
         "Le compactage conserve l'integralite sans perte"),
        ("node_201", "Tension avec un autre premium", "internal_inference", 0.60,
         "Le nombre premier est au coeur de l'identite"),
    ]
    flags = []
    for nid, conflit, src, infl, stored in fakes:
        q.flag(nid, conflit, src, hebbian_weight=1.0, date_str="2026-06-07")  # log .md
        flags.append({"node_id": nid, "source": src, "priority": PRIORITY[src],
                      "conflit": conflit, "stored": stored, "influence": infl,
                      "status": "PREMIUM_FLAGGED", "is_flagged": True})
    json.dump({"flags": flags}, open(state_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    return len(flags)


# --- facades -----------------------------------------------------------------
def run_repl(board):
    print("REPL d'arbitrage. Commandes: status | promote <id> | purge <id> | purge-decayed | diff <id> | quit")
    while True:
        try:
            raw = input("review> ").strip()
        except (EOFError, KeyboardInterrupt):
            print(); break
        if not raw:
            continue
        parts = raw.split()
        cmd, arg = parts[0], (parts[1] if len(parts) > 1 else None)
        if cmd in ("quit", "exit", "q"):
            break
        elif cmd in ("status", "list"):
            print(render_status(board))
        elif cmd == "promote" and arg:
            print("OK promu" if board.promote(arg) else "introuvable/non-drapote"); board.save()
        elif cmd == "purge" and arg:
            print("OK purge" if board.purge(arg) else "introuvable/non-drapote"); board.save()
        elif cmd == "purge-decayed":
            print(f"OK {board.purge_all_decayed()} purge(s)"); board.save()
        elif cmd == "diff" and arg:
            d = board.diff(arg); print(json.dumps(d, ensure_ascii=False, indent=2) if d else "introuvable")
        else:
            print("commande inconnue")


def main(argv=None):
    state = os.path.join(os.path.dirname(__file__), "review_state.json")
    parser = argparse.ArgumentParser(description="Tableau de bord d'arbitrage des premiums drapotes.")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("status"); sub.add_parser("list"); sub.add_parser("repl")
    sub.add_parser("seed")  # injecte des faux conflits pour la demo/test
    pp = sub.add_parser("promote"); pp.add_argument("--id", required=True)
    pg = sub.add_parser("purge"); pg.add_argument("--id"); pg.add_argument("--all-decayed", action="store_true")
    pd = sub.add_parser("diff"); pd.add_argument("--id", required=True)
    args = parser.parse_args(argv)

    if args.cmd == "seed":
        reg = os.path.join(os.path.dirname(__file__), "premium_review_demo.md")
        open(reg, "w", encoding="utf-8").close()
        n = seed_fake_conflicts(reg, state)
        print(f"{n} faux conflits injectes."); return

    board = ReviewBoard(state)
    if args.cmd in (None, "repl"):
        run_repl(board)
    elif args.cmd in ("status", "list"):
        print(render_status(board))
    elif args.cmd == "promote":
        print(f"{args.id} -> PREMIUM (influence 1.0)" if board.promote(args.id) else "introuvable"); board.save()
    elif args.cmd == "purge":
        if getattr(args, "all_decayed", False):
            print(f"{board.purge_all_decayed()} premium(s) endormi(s) -> CHURN"); board.save()
        elif args.id:
            print(f"{args.id} -> CHURN" if board.purge(args.id) else "introuvable"); board.save()
    elif args.cmd == "diff":
        d = board.diff(args.id)
        print(json.dumps(d, ensure_ascii=False, indent=2) if d else "introuvable")


if __name__ == "__main__":
    main()
