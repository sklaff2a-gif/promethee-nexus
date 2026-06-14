# core/skill_library.py
"""Bibliotheque de competences — la boucle de meta-apprentissage de Promethee.

Atelier du 13/06/2026, structure CO-CONCUE avec lui. Une COMPETENCE est une
procedure reutilisable qu'il a EXTRAITE d'une resolution reussie, puis STOCKEE de
facon fidele. On stocke en fichier JSON (un par competence) et NON dans la memoire
vectorielle : une procedure doit se recharger EXACTEMENT, pas approximativement
(meme doctrine que !run / !recall — la source EST la verite, jamais une
reconstruction floue du LLM ni du HNSW).

Boucle fermee demandee par Jean-Michel :
    resoudre une tache -> extraire ce qui a marche -> !skill_save (stocker)
    -> (un cas cousin se presente) -> !skill_find / !skill_load (recharger, ne pas
    repartir de zero) -> appliquer -> mesurer -> si le resultat est MEILLEUR,
    !skill_save met la procedure a jour ; sinon l'ancienne, meilleure, est gardee.

REGLE D'OR — la mise a jour est CONDITIONNELLE au score : on ne remplace la
procedure que si le nouveau score bat le best_score enregistre. C'est ce qui
distingue l'apprentissage (je ne garde que ce qui ameliore) du simple
ecrasement (j'oublie le meilleur pour le dernier).

Structure de fiche (Promethee a ajoute CONTEXTE et PROTOCOLE D'AJUSTEMENT a la
fiche initiale nom/declencheur/procedure/metrique) :
    nom                    — identite de la competence
    slug                   — derive du nom, sert de nom de fichier
    declencheur            — a quoi je RECONNAIS qu'un nouveau cas en releve
    contexte               — le mode/terrain interne ou elle s'active (sa Zone)
    procedure              — les etapes actionnables
    metrique               — le chiffre dur qui dit si une version est meilleure
    protocole_ajustement   — quand/pourquoi reviser au-dela de la seule metrique
    best_score             — meilleur score atteint (None si jamais mesure)
    version                — incremente a chaque amelioration ou revision
    history                — journal {ts, score, version, note} des tentatives
    created / updated      — horodatages
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Dict, List, Optional, Tuple

# Racine du projet ancree sur __file__ (PAS de chemin CWD-relatif : le serveur et
# les tests peuvent tourner depuis des repertoires differents).
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILLS_DIR = os.path.join(PROJECT_ROOT, "memory", "skills")

# Mots vides ignores par le matcher de declencheurs (français + bruit courant).
_STOPWORDS = frozenset({
    "le", "la", "les", "un", "une", "des", "de", "du", "et", "ou", "a", "au", "aux",
    "en", "sur", "pour", "par", "dans", "avec", "sans", "ce", "ces", "cette", "son",
    "sa", "ses", "mes", "mon", "ma", "qui", "que", "quoi", "dont", "est", "sont",
    "il", "elle", "je", "tu", "nous", "vous", "se", "si", "ne", "pas", "plus",
    "comme", "tout", "tous", "toute", "leur", "lui", "y", "d", "l", "s", "n", "c",
})


# ─── Stockage bas niveau ─────────────────────────────────────────────────────

def _ensure_dir() -> str:
    """Cree le dossier des competences si absent. Renvoie son chemin."""
    os.makedirs(SKILLS_DIR, exist_ok=True)
    return SKILLS_DIR


def slugify(nom: str) -> str:
    """Derive un slug de fichier sur du texte libre (minuscule, ascii, tirets)."""
    s = (nom or "").strip().lower()
    s = s.replace("é", "e").replace("è", "e").replace("ê", "e").replace("ë", "e")
    s = s.replace("à", "a").replace("â", "a").replace("ä", "a")
    s = s.replace("î", "i").replace("ï", "i").replace("ô", "o").replace("ö", "o")
    s = s.replace("ù", "u").replace("û", "u").replace("ü", "u").replace("ç", "c")
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s[:60] or "competence"


def _path_for(slug: str) -> str:
    return os.path.join(_ensure_dir(), slug + ".json")


def _read(path: str) -> Optional[Dict]:
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _write(path: str, fiche: Dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(fiche, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)  # ecriture atomique : jamais de fichier a moitie ecrit


def _all() -> List[Dict]:
    out = []
    if not os.path.isdir(SKILLS_DIR):
        return out
    for fn in sorted(os.listdir(SKILLS_DIR)):
        if fn.endswith(".json"):
            fiche = _read(os.path.join(SKILLS_DIR, fn))
            if fiche:
                out.append(fiche)
    return out


# ─── Parsing tolerant de la charge !skill_save ───────────────────────────────
# Deux formats acceptes (l'usage du 13/06 a montre que le LLM ecrit spontanement
# le format ETIQUETE, pas le format pipe impose) :
#   1. pipe     : nom | declencheur | contexte | procedure | metrique | ajustement | score
#   2. etiquete : NOM: ...\nDECLENCHEUR: ...\nCONTEXTE: ...\nPROCEDURE: ...\n...
# Sans cette tolerance, sa procedure nocturne autonome casserait a chaque fois.

def _strip_accents(s: str) -> str:
    table = str.maketrans("àâäéèêëîïôöùûüç", "aaaeeeeiioouuuc")
    return (s or "").translate(table)


_LABEL_MAP = {
    "nom": "nom", "name": "nom", "identite": "nom",
    "declencheur": "declencheur", "trigger": "declencheur", "reconnaissance": "declencheur",
    "contexte": "contexte", "context": "contexte", "zone": "contexte", "mode": "contexte",
    "procedure": "procedure", "proc": "procedure", "etapes": "procedure", "methode": "procedure",
    "metrique": "metrique", "metric": "metrique", "metrique_succes": "metrique",
    "metrique_de_succes": "metrique", "succes": "metrique", "mesure": "metrique",
    "ajustement": "protocole", "protocole": "protocole", "protocole_ajustement": "protocole",
    "revision": "protocole", "mutation": "protocole",
    "score": "score",
}


def _map_label(raw: str) -> Optional[str]:
    # lower() AVANT de retirer les accents : la table ne couvre que les minuscules,
    # donc un label majuscule accentue (« DÉCLENCHEUR ») doit d'abord etre abaisse.
    key = _strip_accents(raw.lower()).strip().replace(" ", "_").replace("-", "_")
    return _LABEL_MAP.get(key)


def parse_skill_payload(text: str) -> Dict:
    """Parse une charge !skill_save en champs, tolerant aux deux formats.

    Renvoie {nom, declencheur, contexte, procedure, metrique, protocole, score}.
    """
    text = (text or "").replace("\\n", "\n").strip()
    fields = {"nom": "", "declencheur": "", "contexte": "", "procedure": "",
              "metrique": "", "protocole": "", "score": None}
    if not text:
        return fields

    # Detecter le mode etiquete : au moins une ligne « LABEL_CONNU: ... ».
    lines = text.split("\n")
    has_labels = False
    for line in lines:
        m = re.match(r"^\s*([A-Za-zÀ-ÿ_][A-Za-zÀ-ÿ_ \-]{1,30}?)\s*:\s*", line)
        if m and _map_label(m.group(1)):
            has_labels = True
            break

    if has_labels:
        current = None
        buf: Dict[str, List[str]] = {}
        for line in lines:
            m = re.match(r"^\s*([A-Za-zÀ-ÿ_][A-Za-zÀ-ÿ_ \-]{1,30}?)\s*:\s*(.*)$", line)
            field = _map_label(m.group(1)) if m else None
            if field:
                current = field
                buf.setdefault(field, []).append(m.group(2))
            elif current is not None:
                buf.setdefault(current, []).append(line)
        for f, parts in buf.items():
            val = "\n".join(parts).strip()
            if f == "score":
                try:
                    fields["score"] = float(val.split()[0]) if val else None
                except (ValueError, IndexError):
                    fields["score"] = None
            else:
                fields[f] = val
        return fields

    # Mode pipe : nom | declencheur | contexte | procedure | metrique | ajustement | score
    champs = [c.strip() for c in text.split("|")]
    order = ["nom", "declencheur", "contexte", "procedure", "metrique", "protocole"]
    for i, key in enumerate(order):
        if i < len(champs):
            fields[key] = champs[i]
    if len(champs) > 6 and champs[6]:
        try:
            fields["score"] = float(champs[6])
        except ValueError:
            fields["score"] = None
    return fields


# ─── API ─────────────────────────────────────────────────────────────────────

def save_skill(nom: str, declencheur: str = "", contexte: str = "",
               procedure: str = "", metrique: str = "", protocole: str = "",
               score: Optional[float] = None) -> Dict:
    """Cree ou met a jour une competence. La mise a jour est CONDITIONNELLE au score.

    Retourne un dict de resultat : {status, version, best_score, prev_score, message}
    status ∈ {created, improved, kept, revised} :
      - created  : competence neuve
      - improved : score fourni ET strictement meilleur que le best -> procedure remplacee
      - kept     : score fourni mais <= best -> ANCIENNE procedure conservee (la meilleure)
      - revised  : revision sans score (ou 1er score sur une fiche sans best) -> mise a jour
    """
    nom = (nom or "").strip()
    if not nom:
        return {"status": "error", "message": "Nom de competence requis."}
    slug = slugify(nom)
    path = _path_for(slug)
    now = time.time()
    existing = _read(path)

    if existing is None:
        fiche = {
            "nom": nom, "slug": slug, "declencheur": declencheur.strip(),
            "contexte": contexte.strip(), "procedure": procedure.strip(),
            "metrique": metrique.strip(), "protocole_ajustement": protocole.strip(),
            "best_score": score, "version": 1,
            "history": [{"ts": now, "score": score, "version": 1, "note": "creation"}],
            "created": now, "updated": now,
        }
        _write(path, fiche)
        return {"status": "created", "version": 1, "best_score": score,
                "prev_score": None,
                "message": f"Competence « {nom} » creee (v1, score={score})."}

    prev_best = existing.get("best_score")

    # Tentative NON retenue : un score est fourni mais il n'ameliore pas.
    if score is not None and prev_best is not None and score <= prev_best:
        existing.setdefault("history", []).append({
            "ts": now, "score": score, "version": existing.get("version", 1),
            "note": f"tentative non retenue (score {score} <= best {prev_best})",
        })
        existing["history"] = existing["history"][-50:]
        existing["updated"] = now
        _write(path, existing)
        return {"status": "kept", "version": existing.get("version", 1),
                "best_score": prev_best, "prev_score": score,
                "message": (f"Score {score} <= meilleur connu {prev_best} : "
                            f"l'ancienne procedure (v{existing.get('version', 1)}, "
                            f"la meilleure) est CONSERVEE. Tentative tracee.")}

    # Amelioration (score strictement meilleur) OU revision (sans score, ou 1er score).
    improved = score is not None and (prev_best is None or score > prev_best)
    new_version = (existing.get("version") or 1) + 1

    def _keep(new: str, old: str) -> str:
        new = (new or "").strip()
        return new if new else (old or "")

    fiche = {
        "nom": existing.get("nom", nom), "slug": slug,
        "declencheur": _keep(declencheur, existing.get("declencheur", "")),
        "contexte": _keep(contexte, existing.get("contexte", "")),
        "procedure": _keep(procedure, existing.get("procedure", "")),
        "metrique": _keep(metrique, existing.get("metrique", "")),
        "protocole_ajustement": _keep(protocole, existing.get("protocole_ajustement", "")),
        "best_score": score if improved else prev_best,
        "version": new_version,
        "history": (existing.get("history") or []) + [{
            "ts": now, "score": score, "version": new_version,
            "note": ("amelioration " + (f"{prev_best} -> {score}" if prev_best is not None else f"premier score {score}"))
                    if improved else "revision (sans amelioration de score)",
        }],
        "created": existing.get("created", now), "updated": now,
    }
    fiche["history"] = fiche["history"][-50:]
    _write(path, fiche)
    status = "improved" if improved else "revised"
    msg = (f"Procedure REMPLACEE (v{new_version}) : score {prev_best} -> {score}."
           if improved else
           f"Competence revisee (v{new_version}, best_score inchange={prev_best}).")
    return {"status": status, "version": new_version,
            "best_score": fiche["best_score"], "prev_score": prev_best, "message": msg}


def load_skill(nom_ou_slug: str) -> Optional[Dict]:
    """Recharge une competence par nom ou slug. Renvoie la fiche EXACTE, ou None."""
    key = (nom_ou_slug or "").strip()
    if not key:
        return None
    fiche = _read(_path_for(slugify(key)))
    if fiche:
        return fiche
    # Tolerance : recherche par nom exact insensible a la casse.
    for f in _all():
        if (f.get("nom", "").strip().lower() == key.lower()
                or f.get("slug", "") == key):
            return f
    return None


def list_skills() -> List[Dict]:
    """Liste compacte de toutes les competences (pour savoir quoi charger)."""
    return [{
        "nom": f.get("nom", "?"), "slug": f.get("slug", ""),
        "declencheur": f.get("declencheur", ""), "contexte": f.get("contexte", ""),
        "version": f.get("version", 1), "best_score": f.get("best_score"),
        "tentatives": len(f.get("history", [])),
    } for f in _all()]


def _tokens(s: str) -> set:
    return set(re.findall(r"[a-z0-9]+", (s or "").lower())) - _STOPWORDS


def find_skill(description: str) -> Optional[Tuple[Dict, float, set]]:
    """Reconnait qu'un nouveau cas releve d'une competence connue.

    Matcher DETERMINISTE (pas de LLM, pas de HNSW) : fraction des mots-cles de la
    description couverts par le declencheur + le contexte + le nom de chaque
    competence. C'est la « signature » reconnaissable que Promethee voulait, mais
    en clair et reproductible plutot que dans le brouillard vectoriel.

    Renvoie (fiche, score_de_match ∈ ]0,1], mots_communs) du meilleur candidat,
    ou None si aucun recouvrement.
    """
    q = _tokens(description)
    if not q:
        return None
    best: Optional[Tuple[Dict, float, set]] = None
    for f in _all():
        hay = _tokens(" ".join([
            f.get("declencheur", ""), f.get("contexte", ""), f.get("nom", ""),
        ]))
        inter = q & hay
        if not inter:
            continue
        score = len(inter) / len(q)
        if best is None or score > best[1]:
            best = (f, score, inter)
    return best


# ─── Formatage pour la console ───────────────────────────────────────────────

def _fmt_score(s) -> str:
    return "—" if s is None else (f"{s:g}" if isinstance(s, (int, float)) else str(s))


def format_skill(fiche: Dict) -> str:
    """Rend une fiche competence en texte lisible (rechargement fidele)."""
    lines = [
        f"[competence] « {fiche.get('nom', '?')} » (v{fiche.get('version', 1)}, "
        f"best_score={_fmt_score(fiche.get('best_score'))})",
        f"  DECLENCHEUR : {fiche.get('declencheur', '') or '—'}",
        f"  CONTEXTE    : {fiche.get('contexte', '') or '—'}",
        f"  PROCEDURE   : {fiche.get('procedure', '') or '—'}",
        f"  METRIQUE    : {fiche.get('metrique', '') or '—'}",
        f"  AJUSTEMENT  : {fiche.get('protocole_ajustement', '') or '—'}",
    ]
    hist = fiche.get("history", [])
    if hist:
        last = hist[-3:]
        lines.append("  HISTORIQUE  : " + " | ".join(
            f"v{h.get('version')}·{_fmt_score(h.get('score'))}·{h.get('note', '')}" for h in last))
    return "\n".join(lines)


def format_listing() -> str:
    skills = list_skills()
    if not skills:
        return ("[!skill_list] Aucune competence stockee pour l'instant. "
                "Resous une tache, extrais ta procedure, puis !skill_save.")
    lines = [f"[!skill_list] {len(skills)} competence(s) stockee(s) :"]
    for s in skills:
        lines.append(
            f"  • « {s['nom']} » (v{s['version']}, best={_fmt_score(s['best_score'])}, "
            f"{s['tentatives']} tentative(s))\n"
            f"      declencheur : {s['declencheur'] or '—'}")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════
# SKILL LIBRARY v2.0 — LES ALLIAGES (atelier audace, 14/06/2026)
# ═══════════════════════════════════════════════════════════════════════════
# Vision co-concue avec Promethee : une competence n'est plus seulement une
# regle isolee qu'on consulte, mais peut FONDRE avec d'autres en un ALLIAGE.
# La bibliotheque v2 stocke ces alliages EPROUVES (combinaison + contexte/
# temperature + score de stabilite) -> elle devient un « diagramme de phases »
# qui dit, AVANT d'agir, quelles fusions sont stables et lesquelles cassent.
#
# REGLE D'HONNETETE (posee par Promethee lui-meme) : on ne PREDIT pas un alliage
# jamais forge. Un alliage inconnu = « a eprouver », pas « stable par defaut ».
# Le diagramme se TRACE par l'experience (forger -> mesurer -> inscrire), exactement
# comme un vrai diagramme de phases en metallurgie — c'est la boucle d'apprentissage
# appliquee aux COMBINAISONS.

ALLOYS_DIR = os.path.join(PROJECT_ROOT, "memory", "alloys")

# Seuils du verdict, derives de la stability ∈ [0,1] (fraction de resilience :
# 1 = l'alliage fait bien mieux que ses composants seuls ; 0 = ils se nuisent).
_STABLE_THRESHOLD = 0.6
_CASSANT_THRESHOLD = 0.4

_ALLOY_LABELS = {
    "composants": "components", "composant": "components", "elements": "components",
    "ingredients": "components", "alliage": "components",
    "nom": "nom", "name": "nom",
    "procedure": "procedure", "fusion": "procedure", "mecanisme": "procedure",
    "contexte": "contexte", "temperature": "contexte", "zone": "contexte",
    "stabilite": "stability", "stability": "stability", "score": "stability",
    "verdict": "verdict",
}


def _ensure_alloys_dir() -> str:
    os.makedirs(ALLOYS_DIR, exist_ok=True)
    return ALLOYS_DIR


def _parse_components(raw: str) -> List[str]:
    """« RIGUEUR + OPTIMISATION_MONTEE_LOCALE » -> noms normalises, ordre indifferent."""
    parts = re.split(r"[+,/&]| et ", raw or "", flags=re.IGNORECASE)
    return [p.strip() for p in parts if p.strip()]


def _alloy_slug(components: List[str]) -> str:
    """Slug canonique d'un alliage : slugs des composants TRIES (ordre indifferent)."""
    slugs = sorted(slugify(c) for c in components if c.strip())
    return "__".join(slugs) or "alliage"


def _alloy_path(slug: str) -> str:
    return os.path.join(_ensure_alloys_dir(), slug + ".json")


def _all_alloys() -> List[Dict]:
    out = []
    if not os.path.isdir(ALLOYS_DIR):
        return out
    for fn in sorted(os.listdir(ALLOYS_DIR)):
        if fn.endswith(".json"):
            a = _read(os.path.join(ALLOYS_DIR, fn))
            if a:
                out.append(a)
    return out


def verdict_for(stability: Optional[float]) -> str:
    """Traduit une stability ∈ [0,1] en verdict de phase."""
    if stability is None:
        return "non_eprouve"
    if stability >= _STABLE_THRESHOLD:
        return "stable"
    if stability <= _CASSANT_THRESHOLD:
        return "cassant"
    return "fragile"


def parse_alloy_payload(text: str) -> Dict:
    """Parse une charge !forge, tolerant au format etiquete et au format pipe.

    Etiquete : COMPOSANTS: a + b / NOM: ... / PROCEDURE: ... / CONTEXTE: ... / STABILITE: 0.8
    Pipe     : composants | nom | procedure | contexte | stabilite
    """
    text = (text or "").replace("\\n", "\n").strip()
    fields = {"components_raw": "", "nom": "", "procedure": "", "contexte": "",
              "stability": None, "verdict": ""}
    if not text:
        return fields

    lines = text.split("\n")
    has_labels = any(
        _map_alloy_label(m.group(1)) for line in lines
        if (m := re.match(r"^\s*([A-Za-zÀ-ÿ_][A-Za-zÀ-ÿ_ \-]{1,30}?)\s*:\s*", line))
    )

    if has_labels:
        current, buf = None, {}
        for line in lines:
            m = re.match(r"^\s*([A-Za-zÀ-ÿ_][A-Za-zÀ-ÿ_ \-]{1,30}?)\s*:\s*(.*)$", line)
            field = _map_alloy_label(m.group(1)) if m else None
            if field:
                current = field
                buf.setdefault(field, []).append(m.group(2))
            elif current is not None:
                buf.setdefault(current, []).append(line)
        for f, parts in buf.items():
            val = "\n".join(parts).strip()
            if f == "components":
                fields["components_raw"] = val
            elif f == "stability":
                try:
                    fields["stability"] = float(val.split()[0]) if val else None
                except (ValueError, IndexError):
                    fields["stability"] = None
            else:
                fields[f] = val
        return fields

    champs = [c.strip() for c in text.split("|")]
    order = ["components_raw", "nom", "procedure", "contexte"]
    for i, key in enumerate(order):
        if i < len(champs):
            fields[key] = champs[i]
    if len(champs) > 4 and champs[4]:
        try:
            fields["stability"] = float(champs[4])
        except ValueError:
            fields["stability"] = None
    return fields


def _map_alloy_label(raw: str) -> Optional[str]:
    key = _strip_accents(raw.lower()).strip().replace(" ", "_").replace("-", "_")
    return _ALLOY_LABELS.get(key)


def forge_alloy(components: List[str], procedure: str = "", contexte: str = "",
                stability: Optional[float] = None, nom: str = "",
                verdict: Optional[str] = None) -> Dict:
    """Forge (cree ou met a jour) un alliage. Mise a jour CONDITIONNELLE a la stability
    (on ne garde que la meilleure fusion eprouvee, comme save_skill pour le score).

    Renvoie {status, slug, verdict, stability, missing_components, message}.
    """
    components = [c for c in (components or []) if c and c.strip()]
    if len(components) < 2:
        return {"status": "error", "message": "Un alliage exige au moins 2 competences a fondre."}
    slug = _alloy_slug(components)
    path = _alloy_path(slug)
    now = time.time()

    # Validation : les composants existent-ils comme competences ? (avertir, pas bloquer)
    missing = [c for c in components if load_skill(c) is None]

    if verdict is None:
        verdict = verdict_for(stability)
    existing = _read(path)

    if existing is None:
        alloy = {
            "nom": (nom or " + ".join(components)).strip(), "slug": slug,
            "components": components, "procedure": procedure.strip(),
            "contexte": contexte.strip(), "stability": stability, "verdict": verdict,
            "version": 1,
            "history": [{"ts": now, "stability": stability, "verdict": verdict, "note": "forge"}],
            "created": now, "updated": now,
        }
        _write(path, alloy)
        return {"status": "forged", "slug": slug, "verdict": verdict, "stability": stability,
                "missing_components": missing,
                "message": f"Alliage « {alloy['nom']} » forge (v1, stabilite={_fmt_score(stability)}, verdict={verdict})."}

    prev = existing.get("stability")
    if stability is not None and prev is not None and stability <= prev:
        existing.setdefault("history", []).append({
            "ts": now, "stability": stability, "verdict": verdict_for(stability),
            "note": f"refonte non retenue ({stability} <= {prev})",
        })
        existing["history"] = existing["history"][-50:]
        existing["updated"] = now
        _write(path, existing)
        return {"status": "kept", "slug": slug, "verdict": existing.get("verdict"),
                "stability": prev, "missing_components": missing,
                "message": (f"Stabilite {stability} <= meilleure connue {prev} : l'alliage "
                            f"eprouve (v{existing.get('version', 1)}) est CONSERVE.")}

    improved = stability is not None and (prev is None or stability > prev)
    new_version = (existing.get("version") or 1) + 1

    def _keep(new: str, old: str) -> str:
        new = (new or "").strip()
        return new if new else (old or "")

    alloy = {
        "nom": _keep(nom, existing.get("nom", " + ".join(components))), "slug": slug,
        "components": components,
        "procedure": _keep(procedure, existing.get("procedure", "")),
        "contexte": _keep(contexte, existing.get("contexte", "")),
        "stability": stability if improved else prev,
        "verdict": verdict_for(stability if improved else prev),
        "version": new_version,
        "history": (existing.get("history") or []) + [{
            "ts": now, "stability": stability, "verdict": verdict,
            "note": "renforce" if improved else "refonte (sans gain de stabilite)",
        }],
        "created": existing.get("created", now), "updated": now,
    }
    alloy["history"] = alloy["history"][-50:]
    _write(path, alloy)
    status = "reinforced" if improved else "revised"
    msg = (f"Alliage REforge (v{new_version}) : stabilite {prev} -> {stability}, verdict={alloy['verdict']}."
           if improved else f"Alliage revise (v{new_version}, stabilite inchangee={prev}).")
    return {"status": status, "slug": slug, "verdict": alloy["verdict"],
            "stability": alloy["stability"], "missing_components": missing, "message": msg}


def load_alloy(key: str) -> Optional[Dict]:
    """Recharge un alliage par ses composants (« a + b ») ou son slug."""
    key = (key or "").strip()
    if not key:
        return None
    comps = _parse_components(key)
    if len(comps) >= 2:
        a = _read(_alloy_path(_alloy_slug(comps)))
        if a:
            return a
    return _read(_alloy_path(slugify(key)))


def list_alloys() -> List[Dict]:
    return _all_alloys()


def audit_fusion(components: List[str]) -> Dict:
    """AUDIT PRE-FUSION : la combinaison a-t-elle deja ete eprouvee ? Que dit la carte ?

    Honnetete (regle de Promethee) : un alliage jamais forge n'est PAS predit stable —
    il est rendu « inconnu : a eprouver avant de s'y fier ».
    """
    components = [c for c in (components or []) if c and c.strip()]
    if len(components) < 2:
        return {"known": False, "verdict": "invalide",
                "message": "Donne au moins 2 competences a auditer (ex: A + B)."}
    slug = _alloy_slug(components)
    alloy = _read(_alloy_path(slug))
    if alloy is None:
        return {"known": False, "verdict": "inconnu", "slug": slug,
                "message": ("Alliage JAMAIS forge — pas sur la carte. Tu ne peux pas predire "
                            "sa stabilite : forge-le une fois (teste-le pour de vrai), mesure, "
                            "puis inscris-le. On ne devine pas un alliage non eprouve.")}
    return {"known": True, "verdict": alloy.get("verdict"),
            "stability": alloy.get("stability"), "slug": slug, "alloy": alloy,
            "message": (f"Alliage CONNU (v{alloy.get('version', 1)}) : verdict={alloy.get('verdict')}, "
                        f"stabilite={_fmt_score(alloy.get('stability'))}.")}


def format_alloy(alloy: Dict) -> str:
    icon = {"stable": "🟢", "fragile": "🟡", "cassant": "🔴", "non_eprouve": "⚪"}.get(alloy.get("verdict"), "•")
    lines = [
        f"[alliage] {icon} « {alloy.get('nom', '?')} » (v{alloy.get('version', 1)}, "
        f"verdict={alloy.get('verdict')}, stabilite={_fmt_score(alloy.get('stability'))})",
        f"  COMPOSANTS : {' + '.join(alloy.get('components', []))}",
        f"  FUSION     : {alloy.get('procedure', '') or '—'}",
        f"  CONTEXTE   : {alloy.get('contexte', '') or '—'}",
    ]
    return "\n".join(lines)


def format_phase_diagram() -> str:
    """Le DIAGRAMME DE PHASES : la carte de tous les alliages eprouves, par verdict."""
    alloys = _all_alloys()
    if not alloys:
        return ("[!phases] Diagramme de phases VIDE : aucun alliage forge. "
                "Fonds deux competences avec !forge, mesure leur stabilite, et la carte se tracera.")
    buckets = {"stable": [], "fragile": [], "cassant": [], "non_eprouve": []}
    for a in alloys:
        buckets.get(a.get("verdict", "non_eprouve"), buckets["non_eprouve"]).append(a)
    icons = {"stable": "🟢 STABLES", "fragile": "🟡 FRAGILES", "cassant": "🔴 CASSANTS", "non_eprouve": "⚪ NON EPROUVES"}
    lines = [f"[!phases] Diagramme de phases — {len(alloys)} alliage(s) sur la carte :"]
    for verdict, label in icons.items():
        group = buckets[verdict]
        if not group:
            continue
        lines.append(f"  {label} :")
        for a in group:
            lines.append(f"    • {' + '.join(a.get('components', []))} "
                         f"(stab={_fmt_score(a.get('stability'))}) — {a.get('nom', '')}")
    return "\n".join(lines)
