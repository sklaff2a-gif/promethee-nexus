# -*- coding: utf-8 -*-
"""Benchmark de scalabilite Phase 2 (DRY RUN) — estime le temps de reindexation reel.

Genere des phrases FR synthetiques, les indexe par chunks de 500 dans une collection
ephemere multilingue, mesure le debit, et EXTRAPOLE a 5709 noeuds reels.

NB HONNETE : CUDA absent sur ce poste -> embedding sur CPU (pas de VRAM GPU a mesurer).
On mesure le temps CPU reel d'indexation + la RAM process. On ne benchmarke qu'un
echantillon (BENCH_CHUNKS chunks) puis on extrapole lineairement -> pas besoin
d'attendre les 5000 pour estimer.
"""
import os
import random
import time

random.seed(42)

N_REAL = 5709            # cible reelle (collective_wisdom + source_code)
CHUNK = 500
BENCH_CHUNKS = int(os.environ.get("BENCH_CHUNKS", "4"))   # 4 chunks = 2000 docs mesures
N_GEN = BENCH_CHUNKS * CHUNK

_SUJ = ["le reseau synaptique", "la consolidation onirique", "le veto prefrontal",
        "l'embedder multilingue", "la memoire vectorielle", "le cortex prefrontal",
        "la dopamine de prediction", "le tampon de quarantaine", "le noyau premium",
        "la periode de Pisano", "l'inference locale", "le tissu neuronal"]
_VRB = ["renforce", "elague", "consolide", "retrouve", "encode", "projette",
        "active", "stabilise", "compresse", "reconsolide"]
_CMP = ["les associations pertinentes du voisinage", "le bruit synaptique residuel",
        "les acquis certifies par le gate", "la pensee laterale exploratoire",
        "les coordonnees semantiques francaises", "l'empreinte contextuelle minimale"]


def gen_phrase(i):
    return (f"{random.choice(_SUJ)} {random.choice(_VRB)} {random.choice(_CMP)} "
            f"lors du cycle nocturne numero {i}.")


def _rss_mb():
    try:
        import psutil
        return psutil.Process().memory_info().rss / 1e6
    except Exception:
        return None


def main():
    import chromadb
    from chromadb.utils import embedding_functions

    print("=" * 66)
    print(f"BENCHMARK Phase 2 (dry run) — {N_GEN} docs mesures, extrapolation a {N_REAL}")
    print("=" * 66)

    t_load0 = time.perf_counter()
    ml = embedding_functions.SentenceTransformerEmbeddingFunction(
        model_name="paraphrase-multilingual-MiniLM-L12-v2")
    client = chromadb.EphemeralClient()
    col = client.create_collection("bench", embedding_function=ml)
    t_load = time.perf_counter() - t_load0
    print(f"Chargement modele + client : {t_load:.1f}s")
    rss0 = _rss_mb()

    docs = [gen_phrase(i) for i in range(N_GEN)]
    ids = [f"b{i:05}" for i in range(N_GEN)]
    avg_words = sum(len(d.split()) for d in docs) / len(docs)

    per_chunk = []
    t_all0 = time.perf_counter()
    for c in range(BENCH_CHUNKS):
        s = slice(c * CHUNK, (c + 1) * CHUNK)
        t0 = time.perf_counter()
        col.add(documents=docs[s], ids=ids[s])      # declenche l'embedding multilingue
        dt = time.perf_counter() - t0
        per_chunk.append(dt)
        print(f"  chunk {c+1}/{BENCH_CHUNKS} ({CHUNK} docs) : {dt:.1f}s  ({CHUNK/dt:.0f} docs/s)")
    t_index = time.perf_counter() - t_all0
    rss1 = _rss_mb()

    docs_per_s = N_GEN / t_index
    ms_per_doc = t_index / N_GEN * 1000
    est_total_s = N_REAL / docs_per_s

    print("-" * 66)
    print(f"Longueur moyenne      : {avg_words:.0f} mots/doc")
    print(f"Debit indexation      : {docs_per_s:.0f} docs/s  ({ms_per_doc:.1f} ms/doc)")
    print(f"Temps/chunk moyen     : {sum(per_chunk)/len(per_chunk):.1f}s (+/- {max(per_chunk)-min(per_chunk):.1f}s)")
    if rss0 and rss1:
        print(f"RAM process           : {rss0:.0f} -> {rss1:.0f} MB (delta {rss1-rss0:+.0f} MB)")
    print(f"Substrat              : CPU (CUDA absent) — pas de VRAM GPU")
    print("=" * 66)
    print(f">>> ESTIMATION PHASE 2 REELLE ({N_REAL} noeuds) : ~{est_total_s:.0f}s "
          f"(~{est_total_s/60:.1f} min) d'indexation pure, hors lecture ancienne base.")
    print(f">>> + chargement modele {t_load:.0f}s (une fois). Operation monitorable, reprenable par chunk.")
    print("=" * 66)


if __name__ == "__main__":
    main()
