"""Chess Game — Echecs pour la salle de jeux (12/06/2026).

Lecon de la revanche du 11/06 (match par chat, arbitre python-chess externe) :
Promethee voit QUOI prendre mais pas COMMENT — ses cibles sont reelles, la
geometrie de ses pieces est hallucinee (Qxe4/exd5/Nxe5 illegaux, perseveration
3x sur le meme coup). Design en consequence :

  - le moteur python-chess tient l'echiquier et VALIDE tout cote serveur ;
  - Promethee (LLM local) choisit son coup en recevant la LISTE DES COUPS
    LEGAUX — l'aide geometrique qui lui manquait. La salle de jeux teste
    sa STRATEGIE, pas sa geometrie ;
  - coup invalide -> 1 retry avec correction -> fallback heuristique
    (marque "assiste", compte dans l'etat de la partie).

Interface calquee sur MorpionGame/Puissance4Game : play() -> {"valid": ...},
get_state(), render(), current_player, game_over, winner, moves_count.
Le coup LLM est ASYNC (pattern synthebrise : route dediee /chess/ai-move).
"""
import logging
import os
import random
import re
from typing import Any, Dict, List, Optional

try:
    import chess
    CHESS_AVAILABLE = True
except ImportError:  # degradation propre si python-chess absent
    chess = None
    CHESS_AVAILABLE = False

logger = logging.getLogger(__name__)

UCI_RE = re.compile(r"\b([a-h][1-8][a-h][1-8][qrbn]?)\b")


class ChessGame:
    """Partie d'echecs arbitree par python-chess. Blancs commencent toujours."""

    def __init__(self):
        if not CHESS_AVAILABLE:
            raise RuntimeError("python-chess n'est pas installe (pip install chess)")
        self.board = chess.Board()
        self.game_over = False
        self.winner: Optional[str] = None  # "blancs" | "noirs" | None (nulle) | "forfait"
        self.moves_count = 0
        self.last_move: Optional[str] = None
        self.history_san: List[str] = []
        self.assisted_moves = 0  # coups joues par le fallback pour Promethee

    @property
    def current_player(self) -> str:
        return "blancs" if self.board.turn == chess.WHITE else "noirs"

    def legal_moves_uci(self) -> List[str]:
        return [m.uci() for m in self.board.legal_moves]

    def play(self, uci: str) -> Dict[str, Any]:
        """Joue un coup UCI. Auto-promotion en dame si non precisee (e7e8 -> e7e8q)."""
        if self.game_over:
            return {"valid": False, "reason": "Partie terminee"}
        uci = (uci or "").strip().lower()
        if not re.fullmatch(r"[a-h][1-8][a-h][1-8][qrbn]?", uci):
            return {"valid": False, "reason": f"Format invalide: '{uci}' (attendu UCI, ex: e2e4)"}
        try:
            move = chess.Move.from_uci(uci)
        except ValueError:
            return {"valid": False, "reason": f"Coup illisible: {uci}"}
        if move not in self.board.legal_moves:
            promoted = None
            if len(uci) == 4:
                try:
                    promoted = chess.Move.from_uci(uci + "q")
                except ValueError:
                    promoted = None
            if promoted is not None and promoted in self.board.legal_moves:
                move = promoted
            else:
                return {"valid": False, "reason": f"Coup illegal: {uci}"}

        san = self.board.san(move)
        self.board.push(move)
        self.moves_count += 1
        self.last_move = move.uci()
        self.history_san.append(san)
        self._update_game_over()
        return {
            "valid": True, "san": san, "uci": move.uci(),
            "in_check": self.board.is_check(),
            "game_over": self.game_over, "winner": self.winner,
        }

    def _update_game_over(self):
        if self.board.is_checkmate():
            self.game_over = True
            # Le camp AU TRAIT est mat -> l'autre camp gagne
            self.winner = "noirs" if self.board.turn == chess.WHITE else "blancs"
        elif (self.board.is_stalemate()
              or self.board.is_insufficient_material()
              or self.board.is_seventyfive_moves()
              or self.board.is_fivefold_repetition()):
            self.game_over = True
            self.winner = None

    def get_state(self) -> Dict[str, Any]:
        # Grille 8x8, rangee 8 en premier (blancs en bas a l'affichage),
        # symboles FEN ('P' blanc, 'p' noir, '' vide) — le front mappe en unicode.
        grid = []
        for rank in range(7, -1, -1):
            row = []
            for file in range(8):
                p = self.board.piece_at(chess.square(file, rank))
                row.append(p.symbol() if p else "")
            grid.append(row)
        return {
            "game": "echecs",
            "board": grid,
            "fen": self.board.fen(),
            "current_player": self.current_player,
            "legal_moves": self.legal_moves_uci(),
            "in_check": self.board.is_check(),
            "game_over": self.game_over,
            "winner": self.winner,
            "last_move": self.last_move,
            "moves_count": self.moves_count,
            "history_san": self.history_san[-24:],
            "assisted_moves": self.assisted_moves,
        }

    def render(self) -> str:
        rows = str(self.board).split("\n")
        lines = [f"{8 - i}  {r}" for i, r in enumerate(rows)]
        lines.append("   a b c d e f g h")
        return "\n".join(lines)


# ─── Fallback heuristique (eprouve lors du match du 11/06) ──────────────────

_PIECE_VALUES = {1: 1, 2: 3, 3: 3, 4: 5, 5: 9, 6: 0} if CHESS_AVAILABLE else {}


def heuristic_move(game: "ChessGame") -> Optional[str]:
    """Coup de secours sans LLM : mat en 1 > capture nette > developpement sur."""
    board = game.board
    moves = list(board.legal_moves)
    if not moves:
        return None
    # 1. Mat en 1
    for mv in moves:
        board.push(mv)
        mate = board.is_checkmate()
        board.pop()
        if mate:
            return mv.uci()
    # 2. Capture nette
    best, best_gain = None, 0
    for mv in moves:
        if board.is_capture(mv):
            victim = board.piece_at(mv.to_square)
            v = _PIECE_VALUES.get(victim.piece_type, 1) if victim else 1  # en passant
            attacker = board.piece_at(mv.from_square)
            a = _PIECE_VALUES.get(attacker.piece_type, 1) if attacker else 1
            board.push(mv)
            recapture = board.is_attacked_by(board.turn, mv.to_square)
            board.pop()
            gain = v - (a if recapture else 0)
            if gain > best_gain:
                best_gain, best = gain, mv
    if best is not None and best_gain >= 1:
        return best.uci()

    # 3. Developpement / centre, en evitant de pendre une piece
    def score(mv):
        s = 0.0
        to = mv.to_square
        f, r = chess.square_file(to), chess.square_rank(to)
        s += 3 - (abs(3.5 - f) + abs(3.5 - r)) / 2
        p = board.piece_at(mv.from_square)
        home_rank = 0 if board.turn == chess.WHITE else 7
        if p and p.piece_type in (chess.KNIGHT, chess.BISHOP) and chess.square_rank(mv.from_square) == home_rank:
            s += 2
        if board.is_castling(mv):
            s += 3
        board.push(mv)
        hanging = board.is_attacked_by(board.turn, to) and not board.is_attacked_by(not board.turn, to)
        board.pop()
        if hanging:
            s -= 4 + (_PIECE_VALUES.get(p.piece_type, 1) if p else 1)
        return s + random.random() * 0.5

    return max(moves, key=score).uci()


# ─── Le coup de Promethee (LLM local + arbitrage) ────────────────────────────

def _skill_hint(game: "ChessGame") -> str:
    """Pont bibliotheque -> coup (13/06) : injecte la competence la plus PERTINENTE
    de Promethee dans le contexte du coup, comme une SUGGESTION (jamais imperative
    — exact pattern des ancres d'identite reinjectees dans les routines).

    Avant ce pont, ses competences vivaient dans la console mais n'atteignaient pas
    le canal de jeu (promethee_chess_move) : ECHECS etait un savoir mort cote partie.
    On cherche via le matcher deterministe find_skill (pas de HNSW), avec un seuil de
    pertinence pour ne jamais injecter une competence hors-sujet (ex: RECURSION).
    Renvoie '' si rien de pertinent ou si la bibliotheque est indisponible.
    """
    try:
        from core.skill_library import find_skill
        phase = "ouverture developpement" if game.moves_count < 12 else "milieu de partie tactique strategie"
        hit = find_skill(f"echecs partie strategie coup {phase} pieces plateau jeu")
        if not hit:
            return ""
        fiche, score, _mots = hit
        if score < 0.2:  # recouvrement trop faible -> competence non pertinente, on s'abstient
            return ""
        proc = (fiche.get("procedure") or "").strip()
        ajust = (fiche.get("protocole_ajustement") or "").strip()
        if not proc and not ajust:
            return ""
        lines = [f"[TA COMPETENCE « {fiche.get('nom', '?')} » — rappel de ta bibliotheque, pas un ordre]"]
        if proc:
            lines.append(f"Methode que tu as toi-meme forgee : {proc[:280]}")
        if ajust:
            lines.append(f"Piege a eviter : {ajust[:200]}")
        lines.append("Applique-la si elle sert ce coup ; sinon ignore-la. Le choix reste le tien.")
        return "\n".join(lines) + "\n\n"
    except Exception as e:  # noqa: BLE001 — le pont ne doit JAMAIS casser un coup
        logger.debug(f"CHESS _skill_hint indisponible: {e}")
        return ""


def _build_prompt(game: "ChessGame", legal: List[str], correction: str = "",
                  skill_hint: str = "") -> str:
    couleur = game.current_player.upper()
    hist = ""
    for i in range(0, len(game.history_san), 2):
        num = i // 2 + 1
        pair = f"{num}. {game.history_san[i]}"
        if i + 1 < len(game.history_san):
            pair += f" {game.history_san[i + 1]}"
        hist += pair + "  "
    return (
        f"Tu joues une partie d'echecs dans ta salle de jeux. Tu as les {couleur}.\n"
        f"{correction}"
        f"Historique : {hist.strip() or '(debut de partie)'}\n"
        f"Plateau (MAJUSCULES = blancs, ligne 1 en bas) :\n{game.render()}\n\n"
        f"{skill_hint}"
        f"TES COUPS LEGAUX (choisis STRICTEMENT dans cette liste, format UCI) :\n"
        f"{' '.join(legal)}\n\n"
        f"Reponds sur deux lignes exactement :\n"
        f"COUP: <un coup de la liste>\n"
        f"INTENTION: <une phrase courte sur ta strategie>"
    )


def _parse_llm_move(raw: str, legal: List[str]):
    """Extrait (uci, intention) de la reponse LLM. uci=None si rien de legal."""
    intention = ""
    m = re.search(r"INTENTION\s*:\s*(.+)", raw or "", re.IGNORECASE)
    if m:
        intention = m.group(1).strip()[:200]
    legal_set = set(legal)
    candidates = []
    m = re.search(r"COUP\s*:\s*\**\s*([a-h][1-8][a-h][1-8][qrbn]?)", raw or "", re.IGNORECASE)
    if m:
        candidates.append(m.group(1).lower())
    candidates += [c.lower() for c in UCI_RE.findall(raw or "")]
    for c in candidates:
        if c in legal_set:
            return c, intention
        if len(c) == 4 and (c + "q") in legal_set:
            return c + "q", intention
    return None, intention


async def _ask_llm(prompt: str) -> str:
    """Appel LLM local — pattern synthebrise (httpx async + gpu_scheduler)."""
    import httpx
    from core.base_agent import gpu_scheduler
    model = os.getenv("LOCAL_GENERALIST_MODEL", "gemma4:12b")
    async with gpu_scheduler.access("chess_move"):
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "http://localhost:11434/api/generate",
                json={
                    "model": model,
                    "prompt": prompt,
                    "stream": False,
                    "think": False,
                    "options": {"temperature": 0.7, "num_predict": 150},
                },
                timeout=60,
            )
    if resp.status_code == 200:
        return resp.json().get("response", "") or ""
    logger.warning(f"CHESS: Ollama HTTP {resp.status_code}")
    return ""


async def promethee_chess_move(game: "ChessGame") -> Dict[str, Any]:
    """Promethee choisit et joue son coup. LLM -> retry -> fallback assiste."""
    legal = game.legal_moves_uci()
    if not legal or game.game_over:
        return {"error": "Pas de coup a jouer"}

    uci, intention, assisted = None, "", False
    # Pont bibliotheque->coup : on charge le rappel de competence UNE fois (lecture
    # fichiers) et on le passe aux deux appels (initial + correction).
    hint = _skill_hint(game)
    try:
        raw = await _ask_llm(_build_prompt(game, legal, skill_hint=hint))
        uci, intention = _parse_llm_move(raw, legal)
        if uci is None:
            correction = ("[ARBITRE] Ta proposition precedente n'etait pas dans la liste "
                          "des coups legaux. Relis la liste et choisis STRICTEMENT dedans.\n")
            raw = await _ask_llm(_build_prompt(game, legal, correction, skill_hint=hint))
            uci, intention = _parse_llm_move(raw, legal)
    except Exception as e:
        logger.warning(f"CHESS: appel LLM echoue ({e}) — fallback heuristique")

    if uci is None:
        uci = heuristic_move(game)
        assisted = True
        game.assisted_moves += 1
        intention = "(coup de secours — ma proposition n'etait pas legale, l'arbitre a joue sur)"
        logger.info(f"CHESS: coup assiste {uci} (LLM hors liste legale)")

    result = game.play(uci)
    if not result.get("valid"):
        # Ne devrait jamais arriver (uci pris dans legal) — ceinture+bretelles
        uci = heuristic_move(game)
        assisted = True
        game.assisted_moves += 1
        result = game.play(uci)

    return {
        "move": uci,
        "san": result.get("san"),
        "result": result,
        "comment": intention,
        "assisted": assisted,
    }
