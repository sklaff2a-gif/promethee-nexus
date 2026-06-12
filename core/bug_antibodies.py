"""
Bug Antibodies — Systeme immunitaire logiciel de Promethee.

Quand un bug est trouve et corrige, un anticorps (regle de detection)
est cree pour scanner le reste du codebase a la recherche du meme pattern.

3 types d'anticorps :
- regex : pattern textuel (ex: += event.get dans un handler bus)
- ast   : structure du code (ex: import non protege par try/except)
- structural : proprietes fichiers (ex: fichier > 500KB sans cap)

Registre persistant dans memory/bug_antibodies.json.
Scanner deterministe (0 LLM) appele par SECURITY_AUDIT et SELF_ANALYSIS.

Singleton. 0 appel LLM.
"""

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List

logger = logging.getLogger("BugAntibodies")

# --- Persistance ---
ANTIBODIES_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "memory", "bug_antibodies.json"
)

# --- Repertoires a scanner ---
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCAN_DIRS = ["core", "Agents"]

# --- Structure d'un anticorps ---
@dataclass
class Antibody:
    id: str                  # ex: "AB-001"
    name: str                # ex: "feedback_accumulation"
    description: str         # Description du bug detecte
    pattern: str             # Regex ou description du pattern
    antibody_type: str       # "regex" | "ast" | "structural"
    severity: str            # "critical" | "high" | "medium" | "low"
    exclude_files: List[str] = field(default_factory=list)  # Fichiers a exclure
    enabled: bool = True
    created_at: float = field(default_factory=time.time)
    detections: int = 0      # Nombre total de detections

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Antibody":
        return cls(
            id=d["id"], name=d["name"], description=d["description"],
            pattern=d["pattern"], antibody_type=d.get("antibody_type", "regex"),
            severity=d.get("severity", "medium"),
            exclude_files=d.get("exclude_files", []),
            enabled=d.get("enabled", True),
            created_at=d.get("created_at", 0),
            detections=d.get("detections", 0),
        )


@dataclass
class Infection:
    """Une infection detectee par un anticorps."""
    antibody_id: str
    antibody_name: str
    severity: str
    file: str
    line: int
    context: str      # Ligne de code incriminee


# --- Anticorps initiaux (bases sur les bugs reels de Promethee) ---
INITIAL_ANTIBODIES = [
    Antibody(
        id="AB-001", name="feedback_accumulation",
        description="Utilisation de += au lieu de max() dans les handlers bus — risque de boucle feedback",
        pattern=r"\+=\s*event\.get\(",
        antibody_type="regex", severity="high",
        exclude_files=["test_"],
    ),
    Antibody(
        id="AB-002", name="unprotected_organ_import",
        description="Import d'organe (from core.X import) au top-level d'un module organe (sans try/except)",
        pattern=r"^from core\.\w+ import \w+",
        antibody_type="regex", severity="low",
        enabled=False,  # Desactive — les imports locaux try/except sont le pattern VOULU
        exclude_files=["test_"],
    ),
    Antibody(
        id="AB-003", name="mutable_default_arg",
        description="Argument par defaut mutable (list/dict) dans une signature de fonction",
        pattern=r"def \w+\([^)]*=\s*(\[\]|\{\})[^)]*\)",
        antibody_type="regex", severity="medium",
        exclude_files=["test_"],
    ),
    Antibody(
        id="AB-004", name="hardcoded_windows_path",
        description="Chemin Windows hardcode (C:\\ ou C:/) dans le code source",
        pattern=r'["\']C:[/\\]',
        antibody_type="regex", severity="low",
        exclude_files=["test_", "config.py", "CLAUDE.md"],
    ),
    Antibody(
        id="AB-005", name="bare_except",
        description="except: sans type d'exception (avale toutes les erreurs silencieusement)",
        pattern=r"^\s*except\s*:\s*$",
        antibody_type="regex", severity="medium",
        exclude_files=["test_"],
    ),
    Antibody(
        id="AB-006", name="hardcoded_model_fragility",
        description="Modele IA hardcode sans fallback — risque de blocage si le modele hallucine ou devient indisponible",
        pattern=r"""model\s*=\s*['"][^'"]*llama3.*vision[^'"]*['"]""",
        antibody_type="regex", severity="critical",
        exclude_files=["test_"],
    ),
]


class BugAntibodyRegistry:
    """Singleton — Registre d'anticorps + scanner."""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    @classmethod
    def reset_singleton(cls):
        cls._instance = None

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self.antibodies: Dict[str, Antibody] = {}
        self._load()
        # Ajouter les anticorps initiaux s'ils n'existent pas
        for ab in INITIAL_ANTIBODIES:
            if ab.id not in self.antibodies:
                self.antibodies[ab.id] = ab

    # ============================================================
    # CRUD anticorps
    # ============================================================

    def add(self, antibody: Antibody) -> str:
        """Ajoute un anticorps au registre."""
        self.antibodies[antibody.id] = antibody
        self.save()
        logger.info(f"ANTIBODY: Nouvel anticorps {antibody.id} ({antibody.name})")
        return antibody.id

    def remove(self, antibody_id: str) -> bool:
        if antibody_id in self.antibodies:
            del self.antibodies[antibody_id]
            self.save()
            return True
        return False

    def disable(self, antibody_id: str) -> bool:
        ab = self.antibodies.get(antibody_id)
        if ab:
            ab.enabled = False
            self.save()
            return True
        return False

    def get_next_id(self) -> str:
        """Genere le prochain ID disponible."""
        existing = [int(ab.id.split("-")[1]) for ab in self.antibodies.values()
                    if ab.id.startswith("AB-") and ab.id.split("-")[1].isdigit()]
        next_num = max(existing, default=0) + 1
        return f"AB-{next_num:03d}"

    # ============================================================
    # Scanner
    # ============================================================

    def scan_file(self, filepath: str) -> List[Infection]:
        """Scanne un fichier avec tous les anticorps actifs."""
        infections = []
        filename = os.path.basename(filepath)

        try:
            with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except Exception:
            return infections

        for ab in self.antibodies.values():
            if not ab.enabled:
                continue
            if ab.antibody_type != "regex":
                continue
            # Verifier les exclusions
            if any(excl in filename for excl in ab.exclude_files):
                continue

            try:
                pattern = re.compile(ab.pattern)
            except re.error:
                continue

            for i, line in enumerate(lines, 1):
                if pattern.search(line):
                    infections.append(Infection(
                        antibody_id=ab.id,
                        antibody_name=ab.name,
                        severity=ab.severity,
                        file=filename,
                        line=i,
                        context=line.strip()[:100],
                    ))
                    ab.detections += 1

        return infections

    def scan_all(self) -> List[Infection]:
        """Scanne tous les fichiers Python de core/ et Agents/."""
        all_infections = []

        for scan_dir in SCAN_DIRS:
            dir_path = os.path.join(PROJECT_ROOT, scan_dir)
            if not os.path.isdir(dir_path):
                continue
            for filename in os.listdir(dir_path):
                if not filename.endswith(".py"):
                    continue
                filepath = os.path.join(dir_path, filename)
                infections = self.scan_file(filepath)
                all_infections.extend(infections)

        if all_infections:
            self.save()  # Persister les compteurs de detections

        return all_infections

    def scan_report(self) -> str:
        """Produit un rapport textuel du scan complet."""
        infections = self.scan_all()

        if not infections:
            return "SCAN ANTICORPS: Aucune infection detectee. Codebase sain."

        # Grouper par severite
        by_severity = {"critical": [], "high": [], "medium": [], "low": []}
        for inf in infections:
            by_severity.get(inf.severity, by_severity["low"]).append(inf)

        lines = [f"SCAN ANTICORPS: {len(infections)} infection(s) detectee(s)"]
        for sev in ("critical", "high", "medium", "low"):
            infs = by_severity[sev]
            if infs:
                lines.append(f"\n  [{sev.upper()}] ({len(infs)})")
                for inf in infs[:5]:  # Max 5 par severite
                    lines.append(f"    {inf.file}:{inf.line} [{inf.antibody_name}] {inf.context[:60]}")
                if len(infs) > 5:
                    lines.append(f"    ... et {len(infs) - 5} autres")

        return "\n".join(lines)

    # ============================================================
    # API
    # ============================================================

    def get_status(self) -> Dict[str, Any]:
        return {
            "total_antibodies": len(self.antibodies),
            "enabled": sum(1 for ab in self.antibodies.values() if ab.enabled),
            "total_detections": sum(ab.detections for ab in self.antibodies.values()),
            "antibodies": [
                {
                    "id": ab.id, "name": ab.name, "severity": ab.severity,
                    "enabled": ab.enabled, "detections": ab.detections,
                    "description": ab.description[:60],
                }
                for ab in self.antibodies.values()
            ],
        }

    def list_antibodies(self) -> str:
        """Liste textuelle des anticorps."""
        lines = [f"=== ANTICORPS ({len(self.antibodies)} enregistres) ==="]
        for ab in sorted(self.antibodies.values(), key=lambda a: a.id):
            status = "ON" if ab.enabled else "OFF"
            lines.append(f"  {ab.id} [{status}] {ab.name} ({ab.severity}) — {ab.detections} detections")
            lines.append(f"         {ab.description[:80]}")
        return "\n".join(lines)

    # ============================================================
    # Persistance
    # ============================================================

    def _load(self):
        try:
            with open(ANTIBODIES_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            for d in data.get("antibodies", []):
                ab = Antibody.from_dict(d)
                self.antibodies[ab.id] = ab
            logger.info(f"ANTIBODIES: {len(self.antibodies)} anticorps charges")
        except (FileNotFoundError, json.JSONDecodeError):
            pass

    def save(self):
        os.makedirs(os.path.dirname(ANTIBODIES_FILE), exist_ok=True)
        data = {
            "antibodies": [ab.to_dict() for ab in self.antibodies.values()],
        }
        tmp = ANTIBODIES_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, ANTIBODIES_FILE)


# Singleton global
antibody_registry = BugAntibodyRegistry()
