# -*- coding: utf-8 -*-
"""NETTOYAGE DES NOEUDS MOJIBAKE (audit 11/06) — DRY-RUN par defaut, --apply pour ecrire.

Source du mal (fixee ce jour) : fallback latin-1 des ingestors -> un utf-8 lu en
latin-1 produit 'sÃ©curitÃ©'. Reparation = l'inverse exact : encode('latin-1')
puis decode('utf-8').

⚠️ PIEGE VERIFIE : node_id = md5(concept.lower())[:12] (_make_node_id). Reparer le
concept SANS re-keyer rendrait le noeud introuvable par les futurs ensure_node
('synthèse' hasherait un autre id que l'ancien hash de 'synthÃ¨se') -> doublons.
Donc chaque reparation RE-KEYE le noeud sous md5(concept_repare), et la collision
se teste sur l'ID (case-insensitive), pas sur le concept brut.

Issues par noeud :
  REPARE  : nouvel id libre -> concept corrige + re-keying + synapses re-routees
  FUSIONNE: l'id repare EXISTE deja (jumeau propre) -> re-routage synapses vers lui
            (poids additionnes cap 1.0, pattern levier B), activations transferees
  SUPPRIME: irreparable (double-mojibake destructif) -> retire avec ses synapses

PRECONDITION --apply : le serveur DOIT etre arrete (sinon son auto-save ecrase tout).
Backup horodate systematique avant ecriture.
"""
import hashlib
import json
import shutil
import sys
import time

STATE = r"C:\MesProjets\PROMETHEE_V11_restructuration2026\memory\synaptic_network.json"
MARQUEURS = ("Ã", "Â", "â€")
APPLY = "--apply" in sys.argv


def make_node_id(concept):
    """Copie exacte de _make_node_id (synaptic_network.py:202)."""
    return hashlib.md5(concept.strip().lower().encode("utf-8")).hexdigest()[:12]


def reparer(txt):
    """Inverse du mal : latin-1 -> utf-8. None si echec ou toujours malade."""
    try:
        fixe = txt.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return None
    if fixe == txt or any(m in fixe for m in MARQUEURS):
        return None
    return fixe


d = json.load(open(STATE, encoding="utf-8"))
nodes, syns = d["nodes"], d["synapses"]

mojibake = {nid: n for nid, n in nodes.items()
            if any(m in n.get("concept", "") for m in MARQUEURS)}
print(f"Etat : {len(nodes)} noeuds, {len(syns)} synapses | mojibake : {len(mojibake)}")

plan = {"REPARE": [], "FUSIONNE": [], "SUPPRIME": []}
for nid, n in mojibake.items():
    fixe = reparer(n["concept"])
    if fixe is None:
        plan["SUPPRIME"].append((nid, n["concept"][:50], None, None))
        continue
    new_id = make_node_id(fixe)
    if new_id in nodes and new_id != nid:
        plan["FUSIONNE"].append((nid, n["concept"][:50], fixe, new_id))
    else:
        plan["REPARE"].append((nid, n["concept"][:50], fixe, new_id))

for action, items in plan.items():
    print(f"\n--- {action} : {len(items)} ---")
    for nid, avant, apres, new_id in items[:8]:
        suffix = f" -> {apres!r} [{nid} => {new_id}]" if apres else " (irreparable)"
        print(f"  {avant!r}{suffix}")
    if len(items) > 8:
        print(f"  ... +{len(items) - 8}")

if not APPLY:
    print("\nDRY-RUN : rien n'a ete ecrit. Relancer avec --apply (serveur ARRETE).")
    sys.exit(0)

# ═══ APPLY ═══
bak = STATE + time.strftime(".bak_mojibake_%Y%m%d_%H%M%S")
shutil.copy2(STATE, bak)
print(f"\nBackup : {bak}")

redirects = {}   # vieux nid -> nouveau nid (re-keying ou fusion)
supprimes = set()

for nid, _, fixe, new_id in plan["REPARE"]:
    node = nodes.pop(nid)
    node["concept"] = fixe
    node["id"] = new_id
    nodes[new_id] = node
    redirects[nid] = new_id

for nid, _, fixe, new_id in plan["FUSIONNE"]:
    old = nodes.pop(nid)
    cible = nodes[new_id]
    cible["activation_count"] = cible.get("activation_count", 0) + old.get("activation_count", 0)
    cible["energy"] = min(1.0, max(cible.get("energy", 0), old.get("energy", 0)))
    redirects[nid] = new_id

for nid, _, _, _ in plan["SUPPRIME"]:
    supprimes.add(nid)
    del nodes[nid]

# re-routage des synapses (pattern levier B : poids additionnes, cap 1.0)
nouvelles, absorbees, orphelines = {}, 0, 0
for k, s in syns.items():
    a = redirects.get(s["source"], s["source"])
    b = redirects.get(s["target"], s["target"])
    if a in supprimes or b in supprimes or a not in nodes or b not in nodes:
        orphelines += 1
        continue
    if a == b:
        absorbees += 1
        continue
    key = f"{a}->{b}"
    if key in nouvelles:
        m = nouvelles[key]
        m["weight"] = min(1.0, float(m["weight"]) + float(s["weight"]))
        m["formation_count"] = int(m.get("formation_count", 1)) + int(s.get("formation_count", 1))
    else:
        s = dict(s)
        s["source"], s["target"] = a, b
        nouvelles[key] = s

d["synapses"] = nouvelles
json.dump(d, open(STATE, "w", encoding="utf-8"), ensure_ascii=False)

# verification post-apply : plus aucun mojibake, ids coherents
reste = sum(1 for n in nodes.values() if any(m in n.get("concept", "") for m in MARQUEURS))
ids_ok = all(make_node_id(n["concept"]) == nid or n.get("node_type") in ("affect", "desire", "trait")
             for nid, n in nodes.items() if nid in redirects.values())
print(f"APPLY OK : {len(nodes)} noeuds | synapses {len(syns)} -> {len(nouvelles)} "
      f"(absorbees {absorbees}, orphelines {orphelines})")
print(f"Repares {len(plan['REPARE'])} | fusionnes {len(plan['FUSIONNE'])} | supprimes {len(plan['SUPPRIME'])}")
print(f"VERIF : mojibake restant = {reste} | ids re-keyes coherents = {ids_ok}")
