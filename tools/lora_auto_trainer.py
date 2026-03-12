"""Pipeline automatise de fine-tuning QLoRA nocturne pour Promethee.

Orchestre le cycle complet :
  1. Collecte des donnees d'entrainement depuis le runtime
  2. Verification du seuil (assez de nouvelles donnees ?)
  3. Training QLoRA en subprocess (isolation GPU/RAM)
  4. Deploiement dans Ollama (GGUF + quantization Q4_K_M)
  5. Nettoyage des fichiers temporaires (~24 GB GGUF)
  6. Mise a jour de l'etat persistant

Usage standalone :
  python tools/lora_auto_trainer.py                     # Agent suivant dans la rotation
  python tools/lora_auto_trainer.py --agent strategist   # Agent specifique
  python tools/lora_auto_trainer.py --force              # Ignorer le seuil de donnees
  python tools/lora_auto_trainer.py --status             # Afficher l'etat

Usage programmatique (depuis autonomy_engine) :
  from tools.lora_auto_trainer import auto_trainer
  result = await auto_trainer.run_training_cycle()
"""

import asyncio
import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("LoRAAutoTrainer")

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# --- Chemins ---

TRAINING_STATE_FILE = os.path.join(PROJECT_ROOT, "memory", "lora_training_state.json")
DATASETS_DIR = os.path.join(PROJECT_ROOT, "datasets")
ADAPTERS_DIR = os.path.join(PROJECT_ROOT, "lora_adapters")

# Chemin runtime (la ou Promethee genere les training_pairs.jsonl)
RUNTIME_MEMORY_DIR = os.path.join(
    os.getenv("RUNTIME_PROJECT_DIR",
              r"C:\MesProjets\PROMETHEE_V11_restructuration2026"),
    "memory",
)

# --- Constantes ---

# Ordre de rotation des agents pour le training
TRAINING_ROTATION = ["strategist", "coder", "researcher", "writer"]

# Seuils
MIN_DATASET_SIZE = 30           # Minimum d'exemples pour lancer un training
MIN_NEW_DATA_RATIO = 0.20       # 20% de nouvelles donnees minimum
MIN_HOURS_BETWEEN_TRAINING = 12 # Cooldown entre deux trainings du meme agent
MAX_TRAINING_TIMEOUT = 7200     # 2h max pour un training (seconds) — 12B bf16: load ~1min + train ~30min + GGUF export ~20min

# Noms des modeles Ollama
OLLAMA_MODEL_PREFIX = "promethee-"

# Sources safe pour la collecte (eviter chromadb si runtime actif)
SAFE_SOURCES = ["training", "chat", "hippocampus"]


class LoRAAutoTrainer:
    """Orchestrateur de training QLoRA automatise."""

    def __init__(self):
        self._state: Dict[str, Any] = self._load_state()
        self._training_in_progress: bool = False
        self._training_agent: Optional[str] = None
        self._consecutive_timeouts: int = 0
        self._consecutive_failures: int = 0

    # ================================================================
    # ETAT PERSISTANT
    # ================================================================

    def _load_state(self) -> Dict[str, Any]:
        """Charge l'etat de training depuis le fichier JSON."""
        if os.path.exists(TRAINING_STATE_FILE):
            try:
                with open(TRAINING_STATE_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                pass
        return {"agents": {}, "last_rotation_index": -1, "total_trainings": 0}

    def _save_state(self):
        """Persiste l'etat de training."""
        os.makedirs(os.path.dirname(TRAINING_STATE_FILE), exist_ok=True)
        with open(TRAINING_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(self._state, f, indent=2, ensure_ascii=False)

    def _get_agent_state(self, agent_name: str) -> Dict[str, Any]:
        """Retourne l'etat de training d'un agent (cree si absent)."""
        if agent_name not in self._state.get("agents", {}):
            self._state.setdefault("agents", {})[agent_name] = {
                "last_trained": 0,
                "last_dataset_hash": "",
                "last_dataset_size": 0,
                "last_loss": 0.0,
                "training_count": 0,
                "model_name": f"{OLLAMA_MODEL_PREFIX}{agent_name}",
            }
        return self._state["agents"][agent_name]

    # ================================================================
    # ROTATION DES AGENTS
    # ================================================================

    def get_next_agent(self) -> str:
        """Retourne le prochain agent dans la rotation."""
        idx = self._state.get("last_rotation_index", -1) + 1
        if idx >= len(TRAINING_ROTATION):
            idx = 0
        return TRAINING_ROTATION[idx]

    def _advance_rotation(self):
        """Avance l'index de rotation."""
        idx = self._state.get("last_rotation_index", -1) + 1
        if idx >= len(TRAINING_ROTATION):
            idx = 0
        self._state["last_rotation_index"] = idx

    # ================================================================
    # COLLECTE DE DONNEES
    # ================================================================

    def collect_data(self, agent_name: str) -> Tuple[str, int, str]:
        """Collecte les donnees d'entrainement pour un agent.

        Returns:
            (dataset_path, n_examples, dataset_hash)
        """
        # Import du collector
        sys.path.insert(0, PROJECT_ROOT)
        from tools.lora_data_collector import (
            collect_all, save_datasets,
            TRAINING_PAIRS_FILE, CHAT_HISTORY_FILE, HIPPOCAMPUS_FILE,
        )

        # Pointer vers le dossier memory du runtime
        import tools.lora_data_collector as collector
        if os.path.isdir(RUNTIME_MEMORY_DIR):
            collector.TRAINING_PAIRS_FILE = os.path.join(
                RUNTIME_MEMORY_DIR, "training_pairs.jsonl"
            )
            collector.CHAT_HISTORY_FILE = os.path.join(
                RUNTIME_MEMORY_DIR, "chat_history.json"
            )
            collector.HIPPOCAMPUS_FILE = os.path.join(
                RUNTIME_MEMORY_DIR, "hippocampus_state.json"
            )
            logger.info(f"[LORA] Sources runtime: {RUNTIME_MEMORY_DIR}")
        else:
            logger.warning(f"[LORA] Runtime dir introuvable: {RUNTIME_MEMORY_DIR}, "
                           f"utilisation des sources locales")

        # Collecter (sources safe, pas chromadb)
        # Rediriger stdout vers devnull pour eviter les crashs d'encodage Windows
        # (le collector utilise des fleches Unicode dans ses prints)
        old_stdout = sys.stdout
        try:
            sys.stdout = open(os.devnull, "w", encoding="utf-8")
        except Exception:
            pass

        try:
            by_lora = collect_all(
                sources=SAFE_SOURCES,
                agent_filter=agent_name,
            )
            # Sauvegarder le dataset
            save_datasets(by_lora, output_dir=DATASETS_DIR)
        finally:
            try:
                if sys.stdout != old_stdout:
                    sys.stdout.close()
            except Exception:
                pass
            sys.stdout = old_stdout

        # Calculer le hash et la taille
        dataset_path = os.path.join(DATASETS_DIR, f"{agent_name}.jsonl")
        if not os.path.exists(dataset_path):
            return dataset_path, 0, ""

        with open(dataset_path, "r", encoding="utf-8") as f:
            content = f.read()

        n_examples = content.count("\n")
        dataset_hash = hashlib.md5(content.encode()).hexdigest()[:16]

        return dataset_path, n_examples, dataset_hash

    # ================================================================
    # VERIFICATION DU SEUIL
    # ================================================================

    def should_train(self, agent_name: str, n_examples: int,
                     dataset_hash: str, force: bool = False) -> Tuple[bool, str]:
        """Verifie si on doit lancer un training.

        Returns:
            (should_train, reason)
        """
        if force:
            return True, "Training force"

        state = self._get_agent_state(agent_name)

        # Seuil minimum
        if n_examples < MIN_DATASET_SIZE:
            return False, f"Pas assez de donnees ({n_examples}/{MIN_DATASET_SIZE})"

        # Cooldown temporel
        elapsed = time.time() - state.get("last_trained", 0)
        if elapsed < MIN_HOURS_BETWEEN_TRAINING * 3600:
            hours_left = (MIN_HOURS_BETWEEN_TRAINING * 3600 - elapsed) / 3600
            return False, f"Cooldown actif ({hours_left:.1f}h restantes)"

        # Hash identique = memes donnees
        if dataset_hash == state.get("last_dataset_hash", ""):
            return False, "Donnees identiques au dernier training"

        # Ratio de nouvelles donnees
        prev_size = state.get("last_dataset_size", 0)
        if prev_size > 0:
            growth = (n_examples - prev_size) / prev_size
            if growth < MIN_NEW_DATA_RATIO:
                return False, (f"Pas assez de nouvelles donnees "
                               f"({n_examples} vs {prev_size}, +{growth:.0%})")

        return True, f"Pret ({n_examples} exemples, hash={dataset_hash[:8]})"

    # ================================================================
    # TRAINING (SUBPROCESS)
    # ================================================================

    async def _run_training(self, agent_name: str, dataset_path: str) -> Dict[str, Any]:
        """Lance le training QLoRA en subprocess.

        Le training est isole dans un subprocess pour :
        - Isoler la memoire GPU (PyTorch/Unsloth ~ 12 GB VRAM)
        - Proteger Promethee si le training crash
        - Liberer la RAM Python apres le training
        """
        trainer_script = os.path.join(PROJECT_ROOT, "tools", "lora_trainer.py")
        python_exe = sys.executable

        cmd = [
            python_exe, trainer_script,
            "--dataset", dataset_path,
            "--name", agent_name,
            "--epochs", "2",
            "--deploy",
        ]

        env = os.environ.copy()
        env["TORCHDYNAMO_DISABLE"] = "1"  # Windows : Triton non disponible
        env["PYTHONIOENCODING"] = "utf-8"  # Windows : emojis et fleches dans les prints

        logger.info(f"[LORA] Lancement training {agent_name} : {' '.join(cmd)}")

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=env,
                cwd=PROJECT_ROOT,
            )

            stdout, _ = await asyncio.wait_for(
                proc.communicate(),
                timeout=MAX_TRAINING_TIMEOUT,
            )

            output = stdout.decode("utf-8", errors="replace") if stdout else ""

            if proc.returncode == 0:
                # Extraire la loss finale depuis l'output
                loss = self._extract_loss(output)
                logger.info(f"[LORA] Training {agent_name} termine (loss={loss:.4f})")
                return {
                    "success": True,
                    "loss": loss,
                    "output": output[-2000:],  # Garder les derniers 2000 chars
                }
            else:
                error_type = "CUDA OOM" if proc.returncode == 2 else f"Exit code {proc.returncode}"
                logger.error(f"[LORA] Training {agent_name} echoue ({error_type})")
                return {
                    "success": False,
                    "error": error_type,
                    "output": output[-2000:],
                }

        except asyncio.TimeoutError:
            logger.error(
                f"[LORA] Training {agent_name} timeout ({MAX_TRAINING_TIMEOUT}s)"
            )
            # Tenter de recuperer l'output partiel avant kill
            try:
                proc.kill()
                partial_out, _ = await asyncio.wait_for(
                    proc.communicate(), timeout=10
                )
                partial = partial_out.decode("utf-8", errors="replace") if partial_out else ""
                if partial:
                    # Afficher les dernieres lignes pour diagnostic
                    tail = "\n".join(partial.strip().splitlines()[-10:])
                    logger.error(f"[LORA] Output partiel {agent_name}:\n{tail}")
            except Exception:
                pass
            return {"success": False, "error": "Timeout"}
        except Exception as e:
            logger.error(f"[LORA] Erreur lancement training {agent_name}: {e}")
            return {"success": False, "error": str(e)}

    @staticmethod
    def _extract_loss(output: str) -> float:
        """Extrait la loss finale depuis la sortie du training."""
        import re
        # Chercher "Loss finale: X.XXXX"
        match = re.search(r"Loss finale:\s*([\d.]+)", output)
        if match:
            return float(match.group(1))
        # Fallback: chercher la derniere loss dans les logs
        losses = re.findall(r"'loss':\s*([\d.]+)", output)
        if losses:
            return float(losses[-1])
        return 0.0

    # ================================================================
    # DEPLOIEMENT OLLAMA
    # ================================================================

    async def _deploy_to_ollama(self, agent_name: str) -> Dict[str, Any]:
        """Deploie le modele fine-tune dans Ollama.

        Etapes :
        1. Verifier que le GGUF existe (genere par Unsloth)
        2. Creer le Modelfile avec FROM ./gguf
        3. ollama create --quantize q4_K_M
        4. Nettoyer le GGUF (~24 GB)
        """
        adapter_dir = os.path.join(ADAPTERS_DIR, agent_name)
        gguf_dir = os.path.join(adapter_dir, "gguf")
        modelfile_path = os.path.join(adapter_dir, "Modelfile")
        model_name = f"{OLLAMA_MODEL_PREFIX}{agent_name}"

        # Verifier le GGUF
        if not os.path.isdir(gguf_dir):
            return {"success": False, "error": "Dossier GGUF absent"}

        # Creer le Modelfile
        with open(modelfile_path, "w", encoding="utf-8") as f:
            f.write("FROM ./gguf\n")
            f.write("PARAMETER temperature 0.7\n")
            f.write("PARAMETER top_p 0.9\n")
            f.write("PARAMETER num_ctx 4096\n")

        # Deployer via ollama create avec quantization
        cmd = ["ollama", "create", model_name, "--quantize", "q4_K_M",
               "-f", modelfile_path]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=adapter_dir,
            )
            stdout, _ = await asyncio.wait_for(
                proc.communicate(),
                timeout=1800,  # 30 min max pour merge+quantize 24 GB
            )
            output = stdout.decode("utf-8", errors="replace") if stdout else ""

            if proc.returncode == 0:
                logger.info(f"[LORA] Deploiement {model_name} reussi")
                # Nettoyer le GGUF (~24 GB)
                await self._cleanup_gguf(agent_name)
                return {"success": True, "model": model_name, "output": output}
            else:
                logger.error(f"[LORA] Deploiement {model_name} echoue: {output[:500]}")
                return {"success": False, "error": output[:500]}

        except asyncio.TimeoutError:
            logger.error(f"[LORA] Deploiement {model_name} timeout")
            return {"success": False, "error": "Timeout deploiement"}
        except Exception as e:
            logger.error(f"[LORA] Erreur deploiement {model_name}: {e}")
            return {"success": False, "error": str(e)}

    async def _cleanup_gguf(self, agent_name: str):
        """Supprime le dossier GGUF apres deploiement (~24 GB recuperes)."""
        gguf_dir = os.path.join(ADAPTERS_DIR, agent_name, "gguf")
        if os.path.isdir(gguf_dir):
            try:
                size_gb = sum(
                    os.path.getsize(os.path.join(dp, f))
                    for dp, _, fns in os.walk(gguf_dir) for f in fns
                ) / 1e9
                shutil.rmtree(gguf_dir)
                logger.info(f"[LORA] GGUF nettoye pour {agent_name} ({size_gb:.1f} GB liberes)")
            except Exception as e:
                logger.warning(f"[LORA] Echec nettoyage GGUF {agent_name}: {e}")

    # ================================================================
    # LIBERATION GPU (PREREQUIS TRAINING)
    # ================================================================

    async def _unload_ollama_models(self) -> bool:
        """Decharge tous les modeles Ollama pour liberer la VRAM."""
        try:
            import httpx
            async with httpx.AsyncClient() as client:
                resp = await client.get("http://localhost:11434/api/ps", timeout=10)
                if resp.status_code != 200:
                    return False
                models = resp.json().get("models", [])
                if not models:
                    logger.info("[LORA] Aucun modele Ollama charge")
                    return True
                for m in models:
                    name = m.get("name", "")
                    if name:
                        await client.post(
                            "http://localhost:11434/api/generate",
                            json={"model": name, "keep_alive": 0},
                            timeout=30,
                        )
                        logger.info(f"[LORA] Modele decharge: {name}")
                return True
        except Exception as e:
            logger.warning(f"[LORA] Echec dechargement Ollama: {e}")
            return False

    async def _wait_for_vram(self, min_free_gb: float = 10.0,
                              timeout_s: int = 60) -> bool:
        """Attend que la VRAM soit suffisamment libre pour le training.

        Poll nvidia-smi toutes les 2 secondes pendant max timeout_s.
        Retourne True si la VRAM est libre, False si timeout.
        """
        import subprocess as sp
        for attempt in range(timeout_s // 2):
            try:
                result = sp.run(
                    ["nvidia-smi", "--query-gpu=memory.used,memory.total",
                     "--format=csv,noheader,nounits"],
                    capture_output=True, text=True, timeout=5,
                )
                if result.returncode == 0:
                    parts = result.stdout.strip().split(",")
                    used_mb = int(parts[0].strip())
                    total_mb = int(parts[1].strip())
                    free_gb = (total_mb - used_mb) / 1024
                    logger.info(
                        f"[LORA] VRAM: {used_mb} MB utilises / {total_mb} MB "
                        f"total ({free_gb:.1f} GB libres) "
                        f"[tentative {attempt + 1}]"
                    )
                    if free_gb >= min_free_gb:
                        return True
            except Exception as e:
                logger.warning(f"[LORA] nvidia-smi erreur: {e}")
                return True  # Pas de nvidia-smi = on tente quand meme
            await asyncio.sleep(2)

        logger.warning(
            f"[LORA] VRAM insuffisante apres {timeout_s}s d'attente "
            f"(besoin de {min_free_gb:.0f} GB libres)"
        )
        return False

    # ================================================================
    # PIPELINE COMPLET
    # ================================================================

    async def run_training_cycle(self, agent_name: Optional[str] = None,
                                  force: bool = False) -> Dict[str, Any]:
        """Execute un cycle complet de training QLoRA.

        Args:
            agent_name: Agent a entrainer (None = prochain dans la rotation)
            force: Ignorer les seuils de donnees

        Returns:
            Dict avec le resultat du cycle
        """
        # Verrou : un seul training a la fois
        if self._training_in_progress:
            logger.warning(
                f"[LORA] Training deja en cours ({self._training_agent}), skip"
            )
            return {
                "agent": self._training_agent or "?",
                "phase": "locked",
                "success": False,
                "skipped": True,
                "threshold_reason": f"Training {self._training_agent} deja en cours",
            }

        # Anti-boucle : apres 3 echecs consecutifs (timeout OU exit code), cooldown 4h
        if self._consecutive_failures >= 3 and not force:
            agent = agent_name or self.get_next_agent()
            logger.info(
                f"[LORA] Anti-boucle: {self._consecutive_failures} echecs "
                f"consecutifs (dont {self._consecutive_timeouts} timeouts). "
                f"Cooldown 4h."
            )
            return {
                "agent": agent,
                "phase": "anti_loop",
                "success": False,
                "skipped": True,
                "threshold_reason": (
                    f"Anti-boucle: {self._consecutive_failures} echecs consecutifs. "
                    f"Cooldown jusqu'au prochain reset."
                ),
            }

        start_time = time.time()

        # 1. Determiner l'agent
        if agent_name is None:
            agent_name = self.get_next_agent()
        logger.info(f"[LORA] Cycle training demarre pour '{agent_name}'")

        result = {
            "agent": agent_name,
            "phase": "init",
            "success": False,
        }

        # 2. Collecter les donnees
        result["phase"] = "collect"
        try:
            dataset_path, n_examples, dataset_hash = self.collect_data(agent_name)
        except Exception as e:
            result["error"] = f"Collecte echouee: {e}"
            logger.error(f"[LORA] {result['error']}")
            return result

        result["n_examples"] = n_examples
        result["dataset_hash"] = dataset_hash

        # 3. Verifier le seuil
        result["phase"] = "threshold"
        should, reason = self.should_train(agent_name, n_examples, dataset_hash, force)
        result["threshold_reason"] = reason

        if not should:
            logger.info(f"[LORA] Skip {agent_name}: {reason}")
            result["skipped"] = True
            # Avancer la rotation meme si skip
            self._advance_rotation()
            self._save_state()
            return result

        # 4. Liberer la GPU et attendre que la VRAM soit libre
        result["phase"] = "unload_gpu"
        await self._unload_ollama_models()
        vram_ok = await self._wait_for_vram(min_free_gb=10.0, timeout_s=60)
        if not vram_ok:
            result["error"] = "VRAM insuffisante apres unload Ollama"
            logger.error(f"[LORA] {result['error']}")
            self._consecutive_failures += 1
            self._advance_rotation()
            self._save_state()
            return result

        # 5. Training (avec verrou concurrence)
        self._training_in_progress = True
        self._training_agent = agent_name
        try:
            result["phase"] = "training"
            train_result = await self._run_training(agent_name, dataset_path)

            if not train_result.get("success"):
                result["error"] = train_result.get("error", "Training echoue")
                result["training_output"] = train_result.get("output", "")
                logger.error(f"[LORA] Training {agent_name} echoue: {result['error']}")
                # Logger l'output du subprocess pour diagnostic
                output_tail = train_result.get("output", "")
                if output_tail:
                    tail_lines = output_tail.strip().splitlines()[-15:]
                    logger.error(
                        f"[LORA] Output {agent_name} (dernières lignes):\n"
                        + "\n".join(tail_lines)
                    )
                # Compteur echecs consecutifs (timeout OU exit code)
                self._consecutive_failures += 1
                if train_result.get("error") == "Timeout":
                    self._consecutive_timeouts += 1
                logger.warning(
                    f"[LORA] Echecs consecutifs: {self._consecutive_failures} "
                    f"(dont {self._consecutive_timeouts} timeouts)"
                )
                self._advance_rotation()
                self._save_state()
                return result

            # Reset compteurs sur succes
            self._consecutive_timeouts = 0
            self._consecutive_failures = 0
            result["loss"] = train_result.get("loss", 0.0)

            # 6. Deploiement
            result["phase"] = "deploy"
            deploy_result = await self._deploy_to_ollama(agent_name)

            if not deploy_result.get("success"):
                result["error"] = deploy_result.get("error", "Deploiement echoue")
                logger.error(f"[LORA] Deploy {agent_name} echoue: {result['error']}")
                # Quand meme mettre a jour l'etat (training reussi, deploy echoue)
                state = self._get_agent_state(agent_name)
                state["last_trained"] = time.time()
                state["last_dataset_hash"] = dataset_hash
                state["last_dataset_size"] = n_examples
                state["last_loss"] = result["loss"]
                state["last_deploy_error"] = result["error"]
                self._advance_rotation()
                self._save_state()
                return result

            # 7. Succes complet — mise a jour de l'etat
            result["phase"] = "done"
            result["success"] = True
            result["model_name"] = deploy_result.get("model", "")
            result["duration_s"] = int(time.time() - start_time)

            state = self._get_agent_state(agent_name)
            state["last_trained"] = time.time()
            state["last_dataset_hash"] = dataset_hash
            state["last_dataset_size"] = n_examples
            state["last_loss"] = result["loss"]
            state["training_count"] = state.get("training_count", 0) + 1
            state.pop("last_deploy_error", None)

            self._state["total_trainings"] = self._state.get("total_trainings", 0) + 1
            self._advance_rotation()
            self._save_state()

            logger.info(
                f"[LORA] Cycle complet {agent_name}: "
                f"{n_examples} exemples, loss={result['loss']:.4f}, "
                f"duree={result['duration_s']}s, modele={result['model_name']}"
            )

            return result
        finally:
            self._training_in_progress = False
            self._training_agent = None

    # ================================================================
    # RAPPORT D'ETAT
    # ================================================================

    def get_status(self) -> Dict[str, Any]:
        """Retourne l'etat complet du systeme de training."""
        status = {
            "total_trainings": self._state.get("total_trainings", 0),
            "next_agent": self.get_next_agent(),
            "agents": {},
        }

        for agent_name in TRAINING_ROTATION:
            state = self._get_agent_state(agent_name)
            last = state.get("last_trained", 0)
            status["agents"][agent_name] = {
                "last_trained": time.strftime("%Y-%m-%d %H:%M",
                                              time.localtime(last)) if last else "jamais",
                "dataset_size": state.get("last_dataset_size", 0),
                "last_loss": state.get("last_loss", 0.0),
                "training_count": state.get("training_count", 0),
                "model": state.get("model_name", ""),
                "deploy_error": state.get("last_deploy_error"),
            }

        return status

    def print_status(self):
        """Affiche l'etat du systeme de training."""
        status = self.get_status()
        print("=" * 55)
        print("  LORA AUTO-TRAINER — Etat du systeme")
        print("=" * 55)
        print(f"  Total trainings : {status['total_trainings']}")
        print(f"  Prochain agent  : {status['next_agent']}")
        print()

        for name, info in status["agents"].items():
            marker = " <<" if name == status["next_agent"] else ""
            print(f"  [{name.upper()}]{marker}")
            print(f"    Dernier training : {info['last_trained']}")
            print(f"    Dataset          : {info['dataset_size']} exemples")
            print(f"    Loss             : {info['last_loss']:.4f}" if info["last_loss"] else "    Loss             : -")
            print(f"    Trainings        : {info['training_count']}")
            print(f"    Modele           : {info['model']}")
            if info.get("deploy_error"):
                print(f"    ERREUR deploy    : {info['deploy_error']}")
            print()


# --- Singleton ---
auto_trainer = LoRAAutoTrainer()


# --- CLI ---

def main():
    import argparse
    parser = argparse.ArgumentParser(description="LoRA Auto-Trainer pour Promethee")
    parser.add_argument("--agent", choices=TRAINING_ROTATION,
                        help="Agent a entrainer (defaut: prochain dans la rotation)")
    parser.add_argument("--force", action="store_true",
                        help="Ignorer les seuils de donnees")
    parser.add_argument("--status", action="store_true",
                        help="Afficher l'etat du systeme")
    parser.add_argument("--collect-only", action="store_true",
                        help="Collecter les donnees sans entrainer")
    args = parser.parse_args()

    if args.status:
        auto_trainer.print_status()
        return

    if args.collect_only:
        agent = args.agent or auto_trainer.get_next_agent()
        print(f"Collecte des donnees pour '{agent}'...")
        path, n, h = auto_trainer.collect_data(agent)
        print(f"  Dataset: {path}")
        print(f"  Exemples: {n}")
        print(f"  Hash: {h}")
        should, reason = auto_trainer.should_train(agent, n, h, args.force)
        print(f"  Training {'OUI' if should else 'NON'}: {reason}")
        return

    # Training complet
    result = asyncio.run(
        auto_trainer.run_training_cycle(agent_name=args.agent, force=args.force)
    )

    if result.get("success"):
        print(f"\nTraining {result['agent']} REUSSI")
        print(f"  Exemples : {result.get('n_examples', '?')}")
        print(f"  Loss     : {result.get('loss', '?')}")
        print(f"  Duree    : {result.get('duration_s', '?')}s")
        print(f"  Modele   : {result.get('model_name', '?')}")
    elif result.get("skipped"):
        print(f"\nTraining {result['agent']} SKIP: {result.get('threshold_reason', '?')}")
    else:
        print(f"\nTraining {result['agent']} ECHOUE")
        print(f"  Phase  : {result.get('phase', '?')}")
        print(f"  Erreur : {result.get('error', '?')}")


if __name__ == "__main__":
    main()
