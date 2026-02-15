import ast
import logging
import asyncio
import os
import re
import time
from typing import Dict, Any
from core.base_agent import BaseAgent

logger = logging.getLogger("evolution")

# Requêtes de recherche diversifiées et pertinentes pour le projet
_SEARCH_QUERIES = [
    "python asyncio best practices multi-agent system 2026",
    "FastAPI middleware performance optimization 2026",
    "chromadb vector store RAG optimization tips",
    "python event bus pub/sub patterns async",
    "ollama local LLM inference optimization batch",
    "python autonomous agent error recovery patterns",
    "pytest async testing patterns best practices",
    "python logging rotating file handler best practices",
    "python singleton pattern thread safety async",
    "websocket real-time notification system python",
]

# Modules existants du projet (pour le contexte de pertinence)
_PROJECT_MODULES = [
    "core/orchestrator.py — dispatch multi-agents, kill switch, chaînes de réaction",
    "core/base_agent.py — classe mère, RAG (remember/recall), routage Cloud/Local",
    "core/router.py — RouterAgent : classification d'intent 3 niveaux",
    "core/autonomy_engine.py — routines autonomes après inactivité, scoring, health checks",
    "core/event_bus/bus.py — bus pub/sub en mémoire",
    "core/summoner.py — chargement dynamique d'agents depuis core/grimoire/",
    "core/ci_pipeline.py — tests auto-générés, rollback, mémoire CI/CD",
    "core/self_awareness.py — conscience de soi, snapshots, PSYCHE",
    "core/council.py — débats multi-agents avec consensus",
    "Agents/ — 10 agents (strategist, coder, architect, factory, formatter, researcher, writer, security, infra, evolution)",
]

# Mots-clés hors-sujet dans les specs — si la spec en contient trop, c'est du bruit
_SPEC_OFFTOPIC_KEYWORDS = {
    "blockchain", "smart contract", "solidity", "ethereum", "web3",
    "trading", "trade", "merchant", "marchand", "order",
    "rss", "feedparser", "rss_agent",
    "flask", "django", "streamlit",
    "langchain", "langgraph", "crewai", "autogen",
    "kubernetes", "docker", "terraform", "kafka",
    "nft", "crypto", "wallet", "token",
}
_SPEC_OFFTOPIC_THRESHOLD = 2

# Fichiers existants valides (préfixes) — la spec doit cibler un de ces chemins
_VALID_TARGET_PREFIXES = ("core/", "Agents/", "config.py", "main.py")

# Répertoire racine du projet (pour lire les fichiers cibles)
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _is_spec_offtopic(spec: str) -> bool:
    """Vérifie si la spec contient trop de mots-clés hors-sujet."""
    spec_lower = spec.lower()
    count = sum(1 for kw in _SPEC_OFFTOPIC_KEYWORDS if kw in spec_lower)
    return count >= _SPEC_OFFTOPIC_THRESHOLD


def _spec_targets_existing_file(spec: str) -> bool:
    """Vérifie que la spec mentionne au moins un fichier existant du projet."""
    for prefix in _VALID_TARGET_PREFIXES:
        if prefix in spec:
            return True
    return False


class DivineEvolution(BaseAgent):
    """
    DivineEvolution V6.0 (Catalog Protocol — Sélection > Création)
    - Rôle : Directeur R&D Autonome.
    - V6 : Le LLM est sélecteur (choisit parmi 5 specs pré-écrites), plus créateur.
    - Fallback : si le LLM ne choisit pas, le meilleur score déterministe gagne.
    - Pipeline : Catalogue → Sélection → Lecture source → Coder + template → ast.parse → CI/CD → Deploy.
    """
    _query_index = 0

    def __init__(self):
        super().__init__(name="evolution", role="R&D Director", description="Supervise l'amélioration continue du système.")

    @classmethod
    def _next_search_query(cls) -> str:
        """Sélectionne la prochaine requête de recherche (rotation + jitter)."""
        query = _SEARCH_QUERIES[cls._query_index % len(_SEARCH_QUERIES)]
        cls._query_index += 1
        return query

    def _check_already_explored(self, query: str) -> bool:
        """Vérifie si ce sujet a déjà été exploré récemment via la mémoire RAG."""
        if not self.has_memory:
            return False
        past = self.recall(f"VEILLE DARWIN {query}", limit=1)
        if past and len(past) > 50:
            return True
        return False

    @staticmethod
    def _extract_python_code(text: str) -> str:
        """Extrait le code Python des blocs markdown si présents."""
        # Chercher les blocs ```python ... ``` ou ``` ... ```
        patterns = [
            r'```python\s*\n(.*?)```',
            r'```\s*\n(.*?)```',
        ]
        for pattern in patterns:
            matches = re.findall(pattern, text, re.DOTALL)
            if matches:
                # Prendre le plus long bloc (souvent le fichier complet)
                return max(matches, key=len).strip()
        # Pas de blocs markdown, retourner tel quel
        return text.strip()

    async def _generate_code_cloud(self, prompt: str) -> str:
        """Génère du code via Gemini Cloud (bypass évaluateur de complexité).
        Cascade : essaie tous les modèles Cloud configurés.
        Respecte le cooldown 429 et le budget quotidien Evolution.
        """
        now = time.time()

        # Vérifier cooldown 429
        if now < BaseAgent._cloud_cooldown_until:
            remaining = int(BaseAgent._cloud_cooldown_until - now)
            self.log_thought(f"⏸️ Cloud en cooldown 429 ({remaining}s restantes) → pas de génération Cloud", type="warning")
            return ""

        # Vérifier et reset compteur journalier
        from datetime import date
        today = date.today()
        if BaseAgent._daily_cloud_reset_day != today:
            BaseAgent._daily_cloud_calls = 0
            BaseAgent._daily_cloud_calls_evolution = 0
            BaseAgent._daily_cloud_reset_day = today

        # Vérifier budget quotidien Evolution
        if BaseAgent._daily_cloud_calls_evolution >= BaseAgent.MAX_DAILY_EVOLUTION_CALLS:
            self.log_thought(
                f"💰 Budget Cloud Evolution épuisé ({BaseAgent._daily_cloud_calls_evolution}/{BaseAgent.MAX_DAILY_EVOLUTION_CALLS})",
                type="warning"
            )
            return ""

        for model_name in self.cloud_models:
            try:
                client = self._get_gemini_client(model_name)
                if not client:
                    continue
                self.log_thought(f"☁️ Gemini ({model_name.split('/')[-1]}) — génération de code...", type="thought")
                loop = asyncio.get_running_loop()
                response = await loop.run_in_executor(None, client.generate_content, prompt)
                BaseAgent._cloud_call_count += 1
                BaseAgent._daily_cloud_calls += 1
                BaseAgent._daily_cloud_calls_evolution += 1
                if response.text and len(response.text) > 50:
                    return response.text
            except Exception as e:
                err_str = str(e)
                if "429" in err_str or "quota" in err_str.lower() or "exceeded" in err_str.lower():
                    BaseAgent._cloud_cooldown_until = now + BaseAgent.CLOUD_COOLDOWN_SECONDS
                    self.log_thought(
                        f"🚫 Quota Gemini épuisé (429) — cooldown {BaseAgent.CLOUD_COOLDOWN_SECONDS}s activé",
                        type="warning"
                    )
                    break
                self.log_thought(f"⚠️ Gemini {model_name.split('/')[-1]} échoué: {e}", type="warning")
                continue
        return ""

    def _read_target_file(self, target_file: str) -> str:
        """Lit le fichier cible depuis le projet."""
        file_path = os.path.join(_PROJECT_ROOT, target_file.replace("/", os.sep))
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception as e:
            logger.warning(f"Impossible de lire {target_file}: {e}")
            return ""

    async def _run_grimoire_creation(self) -> Dict[str, Any]:
        """Crée une nouvelle recette Grimoire quand le catalogue est épuisé."""
        from core.orchestrator import orchestrator
        from core.grimoire_writer import GrimoireWriter
        import json

        # Vérifier le nombre de recettes existantes
        try:
            index_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "core", "grimoire", "grimoire_index.json")
            with open(index_path, "r", encoding="utf-8") as f:
                current_recipes = json.load(f)
            if len(current_recipes) >= 12:
                return {"status": "success", "result": "R.A.S — Grimoire déjà complet (>= 12 recettes)."}
            existing_slugs = [r["slug"] for r in current_recipes]
        except Exception:
            existing_slugs = []

        self.log_thought("📜 Grimoire Creation : génération d'une nouvelle recette...", type="info")

        # Demander au Coder de concevoir une nouvelle recette
        spec_prompt = (
            "Conçois un nouvel agent spécialisé pour le Grimoire de Prométhée.\n"
            f"AGENTS EXISTANTS : {', '.join(existing_slugs)}\n"
            "L'agent doit être UTILE et DIFFÉRENT des agents existants.\n"
            "Retourne EXACTEMENT ce format JSON (rien d'autre) :\n"
            '{"slug": "nom_agent", "name": "NomAgent", "description": "...", '
            '"keywords": ["mot1", "mot2", "mot3"]}\n'
        )

        try:
            spec_response = await self.generate_content(spec_prompt)

            # Extraire le JSON de la réponse
            import re
            json_match = re.search(r'\{[^}]+\}', spec_response)
            if not json_match:
                return {"status": "warning", "result": "R.A.S — format spec invalide."}

            spec = json.loads(json_match.group())
            slug = spec.get("slug", "")
            name = spec.get("name", "")
            description = spec.get("description", "")
            keywords = spec.get("keywords", [])

            if not slug or not name or slug in existing_slugs:
                return {"status": "warning", "result": f"R.A.S — slug invalide ou doublon: {slug}"}

            # Demander au Coder de générer le code
            code_prompt = (
                f"Génère le code Python pour un agent Grimoire nommé {name}.\n"
                f"Description : {description}\n"
                f"RÈGLES STRICTES :\n"
                f"- La classe DOIT hériter de BaseAgent (from core.base_agent import BaseAgent)\n"
                f"- La classe DOIT implémenter async def process_task(self, task_payload)\n"
                f"- Le __init__ doit appeler super().__init__('{slug}', '...', '...')\n"
                f"- Ajoute un pré-traitement déterministe AVANT l'appel au LLM\n"
                f"- PAS de os.system, subprocess, eval, exec, __import__\n"
                f"- Retourne UNIQUEMENT le code Python, rien d'autre."
            )

            # Génération via Gemini Cloud (fallback Coder local)
            generated_code = await self._generate_code_cloud(code_prompt)
            if not generated_code or len(generated_code) < 50:
                self.log_thought("⚠️ Cloud indisponible pour Grimoire, tentative Coder local...", type="warning")
                coder_response = await orchestrator.dispatch_task("coder", {
                    "mission": code_prompt,
                    "context": "EVOLUTION_PIPELINE\nGRIMOIRE_CREATION"
                })
                generated_code = coder_response.get("result", "")

            if not generated_code or len(generated_code) < 50:
                return {"status": "warning", "result": "R.A.S — Aucun code produit pour le Grimoire (Cloud + Local)."}

            # Extraction du code Python depuis les blocs markdown
            generated_code = self._extract_python_code(generated_code)

            # Écrire via GrimoireWriter (les 7 validations sont intégrées)
            result = GrimoireWriter.write_recipe(
                slug=slug,
                name=name,
                description=description,
                keywords=keywords,
                code=generated_code
            )

            if result["status"] == "success":
                self.log_thought(f"📜 Nouvelle recette Grimoire créée : {slug} ({name})", type="info")
                if self.has_memory:
                    self.remember(
                        f"GRIMOIRE CREATED [{slug}] {name}: {description}",
                        {"source": "grimoire_creation", "slug": slug}
                    )
            else:
                self.log_thought(f"⚠️ Grimoire creation échouée : {result['message']}", type="warning")

            return result

        except Exception as e:
            logger.warning(f"Erreur grimoire creation: {e}")
            return {"status": "error", "result": str(e)}

    async def _run_catalog_pipeline(self) -> Dict[str, Any]:
        """Pipeline V6 : sélection depuis le catalogue pré-défini."""
        from core.evolution_catalog import EvolutionCatalog
        from core.orchestrator import orchestrator
        catalog = EvolutionCatalog()  # Singleton — toujours l'instance courante

        # --- PHASE 1 : SÉLECTION ---
        self.log_thought("📋 Phase 1 : Sélection depuis le catalogue...", type="thought")

        candidates = catalog.get_top_candidates(5)
        if not candidates:
            self.log_thought("📭 Catalogue épuisé : aucune spec éligible.", type="info")
            # Tenter la méta-évolution
            new_specs = catalog.generate_combinations()
            if new_specs:
                self.log_thought(f"🧬 Méta-évolution : {len(new_specs)} nouvelles specs générées.", type="info")
                candidates = catalog.get_top_candidates(5)
            if not candidates:
                # Fallback : tenter la création d'une recette Grimoire
                return await self._run_grimoire_creation()

        # Sélection LLM (classification simple 1-5)
        prompt = catalog.build_selection_prompt(candidates)
        try:
            llm_response = await self.generate_content(prompt)
            spec = catalog.parse_llm_choice(llm_response, candidates)
        except Exception as e:
            logger.warning(f"LLM sélection échouée ({e}), fallback déterministe")
            spec = candidates[0][0]

        self.log_thought(f"🎯 Spec sélectionnée : [{spec.id}] {spec.name}", type="info")

        # --- PHASE 2 : PRÉPARATION ---
        self.log_thought(f"📖 Phase 2 : Lecture de {spec.target_file}...", type="thought")
        catalog.mark_attempted(spec.id)

        source_code = self._read_target_file(spec.target_file)
        if not source_code:
            reason = f"Fichier cible introuvable: {spec.target_file}"
            catalog.mark_failed(spec.id, reason)
            return {"status": "warning", "result": f"R.A.S — {reason}"}

        # Vérifier que le fichier n'est pas protégé par la Factory
        try:
            from Agents.factory_agent import _PROTECTED_FILES
            normalized_target = spec.target_file.replace("\\", "/")
            if normalized_target in _PROTECTED_FILES:
                reason = f"Fichier protégé par Factory: {spec.target_file}"
                self.log_thought(f"🛡️ {reason} — skip spec [{spec.id}]", type="warning")
                catalog.mark_failed(spec.id, reason)
                return {"status": "warning", "result": f"R.A.S — {reason}"}
        except ImportError:
            pass

        # --- PHASE 3 : MATÉRIALISATION (Gemini Cloud) ---
        self.log_thought(f"🛠️ Phase 3 : Génération du code via Gemini Cloud [{spec.id}]...", type="info")

        code_prompt = (
            f"Applique cette amélioration au fichier {spec.target_file} (méthode: {spec.target_method}).\n"
            f"AMÉLIORATION [{spec.id}] : {spec.name}\n"
            f"DESCRIPTION : {spec.description}\n"
            f"TEMPLATE DE CODE À INTÉGRER :\n{spec.code_template}\n\n"
            f"CODE SOURCE ACTUEL DU FICHIER :\n{source_code[:3000]}\n\n"
            f"INSTRUCTIONS :\n"
            f"- Intègre le template dans le code existant\n"
            f"- Conserve TOUT le code existant fonctionnel\n"
            f"- Adapte les noms de variables si nécessaire\n"
            f"- Retourne LE FICHIER COMPLET modifié (pas juste le diff)\n"
            f"- Donne UNIQUEMENT le code Python, sans explication ni commentaire hors-code."
        )

        generated_code = await self._generate_code_cloud(code_prompt)

        if not generated_code or len(generated_code) < 50:
            # Fallback : dispatch au Coder local
            self.log_thought("⚠️ Cloud indisponible, tentative via Coder local...", type="warning")
            coder_response = await orchestrator.dispatch_task("coder", {
                "mission": code_prompt,
                "context": f"EVOLUTION_PIPELINE\nSPEC_ID: {spec.id}"
            })
            generated_code = coder_response.get("result", "")

        if not generated_code or len(generated_code) < 50:
            reason = "Aucun code produit (Cloud + Local)"
            catalog.mark_failed(spec.id, reason)
            self.log_thought(f"💤 {reason}.", type="info")
            return {"status": "warning", "result": f"R.A.S — {reason}."}

        # --- PHASE 4 : VALIDATION SYNTAXE ---
        self.log_thought("🔍 Phase 4 : Validation syntaxe (ast.parse)...", type="thought")

        # Extraction du code Python depuis les blocs markdown
        generated_code = self._extract_python_code(generated_code)

        try:
            ast.parse(generated_code)
        except SyntaxError as e:
            # Retry : renvoyer l'erreur à Gemini pour correction
            self.log_thought(f"⚠️ Syntaxe invalide ({e}), tentative de correction via Gemini...", type="warning")
            retry_prompt = (
                f"Le code Python suivant contient une erreur de syntaxe : {e}\n"
                f"CODE AVEC ERREUR :\n{generated_code[:4000]}\n\n"
                f"Corrige l'erreur et retourne UNIQUEMENT le code Python corrigé complet, sans explication."
            )
            retry_code = await self._generate_code_cloud(retry_prompt)
            if retry_code:
                retry_code = self._extract_python_code(retry_code)
                try:
                    ast.parse(retry_code)
                    generated_code = retry_code
                    self.log_thought("✅ Code corrigé avec succès après retry !", type="info")
                except SyntaxError as e2:
                    reason = f"ast.parse error (après retry): {e2}"
                    catalog.mark_failed(spec.id, reason)
                    self.log_thought(f"❌ Syntaxe toujours invalide après retry : {e2}", type="error")
                    return {"status": "error", "result": f"Spec [{spec.id}] rejetée : {reason}"}
            else:
                reason = f"ast.parse error: {e}"
                catalog.mark_failed(spec.id, reason)
                self.log_thought(f"❌ Syntaxe invalide (retry Cloud indisponible) : {e}", type="error")
                return {"status": "error", "result": f"Spec [{spec.id}] rejetée : {reason}"}

        # --- PHASE 5 : DÉPLOIEMENT SÉCURISÉ (Architecte) ---
        self.log_thought(f"🛡️ Phase 5 : Soumission [{spec.id}] à l'Architecte...", type="info")

        architect_response = await orchestrator.dispatch_task("architect", {
            "mission": (
                f"Analyse cette amélioration R&D [{spec.id}] {spec.name}.\n"
                f"Fichier cible: {spec.target_file}\n"
                f"S'il est sûr, valide-le pour déploiement (Envoi Formatter)."
            ),
            "context": generated_code
        })

        deploy_status = architect_response.get("status", "unknown")
        if deploy_status == "success":
            catalog.mark_deployed(spec.id)
            self.log_thought(f"✅ [{spec.id}] {spec.name} déployé avec succès !", type="info")

            # Publier l'événement
            try:
                from core.event_bus.bus import bus
                await bus.publish("EVOLUTION_DEPLOYED", {
                    "spec_id": spec.id,
                    "spec_name": spec.name,
                    "target_file": spec.target_file,
                    "category": spec.category,
                    "difficulty": spec.difficulty,
                })
            except Exception:
                pass

            # Journal stratégique
            if self.has_memory:
                self.remember(
                    f"EVOLUTION DEPLOYED [{spec.id}] {spec.name} sur {spec.target_file}",
                    {"source": "evolution_catalog", "spec_id": spec.id, "status": "deployed"}
                )
        else:
            reason = f"Architect: {deploy_status}"
            catalog.mark_failed(spec.id, reason)
            self.log_thought(f"⚠️ [{spec.id}] non validé par l'Architecte ({deploy_status}).", type="warning")

        return {
            "status": "success",
            "result": (
                f"CYCLE CATALOG V6 TERMINÉ.\n"
                f"Spec: [{spec.id}] {spec.name}\n"
                f"Fichier: {spec.target_file}\n"
                f"Syntaxe: OK\n"
                f"Déploiement: {deploy_status}\n"
                f"Catalogue: {catalog.get_summary()}"
            )
        }

    async def _run_legacy_pipeline(self) -> Dict[str, Any]:
        """Pipeline V5 legacy (veille Researcher → LLM spec → Coder → Architect)."""
        from core.orchestrator import orchestrator

        search_query = self._next_search_query()

        if self._check_already_explored(search_query):
            self.log_thought(f"💤 Sujet déjà exploré : {search_query}. Skip.", type="info")
            return {"status": "success", "result": "R.A.S — sujet déjà exploré."}

        self.log_thought(f"🔭 Phase 1 : Lancement Researcher ({search_query})...", type="info")

        research_response = await orchestrator.dispatch_task("researcher", {
            "mission": f"VEILLE TECHNO: Trouve une technique Python avancée ou une librairie récente ({search_query}) utile pour un système d'agents autonomes. Sois concis et technique.",
            "context": "Focus: Performance, Stabilité, Architecture."
        })

        research_data = research_response.get("result", "")
        if not research_data:
            return {"status": "warning", "result": "Recherche infructueuse."}

        if self.has_memory:
            self.remember(
                f"VEILLE DARWIN {search_query}\n{research_data[:500]}",
                {"source": "darwin_protocol", "query": search_query}
            )

        self.log_thought("🧠 Phase 2 : Analyse de la pertinence...", type="thought")

        modules_list = "\n".join(f"  - {m}" for m in _PROJECT_MODULES)
        decision_prompt = (
            f"Tu es le Directeur R&D du projet PROMÉTHÉE (système multi-agents IA autonome).\n"
            f"MODULES EXISTANTS DU PROJET :\n{modules_list}\n\n"
            f"Voici une veille technologique :\n{research_data[:2000]}\n\n"
            f"ANALYSE : Est-ce une amélioration CONCRÈTE et APPLICABLE à un module existant de PROMÉTHÉE ?\n"
            f"ATTENTION : Ne propose PAS de nouveau module générique (trading, commerce, smart contracts, RSS, etc.).\n"
            f"CONTRAINTE : Le projet tourne sur UN SEUL PC Windows avec Ollama local. "
            f"Pas de Kubernetes, Docker, Kafka, microservices, blockchain, Chaos Engineering.\n"
            f"La spécification doit cibler un fichier EXISTANT (core/*.py ou Agents/*.py) et proposer une modification précise.\n\n"
            f"SI OUI : Rédige une SPÉCIFICATION TECHNIQUE pour le Coder :\n"
            f"  - Fichier cible existant (ex: core/orchestrator.py)\n"
            f"  - Modification précise (quelle méthode améliorer, quel pattern appliquer)\n"
            f"SI NON : Réponds juste 'R.A.S'."
        )
        spec_response = await self.generate_content(decision_prompt)

        if "R.A.S" in spec_response:
            self.log_thought("💤 Découverte non pertinente. Fin de cycle.", type="info")
            return {"status": "success", "result": "R.A.S"}

        if _is_spec_offtopic(spec_response):
            self.log_thought(
                "🚫 Spec rejetée : contient des mots-clés hors-périmètre (trading/blockchain/RSS/etc.).",
                type="warning"
            )
            return {"status": "success", "result": "R.A.S — spec hors périmètre projet."}

        if not _spec_targets_existing_file(spec_response):
            self.log_thought(
                "🚫 Spec rejetée : ne cible aucun fichier existant (core/*.py ou Agents/*.py).",
                type="warning"
            )
            return {"status": "success", "result": "R.A.S — spec ne cible aucun module existant."}

        self.log_thought("🛠️ Phase 3 : Délégation au Coder...", type="info")

        coder_response = await orchestrator.dispatch_task("coder", {
            "mission": (
                "Génère le code complet correspondant à cette spécification. "
                "Le code DOIT modifier un fichier EXISTANT du projet PROMÉTHÉE. "
                "Donne UNIQUEMENT le code Python."
            ),
            "context": f"EVOLUTION_PIPELINE\nSPÉCIFICATION :\n{spec_response}"
        })

        generated_code = coder_response.get("result", "")
        if not generated_code or "R.A.S" in generated_code:
            self.log_thought("💤 Coder n'a rien produit de pertinent.", type="info")
            return {"status": "success", "result": "R.A.S — code non pertinent."}

        self.log_thought("🛡️ Phase 4 : Soumission à l'Architecte...", type="info")

        architect_response = await orchestrator.dispatch_task("architect", {
            "mission": "Analyse ce nouveau module R&D. S'il est sûr, valide-le pour déploiement (Envoi Formatter).",
            "context": generated_code
        })

        return {
            "status": "success",
            "result": f"CYCLE DARWIN TERMINÉ.\nRecherche: OK\nSpec: OK\nCode: OK\nDéploiement: {architect_response.get('status')}"
        }

    async def process_task(self, task_payload: Dict[str, Any]) -> Dict[str, Any]:
        mission = task_payload.get("mission", "")
        context = task_payload.get("context", "")

        # DÉCLENCHEMENT : MODE VEILLE (Automatique ou Manuel)
        if "[MODE VEILLE]" in mission or "veille" in mission.lower():
            self.log_thought("🧬 Activation du Protocole Darwin (V6 Catalog Protocol)...", type="thought")

            try:
                # Pipeline V6 par défaut (catalogue pré-défini)
                if "LEGACY" in mission or "legacy" in context:
                    return await self._run_legacy_pipeline()
                return await self._run_catalog_pipeline()
            except Exception as e:
                self.log_thought(f"❌ Erreur critique Protocole Darwin : {e}", type="error")
                return {"status": "error", "result": str(e)}

        # MODE PAR DÉFAUT
        else:
            return {"status": "success", "result": "Evolution en attente d'ordre de veille."}
