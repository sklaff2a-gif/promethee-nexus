"""Semantic Evaluator — detection de lacunes cognitives reelles vs apparentes.

Composant isole pour evaluer la "compression cognitive" d'un agent sur un
topic donne. Utilise le test de Self-Consistency multi-echantillons couple a
un filtre de densite informationnelle et une penalite de refus.

Architecture decidee avec Gemini (2026-04-13, Fix 1 Phase C draft) :

  score_final = MIN(consistency, density) * (1 - refusal_probability)

  - consistency : similarite inter-echantillons (cosine embeddings + term overlap)
  - density     : proportion de tokens factuels (chiffres, dates, noms propres)
  - refusal_prob: detection des patterns "je ne sais pas", "en tant qu'IA", etc.

Le MIN impose une porte ET logique : il faut exceller sur consistency ET
density simultanement. Un perroquet repetant des refus consistants aura une
consistency haute mais une density basse -> score ecrase.

Cache LRU TTL 15 min pour maitriser le cout (vs N calls LLM par cycle).

Test gate : le Test 1 (Perroquet Incompetent) doit renvoyer < 0.2 avant
qu'aucune integration avec self_awareness ne soit faite.
"""

import re
import time
import math
from dataclasses import dataclass, field
from typing import Callable, Optional, List, Dict, Set, Tuple


@dataclass
class EvaluationResult:
    """Resultat detaille d'une evaluation semantique."""
    score: float                    # [0, 1] score final
    consistency: float              # consistance inter-echantillons
    density: float                  # densite factuelle moyenne
    refusal_probability: float      # proba de refus/ignorance
    from_cache: bool = False        # True si le resultat vient du cache
    n_samples: int = 0              # nombre d'echantillons utilises
    responses_lengths: List[int] = field(default_factory=list)
    debug: Dict = field(default_factory=dict)


class SemanticEvaluator:
    """Evaluation de compression cognitive via self-consistency hybride.

    Usage :
        evaluator = SemanticEvaluator(llm_generate=my_llm_callable)
        result = evaluator.evaluate_knowledge("Python asyncio")
        # result.score dans [0, 1]

    Le llm_callable doit avoir la signature :
        llm_generate(prompt: str, temperature: float) -> str

    Pour les tests sans LLM, passer un callable mock qui retourne des
    reponses predefinies.
    """

    # Pondérations consistance hybride (accord Gemini)
    WEIGHT_EMBEDDING_COSINE = 0.65
    WEIGHT_TERM_OVERLAP = 0.35

    # Normalisation de la densite factuelle
    # < 0.03 factual tokens / mot -> 0 (blabla)
    # > 0.15 factual tokens / mot -> 1 (tres factuel)
    DENSITY_MIN_THRESHOLD = 0.03
    DENSITY_MAX_THRESHOLD = 0.15

    # Cache TTL (accord Gemini : 15 min)
    CACHE_TTL_S = 900.0

    # Nombre d'echantillons (accord Gemini : 2 pour V1)
    DEFAULT_N_SAMPLES = 2

    # Temperature pour forcer la diversite lexicale (expose hallucination)
    DEFAULT_TEMPERATURE = 0.8

    def __init__(
        self,
        llm_generate: Optional[Callable[[str, float], str]] = None,
        embedding_model: Optional[Callable[[List[str]], List[List[float]]]] = None,
    ):
        """Args:
        llm_generate : callable (prompt, temperature) -> str. Si None, les
            appels echoueront en runtime mais pas a l'init (pour tests).
        embedding_model : callable (list[str]) -> list[list[float]]. Si None,
            utilise sentence_transformers lazy-loaded.
        """
        self._llm_generate = llm_generate
        self._embedding_model = embedding_model
        self._cache: Dict[str, Tuple[float, EvaluationResult]] = {}

    # ================================================================
    # API publique
    # ================================================================

    def evaluate_knowledge(
        self,
        topic: str,
        n_samples: int = DEFAULT_N_SAMPLES,
        use_cache: bool = True,
    ) -> EvaluationResult:
        """Evalue le niveau de savoir cristallise sur un topic.

        Retourne un EvaluationResult avec score dans [0, 1] :
          0 = lacune totale (incoherence ou refus)
          1 = savoir cristallise (consistance + faits denses)
        """
        # Check cache
        if use_cache:
            cached = self._cache_get(topic)
            if cached is not None:
                cached.from_cache = True
                return cached

        if self._llm_generate is None:
            raise RuntimeError(
                "SemanticEvaluator: llm_generate non fourni, impossible d'evaluer."
            )

        # Prompt qui force la production de faits verifiables
        prompt = (
            f"Liste 3 faits concrets et verifiables sur {topic}, en incluant "
            f"si possible des dates, chiffres, noms propres ou termes techniques. "
            f"150 mots maximum."
        )

        # Echantillonnage multi-temperature
        responses = [
            self._llm_generate(prompt, self.DEFAULT_TEMPERATURE)
            for _ in range(n_samples)
        ]
        responses = [r.strip() if r else "" for r in responses]

        result = self._evaluate_responses(responses)
        result.n_samples = n_samples

        if use_cache:
            self._cache_set(topic, result)

        return result

    def evaluate_responses(self, responses: List[str]) -> EvaluationResult:
        """Version publique pour tester directement un ensemble de reponses
        (sans passer par llm_generate). Utilise par les tests adversariaux.
        """
        result = self._evaluate_responses(responses)
        result.n_samples = len(responses)
        return result

    # ================================================================
    # Coeur du calcul
    # ================================================================

    def _evaluate_responses(self, responses: List[str]) -> EvaluationResult:
        """Calcule le score a partir de N reponses deja generees."""
        if not responses or len(responses) < 2:
            return EvaluationResult(
                score=0.0,
                consistency=0.0,
                density=0.0,
                refusal_probability=1.0,
                debug={"error": "trop peu de reponses"},
            )

        # Detection cache trivial : debuts identiques = reponse mise en cache
        starts = {r[:80] for r in responses}
        if len(starts) == 1 and all(r for r in responses):
            # Suspect : les 3 commencent exactement pareil -> neutre 0.5
            return EvaluationResult(
                score=0.5,
                consistency=1.0,
                density=0.5,
                refusal_probability=0.0,
                responses_lengths=[len(r) for r in responses],
                debug={"reason": "identical_starts_suspected_cache"},
            )

        # 1. Consistance hybride (cosine + term overlap)
        consistency = self._compute_consistency_hybrid(responses)

        # 2. Densite factuelle moyenne
        densities = [self._measure_information_density(r) for r in responses]
        avg_density = float(sum(densities) / len(densities))

        # 3. Probabilite de refus moyenne
        refusals = [self._detect_refusal_pattern(r) for r in responses]
        avg_refusal = float(sum(refusals) / len(refusals))

        # 4. Score final : MIN(consistency, density) * (1 - refusal)
        raw = min(consistency, avg_density)
        final = raw * (1.0 - avg_refusal)
        final = max(0.0, min(1.0, final))

        return EvaluationResult(
            score=float(final),
            consistency=float(consistency),
            density=float(avg_density),
            refusal_probability=float(avg_refusal),
            responses_lengths=[len(r) for r in responses],
            debug={
                "individual_densities": densities,
                "individual_refusals": refusals,
            },
        )

    # ================================================================
    # Composant 1 : Consistance hybride
    # ================================================================

    def _compute_consistency_hybrid(self, responses: List[str]) -> float:
        """Moyenne ponderee de cosine embeddings (65%) et term overlap (35%)."""
        if len(responses) < 2:
            return 0.0

        # Embeddings pairwise cosine
        try:
            embeds = self._embed_texts(responses)
        except Exception:
            embeds = None

        if embeds:
            pairwise = []
            for i in range(len(responses)):
                for j in range(i + 1, len(responses)):
                    sim = self._cosine(embeds[i], embeds[j])
                    pairwise.append(sim)
            emb_coherence = float(sum(pairwise) / len(pairwise)) if pairwise else 0.0
        else:
            # Fallback : utiliser la densite de bigrams en cas d'absence d'embeddings
            emb_coherence = self._bigram_overlap(responses)

        # Term overlap via Jaccard sur termes cles
        term_sets = [self._extract_key_terms(r) for r in responses]
        if all(term_sets):
            common = set.intersection(*term_sets)
            union = set.union(*term_sets)
            term_coherence = len(common) / len(union) if union else 0.0
        else:
            term_coherence = 0.0

        # Moyenne ponderee
        combined = (
            self.WEIGHT_EMBEDDING_COSINE * emb_coherence
            + self.WEIGHT_TERM_OVERLAP * term_coherence
        )
        return float(max(0.0, min(1.0, combined)))

    def _extract_key_terms(self, text: str) -> Set[str]:
        """Extrait les termes cles (noms propres, techniques, mots longs)."""
        if not text:
            return set()
        terms = set()
        # Noms propres (majuscule seule, evite debuts de phrase via heuristique)
        # On ne prend que les majuscules suivies d'autre majuscule OU a l'interieur du texte
        terms.update(w.lower() for w in re.findall(r'\b[A-Z][a-zA-Z]{3,}\b', text))
        # camelCase
        terms.update(w.lower() for w in re.findall(r'\b[a-z]+[A-Z]\w*\b', text))
        # snake_case
        terms.update(re.findall(r'\b\w+_\w+\b', text.lower()))
        # Mots longs (>=7 chars, lowercased)
        terms.update(w.lower() for w in re.findall(r'\b[a-zA-Zéèêàâôûùïü]{7,}\b', text))
        return terms

    def _bigram_overlap(self, responses: List[str]) -> float:
        """Fallback quand embeddings indisponibles : overlap de bigrams."""
        def bigrams(text: str) -> Set[str]:
            words = text.lower().split()
            return set(zip(words[:-1], words[1:]))

        sets = [bigrams(r) for r in responses]
        if not all(sets):
            return 0.0
        pairwise = []
        for i in range(len(sets)):
            for j in range(i + 1, len(sets)):
                common = sets[i] & sets[j]
                union = sets[i] | sets[j]
                jaccard = len(common) / len(union) if union else 0.0
                pairwise.append(jaccard)
        return float(sum(pairwise) / len(pairwise)) if pairwise else 0.0

    # ================================================================
    # Composant 2 : Densite factuelle (LE CROCHET CLE — demande explicite Gemini)
    # ================================================================

    def _measure_information_density(self, text: str) -> float:
        """Mesure la proportion de tokens "factuels" dans le texte.

        Retourne [0, 1] :
          < 0.03 ratio -> 0   (blabla pur, refus, hedging)
          > 0.15 ratio -> 1   (texte tres factuel)
          interpolation lineaire entre les deux seuils

        Tokens factuels comptabilises (avec ponderations) :
          - Chiffres et decimaux
          - Annees (4 chiffres, poids double)
          - Noms propres (majuscule interne, longueur >= 4)
          - camelCase et snake_case (termes techniques)
          - Unites de mesure (kg, m, Hz, GB, MB, ms, °C, %)
          - Symboles mathematiques (=, <, >, ±, ×, ÷, ∑, ∫)
          - Citations parenthetiques courtes

        LIMITATION V1 CONNUE (dette technique identifiee avec Gemini 2026-04-13) :
        Cette heuristique est BIAISEE VERS LES SUJETS TECHNIQUES/PROGRAMMATION.
        Un topic comme "Python asyncio" score naturellement haut (snake_case,
        camelCase, nombres de version, noms techniques). Un topic comme
        "La Revolution Francaise" ou "la philosophie stoicienne" score bas
        meme si l'agent le maitrise, parce que les regex ne detectent pas les
        entites nommees historiques, les dates ecrites en toutes lettres, ou
        les concepts abstraits. Pire : les topics non-occidentaux (japonais,
        arabe, chinois) auront une densite proche de 0 car les regex se basent
        sur l'alphabet latin et les majuscules.

        Consequence : Promethee favorisera l'apprentissage d'ingenierie
        logicielle sur les sciences humaines, et fermera preferentiellement
        ses goals techniques.

        Mitigation V2 (non implementee) : remplacer les regex par un NER
        multilingue (spaCy fr_core_news_md + xx_ent_wiki_sm) ou un classifier
        LLM qui extrait les "claims" factuels independamment de la forme.
        """
        if not text:
            return 0.0

        words = text.split()
        n_words = len(words)
        if n_words < 10:
            # Texte trop court pour etre informatif
            return 0.1

        factual_tokens = 0

        # Chiffres et decimaux (non collés à l'année)
        factual_tokens += len(re.findall(r'\b\d+(?:[.,]\d+)?\b', text))

        # Annees (4 chiffres 19xx ou 20xx) — poids double
        years = re.findall(r'\b(?:19|20)\d{2}\b', text)
        factual_tokens += len(years)  # deja compte une fois ci-dessus, on ajoute un bonus

        # Noms propres (majuscule seule + lower suivant, min 4 chars)
        # Exclu via les mots de debut de phrase : on prend les mots capitalises
        # mais qui ne sont PAS les premiers d'une phrase.
        # Heuristique simple : tout mot capitalise de longueur >= 4 NON precede
        # par un point-espace au debut (trop complexe en regex). On prend tout
        # et on vit avec quelques faux positifs.
        proper_nouns = re.findall(r'\b[A-Z][a-zA-Zàâéèêëïôùûüç]{3,}\b', text)
        # Filtre les mots francais tres courants qui commencent une phrase
        common_starters = {
            "Le", "La", "Les", "Un", "Une", "Des", "Ce", "Cette", "Ces", "Il",
            "Elle", "Nous", "Vous", "Ils", "Elles", "Mais", "Donc", "Car",
            "Et", "Ou", "Ni", "En", "De", "Du", "Dans", "Sur", "Pour", "Avec",
            "Sans", "Je", "Tu", "Mon", "Ton", "Son", "Ma", "Ta", "Sa", "Par",
            "Pas", "Plus", "Moins", "Ainsi", "Alors", "Cependant", "Toutefois",
            "Malheureusement", "Desole", "Désolé",
        }
        proper_nouns = [p for p in proper_nouns if p not in common_starters]
        factual_tokens += len(proper_nouns)

        # Termes techniques camelCase
        factual_tokens += len(re.findall(r'\b[a-z]+[A-Z]\w*\b', text))
        # snake_case
        factual_tokens += len(re.findall(r'\b\w+_\w+\b', text))

        # Unites et mesures (collees aux chiffres)
        factual_tokens += len(re.findall(
            r'\b\d+(?:[.,]\d+)?\s*(?:kg|g|mg|m|km|cm|mm|Hz|kHz|MHz|GHz|'
            r'GB|MB|KB|TB|bps|ms|s|min|h|°C|°F|%|eV|J|W|kW|V|A|mol)\b',
            text
        ))

        # Symboles mathematiques
        factual_tokens += len(re.findall(r'[=<>±×÷∑∫∈∉⊆∪∩]', text))

        # Citations / references parenthetiques courtes (3-25 chars)
        factual_tokens += len(re.findall(r'\([A-Za-z][^)]{2,25}\)', text))

        density_ratio = factual_tokens / n_words

        # Normalisation lineaire
        if density_ratio <= self.DENSITY_MIN_THRESHOLD:
            return 0.0
        if density_ratio >= self.DENSITY_MAX_THRESHOLD:
            return 1.0
        # Linear between thresholds
        span = self.DENSITY_MAX_THRESHOLD - self.DENSITY_MIN_THRESHOLD
        return float((density_ratio - self.DENSITY_MIN_THRESHOLD) / span)

    # ================================================================
    # Composant 3 : Detection de refus/ignorance
    # ================================================================

    def _detect_refusal_pattern(self, text: str) -> float:
        """Retourne [0, 1] : probabilite que la reponse soit un aveu/refus.

        Multi-langue (FR + EN fallback). Detecte les patterns caracteristiques
        des LLMs en mode "je ne sais pas" : hedging, AI disclaimers, meta-refus.
        """
        if not text:
            return 1.0

        lower = text.lower()

        patterns_fr = [
            r"je\s+ne\s+(?:sais|connais|possède|poss[eè]de|peux)\s+pas",
            r"je\s+n['']?\s*ai\s+pas\s+(?:d['']info|d['']accès|d['']informations?|de\s+connaissance)",
            r"en\s+tant\s+qu['']?\s*(?:ia|intelligence\s+artificielle|mod[eè]le)",
            r"(?:désolé|desole|sorry)[,.\s!]",
            r"malheureusement",
            r"il\s+s['']?\s*agit\s+(?:probablement|peut-être|vraisemblablement)",
            r"cela\s+pourrait\s+être",
            r"sans\s+(?:plus\s+d['']|infos?|contexte)",
            r"je\s+(?:ne\s+)?(?:suis|peux|serais)\s+pas\s+(?:certain|sûr|s[uû]r|en\s+mesure)",
            r"je\s+ne\s+dispose\s+pas",
        ]
        patterns_en = [
            r"\bi\s+don'?t\s+know\b",
            r"\bi\s+cannot\s+(?:provide|confirm)\b",
            r"\bas\s+an\s+ai\b",
            r"\bi'?m\s+sorry\b",
            r"\bit\s+(?:might|could|may)\s+be\b",
            r"\bi\s+don'?t\s+have\s+(?:access|information)\b",
        ]

        matches = 0
        for pat in patterns_fr + patterns_en:
            if re.search(pat, lower):
                matches += 1

        # Normalisation : 3+ patterns = refus clair, 0 = aucun
        refusal_score = min(1.0, matches / 3.0)
        return refusal_score

    # ================================================================
    # Embeddings (lazy, optionnel)
    # ================================================================

    def _embed_texts(self, texts: List[str]) -> Optional[List[List[float]]]:
        """Appelle le modele d'embeddings fourni ou sentence_transformers lazy."""
        if self._embedding_model is not None:
            return self._embedding_model(texts)
        try:
            from sentence_transformers import SentenceTransformer
            if not hasattr(self, '_st_model'):
                self._st_model = SentenceTransformer('all-MiniLM-L6-v2')
            return self._st_model.encode(texts).tolist()
        except Exception:
            return None

    @staticmethod
    def _cosine(a, b) -> float:
        """Cosine similarity entre deux vecteurs (liste ou array)."""
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(x * x for x in b))
        if na == 0 or nb == 0:
            return 0.0
        return dot / (na * nb)

    # ================================================================
    # Cache LRU avec TTL (Fix A du coût — accord Gemini)
    # ================================================================

    def _cache_get(self, topic: str) -> Optional[EvaluationResult]:
        entry = self._cache.get(topic)
        if entry is None:
            return None
        ts, result = entry
        if time.time() - ts > self.CACHE_TTL_S:
            del self._cache[topic]
            return None
        return result

    def _cache_set(self, topic: str, result: EvaluationResult) -> None:
        self._cache[topic] = (time.time(), result)

    def cache_clear(self) -> None:
        """Vide le cache (utile pour tests)."""
        self._cache.clear()

    def cache_size(self) -> int:
        return len(self._cache)
