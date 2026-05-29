import hashlib
import re
import os
import time
import httpx
import json
import logging
import unicodedata
from typing import Optional


# ═══════════════════════════════════════════════════════════════════════
# V7.0 (Phase 9 - 2026-04-20) : Filtre Mnemonique du Router Chunking
# ═══════════════════════════════════════════════════════════════════════
# Audit Phase 8 : council_learned_rules.json contenait 23 entrees dont
# 78% de doublons (10x "budget quotidien", 10x "Promethee ressent le
# besoin"). Mecanique append lineaire -> encombrement, pas apprentissage.
# De plus `decision` du Council etait jetee avant V7.0.

# Stopwords d'infrastructure : apparaissent dans tous les prompts councils
# (enrobage systeme) et n'ont aucun pouvoir discriminant pour le routage.
_INFRA_STOPWORDS = frozenset({
    "debat", "debats", "autonome", "conseil", "council",
    "systeme", "system", "promethee", "prometheus",
    "ressent", "besoin", "discussion", "discuter", "discutons",
    "preoccupations", "suivantes", "analyse", "analyses",
    "donnees", "metriques", "routines", "sujet",
    # Artefacts de formatage qui polluaient les keywords pre-V7
    "[debat", "autonome]", "[conflit]", "[debat]",
})

# Stopwords francais classiques (hérités de l'ancienne on_council_rule_learned)
_FR_STOPWORDS = frozenset({
    "pour", "dans", "avec", "cette", "notre", "projet", "comment",
    "faire", "faut", "mode", "veille", "peut", "doit", "quel",
})

_ALL_STOPWORDS = _INFRA_STOPWORDS | _FR_STOPWORDS


def _keyword_signature(keywords: list) -> str:
    """Empreinte stable d'un ensemble de keywords (ordre-insensible).
    Utilisee comme cle de deduplication pour le renforcement Hebbien."""
    return hashlib.md5(
        "|".join(sorted(set(keywords))).encode("utf-8")
    ).hexdigest()[:12]

logger = logging.getLogger("router")

# Configuration minimale pour l'appel LLM autonome du routeur
try:
    from config import Config
except ImportError:
    class Config: 
        OLLAMA_URL = "http://localhost:11434/api/generate"
        AGENT_SPECIFIC_LOCAL_MODELS = {}

class RouterAgent:
    """
    RouterAgent V2.4 - Grimoire-First
    - Niveau 0 : Adressage Direct (Syntaxe 'Nom: Action') -> Passe tout (Grimoire compatible).
    - Niveau 0.5 : Consultation du Grimoire (agents éphémères spécialisés, scoring + normalisation Unicode).
    - Niveau 1 : Détection par Mots-clés (Réflexe instantané sur liste connue).
    - Niveau 2 : Analyse Sémantique LLM (Réflexion en cas d'ambiguïté).
    """

    # Cache de l'index Grimoire (chargé une seule fois)
    _grimoire_index_cache = None

    # Chunking SOAR : regles apprises depuis les Councils
    _learned_rules: list = []
    _LEARNED_RULES_FILE = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "memory", "council_learned_rules.json"
    )
    _MAX_LEARNED_RULES = 50

    @staticmethod
    async def classify_intent(mission: str) -> str:
        m_low = mission.strip().lower()

        # --- NIVEAU 0 : BLIND TRUST (Adressage Direct) ---
        # Si l'utilisateur utilise la syntaxe "Agent: ...", on obéit aveuglément.
        # Cela permet d'appeler des agents éphémères (Grimoire) inconnus de la liste statique.
        if ':' in mission:
            # On prend tout ce qu'il y a avant les deux points
            potential_agent = mission.split(':')[0].strip()

            # Sécurité basique : Un nom d'agent ne doit pas contenir d'espace
            # Ex: "math_wizard" (OK) vs "Calcul le" (KO - ignoré)
            if ' ' not in potential_agent:
                return potential_agent.lower()

        # --- NIVEAU 0.25 : CHUNKING SOAR (Règles apprises depuis les Councils) ---
        # Si un Council a déjà résolu un problème similaire, court-circuiter la délibération.
        chunked = RouterAgent._check_learned_rules(m_low)
        if chunked:
            logger.info(f"🧠 ROUTER: Chunking SOAR match -> {chunked.upper()} (règle apprise)")
            return chunked

        # --- NIVEAU 0.4 : STRONG_KEYWORDS (court-circuit anti-pollution Grimoire) ---
        # 29/05/2026 : promotion des mots-cles critiques (infra hardware +
        # security) AVANT la consultation Grimoire. Garantit que les routages
        # vitaux ne sont jamais detournes par une recette ephemere qui aurait
        # extrait un keyword generique. Doctrine : aucun outil cree
        # dynamiquement ne doit pouvoir intercepter le trafic "secu/faille/
        # cpu/ram" — ce sont des urgences infrastructurelles ou de securite.
        # Les listes ci-dessous DUPLIQUENT volontairement celles du Niveau 1
        # (lignes ~125 et ~131 plus bas) pour rester coherentes meme quand
        # Niveau 0.5 est court-circuite par cette priorite. Cas concret qui a
        # motive cette promotion : keyword generique "analyse" de csv_parser
        # mangait les routages vers security et log_analyst.
        if any(x in m_low for x in ["cpu", "ram", "gpu", "vram", "status", "santé", "check system"]):
            logger.info(f"⚡ ROUTER: Strong keyword (infra) -> INFRA")
            return "infra"
        if any(x in m_low for x in ["secu", "faille", "attack", "protect"]):
            logger.info(f"⚡ ROUTER: Strong keyword (security) -> SECURITY")
            return "security"

        # --- NIVEAU 0.5 : CONSULTATION DU GRIMOIRE (Spécialistes éphémères) ---
        # Les recettes Grimoire sont spécialisées et matchent avant les mots-clés génériques.
        grimoire_match = RouterAgent._check_grimoire_index(m_low)
        if grimoire_match:
            logger.info(f"📖 ROUTER: Grimoire match -> {grimoire_match.upper()}")
            return grimoire_match

        # --- NIVEAU 1 : SYSTÈME RÉFLEXE (Règles strictes sur Agents Connus) ---

        # 1. Priorité Mots-clés (Nom de l'agent en début de phrase sans :)
        agents = ["factory", "coder", "researcher", "architect", "strategist",
                  "writer", "infra", "security", "evolution", "formatter", "vision"]

        first_word = m_low.split(' ')[0].strip()
        if first_word in agents:
            return first_word

        # 2. Déduction Contextuelle (Mots-clés forts)
        if any(x in m_low for x in ["cpu", "ram", "gpu", "vram", "status", "santé", "check system"]): return "infra"
        if any(x in m_low for x in ["scan", "dropzone", "ingest", "archive", "lecture", "lire fichier", "recherche web", "veille"]): return "researcher"
        if any(x in m_low for x in ["reset", "nuke", "crée dossier", "supprime", "tree", "arborescence"]): return "factory"
        if any(x in m_low for x in ["code", "script", "fonction", "python", "bug", "dev", "class ", "def "]): return "coder"
        if any(x in m_low for x in ["évolue", "améliore", "optimise", "mutation", "upgrade"]): return "evolution"
        if any(x in m_low for x in ["plan", "structure", "audit", "valide", "architecture"]): return "architect"
        if any(x in m_low for x in ["secu", "faille", "attack", "protect"]): return "security"
        if any(x in m_low for x in ["rédige", "écris", "article", "tweet", "post", "seo"]): return "writer"
        if any(x in m_low for x in ["format", "clean", "nettoie", "indente", "syntaxe"]): return "formatter"
        if any(x in m_low for x in ["roadmap", "vision", "module planif", "planification"]): return "vision"
        if any(x in m_low for x in ["conseil", "débat", "council", "débattre"]): return "conseil"

        # --- NIVEAU 1.5 : ROUTAGE COMPILÉ (Neural Compiler, 0 LLM) ---
        try:
            from core.neural_compiler import compiler
            compiled = compiler.match_routing(mission)
            if compiled:
                logger.info(f"ROUTER: Routage compile → {compiled.upper()} (0 LLM)")
                return compiled
        except Exception:
            pass

        # --- NIVEAU 2 : AUTO-RÉFLEXION (Appel LLM Local) ---
        # Si aucune règle ne matche, on demande au LLM de trancher
        logger.info(f"ROUTER: Ambiguite detectee sur '{mission[:30]}...'. Analyse Semantique en cours...")
        agent = await RouterAgent._semantic_reflection(mission)

        # Enregistrer la decision N2 pour apprentissage du compiler
        try:
            from core.neural_compiler import compiler
            compiler.record_routing(mission, agent)
        except Exception:
            pass

        return agent

    @staticmethod
    def _normalize(text: str) -> str:
        """Retire les accents et met en minuscule pour comparaison insensible aux accents."""
        nfkd = unicodedata.normalize('NFKD', text)
        return "".join(c for c in nfkd if not unicodedata.combining(c)).lower()

    @staticmethod
    def _load_grimoire_index():
        """Charge le cache de l'index Grimoire si nécessaire."""
        if RouterAgent._grimoire_index_cache is None:
            index_path = os.path.join(os.path.dirname(__file__), "grimoire", "grimoire_index.json")
            if not os.path.exists(index_path):
                return False
            with open(index_path, "r", encoding="utf-8") as f:
                RouterAgent._grimoire_index_cache = json.load(f)
        return True

    @staticmethod
    def _check_grimoire_index(mission_lower: str) -> Optional[str]:
        """Consulte l'index du Grimoire avec matching robuste (scoring + normalisation Unicode)."""
        try:
            if not RouterAgent._load_grimoire_index():
                return None

            mission_norm = RouterAgent._normalize(mission_lower)
            best_slug = None
            best_score = 0

            for entry in RouterAgent._grimoire_index_cache:
                for keyword in entry.get("keywords", []):
                    kw_norm = RouterAgent._normalize(keyword)
                    if len(kw_norm) < 3:
                        continue  # Trop court, risque de faux positif
                    if kw_norm in mission_norm:
                        score = len(kw_norm)  # Les mots-clés longs sont plus précis
                        if score > best_score:
                            best_score = score
                            best_slug = entry["slug"]

            return best_slug
        except Exception as e:
            logger.warning(f"⚠️ ROUTER: Erreur lecture index Grimoire : {e}")
        return None

    @staticmethod
    def invalidate_grimoire_cache():
        """Invalide le cache de l'index Grimoire (après ajout d'une recette)."""
        RouterAgent._grimoire_index_cache = None

    # ============================================================
    # Chunking SOAR : regles apprises depuis les Councils
    # ============================================================

    @staticmethod
    def _load_learned_rules():
        """Charge les regles apprises depuis le fichier JSON.

        V7.0 (2026-04-20) : migration transparente des regles pre-V7 et
        collapse des doublons par signature. Les 23 regles heritage (dont
        10x "budget quotidien", 10x "Promethee ressent...") deviennent
        5 regles uniques avec weight accumule au premier load.
        """
        if RouterAgent._learned_rules:
            return
        try:
            with open(RouterAgent._LEARNED_RULES_FILE, "r", encoding="utf-8") as f:
                RouterAgent._learned_rules = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            RouterAgent._learned_rules = []
            return

        # V7.0 migration : injecter signature + weight + timestamps sur
        # les regles pre-V7 (qui n'ont pas ces champs).
        now = time.time()
        migrated = 0
        for rule in RouterAgent._learned_rules:
            if "signature" not in rule:
                kws = rule.get("keywords", [])
                if kws:
                    rule["signature"] = _keyword_signature(kws)
                    rule.setdefault("weight", 1)
                    rule.setdefault("created_at", now)
                    rule.setdefault("last_seen", now)
                    rule.setdefault("last_decision", "")
                    migrated += 1

        # Collapse des doublons heritage : 10 regles avec meme signature
        # deviennent 1 regle avec weight = 10. Fusion de la decision la
        # plus recente.
        by_sig: dict = {}
        for rule in RouterAgent._learned_rules:
            sig = rule.get("signature")
            if sig is None:
                continue
            if sig in by_sig:
                by_sig[sig]["weight"] = (by_sig[sig].get("weight", 1)
                                         + rule.get("weight", 1))
                if rule.get("last_seen", 0) > by_sig[sig].get("last_seen", 0):
                    by_sig[sig]["last_seen"] = rule["last_seen"]
                    if rule.get("last_decision"):
                        by_sig[sig]["last_decision"] = rule["last_decision"]
            else:
                by_sig[sig] = rule

        collapsed = len(RouterAgent._learned_rules) - len(by_sig)
        if collapsed > 0 or migrated > 0:
            RouterAgent._learned_rules = list(by_sig.values())
            logger.info(
                f"ROUTER V7.0 load: {migrated} regles migrees, "
                f"{collapsed} doublons collapses -> "
                f"{len(RouterAgent._learned_rules)} regles uniques."
            )
            # V8.1 (2026-04-21) : persister immediatement le nettoyage
            # pour qu'il survive au prochain reboot. Avant V8.1, le
            # save n'avait lieu qu'a la prochaine on_council_rule_learned,
            # ce qui pouvait ne jamais arriver (cf. bug observe le
            # 21/04 : fichier disque non mis a jour 10h apres migration).
            try:
                RouterAgent._save_learned_rules()
                logger.info(
                    "ROUTER V8.1: migration persistee sur disque."
                )
            except Exception as e:
                logger.warning(f"ROUTER V8.1: persistance migration echouee: {e}")

    @staticmethod
    def _save_learned_rules():
        """Sauvegarde les regles apprises.

        V7.0 : l'eviction est faite dans on_council_rule_learned (tri par
        weight desc). On sauvegarde la liste telle quelle. L'ancien
        [-MAX:] FIFO aveugle aurait jete les plus ponderees car la liste
        est maintenant triee weight desc au moment du save.
        """
        os.makedirs(os.path.dirname(RouterAgent._LEARNED_RULES_FILE), exist_ok=True)
        with open(RouterAgent._LEARNED_RULES_FILE, "w", encoding="utf-8") as f:
            json.dump(RouterAgent._learned_rules,
                      f, indent=2, ensure_ascii=False)

    @staticmethod
    def _check_learned_rules(mission_lower: str) -> Optional[str]:
        """Consulte les regles apprises pour un raccourci direct.

        V7.0 (2026-04-20) : routage par renforcement memoriel. Au lieu
        de retourner le premier match (FIFO aveugle), on scanne TOUS les
        matches et on retourne l'agent de la regle la plus ponderee.
        Plus un Parlement a renforce un routage (repetition = weight
        croissant), plus ce routage gagne face aux concurrents.
        """
        RouterAgent._load_learned_rules()
        mission_norm = RouterAgent._normalize(mission_lower)
        matches = []
        for rule in RouterAgent._learned_rules:
            for kw in rule.get("keywords", []):
                if kw in mission_norm and len(kw) >= 4:
                    matches.append(rule)
                    break  # 1 match par regle suffit
        if not matches:
            return None
        # Selection : weight descendant, puis last_seen descendant (recence)
        winner = max(matches,
                     key=lambda r: (r.get("weight", 1), r.get("last_seen", 0)))
        return winner.get("agent", "strategist")

    @staticmethod
    async def on_council_rule_learned(event: dict):
        """COUNCIL_RULE_LEARNED : compiler la deliberation en regle directe.

        V7.0 (2026-04-20) : Filtre Mnemonique.
          - Deduplication ponderee : meme signature de keywords -> weight++
            (renforcement Hebbien au lieu d'une nouvelle ligne).
          - Nettoyage semantique : _INFRA_STOPWORDS filtre les mots
            d'enrobage systeme (debat, autonome, promethee, ressent...)
            qui polluaient les keywords des regles pre-V7.
          - Persistance de la decision : le champ `decision` du Council
            est stocke dans la regle (droppé avant V7.0) pour future
            exploitation par le Grimoire ou d'autres organes.
        """
        mission = event.get("mission", "")
        decision = event.get("decision", "")       # V7.0 : on persiste
        participants = event.get("participants", [])
        if not mission or not participants:
            return

        # V7.0 : nettoyage semantique. Strip des crochets/ponctuation
        # d'encadrement AVANT filtrage stopwords (evite "[debat" comme keyword).
        raw = RouterAgent._normalize(mission.lower())
        words = [w.strip("[](),.:;-—'\"") for w in raw.split()]
        keywords = [w for w in words
                    if len(w) >= 4 and w not in _ALL_STOPWORDS][:5]

        if not keywords:
            return

        signature = _keyword_signature(keywords)
        agent = participants[0] if participants else "strategist"

        RouterAgent._load_learned_rules()

        # V7.0 : deduplication par signature.
        existing = None
        for rule in RouterAgent._learned_rules:
            if rule.get("signature") == signature:
                existing = rule
                break

        now = time.time()
        if existing is not None:
            # Renforcement Hebbien : incrementer le poids
            existing["weight"] = existing.get("weight", 1) + 1
            existing["last_seen"] = now
            if decision:
                existing["last_decision"] = decision[:500]
            logger.info(
                f"🧠 ROUTER CHUNKING: renforcement [{', '.join(keywords)}] -> "
                f"{existing['agent']} (weight={existing['weight']})"
            )
        else:
            rule = {
                "signature": signature,
                "mission_preview": mission[:100],
                "keywords": keywords,
                "agent": agent,
                "source": "council_chunking",
                "weight": 1,
                "created_at": now,
                "last_seen": now,
                "last_decision": decision[:500] if decision else "",
            }
            RouterAgent._learned_rules.append(rule)
            logger.info(
                f"🧠 ROUTER CHUNKING: regle apprise [{', '.join(keywords)}] -> "
                f"{agent} (sig={signature[:8]})"
            )

        # V7.0 eviction intelligente : au depassement MAX, on garde les
        # plus lourdes et les plus recentes (tri weight desc, last_seen desc).
        # Finis le FIFO aveugle qui jetait les fondations les plus renforcees.
        if len(RouterAgent._learned_rules) > RouterAgent._MAX_LEARNED_RULES:
            RouterAgent._learned_rules.sort(
                key=lambda r: (r.get("weight", 1), r.get("last_seen", 0)),
                reverse=True
            )
            RouterAgent._learned_rules = RouterAgent._learned_rules[
                :RouterAgent._MAX_LEARNED_RULES
            ]

        RouterAgent._save_learned_rules()

    @staticmethod
    def _get_grimoire_slugs() -> list:
        """Retourne la liste des slugs des agents du Grimoire."""
        try:
            if RouterAgent._grimoire_index_cache is None:
                RouterAgent._check_grimoire_index("")  # Force le chargement
            if RouterAgent._grimoire_index_cache:
                return [entry["slug"] for entry in RouterAgent._grimoire_index_cache]
        except Exception:
            pass
        return []

    @staticmethod
    async def _semantic_reflection(mission: str) -> str:
        """Demande au modèle routeur léger (4B) de classer l'intention."""
        try:
            from config import Config
            model = getattr(Config, "ROUTER_MODEL", "qwen3:4b")

            # Ajout des agents Grimoire dans la liste
            grimoire_slugs = RouterAgent._get_grimoire_slugs()
            all_agents = ["coder", "researcher", "strategist", "writer", "architect", "infra", "security", "evolution", "factory", "formatter"] + grimoire_slugs

            prompt = (
                f"Classifie cette mission vers l'agent le plus approprié.\n"
                f"AGENTS : {', '.join(all_agents)}\n"
                f"MISSION : \"{mission[:200]}\"\n"
                f"Réponds UNIQUEMENT par le nom de l'agent. UN SEUL MOT."
            )

            payload = {
                "model": model,
                "prompt": prompt,
                "stream": False,
                "think": False,
                "options": {
                    "temperature": 0.0,
                    "num_predict": 800,
                    "num_ctx": 2048,
                }
            }

            url = getattr(Config, "OLLAMA_URL", "http://localhost:11434/api/generate")
            async with httpx.AsyncClient() as client:
                response = await client.post(url, json=payload, timeout=15)

            if response.status_code == 200:
                data = response.json()
                # Priorité 1 : réponse directe (après le thinking)
                choice = data.get("response", "").strip().lower()
                # Priorité 2 : si vide, chercher dans le thinking (modèle a manqué de tokens)
                if not choice:
                    choice = data.get("thinking", "").strip().lower()
                # Extraire le dernier agent mentionné (conclusion du raisonnement)
                last_match = None
                for agent in all_agents:
                    if agent in choice:
                        last_match = agent
                if last_match:
                    logger.info(f"💡 ROUTER: {model} -> {last_match.upper()}")
                    return last_match

            logger.warning("⚠️ ROUTER: Echec réflexion IA, repli sur STRATEGIST.")
            return "strategist"

        except Exception as e:
            logger.error(f"❌ ROUTER ERROR: {e}")
            return "strategist"