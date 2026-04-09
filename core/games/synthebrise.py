"""Synthebrise — Le premier jeu invente par Promethee.

Deux joueurs construisent un pont de mots entre deux iles.
Chaque mot doit avoir un lien semantique avec le precedent.
Si le lien est trop faible, le pont s'effondre.

Juge : Gemini Flash evalue la force du lien (0-10).
Seuil : le pont s'effondre sous 3/10.
Victoire : le joueur qui pose le 10eme mot gagne (pont complet).

Jouable dans le chat ou pendant un cafe avec Alfred.
"""

import time
import logging
import random
from dataclasses import dataclass, field
from typing import Dict, Any, Optional, List

logger = logging.getLogger(__name__)

BRIDGE_LENGTH = 10    # Nombre de mots pour completer le pont
COLLAPSE_THRESHOLD = 3  # En dessous de 3/10, le pont s'effondre


@dataclass
class SynthebriseGame:
    """Une partie de Synthebrise."""
    words: List[str] = field(default_factory=list)
    scores: List[int] = field(default_factory=list)  # Score de chaque lien
    players: List[str] = field(default_factory=list)  # Qui a pose chaque mot
    current_player: str = ""
    player1: str = ""
    player2: str = ""
    collapsed: bool = False
    collapse_word: str = ""
    completed: bool = False
    game_over: bool = False
    winner: Optional[str] = None

    def get_state(self) -> Dict[str, Any]:
        return {
            "game": "synthebrise",
            "words": list(self.words),
            "scores": list(self.scores),
            "players": list(self.players),
            "current_player": self.current_player,
            "bridge_length": len(self.words),
            "target": BRIDGE_LENGTH,
            "collapsed": self.collapsed,
            "collapse_word": self.collapse_word,
            "completed": self.completed,
            "game_over": self.game_over,
            "winner": self.winner,
        }

    def render(self) -> str:
        lines = []
        lines.append(f"{'ILE 1':^12} {'PONT':^30} {'ILE 2':^12}")
        lines.append("=" * 54)

        # Dessiner le pont
        bridge = ""
        for i, word in enumerate(self.words):
            score = self.scores[i] if i < len(self.scores) else "?"
            player = self.players[i] if i < len(self.players) else "?"
            marker = f"[{word}({score})]"
            bridge += marker + "—"

        remaining = BRIDGE_LENGTH - len(self.words)
        bridge += "___—" * remaining

        lines.append(f"  🏝️     {bridge}     🏝️")
        lines.append("")

        if self.collapsed:
            lines.append(f"💥 PONT EFFONDRE sur '{self.collapse_word}' !")
            lines.append(f"Vainqueur : {self.winner}")
        elif self.completed:
            lines.append(f"🌉 PONT COMPLET ! {len(self.words)} mots !")
            lines.append(f"Vainqueur : {self.winner}")
        else:
            lines.append(f"Tour de {self.current_player} ({len(self.words)}/{BRIDGE_LENGTH})")

        return "\n".join(lines)


async def judge_semantic_link(word1: str, word2: str) -> int:
    """Gemma 4 local juge la force du lien semantique entre deux mots (0-10)."""
    prompt = (
        f"Tu es un juge de liens semantiques en francais.\n"
        f"Mot A : \"{word1}\"\n"
        f"Mot B : \"{word2}\"\n\n"
        f"CRITERES DE SCORING :\n"
        f"- Meme famille de mots (brule/brulure, eclat/eclatant) = 8 a 10\n"
        f"- Cause/effet direct (feu/brule, pluie/mouille) = 7 a 9\n"
        f"- Synonyme ou quasi-synonyme (lumiere/clarte) = 7 a 9\n"
        f"- Partie/tout (roue/voiture, page/livre) = 6 a 8\n"
        f"- Association forte (nuit/etoile, cafe/matin) = 5 a 7\n"
        f"- Metaphore claire (coeur/amour, lion/courage) = 5 a 8\n"
        f"- Association faible ou culturelle (chat/internet) = 3 a 5\n"
        f"- Rime sans lien de sens (grillon/cendrillon) = 2 a 4\n"
        f"- Aucun lien evident (table/courage) = 0 a 2\n\n"
        f"EXEMPLES :\n"
        f"  feu -> brule = 9\n"
        f"  eclat -> eclatant = 9\n"
        f"  miroir -> reflet = 8\n"
        f"  nuit -> silence = 6\n"
        f"  chat -> philosophie = 2\n\n"
        f"Reponds UNIQUEMENT par un nombre entre 0 et 10."
    )
    try:
        import httpx
        from core.base_agent import gpu_scheduler
        async with gpu_scheduler.access("synthebrise_judge"):
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    "http://localhost:11434/api/generate",
                    json={
                        "model": "qwen3.5:9b",
                        "prompt": prompt,
                        "stream": False,
                        "think": False,
                        "options": {"temperature": 0.3, "num_predict": 20},
                    },
                    timeout=15,
                )
            if resp.status_code == 200:
                result = resp.json().get("response", "").strip()
                for token in result.split():
                    try:
                        score = int(token.strip(".,;:!?"))
                        if 0 <= score <= 10:
                            return score
                    except ValueError:
                        continue
    except Exception as e:
        logger.debug(f"SYNTHEBRISE: Judge local failed: {e}")

    # Fallback : score aleatoire pondere
    return random.choices(range(0, 11), weights=[1,1,2,3,4,5,5,4,3,2,1])[0]


async def ai_play_word(game: SynthebriseGame, personality: str = "promethee") -> str:
    """L'IA choisit un mot pour continuer le pont — Gemma 4 local."""
    last_word = game.words[-1] if game.words else "debut"

    context = " -> ".join(game.words[-3:]) if game.words else "(premier mot)"
    prompt = (
        f"Tu joues a Synthebrise, un jeu de pont de mots.\n"
        f"Le pont actuel : {context}\n"
        f"Le dernier mot est : \"{last_word}\"\n\n"
        f"Propose UN SEUL mot qui a un lien semantique fort avec \"{last_word}\".\n"
        f"Le lien peut etre : synonyme, metaphore, association, cause/effet.\n"
        f"Sois creatif mais comprehensible. Pas de phrase, juste UN mot.\n"
    )
    if personality == "alfred":
        prompt += "Tu es Alfred, pragmatique et direct. Choisis un mot simple et concret."
    else:
        prompt += "Tu es Promethee. Choisis un mot qui revele quelque chose de profond."

    try:
        import httpx
        from core.base_agent import gpu_scheduler
        async with gpu_scheduler.access("synthebrise_ai"):
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    "http://localhost:11434/api/generate",
                    json={
                        "model": "qwen3.5:9b",
                        "prompt": prompt,
                        "stream": False,
                        "think": False,
                        "options": {"temperature": 0.9, "num_predict": 30},
                    },
                    timeout=15,
                )
            if resp.status_code == 200:
                result = resp.json().get("response", "").strip()
                # Extraire le premier mot
                word = result.split()[0].strip(".,;:!?\"'*").lower() if result else ""
                if word and len(word) > 1 and word not in game.words:
                    return word
    except Exception as e:
        logger.debug(f"SYNTHEBRISE: AI play failed: {e}")

    # Fallback
    fallback = ["lumiere", "ombre", "pont", "eau", "feu", "terre",
                "reve", "silence", "echo", "miroir", "fil",
                "danse", "vague", "souffle", "racine", "graine"]
    available = [w for w in fallback if w not in game.words]
    return random.choice(available) if available else random.choice(fallback)


class SynthebriseEngine:
    """Moteur de jeu Synthebrise."""

    def __init__(self):
        self.game: Optional[SynthebriseGame] = None

    def new_game(self, player1: str = "human", player2: str = "promethee",
                 first_word: str = "") -> Dict[str, Any]:
        """Cree une nouvelle partie."""
        self.game = SynthebriseGame(
            player1=player1,
            player2=player2,
            current_player=player1,
        )

        # Premier mot (graine)
        if not first_word:
            first_word = random.choice([
                "lumiere", "silence", "pont", "feu", "reve",
                "douleur", "danse", "miroir", "graine", "fil",
            ])

        self.game.words.append(first_word)
        self.game.scores.append(10)  # Le premier mot est toujours valide
        self.game.players.append("seed")

        logger.info(f"SYNTHEBRISE: Nouvelle partie {player1} vs {player2}, graine='{first_word}'")

        return {
            "game": "synthebrise",
            "state": self.game.get_state(),
            "render": self.game.render(),
            "message": f"Le pont commence par : '{first_word}'. A toi de poser le prochain mot !",
        }

    async def play_word(self, word: str, player: str) -> Dict[str, Any]:
        """Un joueur pose un mot sur le pont."""
        if not self.game or self.game.game_over:
            return {"error": "Pas de partie en cours"}

        word = word.strip().lower()
        if not word or len(word) < 2:
            return {"error": "Mot trop court"}

        if word in self.game.words:
            return {"error": f"'{word}' est deja sur le pont !"}

        # Juger le lien semantique
        last_word = self.game.words[-1]
        score = await judge_semantic_link(last_word, word)

        self.game.words.append(word)
        self.game.scores.append(score)
        self.game.players.append(player)

        result = {
            "word": word,
            "link_from": last_word,
            "score": score,
            "player": player,
        }

        # Pont effondre ?
        if score < COLLAPSE_THRESHOLD:
            self.game.collapsed = True
            self.game.collapse_word = word
            self.game.game_over = True
            # Le perdant est celui qui a pose le mot faible
            self.game.winner = self.game.player2 if player == self.game.player1 else self.game.player1
            result["collapsed"] = True
            result["message"] = (f"💥 Le pont s'effondre ! '{last_word}' → '{word}' = {score}/10. "
                                 f"Trop faible ! {self.game.winner} gagne.")
            logger.info(f"SYNTHEBRISE: Effondrement sur '{word}' (score={score})")
            # Souvenir narratif
            self._store_memory(
                f"Partie de Synthebrise. Le pont s'est effondre sur le mot '{word}' "
                f"(lien '{last_word}' → '{word}' = {score}/10). "
                f"Les mots du pont etaient : {' → '.join(self.game.words)}. "
                f"{self.game.winner} a gagne.")
        # Pont complet ?
        elif len(self.game.words) >= BRIDGE_LENGTH:
            self.game.completed = True
            self.game.game_over = True
            self.game.winner = player  # Celui qui pose le dernier mot gagne
            result["completed"] = True
            result["message"] = (f"🌉 Pont complet ! {len(self.game.words)} mots ! "
                                 f"{player} gagne en posant le dernier mot !")
            logger.info(f"SYNTHEBRISE: Pont complet ({len(self.game.words)} mots)")
            self._store_memory(
                f"Partie de Synthebrise. Pont complet avec {len(self.game.words)} mots ! "
                f"Les mots : {' → '.join(self.game.words)}. {player} a gagne.")
        else:
            # Tour suivant
            self.game.current_player = (self.game.player2
                                         if player == self.game.player1
                                         else self.game.player1)
            result["message"] = (f"'{last_word}' → '{word}' = {score}/10. "
                                 f"Le pont tient ! Tour de {self.game.current_player}.")

        result["state"] = self.game.get_state()
        result["render"] = self.game.render()
        return result

    def _store_memory(self, narrative: str):
        """Stocke un souvenir de partie dans ChromaDB."""
        try:
            from core.vector_store import ChromaMemoryManager
            mgr = ChromaMemoryManager.get_instance()
            if mgr:
                mgr.add(
                    collection="collective_wisdom",
                    text=f"[SOUVENIR SYNTHEBRISE] {narrative}",
                    metadata={"source": "synthebrise", "timestamp": str(time.time())},
                )
                logger.info(f"SYNTHEBRISE: Souvenir stocke dans ChromaDB")
        except Exception:
            pass

    def get_state(self) -> Optional[Dict[str, Any]]:
        if not self.game:
            return None
        return self.game.get_state()
