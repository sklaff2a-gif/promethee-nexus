"""
ProfessorAgent — Evaluateur pedagogique de Promethee.

Evalue les livrables scolaires avec un bareme hybride :
- 80% deterministe (longueur, structure, validite, pertinence, nouveaute)
- 20% LLM Cloud (critique qualitative, feedback, challenge)
Ajuste la difficulte adaptativement et nourrit le DesireEngine.
"""
import ast
import json
import logging
import os
import time
from typing import Dict, Any, List

logger = logging.getLogger("ProfessorAgent")

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_GRADES_FILE_DEFAULT = os.path.join(_PROJECT_ROOT, "memory", "school", "grades.json")
_CURRICULUM_FILE_DEFAULT = os.path.join(_PROJECT_ROOT, "memory", "school", "curriculum.json")

# Seuils de difficulte adaptative
DIFFICULTY_MIN = 0.5
DIFFICULTY_MAX = 3.0
DIFFICULTY_DEFAULT = 1.0
MASTERY_UP_THRESHOLD = 8.0     # 3 notes >= 8.0 -> difficulte++
MASTERY_DOWN_THRESHOLD = 4.0   # 2 notes <= 4.0 -> difficulte--
MASTERY_UP_STREAK = 3
MASTERY_DOWN_STREAK = 2

# Mots-cles structurels pour scoring
_MARKDOWN_MARKERS = {"#", "##", "###", "-", "*", "1.", "2.", "3.", "```"}


try:
    from core.base_agent import BaseAgent
except ImportError:
    BaseAgent = object


class ProfessorAgent(BaseAgent):
    """Professeur de Promethee — evalue les livrables avec un bareme hybride."""

    def __init__(self):
        super().__init__(
            name="professor",
            role="Evaluateur Pedagogique",
            description="Evalue les livrables scolaires, donne des notes, feedback et defis."
        )
        self.system_instructions = (
            "Tu es un professeur exigeant mais bienveillant. "
            "Tu evalues le travail de Promethee avec rigueur et constructivite."
        )
        self._grades: List[Dict] = []
        self._curriculum: Dict[str, Dict] = {}
        self._grades_file = _GRADES_FILE_DEFAULT
        self._curriculum_file = _CURRICULUM_FILE_DEFAULT
        self._load_grades()
        self._load_curriculum()

    async def process_task(self, task_payload: Dict[str, Any]) -> Dict[str, Any]:
        """Point d'entree standard BaseAgent."""
        deliverable = task_payload.get("mission", "") or task_payload.get("deliverable", "")
        course_type = task_payload.get("course_type", "CODE_REVIEW")
        subject = task_payload.get("subject", "")

        if not deliverable or not deliverable.strip():
            return {"status": "error", "result": "Aucun livrable a evaluer.", "agent": self.name}

        result = await self.evaluate(deliverable, course_type, subject)
        return {"status": "success", "result": result, "agent": self.name}

    async def evaluate(self, deliverable: str, course_type: str,
                       subject: str = "") -> Dict[str, Any]:
        """Evaluation hybride d'un livrable.

        Returns:
            {"grade": float 0-10, "feedback": str, "challenge": str,
             "breakdown": dict, "difficulty_adjusted": bool}
        """
        # 1. Score deterministe (80% = note sur 8)
        det = self._score_deterministic(deliverable, course_type, subject)
        det_score = det["score"]

        # 2. Score qualitatif LLM (20% = note sur 2)
        try:
            qual = await self._score_qualitative(deliverable, course_type, subject)
        except Exception as e:
            logger.warning(f"[PROFESSOR] Evaluation qualitative echouee: {e}")
            qual = {"score": 1.0, "feedback": "Evaluation qualitative indisponible.", "challenge": ""}

        qual_score = qual["score"]

        # Note finale
        grade = round(det_score + qual_score, 1)
        grade = max(0.0, min(10.0, grade))

        # Feedback combine
        feedback_parts = det.get("comments", [])
        if qual.get("feedback"):
            feedback_parts.append(qual["feedback"])
        feedback = " | ".join(feedback_parts) if feedback_parts else "Travail evalue."

        challenge = qual.get("challenge", "")

        # 3. Ajuster la difficulte
        adjusted = self._adjust_difficulty(course_type, grade)

        # 4. Nourrir le DesireEngine
        self._feed_desire_engine(course_type, grade)

        # 5. Sauvegarder la note
        grade_entry = {
            "timestamp": time.time(),
            "date": __import__("datetime").date.today().isoformat(),
            "course_type": course_type,
            "subject": subject,
            "grade": grade,
            "feedback": feedback[:300],
            "challenge": challenge[:200],
        }
        self._grades.append(grade_entry)
        if len(self._grades) > 500:
            self._grades = self._grades[-500:]
        self._save_grades()
        self._save_curriculum()

        return {
            "grade": grade,
            "feedback": feedback,
            "challenge": challenge,
            "breakdown": {
                "deterministic": round(det_score, 2),
                "qualitative": round(qual_score, 2),
                "details": det.get("breakdown", {}),
            },
            "difficulty_adjusted": adjusted,
        }

    # ── Scoring deterministe (80%) ──────────────────────────────────────

    def _score_deterministic(self, deliverable: str, course_type: str,
                             subject: str = "") -> dict:
        """Bareme deterministe — retourne score sur 8.0 + breakdown + comments."""
        comments = []
        breakdown = {}
        text = deliverable.strip()

        if not text:
            return {"score": 0.0, "breakdown": {}, "comments": ["Livrable vide."]}

        # 1. Longueur (0-2 pts)
        char_count = len(text)
        if char_count < 50:
            length_score = 0.0
            comments.append("Trop court (< 50 caracteres).")
        elif char_count < 150:
            length_score = 0.5
            comments.append("Court — developpe davantage.")
        elif char_count < 300:
            length_score = 1.0
        elif char_count < 600:
            length_score = 1.5
        else:
            length_score = 2.0
        breakdown["length"] = length_score

        # 2. Structure (0-2 pts)
        lines = text.split("\n")
        has_sections = any(line.strip().startswith(m) for line in lines for m in _MARKDOWN_MARKERS)
        has_paragraphs = text.count("\n\n") >= 1
        has_lists = any(line.strip().startswith(("-", "*", "1.", "2.")) for line in lines)
        structure_score = 0.0
        if has_sections:
            structure_score += 0.8
        if has_paragraphs:
            structure_score += 0.6
        if has_lists:
            structure_score += 0.6
        structure_score = min(2.0, structure_score)
        if structure_score < 0.5:
            comments.append("Manque de structure (pas de sections, listes ou paragraphes).")
        breakdown["structure"] = structure_score

        # 3. Validite technique (0-2 pts) — depend du type de cours
        tech_score = self._score_technical_validity(text, course_type)
        breakdown["technical"] = tech_score

        # 4. Pertinence sujet (0-1 pt)
        relevance_score = 0.0
        if subject:
            subject_words = set(subject.lower().split())
            text_words = set(text.lower().split())
            overlap = len(subject_words & text_words)
            if overlap >= 2:
                relevance_score = 1.0
            elif overlap >= 1:
                relevance_score = 0.5
            else:
                comments.append(f"Hors sujet — le sujet etait: {subject[:50]}")
        else:
            relevance_score = 0.5  # Pas de sujet impose = bonus partiel
        breakdown["relevance"] = relevance_score

        # 5. Nouveaute (0-1 pt) — pas de repetition avec livrables precedents
        novelty_score = self._score_novelty(text, course_type)
        if novelty_score < 0.3:
            comments.append("Trop similaire a un livrable precedent.")
        breakdown["novelty"] = novelty_score

        total = length_score + structure_score + tech_score + relevance_score + novelty_score
        total = min(8.0, total)

        return {"score": round(total, 2), "breakdown": breakdown, "comments": comments}

    def _score_technical_validity(self, text: str, course_type: str) -> float:
        """Note la validite technique selon le type de cours (0-2 pts)."""
        if course_type == "WORKSHOP":
            # Code doit etre valide AST
            code_blocks = self._extract_code_blocks(text)
            if not code_blocks:
                # Peut-etre que tout le texte est du code
                code_blocks = [text]
            for code in code_blocks:
                try:
                    tree = ast.parse(code)
                    has_def = any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) for n in ast.walk(tree))
                    has_class = any(isinstance(n, ast.ClassDef) for n in ast.walk(tree))
                    if has_def or has_class:
                        return 2.0
                    return 1.0  # AST valide mais pas de def/class
                except SyntaxError:
                    continue
            return 0.5  # Aucun code valide AST

        elif course_type == "CODE_REVIEW":
            # Doit mentionner des noms de fonctions/classes
            has_code_refs = any(kw in text.lower() for kw in ["def ", "class ", "self.", "return ", "import "])
            has_line_refs = any(kw in text.lower() for kw in ["ligne", "line", "l.", "fonction", "methode"])
            score = 0.0
            if has_code_refs:
                score += 1.0
            if has_line_refs:
                score += 1.0
            return min(2.0, score)

        elif course_type == "RESEARCH":
            # Doit avoir des sections et du contenu technique
            has_tech_words = sum(1 for w in text.lower().split()
                                if w in {"async", "python", "agent", "pattern", "algorithme",
                                         "architecture", "memoire", "cache", "api", "bus",
                                         "scoring", "modele", "reseau", "donnees", "pipeline"})
            if has_tech_words >= 5:
                return 2.0
            elif has_tech_words >= 2:
                return 1.0
            return 0.5

        elif course_type == "CREATION":
            # Originalite et effort creatif
            words = text.split()
            unique_ratio = len(set(w.lower() for w in words)) / max(len(words), 1)
            if unique_ratio > 0.6 and len(words) >= 30:
                return 2.0
            elif unique_ratio > 0.4:
                return 1.0
            return 0.5

        elif course_type == "BULLETIN":
            # Completude du bulletin
            expected = ["accompli", "interest", "frustre", "demain", "note"]
            found = sum(1 for kw in expected if kw in text.lower())
            return min(2.0, found * 0.5)

        return 1.0  # Default

    def _extract_code_blocks(self, text: str) -> List[str]:
        """Extrait les blocs de code markdown."""
        blocks = []
        in_block = False
        current = []
        for line in text.split("\n"):
            if line.strip().startswith("```"):
                if in_block:
                    blocks.append("\n".join(current))
                    current = []
                    in_block = False
                else:
                    in_block = True
            elif in_block:
                current.append(line)
        return blocks

    def _score_novelty(self, text: str, course_type: str) -> float:
        """Compare avec les 10 derniers livrables du meme type (0-1 pt)."""
        recent = [g for g in self._grades[-20:] if g.get("course_type") == course_type]
        if not recent:
            return 1.0  # Premier livrable = maximalement nouveau

        # Comparaison bigram simplifiee
        words = set(text.lower().split())
        max_overlap = 0.0
        for entry in recent:
            prev_preview = entry.get("feedback", "") + entry.get("subject", "")
            prev_words = set(prev_preview.lower().split())
            if not prev_words or not words:
                continue
            overlap = len(words & prev_words) / max(len(words), len(prev_words))
            max_overlap = max(max_overlap, overlap)

        if max_overlap > 0.7:
            return 0.0
        elif max_overlap > 0.5:
            return 0.5
        return 1.0

    # ── Scoring qualitatif LLM (20%) ────────────────────────────────────

    async def _score_qualitative(self, deliverable: str, course_type: str,
                                  subject: str) -> dict:
        """Critique qualitative via LLM Cloud — note sur 2.0."""
        difficulty = self._curriculum.get(course_type, {}).get("difficulty", DIFFICULTY_DEFAULT)

        prompt = (
            f"Tu es un professeur exigeant qui evalue un travail d'IA autonome.\n"
            f"Type de cours: {course_type}\n"
            f"Sujet: {subject or 'libre'}\n"
            f"Niveau de difficulte: {difficulty:.1f}/3.0\n\n"
            f"LIVRABLE A EVALUER:\n{deliverable[:1500]}\n\n"
            f"Donne:\n"
            f"1. Une NOTE sur 2 (0.0 = nul, 1.0 = correct, 2.0 = excellent)\n"
            f"2. Un FEEDBACK constructif en 1-2 phrases\n"
            f"3. Un DEFI pour le prochain cours (plus difficile si la note est haute)\n\n"
            f"Format EXACT de ta reponse:\n"
            f"NOTE: X.X\n"
            f"FEEDBACK: ...\n"
            f"DEFI: ..."
        )

        response = await self.generate_content(prompt)
        return self._parse_qualitative_response(response)

    def _parse_qualitative_response(self, response: str) -> dict:
        """Parse la reponse LLM du professeur."""
        result = {"score": 1.0, "feedback": "", "challenge": ""}

        if not response:
            return result

        for line in response.split("\n"):
            line = line.strip()
            if line.upper().startswith("NOTE:"):
                try:
                    score_str = line.split(":", 1)[1].strip()
                    # Extraire le premier nombre
                    import re
                    match = re.search(r"(\d+\.?\d*)", score_str)
                    if match:
                        score = float(match.group(1))
                        result["score"] = max(0.0, min(2.0, score))
                except (ValueError, IndexError):
                    pass
            elif line.upper().startswith("FEEDBACK:"):
                result["feedback"] = line.split(":", 1)[1].strip()
            elif line.upper().startswith("DEFI:"):
                result["challenge"] = line.split(":", 1)[1].strip()

        return result

    # ── Difficulte adaptative ───────────────────────────────────────────

    def _adjust_difficulty(self, course_type: str, grade: float) -> bool:
        """Ajuste la difficulte. Retourne True si ajustee."""
        if course_type not in self._curriculum:
            self._curriculum[course_type] = {
                "difficulty": DIFFICULTY_DEFAULT,
                "mastery": 5.0,
                "sessions": 0,
                "last_grades": [],
            }

        cur = self._curriculum[course_type]
        cur["sessions"] += 1
        cur["last_grades"].append(grade)
        if len(cur["last_grades"]) > 10:
            cur["last_grades"] = cur["last_grades"][-10:]

        # Moyenne glissante
        cur["mastery"] = sum(cur["last_grades"]) / len(cur["last_grades"])

        adjusted = False
        recent = cur["last_grades"]

        # Monter la difficulte
        if len(recent) >= MASTERY_UP_STREAK:
            if all(g >= MASTERY_UP_THRESHOLD for g in recent[-MASTERY_UP_STREAK:]):
                old = cur["difficulty"]
                cur["difficulty"] = min(DIFFICULTY_MAX, cur["difficulty"] + 0.3)
                if cur["difficulty"] != old:
                    adjusted = True
                    logger.info(f"[PROFESSOR] Difficulte {course_type} augmentee: {old:.1f} -> {cur['difficulty']:.1f}")

        # Baisser la difficulte
        if not adjusted and len(recent) >= MASTERY_DOWN_STREAK:
            if all(g <= MASTERY_DOWN_THRESHOLD for g in recent[-MASTERY_DOWN_STREAK:]):
                old = cur["difficulty"]
                cur["difficulty"] = max(DIFFICULTY_MIN, cur["difficulty"] - 0.2)
                if cur["difficulty"] != old:
                    adjusted = True
                    logger.info(f"[PROFESSOR] Difficulte {course_type} baissee: {old:.1f} -> {cur['difficulty']:.1f}")

        return adjusted

    def _feed_desire_engine(self, course_type: str, grade: float):
        """Nourrit le DesireEngine selon la note."""
        try:
            from core.event_bus.bus import publish_from_sync
            event_type = "SCHOOL_GRADE_HIGH" if grade >= 7.0 else "SCHOOL_GRADE_LOW"
            event = {
                "course_type": course_type,
                "grade": grade,
                "event_type": event_type,
            }
            publish_from_sync("SCHOOL_GRADE_RECEIVED", event, label="professor.feed_desire")
        except Exception as e:
            logger.debug(f"[PROFESSOR] Bus publish failed: {e}")

    def get_mastery_summary(self) -> dict:
        """Resume des maitrise par matiere."""
        summary = {}
        for course_type, data in self._curriculum.items():
            summary[course_type] = {
                "mastery": round(data.get("mastery", 5.0), 1),
                "difficulty": round(data.get("difficulty", 1.0), 1),
                "sessions": data.get("sessions", 0),
            }
        return summary

    def get_difficulty(self, course_type: str) -> float:
        """Retourne la difficulte actuelle pour un type de cours."""
        return self._curriculum.get(course_type, {}).get("difficulty", DIFFICULTY_DEFAULT)

    # ── Persistance ─────────────────────────────────────────────────────

    def _load_grades(self):
        try:
            with open(self._grades_file, "r", encoding="utf-8") as f:
                self._grades = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            self._grades = []

    def _save_grades(self):
        try:
            os.makedirs(os.path.dirname(self._grades_file), exist_ok=True)
            with open(self._grades_file, "w", encoding="utf-8") as f:
                json.dump(self._grades, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"[PROFESSOR] Erreur sauvegarde grades: {e}")

    def _load_curriculum(self):
        try:
            with open(self._curriculum_file, "r", encoding="utf-8") as f:
                self._curriculum = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            self._curriculum = {}

    def _save_curriculum(self):
        try:
            os.makedirs(os.path.dirname(self._curriculum_file), exist_ok=True)
            with open(self._curriculum_file, "w", encoding="utf-8") as f:
                json.dump(self._curriculum, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"[PROFESSOR] Erreur sauvegarde curriculum: {e}")
