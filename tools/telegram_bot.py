"""
Bot Telegram — Contrôle à distance de Prométhée.

Commandes :
    /start          Message de bienvenue
    /status         État de Prométhée (autonomie, tissu, cardiac…)
    /tests          Lancer pytest sur la copie de travail
    /sync           Sync original → git + commit + push
    /logs [n]       Dernières n lignes du log du jour
    /mission <txt>  Envoyer une mission à Prométhée
    texte libre     Exécuter via Claude Code CLI

Sécurité : seul le TELEGRAM_CHAT_ID configuré peut interagir.
"""

import asyncio
import datetime
import logging
import os
import sys
from functools import wraps
from pathlib import Path

from dotenv import load_dotenv

# ── Chemins projet ──────────────────────────────────────────────────
PROMETHEE_DIR = Path(r"C:\Users\redla\projetclaude\PROMETHEE_V11_restructuration2026")
GIT_DIR = Path(r"C:\MesProjets\PROMETHEE_V11_restructuration2026")
LOGS_DIR = PROMETHEE_DIR / "logs"
API_BASE = "http://127.0.0.1:8000"
MAX_MSG_LEN = 4096

# ── Chargement .env ─────────────────────────────────────────────────
load_dotenv(PROMETHEE_DIR / ".env")
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

if not BOT_TOKEN:
    print("ERREUR : TELEGRAM_BOT_TOKEN manquant dans .env")
    sys.exit(1)
if not CHAT_ID:
    print("ERREUR : TELEGRAM_CHAT_ID manquant dans .env")
    sys.exit(1)

# ── Logging ─────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [TelegramBot] %(levelname)s %(message)s",
)
log = logging.getLogger("telegram_bot")

# ── Imports Telegram (après vérif token) ────────────────────────────
try:
    from telegram import Update
    from telegram.ext import (
        ApplicationBuilder,
        CommandHandler,
        ContextTypes,
        MessageHandler,
        filters,
    )
except ImportError:
    print("ERREUR : python-telegram-bot non installé.")
    print("  pip install python-telegram-bot")
    sys.exit(1)

try:
    import httpx
except ImportError:
    print("ERREUR : httpx non installé.  pip install httpx")
    sys.exit(1)


# ═══════════════════════════════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════════════════════════════

def restricted(func):
    """Décorateur : ignore les messages venant d'un chat non autorisé."""
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if str(update.effective_chat.id) != CHAT_ID:
            log.warning("Message ignoré de chat_id=%s", update.effective_chat.id)
            return
        return await func(update, context)
    return wrapper


def _clean_env() -> dict:
    """Retourne un env nettoyé des variables Claude Code (évite le blocage de claude CLI)."""
    env = os.environ.copy()
    for key in list(env):
        if key.startswith("CLAUDE_"):
            del env[key]
    return env


async def run_subprocess(cmd: list[str], cwd: str | Path, timeout: int = 300) -> str:
    """Lance un subprocess async et retourne stdout+stderr."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(cwd),
            env=_clean_env(),
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return stdout.decode("utf-8", errors="replace")
    except asyncio.TimeoutError:
        proc.kill()
        return f"[TIMEOUT après {timeout}s]"
    except Exception as e:
        return f"[ERREUR subprocess] {e}"


def truncate(text: str, max_len: int = MAX_MSG_LEN) -> str:
    """Tronque un texte pour Telegram en gardant début et fin."""
    if len(text) <= max_len:
        return text
    lines = text.splitlines()
    half = max_len // 2 - 50
    head = text[:half]
    tail = text[-half:]
    return f"{head}\n\n[...tronqué, {len(lines)} lignes au total...]\n\n{tail}"


def today_log_path() -> Path | None:
    """Retourne le chemin du log du jour, ou None."""
    today = datetime.date.today().isoformat()
    path = LOGS_DIR / f"promethee_{today}.log"
    if path.exists():
        return path
    # Chercher aussi dans le dossier git (la copie où tourne le programme)
    git_log = GIT_DIR / "logs" / f"promethee_{today}.log"
    if git_log.exists():
        return git_log
    return None


async def api_get(endpoint: str) -> dict | None:
    """GET sur l'API Prométhée. Retourne None si offline."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{API_BASE}{endpoint}")
            r.raise_for_status()
            return r.json()
    except Exception:
        return None


async def api_post(endpoint: str, json_data: dict) -> dict | None:
    """POST sur l'API Prométhée. Retourne None si offline."""
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(f"{API_BASE}{endpoint}", json=json_data)
            r.raise_for_status()
            return r.json()
    except Exception:
        return None


def format_status(autonomy: dict | None, tissue: dict | None,
                  cardiac: dict | None) -> str:
    """Formate les données de status en Markdown Telegram."""
    parts = []

    if autonomy:
        budget_used = autonomy.get("daily_budget_used", "?")
        budget_max = autonomy.get("daily_budget_max", 200)
        routines = autonomy.get("routines_today", "?")
        phase = autonomy.get("current_phase", "?")
        parts.append(
            f"*Autonomie*\n"
            f"  Budget : {budget_used}/{budget_max} pts\n"
            f"  Routines : {routines}\n"
            f"  Phase : {phase}"
        )
    else:
        parts.append("*Autonomie* : offline")

    if tissue:
        pop = tissue.get("population", "?")
        cap = tissue.get("capacity", "?")
        pandemic = tissue.get("pandemic_active", False)
        parts.append(
            f"*Tissu*\n"
            f"  Population : {pop}/{cap}\n"
            f"  Pandemie : {'ACTIVE' if pandemic else 'inactive'}"
        )

    if cardiac:
        bpm = cardiac.get("bpm", "?")
        uptime = cardiac.get("uptime_human", "?")
        parts.append(
            f"*Cardiac*\n"
            f"  BPM : {bpm}\n"
            f"  Uptime : {uptime}"
        )

    return "\n\n".join(parts) if parts else "Aucune donnee disponible."


# ═══════════════════════════════════════════════════════════════════
#  Commandes Telegram
# ═══════════════════════════════════════════════════════════════════

@restricted
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Message de bienvenue."""
    await update.message.reply_text(
        "*Promethee — Controle a distance*\n\n"
        "Commandes disponibles :\n"
        "/status — Etat de Promethee\n"
        "/tests — Lancer les tests\n"
        "/sync — Synchroniser et pousser vers git\n"
        "/logs \\[n\\] — Dernieres lignes du log\n"
        "/mission <texte> — Envoyer une mission\n"
        "Texte libre — Tache Claude Code",
        parse_mode="Markdown",
    )


@restricted
async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Récupère le status de Prométhée via l'API."""
    await update.message.reply_text("Interrogation de Promethee...")

    autonomy, tissue, cardiac = await asyncio.gather(
        api_get("/api/autonomy/status"),
        api_get("/api/tissue/status"),
        api_get("/api/circadian/status"),
    )

    if autonomy is None and tissue is None and cardiac is None:
        await update.message.reply_text(
            "Promethee n'est pas en ligne (serveur eteint ou inaccessible)."
        )
        return

    text = format_status(autonomy, tissue, cardiac)
    await update.message.reply_text(text, parse_mode="Markdown")


@restricted
async def cmd_tests(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lance pytest sur la copie de travail."""
    await update.message.reply_text("Lancement des tests... (timeout 5 min)")

    result = await run_subprocess(
        [sys.executable, "-m", "pytest", "tests/", "-x", "--tb=short", "-q"],
        cwd=PROMETHEE_DIR,
        timeout=300,
    )

    await update.message.reply_text(
        f"*Resultats tests :*\n```\n{truncate(result, MAX_MSG_LEN - 50)}\n```",
        parse_mode="Markdown",
    )


@restricted
async def cmd_sync(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Synchronise original → git, commit et push."""
    await update.message.reply_text("Synchronisation en cours...")

    # Étape 1 : copier les fichiers modifiés vers le repo git
    # On utilise robocopy pour copier le contenu (exclut .git, __pycache__, etc.)
    copy_result = await run_subprocess(
        [
            "powershell.exe", "-Command",
            (
                f"robocopy '{PROMETHEE_DIR}' '{GIT_DIR}' /MIR "
                f"/XD .git __pycache__ .pytest_cache memory\\chroma_db node_modules "
                f"/XF *.pyc .env; "
                f"if ($LASTEXITCODE -le 7) {{ 'Copie OK' }} else {{ 'Copie ERREUR' }}"
            ),
        ],
        cwd=PROMETHEE_DIR,
        timeout=120,
    )

    # Étape 2 : git add + commit + push
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    git_result = await run_subprocess(
        [
            "powershell.exe", "-Command",
            (
                f"cd '{GIT_DIR}'; "
                f"git add -A; "
                f"git commit -m 'sync telegram {timestamp}'; "
                f"git push 2>&1"
            ),
        ],
        cwd=GIT_DIR,
        timeout=120,
    )

    text = f"*Sync*\n```\n{truncate(copy_result, 1500)}\n```\n*Git*\n```\n{truncate(git_result, 1500)}\n```"
    await update.message.reply_text(text, parse_mode="Markdown")


@restricted
async def cmd_logs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Affiche les dernières lignes du log du jour."""
    # Nombre de lignes (défaut 30)
    n = 30
    if context.args:
        try:
            n = int(context.args[0])
            n = min(n, 200)  # Max 200 lignes
        except ValueError:
            pass

    log_path = today_log_path()
    if not log_path:
        await update.message.reply_text("Aucun log trouve pour aujourd'hui.")
        return

    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        tail = lines[-n:]
        text = "".join(tail)
        await update.message.reply_text(
            f"*Log* ({len(tail)}/{len(lines)} lignes)\n```\n{truncate(text, MAX_MSG_LEN - 80)}\n```",
            parse_mode="Markdown",
        )
    except Exception as e:
        await update.message.reply_text(f"Erreur lecture log : {e}")


@restricted
async def cmd_mission(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Envoie une mission à Prométhée via l'API."""
    if not context.args:
        await update.message.reply_text("Usage : /mission <texte de la mission>")
        return

    mission_text = " ".join(context.args)
    await update.message.reply_text(f"Mission envoyee : _{mission_text}_", parse_mode="Markdown")

    result = await api_post("/api/mission", {"mission": mission_text})
    if result is None:
        await update.message.reply_text("Promethee n'est pas en ligne.")
        return

    target = result.get("target", "?")
    action = result.get("action", "?")
    await update.message.reply_text(
        f"*Mission acceptee*\n  Agent : {target}\n  Action : {action}",
        parse_mode="Markdown",
    )


@restricted
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Texte libre → exécution via Claude Code CLI."""
    user_text = update.message.text.strip()
    if not user_text:
        return

    await update.message.reply_text(
        f"Envoi a Claude Code...\n_{truncate(user_text, 200)}_",
        parse_mode="Markdown",
    )

    # Claude Code CLI avec restrictions d'outils pour la sécurité
    result = await run_subprocess(
        [
            "claude", "-p", user_text,
            "--allowedTools", "Read,Glob,Grep,Write,Edit,Bash",
        ],
        cwd=PROMETHEE_DIR,
        timeout=600,  # 10 minutes max
    )

    if not result.strip():
        result = "(pas de reponse)"

    await update.message.reply_text(truncate(result))


# ═══════════════════════════════════════════════════════════════════
#  Messages proactifs
# ═══════════════════════════════════════════════════════════════════

async def check_proactive_messages(context: ContextTypes.DEFAULT_TYPE):
    """Poll l'API pour les messages proactifs et les envoie."""
    try:
        data = await api_get("/api/proactive/pending")
        if not data or not data.get("messages"):
            return
        for msg in data["messages"]:
            text = msg.get("text", "")
            if text:
                await context.bot.send_message(
                    chat_id=CHAT_ID,
                    text=text,
                    parse_mode="Markdown",
                )
        # Acquitter les messages envoyés
        await api_post("/api/proactive/ack", {})
        log.info("Messages proactifs envoyes: %d", len(data["messages"]))
    except Exception as e:
        log.debug("Proactive check failed: %s", e)


# ═══════════════════════════════════════════════════════════════════
#  Point d'entrée
# ═══════════════════════════════════════════════════════════════════

def main():
    """Démarre le bot en polling."""
    log.info("Demarrage du bot Telegram Promethee...")
    log.info("Chat ID autorise : %s", CHAT_ID)

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Commandes
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("tests", cmd_tests))
    app.add_handler(CommandHandler("sync", cmd_sync))
    app.add_handler(CommandHandler("logs", cmd_logs))
    app.add_handler(CommandHandler("mission", cmd_mission))

    # Texte libre (catch-all)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    # --- Messages proactifs : poll l'API toutes les 60s ---
    app.job_queue.run_repeating(
        check_proactive_messages,
        interval=60,
        first=30,
    )

    log.info("Bot Telegram demarre. En attente de messages...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
