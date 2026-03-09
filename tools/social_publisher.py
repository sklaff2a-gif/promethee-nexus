# tools/social_publisher.py — Publication Reddit pour Prométhée
# Usage: python tools/social_publisher.py --subreddit LocalLLaMA --title "..." --body "..."
#        python tools/social_publisher.py --check  (vérifie les credentials)
#        python tools/social_publisher.py --stats  (stats des posts récents)

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

# Charger .env manuellement (pas de dépendance dotenv obligatoire)
def _load_env():
    env_path = Path(__file__).parent.parent / ".env"
    if env_path.exists():
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, value = line.partition("=")
                    os.environ.setdefault(key.strip(), value.strip())

_load_env()

# --- Config ---
REDDIT_CLIENT_ID = os.environ.get("REDDIT_CLIENT_ID", "")
REDDIT_CLIENT_SECRET = os.environ.get("REDDIT_CLIENT_SECRET", "")
REDDIT_USERNAME = os.environ.get("REDDIT_USERNAME", "")
REDDIT_PASSWORD = os.environ.get("REDDIT_PASSWORD", "")
USER_AGENT = "promethee-publisher/1.0 by " + REDDIT_USERNAME

# Subreddits cibles recommandés
RECOMMENDED_SUBREDDITS = [
    "LocalLLaMA",       # IA locale, Ollama, modèles open-source
    "MachineLearning",  # ML académique (tag [P] pour Project)
    "artificial",       # IA générale
    "Python",           # Projets Python notables
    "singularity",      # IA avancée, AGI
]

# Historique des publications (anti-spam)
PUBLISH_HISTORY_FILE = Path(__file__).parent.parent / "memory" / "reddit_publish_history.json"


def _load_history() -> list:
    if PUBLISH_HISTORY_FILE.exists():
        try:
            with open(PUBLISH_HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return []
    return []


def _save_history(history: list):
    PUBLISH_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(PUBLISH_HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history[-50:], f, indent=2, ensure_ascii=False)


def _get_reddit():
    """Retourne une instance Reddit authentifiée."""
    import praw
    if not all([REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, REDDIT_USERNAME, REDDIT_PASSWORD]):
        print("ERREUR: Variables manquantes dans .env:")
        print("  REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, REDDIT_USERNAME, REDDIT_PASSWORD")
        print("\nPour les obtenir:")
        print("  1. Connecte-toi sur https://www.reddit.com/prefs/apps")
        print("  2. Crée une app de type 'script'")
        print("  3. Ajoute les 4 variables dans .env")
        sys.exit(1)

    return praw.Reddit(
        client_id=REDDIT_CLIENT_ID,
        client_secret=REDDIT_CLIENT_SECRET,
        username=REDDIT_USERNAME,
        password=REDDIT_PASSWORD,
        user_agent=USER_AGENT,
    )


def check_credentials():
    """Vérifie que les credentials Reddit fonctionnent."""
    reddit = _get_reddit()
    try:
        user = reddit.user.me()
        print(f"OK: Connecte en tant que u/{user.name}")
        print(f"   Karma: {user.link_karma} link / {user.comment_karma} comment")
        print(f"   Compte cree: {datetime.fromtimestamp(user.created_utc).strftime('%Y-%m-%d')}")
        return True
    except Exception as e:
        print(f"ERREUR: {e}")
        return False


def publish(subreddit: str, title: str, body: str, flair: str = None, dry_run: bool = False):
    """Publie un post sur un subreddit."""
    # Anti-spam : vérifier qu'on n'a pas déjà posté sur ce sub récemment
    history = _load_history()
    recent_same_sub = [
        h for h in history
        if h.get("subreddit") == subreddit
        and time.time() - h.get("timestamp", 0) < 7 * 24 * 3600  # 7 jours
    ]
    if recent_same_sub:
        last = recent_same_sub[-1]
        days_ago = (time.time() - last["timestamp"]) / 86400
        print(f"ATTENTION: Dernier post sur r/{subreddit} il y a {days_ago:.1f} jours.")
        print(f"  Titre: {last.get('title', '?')[:80]}")
        if not dry_run:
            print("  Utilise --force pour poster quand meme, ou attends 7 jours.")
            return None

    if dry_run:
        print(f"\n--- DRY RUN (pas de publication) ---")
        print(f"Subreddit: r/{subreddit}")
        print(f"Titre: {title}")
        if flair:
            print(f"Flair: {flair}")
        print(f"Corps ({len(body)} chars):")
        print("-" * 40)
        print(body)
        print("-" * 40)
        return None

    reddit = _get_reddit()
    sub = reddit.subreddit(subreddit)

    try:
        submission = sub.submit(title=title, selftext=body, flair_id=flair)
        print(f"OK: Post publie sur r/{subreddit}")
        print(f"   URL: https://reddit.com{submission.permalink}")
        print(f"   ID: {submission.id}")

        # Sauvegarder dans l'historique
        history.append({
            "subreddit": subreddit,
            "title": title,
            "post_id": submission.id,
            "url": f"https://reddit.com{submission.permalink}",
            "timestamp": time.time(),
            "date": datetime.now().isoformat(),
        })
        _save_history(history)
        return submission

    except Exception as e:
        print(f"ERREUR publication: {e}")
        return None


def check_responses():
    """Vérifie les réponses/commentaires sur nos posts récents."""
    history = _load_history()
    if not history:
        print("Aucun post dans l'historique.")
        return []

    reddit = _get_reddit()
    results = []

    for entry in history[-10:]:
        post_id = entry.get("post_id")
        if not post_id:
            continue
        try:
            submission = reddit.submission(id=post_id)
            submission.comments.replace_more(limit=0)
            comments = list(submission.comments)
            results.append({
                "subreddit": entry.get("subreddit"),
                "title": submission.title[:80],
                "url": entry.get("url"),
                "score": submission.score,
                "upvote_ratio": submission.upvote_ratio,
                "num_comments": submission.num_comments,
                "comments": [
                    {
                        "author": str(c.author),
                        "score": c.score,
                        "body": c.body[:300],
                        "created": datetime.fromtimestamp(c.created_utc).isoformat(),
                    }
                    for c in comments[:10]
                ],
                "date_posted": entry.get("date"),
            })
            print(f"\nr/{entry.get('subreddit')} — {submission.title[:60]}")
            print(f"  Score: {submission.score} | Ratio: {submission.upvote_ratio:.0%} | Commentaires: {submission.num_comments}")
            if comments:
                for c in comments[:5]:
                    print(f"  > u/{c.author} ({c.score} pts): {c.body[:120]}")
            else:
                print(f"  (pas de commentaires)")
        except Exception as e:
            print(f"  Erreur lecture post {post_id}: {e}")

    return results


def main():
    parser = argparse.ArgumentParser(description="Publication Reddit pour Promethee")
    parser.add_argument("--check", action="store_true", help="Verifier les credentials")
    parser.add_argument("--stats", action="store_true", help="Stats des posts recents")
    parser.add_argument("--subreddit", "-s", help="Subreddit cible")
    parser.add_argument("--title", "-t", help="Titre du post")
    parser.add_argument("--body", "-b", help="Corps du post (ou @fichier pour lire un fichier)")
    parser.add_argument("--flair", "-f", help="Flair ID (optionnel)")
    parser.add_argument("--dry-run", action="store_true", help="Afficher sans publier")
    parser.add_argument("--force", action="store_true", help="Ignorer l'anti-spam 7 jours")
    parser.add_argument("--list-subs", action="store_true", help="Lister les subreddits recommandes")
    args = parser.parse_args()

    if args.check:
        check_credentials()
    elif args.stats:
        check_responses()
    elif args.list_subs:
        print("Subreddits recommandes:")
        for s in RECOMMENDED_SUBREDDITS:
            print(f"  r/{s}")
    elif args.subreddit and args.title and args.body:
        body = args.body
        if body.startswith("@"):
            filepath = body[1:]
            with open(filepath, "r", encoding="utf-8") as f:
                body = f.read()
        publish(args.subreddit, args.title, body, flair=args.flair,
                dry_run=args.dry_run or not args.force)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
