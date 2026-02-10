import os
import shutil
import logging
from datetime import datetime

from core.event_bus.bus import bus
from core.capabilities.dropzone_indexer import DropzoneIndexer

logger = logging.getLogger("DropzonePipeline")

# Limite de fichiers analysés par scan pour éviter l'emballement LLM
# (80 fichiers = 160-320 appels LLM → saturation Ollama)
MAX_FILES_PER_SCAN = 10


class DropzonePipeline:
    """Pipeline d'ingestion intelligente : catalog → analyze → debate → harvest."""

    # Routage type → agent
    AGENT_ROUTING = {
        "code": "coder",       # Analyse qualité, patterns, bugs
        "config": "architect", # Structure, dépendances, cohérence
        "docs": "researcher",  # Synthèse, extraction de connaissances
    }

    def __init__(self, orchestrator, indexer=None):
        self.orchestrator = orchestrator
        self.indexer = indexer or DropzoneIndexer()

    async def run(self, dropzone_path="USER_DROPZONE") -> dict:
        """Pipeline complet : catalog → analyze → debate → harvest."""
        dropzone_path = os.path.abspath(dropzone_path)

        # 1. Catalogage
        manifest = self.indexer.catalog(dropzone_path)
        await bus.publish("DROPZONE_CATALOG", manifest["summary"])

        if manifest["summary"]["total_files"] == 0:
            return {"status": "warning", "manifest": manifest["summary"],
                    "analyses": 0, "harvest": {"memorized": 0, "grimoire_proposals": 0}}

        # 2. Analyse multi-agents par projet (dédup intégrée)
        analyzed_hashes = set()
        all_analyses = []
        for project in manifest["projects"]:
            analyses = await self._analyze_project(project, analyzed_hashes, dropzone_path)
            if analyses:
                all_analyses.append({"project": project["name"], "analyses": analyses})

        if not all_analyses:
            return {"status": "warning", "manifest": manifest["summary"],
                    "analyses": 0, "harvest": {"memorized": 0, "grimoire_proposals": 0}}

        # 3. Débat Council sur les meilleurs patterns
        debate_result = await self._debate_findings(all_analyses)

        # 4. Récolte : mémorisation RAG + propositions Grimoire
        harvest = await self._harvest_patterns(debate_result, all_analyses)

        # 5. Archivage
        self._archive_processed(dropzone_path, manifest)

        return {"status": "success", "manifest": manifest["summary"],
                "analyses": len(all_analyses), "harvest": harvest}

    async def _analyze_project(self, project: dict, analyzed_hashes: set, dropzone_root: str) -> list:
        """Analyse un projet en routant chaque type de fichier vers l'agent approprié.
        Limité à MAX_FILES_PER_SCAN fichiers par scan pour éviter l'emballement LLM."""
        analyses = []

        # Grouper les fichiers par type, en excluant les doublons déjà analysés
        by_type = {}
        total_files_queued = 0
        for f in project.get("files", []):
            sha = f.get("sha256", "")
            if sha in analyzed_hashes:
                continue
            analyzed_hashes.add(sha)
            by_type.setdefault(f["type"], []).append(f)
            total_files_queued += 1

        # Appliquer la limite MAX_FILES_PER_SCAN sur le total de fichiers envoyés aux agents
        files_sent = 0
        skipped_types = []

        for file_type, files in by_type.items():
            agent_slug = self.AGENT_ROUTING.get(file_type)
            if not agent_slug:
                continue

            # Tronquer la liste de fichiers si on dépasse la limite
            remaining_budget = MAX_FILES_PER_SCAN - files_sent
            if remaining_budget <= 0:
                skipped_types.append(f"{file_type}({len(files)})")
                continue
            files_to_process = files[:remaining_budget]
            files_sent += len(files_to_process)

            # Construire un batch de contenu (rel_path est relatif à dropzone_root)
            batch_content = self._read_batch(dropzone_root, files_to_process, max_chars=8000)
            if not batch_content.strip():
                continue

            # Prompt spécifique par type
            prompt = self._build_analysis_prompt(project["name"], file_type, batch_content)

            # Dispatch vers l'agent
            try:
                result = await self.orchestrator.dispatch_task(agent_slug, {
                    "mission": prompt,
                    "context": f"DROPZONE_ANALYSIS:{project['name']}"
                })
                if result and result.get("status") == "success":
                    analyses.append({
                        "type": file_type,
                        "agent": agent_slug,
                        "result": result.get("result", "")
                    })
            except Exception as e:
                logger.warning(f"Erreur analyse {file_type} par {agent_slug}: {e}")

        if skipped_types:
            logger.info(
                f"[DROPZONE] Limite {MAX_FILES_PER_SCAN} fichiers/scan atteinte pour '{project['name']}'. "
                f"Types ignorés : {', '.join(skipped_types)}. "
                f"Total en file : {total_files_queued}. Reste au prochain cycle."
            )

        return analyses

    async def _debate_findings(self, all_analyses: list) -> dict:
        """Lance un Council coder+architect+security pour évaluer les patterns trouvés."""
        summary = self._compile_analysis_summary(all_analyses)

        try:
            result = await self.orchestrator.dispatch_council(
                participants=["coder", "architect", "security"],
                mission=(
                    f"Voici les analyses de {len(all_analyses)} projets ingérés depuis la Dropzone.\n"
                    f"Évaluez les patterns, identifiez ce qui mérite d'être conservé comme "
                    f"recette Grimoire ou savoir mémorisé.\n\n{summary}"
                ),
                max_rounds=3
            )
            return result or {}
        except Exception as e:
            logger.warning(f"Erreur débat Council: {e}")
            return {}

    async def _harvest_patterns(self, debate_result: dict, analyses: list) -> dict:
        """Mémorise les insights dans ChromaDB et propose des recettes Grimoire."""
        memorized = 0
        grimoire_proposals = []

        # Mémoriser chaque analyse de projet via le Researcher (qui a remember() via BaseAgent)
        researcher = self.orchestrator.agents.get("researcher")

        for project_data in analyses:
            project_name = project_data["project"]
            for analysis in project_data["analyses"]:
                result_text = analysis.get("result", "")
                if not result_text:
                    continue

                # Mémorisation RAG via remember() si le researcher est disponible
                if researcher and hasattr(researcher, "remember"):
                    researcher.remember(
                        text=f"DROPZONE [{project_name}] ({analysis['type']}): {result_text[:2000]}",
                        metadata={"source": f"dropzone:{project_name}", "type": analysis["type"]}
                    )
                    memorized += 1

        # Extraire les propositions Grimoire du débat Council
        debate_text = str(debate_result.get("result", ""))
        if "grimoire" in debate_text.lower() or "recette" in debate_text.lower():
            grimoire_proposals.append(debate_text[:500])

        return {
            "memorized": memorized,
            "grimoire_proposals": len(grimoire_proposals),
            "proposals_detail": grimoire_proposals,
        }

    def _read_batch(self, root, files, max_chars=8000) -> str:
        """Lit un batch de fichiers, tronqué à max_chars total."""
        content_parts = []
        total_chars = 0

        for f in files:
            if total_chars >= max_chars:
                break

            filepath = os.path.join(root, f["rel_path"].replace("/", os.sep))
            try:
                with open(filepath, "r", encoding="utf-8") as fh:
                    text = fh.read()
            except (UnicodeDecodeError, OSError):
                try:
                    with open(filepath, "r", encoding="latin-1") as fh:
                        text = fh.read()
                except OSError:
                    continue

            remaining = max_chars - total_chars
            if len(text) > remaining:
                text = text[:remaining] + "\n[...tronqué...]"

            content_parts.append(f"--- {f['rel_path']} ---\n{text}")
            total_chars += len(text)

        return "\n\n".join(content_parts)

    def _build_analysis_prompt(self, project_name, file_type, content) -> str:
        """Construit le prompt d'analyse adapté au type de fichier."""
        prompts = {
            "code": (
                f"ANALYSE DE CODE — Projet '{project_name}'\n"
                f"Analyse la qualité du code ci-dessous. Identifie :\n"
                f"1. Les patterns de conception réutilisables\n"
                f"2. Les problèmes de qualité (bugs, dette technique)\n"
                f"3. Les bonnes pratiques à retenir\n\n{content}"
            ),
            "config": (
                f"ANALYSE DE STRUCTURE — Projet '{project_name}'\n"
                f"Analyse les fichiers de configuration ci-dessous. Identifie :\n"
                f"1. L'architecture et les dépendances du projet\n"
                f"2. Les choix technologiques\n"
                f"3. La cohérence de la configuration\n\n{content}"
            ),
            "docs": (
                f"SYNTHÈSE DOCUMENTAIRE — Projet '{project_name}'\n"
                f"Analyse la documentation ci-dessous. Extrais :\n"
                f"1. L'objectif et le périmètre du projet\n"
                f"2. Les connaissances clés à retenir\n"
                f"3. Les instructions ou processus décrits\n\n{content}"
            ),
        }
        return prompts.get(file_type, f"Analyse ce contenu du projet '{project_name}':\n{content}")

    def _compile_analysis_summary(self, all_analyses) -> str:
        """Compile toutes les analyses en un résumé pour le Council."""
        parts = []
        for project_data in all_analyses:
            parts.append(f"## Projet : {project_data['project']}")
            for analysis in project_data["analyses"]:
                result = analysis.get("result", "")
                # Tronquer chaque résultat pour garder le résumé compact
                parts.append(f"**{analysis['agent']}** ({analysis['type']}):\n{result[:1000]}")
            parts.append("")
        return "\n".join(parts)

    def _archive_processed(self, dropzone_path, manifest):
        """Crée un rapport d'archivage dans processed/."""
        processed_dir = os.path.join(dropzone_path, "processed")
        os.makedirs(processed_dir, exist_ok=True)

        report_path = os.path.join(
            processed_dir,
            f"ingestion_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        )
        try:
            summary = manifest["summary"]
            with open(report_path, "w", encoding="utf-8") as f:
                f.write(f"RAPPORT D'INGESTION DROPZONE 2.0\n")
                f.write(f"Date: {manifest['scan_date']}\n")
                f.write(f"Projets: {summary['total_projects']}\n")
                f.write(f"Fichiers totaux: {summary['total_files']}\n")
                f.write(f"Fichiers uniques: {summary['unique_files']}\n")
                f.write(f"Doublons ignorés: {summary['duplicates']}\n")
            logger.info(f"📄 Rapport d'ingestion sauvé : {report_path}")
        except Exception as e:
            logger.warning(f"Impossible de sauver le rapport : {e}")
