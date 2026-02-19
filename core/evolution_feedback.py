# core/evolution_feedback.py — Boucle de feedback post-déploiement Evolution
# Observe les métriques avant/après déploiement d'une spec, évalue l'impact,
# rollback automatique si dégradation, confirmation si amélioration/neutre.

import json
import os
import shutil
import logging
from datetime import datetime
from typing import Optional, List, Dict, Any

logger = logging.getLogger("EvolutionFeedback")

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FEEDBACK_STATE_FILE = os.path.join(
    PROJECT_ROOT, "memory", "evolution_feedback.json"
)


class EvolutionFeedbackLoop:
    """Singleton — observe, évalue et décide du sort des specs déployées."""

    _instance: Optional["EvolutionFeedbackLoop"] = None

    OBSERVATION_PERIOD = 15  # routines avant évaluation

    # Seuils de dégradation
    SUCCESS_RATE_DROP_THRESHOLD = -0.15   # -15%
    ERROR_STREAK_INCREASE_THRESHOLD = 3
    CI_FAIL_INCREASE_THRESHOLD = 2

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._pending_observations: List[Dict[str, Any]] = []
        self._completed_feedbacks: List[Dict[str, Any]] = []
        self._subscribed = False
        self._load()

    # --- Init & Reset ---

    def init(self):
        """Souscrit aux événements bus pour la collecte passive."""
        if self._subscribed:
            return
        try:
            from core.event_bus.bus import bus
            bus.subscribe("EVOLUTION_DEPLOYED", self._on_evolution_deployed)
            bus.subscribe("AUTONOMY_ROUTINE_COMPLETE", self._on_routine_complete)
            bus.subscribe("ARTIFACT_CREATED", self._on_artifact_created)
            self._subscribed = True
            logger.info("FEEDBACK: Boucle de feedback Evolution active.")
        except Exception as e:
            logger.warning(f"FEEDBACK: Erreur init bus: {e}")

    @classmethod
    def reset_singleton(cls):
        """Reset le singleton (utilisé par les tests)."""
        cls._instance = None

    # --- Capture de métriques ---

    def _capture_metrics(self) -> Dict[str, Any]:
        """Snapshot léger des métriques système."""
        metrics = {
            "success_rate": 1.0,
            "error_streak": 0,
            "ci_pass": 0,
            "ci_fail": 0,
            "health_verdict": "UNKNOWN",
            "timestamp": datetime.now().isoformat(),
        }
        try:
            from core.self_awareness import awareness
            snap = awareness.get_latest_snapshot()
            if snap:
                perf = snap.get("performance", {})
                metrics["success_rate"] = perf.get("success_rate", 1.0)
                metrics["error_streak"] = perf.get("error_streak", 0)
                metrics["ci_pass"] = perf.get("ci_pass", 0)
                metrics["ci_fail"] = perf.get("ci_fail", 0)
                metrics["health_verdict"] = snap.get("health", {}).get("verdict", "UNKNOWN")
        except Exception as e:
            logger.warning(f"FEEDBACK: Erreur capture métriques: {e}")
        return metrics

    # --- Handlers bus ---

    async def _on_evolution_deployed(self, event: dict):
        """Quand une spec est déployée, démarre l'observation."""
        data = event.get("data", event)
        spec_id = data.get("spec_id", "")
        spec_name = data.get("spec_name", "")
        target_file = data.get("target_file", "")

        if not spec_id:
            return

        # Vérifier qu'on n'observe pas déjà cette spec
        for obs in self._pending_observations:
            if obs["spec_id"] == spec_id:
                logger.info(f"FEEDBACK: {spec_id} déjà en observation, skip.")
                return

        before_snapshot = self._capture_metrics()
        observation = {
            "spec_id": spec_id,
            "spec_name": spec_name,
            "target_file": target_file,
            "before_snapshot": before_snapshot,
            "routines_observed": 0,
            "started_at": datetime.now().isoformat(),
        }
        self._pending_observations.append(observation)
        self._save()
        logger.info(
            f"FEEDBACK: Observation démarrée pour [{spec_id}] "
            f"({self.OBSERVATION_PERIOD} routines)"
        )

    async def _on_routine_complete(self, event: dict):
        """À chaque routine, incrémente les compteurs d'observation."""
        if not self._pending_observations:
            return

        completed = []
        for obs in self._pending_observations:
            obs["routines_observed"] += 1
            if obs["routines_observed"] >= self.OBSERVATION_PERIOD:
                completed.append(obs)

        for obs in completed:
            self._pending_observations.remove(obs)
            await self._evaluate_deployment(obs)

        if completed:
            self._save()

    async def _on_artifact_created(self, event: dict):
        """Quand la Factory écrit un fichier, confirme le déploiement si spec_id présent."""
        data = event.get("data", event)
        spec_id = data.get("spec_id")
        if not spec_id:
            return
        try:
            from core.evolution_catalog import catalog
            spec = catalog.get_spec(spec_id)
            if spec and spec.status == "pending_deploy":
                catalog.mark_deployed(spec_id)
                logger.info(f"FEEDBACK: [{spec_id}] confirmé deployed via ARTIFACT_CREATED.")
        except Exception as e:
            logger.warning(f"FEEDBACK: Erreur confirmation deploy {spec_id}: {e}")

    # --- Évaluation ---

    async def _evaluate_deployment(self, obs: Dict[str, Any]):
        """Compare before/after et prend une décision."""
        after_snapshot = self._capture_metrics()
        before = obs["before_snapshot"]

        # Calcul des deltas
        delta_success = after_snapshot["success_rate"] - before["success_rate"]
        delta_error_streak = after_snapshot["error_streak"] - before["error_streak"]
        delta_ci_fail = after_snapshot["ci_fail"] - before["ci_fail"]

        # Déterminer le verdict
        verdict = "neutral"
        reasons = []

        if delta_success <= self.SUCCESS_RATE_DROP_THRESHOLD:
            verdict = "degraded"
            reasons.append(
                f"success_rate: {before['success_rate']:.0%} → "
                f"{after_snapshot['success_rate']:.0%} ({delta_success:+.0%})"
            )
        if delta_error_streak >= self.ERROR_STREAK_INCREASE_THRESHOLD:
            verdict = "degraded"
            reasons.append(
                f"error_streak: {before['error_streak']} → "
                f"{after_snapshot['error_streak']} (+{delta_error_streak})"
            )
        if delta_ci_fail >= self.CI_FAIL_INCREASE_THRESHOLD:
            verdict = "degraded"
            reasons.append(
                f"ci_fail: {before['ci_fail']} → "
                f"{after_snapshot['ci_fail']} (+{delta_ci_fail})"
            )

        if verdict != "degraded" and delta_success > 0.05:
            verdict = "improved"

        comparison = {
            "before": before,
            "after": after_snapshot,
            "delta_success": round(delta_success, 4),
            "delta_error_streak": delta_error_streak,
            "delta_ci_fail": delta_ci_fail,
            "verdict": verdict,
            "reasons": reasons,
        }

        # Mettre à jour l'expérience dans le registre
        spec_id = obs["spec_id"]
        try:
            from core.experience_registry import ExperienceRegistry
            exp_registry = ExperienceRegistry()
            exp_history = exp_registry.get_spec_history(spec_id)
            if exp_history:
                latest = exp_history[-1]
                latest["post_deploy_verdict"] = verdict
                latest["post_deploy_delta"] = round(delta_success, 4)
                exp_registry._save()
        except Exception as e:
            logger.warning(f"FEEDBACK: Erreur mise à jour registre: {e}")

        # Actions selon le verdict
        try:
            from core.evolution_catalog import catalog
            if verdict == "degraded":
                reason_text = "; ".join(reasons) if reasons else "dégradation détectée"
                await self._rollback_spec(obs)
                catalog.mark_rolled_back(spec_id, reason_text)
                logger.warning(
                    f"FEEDBACK: [{spec_id}] DÉGRADÉ → rollback. Raisons: {reason_text}"
                )
            else:
                catalog.mark_confirmed(spec_id)
                logger.info(
                    f"FEEDBACK: [{spec_id}] {verdict.upper()} → confirmé."
                )
        except Exception as e:
            logger.warning(f"FEEDBACK: Erreur mise à jour catalogue: {e}")

        # Écrire la leçon dans le journal stratégique
        lesson = self._format_lesson(obs, comparison)
        try:
            from core.strategic_journal import journal
            journal.append_evolution_report(lesson)
        except Exception as e:
            logger.warning(f"FEEDBACK: Erreur écriture journal: {e}")

        # Publier l'événement
        try:
            from core.event_bus.bus import bus
            await bus.publish("EVOLUTION_FEEDBACK", {
                "spec_id": spec_id,
                "spec_name": obs["spec_name"],
                "verdict": verdict,
                "comparison": comparison,
            })
        except Exception:
            pass

        # Archiver le feedback
        feedback_record = {
            "spec_id": spec_id,
            "spec_name": obs["spec_name"],
            "target_file": obs["target_file"],
            "verdict": verdict,
            "comparison": comparison,
            "evaluated_at": datetime.now().isoformat(),
        }
        self._completed_feedbacks.append(feedback_record)
        self._save()

    # --- Rollback ---

    async def _rollback_spec(self, obs: Dict[str, Any]):
        """Rollback via le fichier .bak de Factory."""
        target_file = obs.get("target_file", "")
        if not target_file:
            logger.warning("FEEDBACK: Pas de target_file pour rollback.")
            return

        filepath = os.path.join(PROJECT_ROOT, target_file.replace("/", os.sep))
        bak_path = filepath + ".bak"

        if os.path.exists(bak_path):
            try:
                shutil.copy2(bak_path, filepath)
                logger.info(f"FEEDBACK: Rollback {target_file} depuis .bak réussi.")
            except Exception as e:
                logger.warning(f"FEEDBACK: Erreur rollback {target_file}: {e}")
        else:
            logger.warning(
                f"FEEDBACK: Pas de .bak pour {target_file}, rollback impossible."
            )

        # Publier l'événement rollback + Smart Restart pour recharger le code restauré
        try:
            from core.event_bus.bus import bus
            await bus.publish("EVOLUTION_ROLLED_BACK", {
                "spec_id": obs["spec_id"],
                "spec_name": obs["spec_name"],
                "target_file": target_file,
            })
            # Smart Restart : le fichier sur disque est l'ancien, mais le code en mémoire est le nouveau
            await bus.publish("SMART_RESTART_REQUESTED", {
                "filename": target_file,
                "reason": f"rollback spec {obs.get('spec_name', '?')}",
            })
        except Exception:
            pass

    # --- Rapport / Leçon ---

    def _format_lesson(self, obs: Dict[str, Any],
                       comparison: Dict[str, Any]) -> str:
        """Génère un rapport lisible pour le journal stratégique."""
        verdict = comparison["verdict"]
        before = comparison["before"]
        after = comparison["after"]
        delta_s = comparison["delta_success"]
        delta_e = comparison["delta_error_streak"]

        verdict_label = {
            "improved": "AMELIORE",
            "neutral": "NEUTRE",
            "degraded": "DEGRADE (rollback)",
        }.get(verdict, verdict.upper())

        lines = [
            f"**Spec**: [{obs['spec_id']}] {obs['spec_name']}",
            f"**Fichier**: {obs['target_file']}",
            f"**Verdict**: {verdict_label}",
            f"**Metriques**: success_rate: {before['success_rate']:.0%} → "
            f"{after['success_rate']:.0%} ({delta_s:+.0%}), "
            f"error_streak: {before['error_streak']} → "
            f"{after['error_streak']} ({delta_e:+d})",
        ]

        if verdict == "degraded":
            reasons = comparison.get("reasons", [])
            lines.append(f"**Raisons rollback**: {'; '.join(reasons)}")
        elif verdict == "improved":
            lines.append("**Lecon**: La spec a ameliore les metriques systeme.")
        else:
            lines.append("**Lecon**: Impact neutre, pas d'effet secondaire detecte.")

        return "\n".join(lines)

    # --- Accesseurs ---

    def get_pending_observations(self) -> List[Dict[str, Any]]:
        """Retourne les observations en cours."""
        return list(self._pending_observations)

    def get_completed_feedbacks(self) -> List[Dict[str, Any]]:
        """Retourne l'historique des feedbacks."""
        return list(self._completed_feedbacks)

    # --- Persistance ---

    def _load(self):
        """Charge l'état depuis le JSON."""
        try:
            with open(FEEDBACK_STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._pending_observations = data.get("pending_observations", [])
            self._completed_feedbacks = data.get("completed_feedbacks", [])
        except (FileNotFoundError, json.JSONDecodeError):
            pass

    def _save(self):
        """Persiste l'état dans le JSON."""
        os.makedirs(os.path.dirname(FEEDBACK_STATE_FILE), exist_ok=True)
        data = {
            "pending_observations": self._pending_observations,
            "completed_feedbacks": self._completed_feedbacks[-50:],  # garder 50 max
        }
        tmp_path = FEEDBACK_STATE_FILE + ".tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(tmp_path, FEEDBACK_STATE_FILE)
        except Exception as e:
            logger.warning(f"FEEDBACK: Erreur sauvegarde: {e}")


# Singleton global
feedback_loop = EvolutionFeedbackLoop()
