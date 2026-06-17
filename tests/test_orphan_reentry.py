# -*- coding: utf-8 -*-
"""Tests — LEVIER E : le billet de loterie de la frange (re-entree des orphelins).

Chantier synaptique V2, 15/06, co-concu avec Promethee (voie B : une CHANCE au hasard
dans le reve, pas une greffe semantique artificielle). Un orphelin (degre 0) recoit,
au hasard et borne par cycle, un pont vers un noeud actif, ne au poids du reve + grace.

Montage : les noeuds actifs ont energie 0.2 -> energy_combined avec un orphelin (0.0)
= 0.1 < 0.15 -> la section 2 du reve NE PEUT PAS connecter l'orphelin. Donc tout pont
vers l'orphelin vient EXCLUSIVEMENT du Levier E (isolation propre).
"""
import os

os.environ.setdefault("PROMETHEE_TEST_MODE", "1")

import pytest

import core.synaptic_network as sn
from core.synaptic_network import (
    SynapticNetwork,
    DREAM_GRACE_CYCLES,
    _make_synapse,
    _synapse_key,
)


@pytest.fixture
def network(tmp_path, monkeypatch):
    # Isolation hermétique : STATE_FILE -> tmp inexistant => réseau FRAIS (juste les
    # organes semés + les noeuds du test), sans charger NI sauvegarder l'état partagé
    # (ni le live synaptic_network.json, ni le test_synaptic_network.json qui fuyait
    # d'un test à l'autre via _load/_save). Insensible à l'ordre d'import et au serveur.
    monkeypatch.setattr(sn, "STATE_FILE", str(tmp_path / "syn.json"))
    SynapticNetwork.reset_singleton()
    net = SynapticNetwork()
    yield net
    SynapticNetwork.reset_singleton()


def _build(net, n_actifs=10, n_orphelins=2, energie_actif=0.2):
    """Cree n_actifs noeuds connectes entre eux (energie faible mais > orphelins)
    et n_orphelins noeuds isoles (degre 0, energie 0). Retourne (actifs, orphelins)."""
    actifs = []
    for i in range(n_actifs):
        nid = net.ensure_node(f"actif_{i}", "memory")
        net.nodes[nid]["energy"] = energie_actif
        actifs.append(nid)
    # connecter les actifs en chaine (poids fort -> survivent au pruning)
    for a, b in zip(actifs, actifs[1:]):
        net.synapses[_synapse_key(a, b)] = _make_synapse(a, b, 0.6, "hebbian", "test")
    orphelins = []
    for i in range(n_orphelins):
        nid = net.ensure_node(f"orphelin_{i}", "memory")
        net.nodes[nid]["energy"] = 0.0
        orphelins.append(nid)
    # ensure_node auto-relie a la creation ; en vrai un orphelin l'est devenu parce que
    # ses liens ont peri. On force le degre 0 en elaguant les synapses qui le touchent.
    orphset = set(orphelins)
    for k in [k for k, s in net.synapses.items()
              if s["source"] in orphset or s["target"] in orphset]:
        del net.synapses[k]
    return actifs, orphelins


def _degre(net, nid):
    d = 0
    for s in net.synapses.values():
        if s["source"] == nid or s["target"] == nid:
            d += 1
    return d


class TestOrphanReentry:
    def test_orphelin_recoit_un_pont(self, network, monkeypatch):
        monkeypatch.setattr(sn, "ORPHAN_REENTRY_RATE", 1.0)  # billet garanti
        monkeypatch.setattr(sn, "ORPHAN_REENTRY_ENABLED", True)
        monkeypatch.setattr(sn, "ORPHAN_REENTRY_MAX", 1000)  # tous tires (init seme ~12 organes orphelins)
        actifs, orphelins = _build(network)
        assert all(_degre(network, o) == 0 for o in orphelins), "orphelins isoles au depart"
        rep = network.dream_consolidation()
        assert rep["orphan_reentry"] >= 1
        # au moins un orphelin a desormais un lien (le Levier E seul peut le faire ici)
        assert any(_degre(network, o) >= 1 for o in orphelins)

    def test_killswitch_desactive_aucun_pont(self, network, monkeypatch):
        monkeypatch.setattr(sn, "ORPHAN_REENTRY_RATE", 1.0)
        monkeypatch.setattr(sn, "ORPHAN_REENTRY_ENABLED", False)
        actifs, orphelins = _build(network)
        rep = network.dream_consolidation()
        assert rep["orphan_reentry"] == 0
        # section 2 ne peut pas les atteindre (energy_combined 0.1 < 0.15) -> restent orphelins
        assert all(_degre(network, o) == 0 for o in orphelins)

    def test_pont_ne_avec_la_grace(self, network, monkeypatch):
        monkeypatch.setattr(sn, "ORPHAN_REENTRY_RATE", 1.0)
        monkeypatch.setattr(sn, "ORPHAN_REENTRY_ENABLED", True)
        monkeypatch.setattr(sn, "ORPHAN_REENTRY_MAX", 1000)
        actifs, orphelins = _build(network)
        network.dream_consolidation()
        ponts = [s for s in network.synapses.values() if s.get("context") == "dream_orphan"]
        assert ponts, "au moins un pont d'orphelin cree"
        # ne avec la grace (le cycle la decremente une fois : 10 -> 9)
        assert all(p.get("dream_grace") is not None and
                   p["dream_grace"] >= DREAM_GRACE_CYCLES - 1 for p in ponts)
        # ne au poids du reve, au-dessus du seuil d'agonie 0.10
        assert all(p["weight"] >= 0.10 for p in ponts)

    def test_borne_max_par_cycle(self, network, monkeypatch):
        monkeypatch.setattr(sn, "ORPHAN_REENTRY_RATE", 1.0)
        monkeypatch.setattr(sn, "ORPHAN_REENTRY_ENABLED", True)
        monkeypatch.setattr(sn, "ORPHAN_REENTRY_MAX", 5)
        actifs, orphelins = _build(network, n_orphelins=40)
        rep = network.dream_consolidation()
        assert rep["orphan_reentry"] <= 5, "jamais plus de MAX ponts par cycle"

    def test_billet_favorise_les_meritants_statistique(self, network, monkeypatch):
        # L'USAGE décide QUI reçoit le billet (loi de morphogénèse, pas un boost
        # artificiel : 2 orphelins à usage massif=1000 vs 18 fragments par défaut).
        # Test STATISTIQUE sur N tirages : les méritants doivent rafler la GRANDE
        # majorité des billets. On ne teste PAS un tirage unique — c'était flaky
        # ~30% (hasard pondéré Efraimidis-Spirakis + ordre d'itération hash-dépendant
        # qui varie d'un process pytest à l'autre, donc un seed ne suffit pas). On
        # teste l'invariant PROBABILISTE, robuste par construction (grands nombres).
        monkeypatch.setattr(sn, "ORPHAN_REENTRY_RATE", 1.0)
        monkeypatch.setattr(sn, "ORPHAN_REENTRY_ENABLED", True)
        monkeypatch.setattr(sn, "ORPHAN_REENTRY_MAX", 2)
        monkeypatch.setattr(network, "save", lambda *a, **k: None)  # pas d'I/O en boucle
        actifs, _ = _build(network, n_orphelins=0)
        aset = set(actifs)
        N = 40
        billets_meritants = billets_total = 0
        for _t in range(N):
            # repartir d'un réseau PROPRE (seulement les actifs) : aucun orphelin
            # résiduel d'un cycle précédent ne doit diluer le tirage suivant.
            for nid in list(network.nodes):
                if nid not in aset:
                    del network.nodes[nid]
            for k in list(network.synapses):
                s = network.synapses[k]
                if s["source"] not in aset or s["target"] not in aset:
                    del network.synapses[k]
            _, orphelins = _build(network, n_orphelins=20)
            meritants = {orphelins[0], orphelins[1]}
            for o in meritants:
                network.nodes[o]["activation_count"] = 1000
            network.dream_consolidation()
            relies = {end for s in network.synapses.values()
                      if s.get("context") == "dream_orphan"
                      for end in (s["source"], s["target"]) if end in set(orphelins)}
            billets_meritants += len(relies & meritants)
            billets_total += len(relies)
        assert billets_total >= N, f"trop peu de billets émis ({billets_total}/{N} cycles)"
        part = billets_meritants / billets_total
        # 2 méritants sur 22 orphelins -> part "équitable" ~9%. L'usage doit les
        # propulser BIEN au-dessus (calibré ~0.64 stable ; seuil conservateur 0.55).
        assert part >= 0.55, f"l'usage doit fortement favoriser les méritants : part={part:.3f}"

    def test_partenaire_est_un_noeud_actif(self, network, monkeypatch):
        monkeypatch.setattr(sn, "ORPHAN_REENTRY_RATE", 1.0)
        monkeypatch.setattr(sn, "ORPHAN_REENTRY_ENABLED", True)
        monkeypatch.setattr(sn, "ORPHAN_REENTRY_MAX", 1000)  # garantit que mon orphelin est tire
        actifs, orphelins = _build(network, n_orphelins=1)
        network.dream_consolidation()
        orph = orphelins[0]
        partenaires = set()
        for s in network.synapses.values():
            if s.get("context") == "dream_orphan":
                partenaires.add(s["target"] if s["source"] == orph else s["source"])
        # le partenaire d'un pont d'orphelin est un noeud actif (pas un autre orphelin)
        assert partenaires, "un pont cree pour l'unique orphelin"
        assert partenaires.issubset(set(actifs))
