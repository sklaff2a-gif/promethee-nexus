# -*- coding: utf-8 -*-
"""Prototype MESURABLE — Memoire V2 : routeur de retrieval a 3 etages etiquetant.

Sandbox ISOLE (hors runtime, ne touche a aucun organe). Objectif : PROUVER PAR LA
MESURE, pas par l'affirmation. Trois theses du blueprint V2 :
  1. FUSION SEMANTIQUE (le trou #1) : un embedding retrouve un acquis meme reformule,
     la ou le hash MD5 (mecanisme actuel de _make_node_id) cree deux noeuds etrangers.
  2. ETIQUETAGE / QUARANTAINE : le routeur assemble un contexte ou le PREMIUM est
     injecte comme verite et le TAMPON sous label strict [PISTE NON VERIFIEE].
  3. ETINCELLE LOCALE : la reponse est assemblee par traversee de graphe (voisinage
     pre-cable) au lieu d'un re-scan global. Mesure honnete + reserve a l'echelle.
"""
import hashlib
import numpy as np
from sentence_transformers import SentenceTransformer

# ---------------------------------------------------------------------------
# 1. MINI-CORPUS JOUET (domaine Promethee). tier in {CHURN, TAMPON, PREMIUM}.
# ---------------------------------------------------------------------------
STOCK = [
    # --- PREMIUM (savoir certifie) ---
    ("p_veto",      "Le veto prefrontal refuse une tache bien notee quand elle distrait de l'objectif", "PREMIUM"),
    ("p_honnete",   "L'honnetete est l'invariant absolu : ne jamais confabuler une preuve", "PREMIUM"),
    ("p_intention", "Une logique d'anti-repetition doit s'appuyer sur l'intention structuree stockee a la source, jamais sur le re-parsing d'un texte auto-genere", "PREMIUM"),
    ("p_gate",      "Le gate humain reste l'ancrage de verite ; l'interne propose, l'externe consacre", "PREMIUM"),
    # --- TAMPON (hypotheses non verifiees) ---
    ("t_graphrag",  "Le GraphRAG retrouve des relations semantiques entre entites la ou le RAG vectoriel ne voit que la similarite de texte", "TAMPON"),
    ("t_interleav", "L'interleaved retrieval alterne recherche puis raisonnement puis recherche au lieu d'une passe unique", "TAMPON"),
    ("t_neuromorph","Un substrat neuromorphique evenementiel abolirait le mur de von Neumann pour le predictive coding", "TAMPON"),
    ("t_sqlite",    "SQLite pourrait servir de memory bank local pour l'etat des agents entre les sessions", "TAMPON"),
    # --- CHURN (memoire courante / bruit) ---
    ("c_dream",     "La consolidation onirique nocturne elague le bruit et renforce les liens essentiels", "CHURN"),
    ("c_bpm",       "Le rythme cardiaque monte a 130 BPM en etat de flow", "CHURN"),
    ("c_dropzone",  "La dropzone ingere les fichiers et photos deposes par l'utilisateur", "CHURN"),
    ("c_gpu",       "La RTX 5070 Ti est bridee a 250W avec throttle a 75 degres", "CHURN"),
    ("c_pisano",    "La periode de Pisano donne le cycle des restes de Fibonacci modulo n", "CHURN"),
    ("c_collatz",   "La conjecture de Collatz reste un mur ouvert : vrai presque partout n'est pas vrai partout", "CHURN"),
    ("c_compact",   "Au seuil de contexte, le chat resume les vieux tours en un digest et archive le brut", "CHURN"),
    ("c_sieste",    "La sieste profonde decharge Ollama de la VRAM et lance la consolidation", "CHURN"),
]

# Requetes de fusion : une reformulation (3e phrasing, ABSENTE du stock) + l'id attendu.
TEST_FUSION = [
    ("Pourquoi le cortex bloque-t-il une mission bien evaluee mais hors-sujet ?", "p_veto"),
    ("Ne pas inventer de demonstration, rester sincere coute que coute", "p_honnete"),
    ("Stocker le choix a l'origine plutot que de le redeviner depuis un log", "p_intention"),
    ("Un graphe de connaissances capte les liens entre concepts, pas juste la ressemblance des mots", "t_graphrag"),
    ("Le sommeil consolide la memoire en taillant le superflu", "c_dream"),
]

SIM_EDGE = 0.45   # seuil de similarite cosinus pour cabler une arete (voisinage)
TOPK_SEEDS = 2    # nb de graines d'entree (l'etincelle part de la)

# ---------------------------------------------------------------------------
# 2. EMBEDDINGS + GRAPHE
# ---------------------------------------------------------------------------
def _hash_id(text):
    return hashlib.md5(text.strip().lower().encode("utf-8")).hexdigest()[:12]

def cosine(a, b):
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9))

import os
EMBED_MODEL = os.environ.get("PROTO_EMBED", "paraphrase-multilingual-MiniLM-L12-v2")
print(f"Chargement de l'embedder : {EMBED_MODEL} ...")
MODEL = SentenceTransformer(EMBED_MODEL)

ids = [x[0] for x in STOCK]
texts = [x[1] for x in STOCK]
tiers = {x[0]: x[2] for x in STOCK}
emb = MODEL.encode(texts, normalize_embeddings=True)
EMB = {ids[i]: emb[i] for i in range(len(ids))}

# Graphe : arete entre deux acquis si cosine > SIM_EDGE (voisinage associatif pre-cable)
ADJ = {i: [] for i in ids}
n_edges = 0
for i in range(len(ids)):
    for j in range(i + 1, len(ids)):
        s = cosine(emb[i], emb[j])
        if s > SIM_EDGE:
            ADJ[ids[i]].append((ids[j], s)); ADJ[ids[j]].append((ids[i], s)); n_edges += 1

# ---------------------------------------------------------------------------
# 3. LE ROUTEUR : etincelle locale + assemblage etiquete par tier
# ---------------------------------------------------------------------------
LABELS = {
    "PREMIUM": "[CERTIFIE]",
    "TAMPON":  "[PISTE NON VERIFIEE]",
    "CHURN":   "[memoire courante]",
}

def route(query):
    """Retourne (contexte_assemble, stats). Compte les comparaisons de similarite."""
    qv = MODEL.encode([query], normalize_embeddings=True)[0]
    # --- phase graines : on compare la requete au stock pour trouver les points d'entree
    sims = [(i, cosine(qv, EMB[i])) for i in ids]
    comparaisons_graines = len(ids)
    sims.sort(key=lambda t: t[1], reverse=True)
    seeds = [i for i, _ in sims[:TOPK_SEEDS]]
    # --- phase etincelle : voisinage a 1 saut SUIVI PAR LES ARETES (0 re-calcul de similarite)
    touched = set(seeds)
    for s in seeds:
        for (nb, _w) in ADJ[s]:
            touched.add(nb)
    comparaisons_etincelle = comparaisons_graines  # seules les graines coutent un calcul
    # --- assemblage etiquete par tier ---
    contexte = []
    for nid in touched:
        contexte.append(f"{LABELS[tiers[nid]]} {dict(zip(ids, texts))[nid]}")
    # tri : premium d'abord (colore), puis tampon, puis churn
    ordre = {"[CERTIFIE]": 0, "[PISTE NON VERIFIEE]": 1, "[memoire courante]": 2}
    contexte.sort(key=lambda c: ordre[c.split(']')[0] + ']'])
    return contexte, {"seeds": seeds, "touched": len(touched), "comp": comparaisons_etincelle}

# ---------------------------------------------------------------------------
# 4. MESURES
# ---------------------------------------------------------------------------
print("\n" + "=" * 70)
print(f"CORPUS : {len(ids)} acquis | {n_edges} aretes (seuil cosine {SIM_EDGE})")
print("=" * 70)

# --- Mesure 1 : FUSION SEMANTIQUE (embedding vs hash MD5) ---
print("\n--- MESURE 1 : FUSION SEMANTIQUE (le trou #1) ---")
ok_top1 = 0; ok_top3 = 0; ok_hash = 0
for q, attendu in TEST_FUSION:
    qv = MODEL.encode([q], normalize_embeddings=True)[0]
    classe = sorted(ids, key=lambda i: cosine(qv, EMB[i]), reverse=True)
    rang = classe.index(attendu) + 1
    sim = cosine(qv, EMB[attendu])
    hit1 = (classe[0] == attendu); hit3 = (attendu in classe[:3])
    hit_hash = (_hash_id(q) == _hash_id(dict(zip(ids, texts))[attendu]))
    ok_top1 += hit1; ok_top3 += hit3; ok_hash += hit_hash
    print(f"  '{q[:52]}...'")
    print(f"     cible={attendu} rang={rang} sim={sim:.2f} | top1={'OK' if hit1 else classe[0]} | top3={'OK' if hit3 else 'NON'} | hash=etranger")
print(f"  >>> EMBEDDING top1 {ok_top1}/{len(TEST_FUSION)} | top3 {ok_top3}/{len(TEST_FUSION)}  vs  HASH MD5 {ok_hash}/{len(TEST_FUSION)}")

# --- Mesure 2 : ETIQUETAGE / QUARANTAINE ---
print("\n--- MESURE 2 : ETIQUETAGE / QUARANTAINE (exemple) ---")
q_demo = "Comment ma memoire retrouve-t-elle les bons souvenirs sans tout activer ?"
ctx, st = route(q_demo)
print(f"  Requete : '{q_demo}'")
print(f"  Graines : {st['seeds']} | noeuds assembles : {st['touched']}")
for c in ctx:
    print("    " + c[:95])
prem = sum(1 for c in ctx if c.startswith("[CERTIFIE]"))
tamp = sum(1 for c in ctx if c.startswith("[PISTE"))
print(f"  >>> Contexte : {prem} certifie(s) en tete, {tamp} piste(s) en quarantaine etiquetee. Aucun tampon presente comme verite.")

# --- Mesure 3 : ETINCELLE LOCALE vs RAG PLAT ---
print("\n--- MESURE 3 : ETINCELLE LOCALE vs RAG PLAT (sobriete) ---")
total_touched = 0; total_q = 0
for q, _ in TEST_FUSION:
    _, st = route(q)
    total_touched += st["touched"]; total_q += 1
moy = total_touched / total_q
print(f"  Etincelle : {moy:.1f} noeuds assembles/requete (graines={TOPK_SEEDS} + voisinage pre-cable, 0 re-calcul de similarite pour le voisinage).")
print(f"  RAG plat  : re-classe et considere les {len(ids)} noeuds a chaque requete pour produire le contexte.")
print(f"  >>> Le voisinage vient des ARETES (O(degre)), pas d'un re-scan. NB honnete : le vrai gain")
print(f"      energetique exige un index ANN pour la phase graines (futur) ; ici les graines scannent encore tout.")
print("\n" + "=" * 70)
print("FIN — prototype isole, mesures reelles sur le corpus ci-dessus.")
print("=" * 70)
