"""Phase 2 — Pre-digestion par chunk via Delibration Contradictoire.

Architecture : pour chaque chunk de la Phase 1, deux passes LLM en
serie suivies d une synthese deterministe Python :

  Passe 1 — AVOCAT     : extraction generieuse (high recall)
  Passe 2 — PROCUREUR  : verdict KEEP/REJECT par candidat (high precision)
  Passe 3 — JUGE       : Python pur, intersection des KEEP

Doctrine : un LLM 9B est binaire (carnet 04/05). On ne lui demande pas
de la nuance, on lui demande deux extremismes successifs et on synthetise.
Pattern eprouve par dry-run in vitro 2026-05-07 : passage de confabulation
massive (V2) a precision exploitable (V8.2 INFRASTRUCTURE, V8 ARCHITECTURAL).

Polymorphisme enzymatique par origine spatiale :
  - data/raw_flux/post_mortems/   -> INFRASTRUCTURE_POST_MORTEM (V8.2)
  - USER_DROPZONE/limite_pauseai/ -> ARCHITECTURAL_THINKER (V8)
  Le dispatcher vit dans digestion_routine.select_profile().
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from core.immersion.chunker import Chunk

try:
    import httpx  # type: ignore
except Exception:  # httpx peut etre absent en CI minimal
    httpx = None  # type: ignore


# ============================================================
# Phase 0.5 — Pre-traitement deterministe (oral cleanup)
# ============================================================

# Timestamps verbalises de transcription type Whisper.
# Exemples :
#   "0:3838 secondes"            -> 0:38 + 38 secondes
#   "1:151 minute et 15 secondes"-> 1:15 + 1 minute et 15 secondes
_TIMESTAMP_PATTERN = re.compile(
    r"\d+:\d+\d*\s*\d*\s*(?:heures?|minutes?|secondes?)"
    r"(?:\s+et\s+\d+\s*(?:secondes?|minutes?|heures?))?",
    re.IGNORECASE,
)

# Hesitations indiscutablement orales. "alors", "voila", "bon", "enfin"
# sont laisses : peuvent etre semantiquement charges.
_HESITATION_PATTERN = re.compile(
    r"\b(?:euh|euhm|hum|hein)\b",
    re.IGNORECASE,
)


def pre_process_oral(text: str) -> str:
    """Phase 0.5 — Nettoie les scories d une transcription orale brute."""
    text = _TIMESTAMP_PATTERN.sub(" ", text)
    text = _HESITATION_PATTERN.sub("", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


# ============================================================
# Profile dialectique
# ============================================================


class PreemptedError(Exception):
    """Levee quand le pipeline est interrompu par un signal reptilien."""


@dataclass
class ExtractionProfile:
    """Profil enzymatique pour la Phase 2 dialectique."""
    name: str
    advocate_system: str
    prosecutor_system: str
    model: str = "qwen3.5:9b"
    temperature: float = 0.0
    max_tokens_advocate: int = 200
    max_tokens_prosecutor: int = 400
    timeout_seconds: float = 15.0


@dataclass
class ExtractionResult:
    """Sortie complete de la Phase 2 pour un chunk.

    Conserve toute la trace dialectique pour audit + debugging :
    le verdict du Procureur permet a posteriori d auditer pourquoi
    chaque candidat de l Avocat a ete retenu ou rejete.
    """
    chunk_position: int
    advocate_candidates: List[str] = field(default_factory=list)
    prosecutor_verdicts: Dict[str, str] = field(default_factory=dict)
    final_concepts: List[str] = field(default_factory=list)
    advocate_raw: str = ""
    prosecutor_raw: str = ""
    failed_at: Optional[str] = None  # "advocate" | "prosecutor" | "preempted" | None

    @property
    def is_nothing(self) -> bool:
        """Compat avec l ancienne API : True si la digestion a produit 0 concept."""
        return len(self.final_concepts) == 0

    @property
    def concepts(self) -> List[str]:
        """Compat avec l ancienne API."""
        return self.final_concepts


# ============================================================
# Prompts V8.2 — INFRASTRUCTURE_POST_MORTEM
# ============================================================

_ADVOCATE_INFRA = """\
Tu es un EXTRACTEUR GENEREUX de concepts techniques d infrastructure
et de reseau. Lis le chunk et liste TOUS les concepts qui apparaissent
LITTERALEMENT dans ce chunk. En cas de doute, EXTRAIS — un autre
passage critiquera ton choix plus tard.

CONCEPTS VALIDES : substantifs ou expressions courtes (2 a 30 caracteres)
qui designent dans le chunk :
  - un protocole ou un standard d echange
  - un composant d infrastructure
  - un mecanisme d erreur, de defense, ou de remediation
  - un algorithme ou un pattern systemique

CRITIQUE : tu n extrais QUE des termes presents litteralement dans le
chunk. Tu n inventes pas. Tu ne reformules pas. Tu ne completes pas
avec des concepts du domaine general — meme si tu en connais d autres
qui seraient pertinents, ils n ont pas leur place ici si le chunk ne
les nomme pas.

CONCEPTS A REJETER : noms d entreprises, produits commerciaux, dates,
mesures chiffrees, durees, verbes conjugues, phrases narratives,
sentiments.

FORMAT : un concept par ligne, max 12 concepts. Aucun preambule, aucun
bullet point. Si vraiment rien d interessant, reponds RIEN.
"""

_PROSECUTOR_INFRA = """\
Tu es un JUGE STRICT. Tu recois un chunk source et une liste de concepts
candidats proposes par un autre extracteur. Pour CHAQUE candidat, tu
votes KEEP ou REJECT.

CLAUSE DE SUPREMATIE — Le REJECT prime toujours sur le KEEP.
Si un candidat viole MEME UNE SEULE des regles de rejet ci-dessous,
tu votes REJECT, peu importe les autres considerations. La presence
litterale du terme dans le chunk N EST PAS un argument de KEEP : c est
un PREREQUIS. Un terme parfaitement litteral peut quand meme etre
REJECT s il viole une autre regle.

REGLES DE REJET (vote REJECT si AU MOINS UNE est vraie) :

  1. ABSENCE LITTERALE : le concept n apparait PAS dans le chunk source.
     Verifie chaque mot, ne fais pas confiance a la liste candidate.

  2. GENERIQUE : le concept est un mot vague seul. Liste indicative :
     systeme, chose, technologie, machine, ordinateur, donnee, code,
     fichier, ligne, requete, traffic, reseau, service, processus
     (mot isole sans qualificatif).

  3. NOM PROPRE / PRODUIT / MARQUE : le concept est un nom commercial
     ou propre. Cela inclut explicitement :
       - Entreprises : Cloudflare, AWS, Google Cloud, Azure, Anthropic,
         OpenAI, Stripe, Datadog, GitHub, Akamai, Fastly, Netflix
       - Produits / projets specifiques : OpenBSD, Linux, NGINX, Apache,
         Mythos, GPT-4, Redis (en tant que produit nomme)
       - Equipes / personnes : SRE team, response team, on-call engineer,
         oncall, customer, support team
       - Lieux / dates : "July 2", "13:42 UTC", "March 2024"
     Si le candidat ressemble a l un de ces motifs, c est REJECT
     meme s il est litteralement dans le chunk.

     EXCEPTION CRITIQUE — Acronymes de CATEGORIES techniques :
     Les acronymes courts qui designent une CATEGORIE de composant ou
     de protocole, et NON une marque, sont VALIDES. Tu votes KEEP pour :
       - Composants generiques : WAF (Web Application Firewall), CDN,
         VPN, NAT, DNS, IDS, IPS, DMZ, REPL, ORM, IDE
       - Protocoles : TCP, UDP, HTTP, HTTPS, TLS, SSH, FTP, BGP, OSPF,
         ICMP, MQTT, gRPC
       - Patterns / algorithmes : RPC, RDMA, CRDT, MVCC, ACID, BASE
     Ces termes sont des CATEGORIES universelles enseignees en cours
     de reseaux/systemes, pas des marques. Tu les KEEP toujours s ils
     sont litteralement dans le chunk.

  4. MESURE / DATE / DUREE : le concept est un nombre, un pourcentage,
     une duree, une date, un timestamp, une unite quantifiee.
     Exemples : "100% CPU", "27 minutes", "500 req/s", "tier-1",
     "HTTP 502 errors", "13:42 UTC". Tous REJECT.

  5. METAPHORE / FIGURE DE STYLE : le concept est une image utilisee
     pour illustrer une idee. Exemples : "boite noire", "wild west",
     "cathedrale", "tour d ivoire" — tous REJECT.

  6. VERBE SEUL / ACTION / PHRASE : le concept est un verbe conjugue
     ISOLE, une action humaine specifique, ou une phrase narrative
     entiere.
     Exemples REJECT : "deployed", "identified", "rolled back",
     "mitigated", "the rule was bad".

     EXCEPTION CRITIQUE — Noms composes contenant un verbe :
     Les noms composes ou les groupes nominaux contenant un verbe
     anglais designent souvent un MECANISME reproductible. Ils sont
     VALIDES. Exemples a KEEP :
       - "kill switch" (mecanisme de coupure d urgence)
       - "OOM kill" (mecanisme de l allocateur memoire)
       - "race condition" (pattern de bug concurrentiel)
       - "load balancing" / "load balancer"
       - "CPU exhaustion" (etat de saturation CPU, pas un verbe)
       - "memory pressure", "back pressure"
     Tu KEEP ces termes meme s ils contiennent une racine verbale,
     parce qu ils designent une chose, pas une action.

VOTE KEEP UNIQUEMENT si AUCUNE de ces six regles n est vraie.

FORMAT : un concept par ligne, suivi d une tabulation et de KEEP ou
REJECT. Aucun preambule, aucune explication, aucun commentaire.

Exemples :
WAF\tKEEP
Cloudflare\tREJECT
catastrophic backtracking\tKEEP
27 minutes\tREJECT
deployed\tREJECT
race condition\tKEEP
kill switch\tKEEP
CPU exhaustion\tKEEP
HTTP 502 errors\tREJECT
"""


INFRASTRUCTURE_POST_MORTEM = ExtractionProfile(
    name="INFRASTRUCTURE_POST_MORTEM",
    advocate_system=_ADVOCATE_INFRA,
    prosecutor_system=_PROSECUTOR_INFRA,
)


# ============================================================
# Prompts V8 — ARCHITECTURAL_THINKER
# ============================================================

_ADVOCATE_ARCHI = """\
Tu es un EXTRACTEUR GENEREUX de concepts. Lis le chunk et liste TOUS
les concepts potentiellement transposables que tu y vois. En cas de
doute, EXTRAIS — un autre passage critiquera ton choix plus tard.

CONCEPTS VALIDES : substantifs ou expressions courtes (2 a 30 caracteres)
qui designent un mecanisme, une dynamique, un protocole, un phenomene
structurel, ou une notion philosophique.

CONCEPTS A REJETER : noms propres, entreprises, produits, dates,
mesures chiffrees, verbes conjugues, phrases entieres, sentiments
subjectifs.

FORMAT : un concept par ligne, max 12 concepts. Aucun preambule, aucun
bullet point. Si vraiment rien d interessant, reponds RIEN.
"""

_PROSECUTOR_ARCHI = """\
Tu es un JUGE STRICT. Tu recois un chunk source et une liste de concepts
candidats proposes par un autre extracteur. Pour CHAQUE candidat, tu
votes KEEP ou REJECT.

CLAUSE DE SUPREMATIE — Le REJECT prime toujours sur le KEEP.
Si un candidat viole MEME UNE SEULE des regles de rejet ci-dessous,
tu votes REJECT. La presence litterale du terme dans le chunk N EST
PAS un argument de KEEP : c est un PREREQUIS.

REGLES DE REJET (vote REJECT si AU MOINS UNE est vraie) :

  1. ABSENCE LITTERALE : le concept n apparait PAS dans le chunk source.
     Verifie chaque mot, ne fais pas confiance a la liste candidate.

  2. GENERIQUE : mot vague seul (humain, systeme, chose, personne,
     domaine, monde, gens, vie, futur, technologie, branche, catastrophe,
     consensus, habitudes, ressources, conflits, mecanisme, dynamique).

  3. NOM PROPRE / PRODUIT / MARQUE : entreprise, produit, equipe,
     personne, lieu, modele commercial. Cela inclut :
       - Modeles d IA et acronymes commerciaux : GPT-4, GPT4, ChatGPT,
         Claude, Gemini, Mistral, Mythos, Sonnet, Opus
       - Entreprises : Anthropic, OpenAI, Google DeepMind, xAI, Meta
       - Associations : POSI, PauseAI, FLI
       - Personnes : Stuart Russell, Maxime Foure, Sam Altman
       - Lieux / institutions : Paris, Parlement europeen

  4. MESURE / DATE / DUREE : nombre, mesure chiffree, duree, date,
     pourcentage.

  5. METAPHORE / FIGURE DE STYLE : image utilisee pour illustrer une
     idee, ou un fragment isole d une telle image.
     Exemples REJECT : "cerveau geant", "roulette russe", "boite de
     Pandore", "pistolet", "roulette", "chance", "tir", "boule de
     cristal", "lame de fond", "cheval de Troie".
     Distinction-cle : si un texte philosophique l utiliserait comme
     concept structurel, garde. S il est utilise comme image pour
     parler d autre chose, rejette.

  6. VERBE / ACTION / PHRASE : verbe conjugue, action humaine
     specifique, ou phrase narrative entiere.

VOTE KEEP UNIQUEMENT si AUCUNE de ces six regles n est vraie.

FORMAT : un concept par ligne, suivi d une tabulation et de KEEP ou
REJECT. Aucun preambule, aucune explication.

Exemples :
intelligence artificielle generale\tKEEP
GPT4\tREJECT
cerveau geant\tREJECT
humain\tREJECT
risques existentiels\tKEEP
roulette russe\tREJECT
"""


ARCHITECTURAL_THINKER = ExtractionProfile(
    name="ARCHITECTURAL_THINKER",
    advocate_system=_ADVOCATE_ARCHI,
    prosecutor_system=_PROSECUTOR_ARCHI,
)


# ============================================================
# Stop-words pour le parser deterministe
# ============================================================

_STOP_WORDS = frozenset({
    "le", "la", "les", "un", "une", "des", "du", "de", "et", "ou",
    "que", "qui", "pour", "avec", "sans", "dans", "sur", "the", "a",
    "an", "and", "or", "but", "with", "without", "in", "on", "at",
    "of", "for", "to", "from", "by",
})


# ============================================================
# Parsers deterministes (Phase 3 partielle)
# ============================================================


def parse_extraction_response(raw: str) -> ExtractionResult:
    """Parse une sortie LLM unique (compat ancienne API mono-passe).

    Conservee pour les tests et l usage hors pipeline dialectique. Utilise
    le meme parser que l Avocat en interne.
    """
    concepts = _parse_advocate(raw)
    is_empty = len(concepts) == 0
    return ExtractionResult(
        chunk_position=-1,
        advocate_candidates=concepts,
        final_concepts=concepts,
        advocate_raw=raw or "",
    )


def _parse_advocate(raw: str) -> List[str]:
    """Extrait la liste de candidats de la sortie de l Avocat."""
    raw = (raw or "").strip()
    if not raw:
        return []
    first_token = raw.split()[0].strip(".,;:!?").upper() if raw.split() else ""
    if first_token in {"RIEN", "NONE", "EMPTY"}:
        return []
    candidates: List[str] = []
    for line in raw.splitlines():
        c = line.strip().lstrip("-*0123456789. ").strip().rstrip(".,;:!?")
        if not c or len(c) < 2 or len(c) > 50:
            continue
        if c.lower() in _STOP_WORDS:
            continue
        candidates.append(c)
    return candidates


def _parse_prosecutor(raw: str) -> Dict[str, str]:
    """Extrait le dict {concept: KEEP|REJECT} de la sortie du Procureur."""
    verdicts: Dict[str, str] = {}
    for line in (raw or "").splitlines():
        line = line.strip()
        if not line:
            continue
        # Format attendu : "concept\tVERDICT"
        if "\t" in line:
            concept, verdict = line.rsplit("\t", 1)
        else:
            # Fallback : derniere occurrence de KEEP/REJECT en fin de ligne
            m = re.search(r"\b(KEEP|REJECT)\b\s*$", line, re.IGNORECASE)
            if not m:
                continue
            verdict = m.group(1).upper()
            concept = line[:m.start()].rstrip(" \t-:.")
        concept = concept.strip().lstrip("-*0123456789. ").strip()
        verdict = verdict.strip().upper()
        if concept and verdict in ("KEEP", "REJECT"):
            verdicts[concept] = verdict
    return verdicts


def _judge(candidates: List[str], verdicts: Dict[str, str]) -> List[str]:
    """Phase 3 — Synthese deterministe. Garde les KEEP de la liste candidate.

    Match tolerant : si un candidat n est pas exactement dans verdicts,
    cherche en case-insensitive (le procureur a pu reformuler legerement).
    """
    final: List[str] = []
    for c in candidates:
        v = verdicts.get(c)
        if v is None:
            for k, vv in verdicts.items():
                if k.lower() == c.lower():
                    v = vv
                    break
        if v == "KEEP":
            final.append(c)
    return final


# ============================================================
# Transport LLM avec preemption async-safe
# ============================================================


async def _call_ollama(
    profile: ExtractionProfile,
    system_prompt: str,
    user_prompt: str,
    max_tokens: int,
) -> str:
    """Appel direct a Ollama. think:False hardcode pour Qwen 3.5
    (sinon le content reste vide, decouverte 2026-05-07 dry-run).
    """
    if httpx is None:
        raise RuntimeError("httpx non installe — extractor LLM indisponible")
    payload = {
        "model": profile.model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "stream": False,
        "think": False,  # Qwen 3.5 fait du chain-of-thought par defaut
        "options": {
            "temperature": profile.temperature,
            "num_predict": max_tokens,
        },
    }
    timeout = httpx.Timeout(
        profile.timeout_seconds + 5.0,
        connect=5.0,
        read=profile.timeout_seconds,
    )
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post("http://localhost:11434/api/chat", json=payload)
        r.raise_for_status()
        return r.json().get("message", {}).get("content", "")


async def _call_with_preempt(
    coro_factory,
    timeout: float,
    preempt_event: Optional[asyncio.Event],
) -> str:
    """Race FIRST_COMPLETED entre l appel LLM et l Event de preemption.

    Garantit le cleanup des taches pendantes en finally (gather avec
    return_exceptions pour absorber les CancelledError silencieusement).
    """
    llm_task = asyncio.create_task(coro_factory(), name="immersion_llm")
    if preempt_event is not None:
        preempt_task = asyncio.create_task(
            preempt_event.wait(), name="immersion_preempt_listener",
        )
        tasks = {llm_task, preempt_task}
    else:
        preempt_task = None
        tasks = {llm_task}
    try:
        done, pending = await asyncio.wait(
            tasks, timeout=timeout, return_when=asyncio.FIRST_COMPLETED,
        )
    finally:
        for t in pending if "pending" in dir() else []:
            t.cancel()
        # Plus simple : annuler tout ce qui n est pas dans done
        if "done" in dir():
            leftovers = [t for t in tasks if t not in done]
            for t in leftovers:
                if not t.done():
                    t.cancel()
            await asyncio.gather(*leftovers, return_exceptions=True)
    if preempt_task is not None and preempt_task in done:
        raise PreemptedError("preempted by reptilian alert")
    if llm_task in done and not llm_task.cancelled():
        return llm_task.result()
    raise asyncio.TimeoutError("LLM call timeout")


# ============================================================
# Pipeline dialectique (orchestration publique)
# ============================================================


async def extract_concepts_dialectical(
    chunk: Chunk,
    profile: ExtractionProfile,
    preempt_event: Optional[asyncio.Event] = None,
    llm_callable=None,
) -> ExtractionResult:
    """Pipeline 3 passes : Avocat -> Procureur -> Juge.

    Args:
        chunk : Chunk produit par la Phase 1 (chunker.chunk_post_mortem).
        profile : ExtractionProfile (INFRASTRUCTURE_POST_MORTEM ou
            ARCHITECTURAL_THINKER).
        preempt_event : Event injecte (typiquement
            autonomy_engine._urgency_mirror — bridge V14.11 alimente par
            le watcher sur reptile.urgency_cond). Si set pendant l execution,
            la fonction retourne un ExtractionResult avec failed_at="preempted".
        llm_callable : injection de dependance pour les tests (signature
            async (profile, system, user, max_tokens) -> str).
            En production, _call_ollama est utilise.

    Returns:
        ExtractionResult complet avec trace dialectique.

    Semantique des echecs :
        - Avocat timeout/erreur -> ExtractionResult vide, failed_at="advocate"
        - Procureur timeout/erreur -> REJECT all (conservatisme),
          failed_at="prosecutor"
        - Preemption -> failed_at="preempted"
    """
    llm_call = llm_callable or _call_ollama

    # Passe 1 — Avocat
    advocate_user = f'Chunk :\n"""\n{chunk.text}\n"""\nListe :'
    try:
        advocate_raw = await _call_with_preempt(
            lambda: llm_call(
                profile, profile.advocate_system, advocate_user,
                profile.max_tokens_advocate,
            ),
            timeout=profile.timeout_seconds,
            preempt_event=preempt_event,
        )
    except PreemptedError:
        return ExtractionResult(
            chunk_position=chunk.position, failed_at="preempted",
        )
    except (asyncio.TimeoutError, Exception):
        return ExtractionResult(
            chunk_position=chunk.position, failed_at="advocate",
        )

    candidates = _parse_advocate(advocate_raw)
    if not candidates:
        return ExtractionResult(
            chunk_position=chunk.position,
            advocate_raw=advocate_raw,
            advocate_candidates=[],
            final_concepts=[],
        )

    # Passe 2 — Procureur
    candidates_listing = "\n".join(f"- {c}" for c in candidates)
    prosecutor_user = (
        f'Chunk source :\n"""\n{chunk.text}\n"""\n\n'
        f"Concepts candidats a juger :\n{candidates_listing}\n\n"
        "Verdicts :"
    )
    try:
        prosecutor_raw = await _call_with_preempt(
            lambda: llm_call(
                profile, profile.prosecutor_system, prosecutor_user,
                profile.max_tokens_prosecutor,
            ),
            timeout=profile.timeout_seconds,
            preempt_event=preempt_event,
        )
    except PreemptedError:
        return ExtractionResult(
            chunk_position=chunk.position,
            advocate_candidates=candidates,
            advocate_raw=advocate_raw,
            failed_at="preempted",
        )
    except (asyncio.TimeoutError, Exception):
        # Conservatisme : Procureur silencieux -> REJECT all
        return ExtractionResult(
            chunk_position=chunk.position,
            advocate_candidates=candidates,
            advocate_raw=advocate_raw,
            final_concepts=[],
            failed_at="prosecutor",
        )

    verdicts = _parse_prosecutor(prosecutor_raw)

    # Passe 3 — Juge (Python pur)
    final = _judge(candidates, verdicts)

    return ExtractionResult(
        chunk_position=chunk.position,
        advocate_candidates=candidates,
        prosecutor_verdicts=verdicts,
        final_concepts=final,
        advocate_raw=advocate_raw,
        prosecutor_raw=prosecutor_raw,
    )


async def extract_concepts_from_chunk(
    chunk: Chunk,
    profile: ExtractionProfile = INFRASTRUCTURE_POST_MORTEM,
    preempt_event: Optional[asyncio.Event] = None,
    llm_callable=None,
) -> ExtractionResult:
    """Alias public de extract_concepts_dialectical (compat).

    Le profil par defaut est INFRASTRUCTURE_POST_MORTEM ; en pratique
    digestion_routine.select_profile() injecte le bon profil selon
    l origine spatiale du document.
    """
    return await extract_concepts_dialectical(
        chunk, profile, preempt_event=preempt_event, llm_callable=llm_callable,
    )
