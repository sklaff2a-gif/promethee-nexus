# tests/test_chat_antiboucle.py — Anti-boucle V2 du chat (_cut_repetition_loop)
#
# Contexte (11/06/2026 soir) : l'anti-boucle V1 inline de _run_chat a amputé
# 3 réponses légitimes pendant la session de maths (logs 19:56, 20:28, 20:43
# « Boucle detectee — reponse coupee a la ligne N »). Cause : un raisonnement
# itératif honnête (« Attends... Rectifions ») répète les mêmes en-têtes de
# structure → faux positif. Bug aggravant : index calculé sur la liste filtrée
# (lignes > 20 chars) mais coupe appliquée à une autre liste.
import pytest
from unittest.mock import patch

from core.chat_engine import ChatEngine


@pytest.fixture(autouse=True)
def reset_chat_engine():
    ChatEngine.reset_singleton()
    yield
    ChatEngine.reset_singleton()


def cut(text):
    return ChatEngine._cut_repetition_loop(text)


# --- Faux positifs historiques : ne DOIVENT PLUS couper ---

class TestRaisonnementIteratifLegitime:

    def test_rattrapage_simpson_du_11_06_non_coupe(self):
        """Fixture réelle (troncature 20:28) : auto-correction qui répète les
        en-têtes de structure entre deux tentatives. Ne doit PAS être coupée."""
        texte = (
            "Jean-Michel, je reprends là où mon processus s'est interrompu.\n"
            "**Rattrapage : Le vrai paradoxe de Simpson (Corrigé)**\n"
            "Pour un paradoxe pur, une routine doit être supérieure dans chaque période.\n"
            "*   **Période 1 (Petit échantillon) :**\n"
            "    *   ALPHA : $9 / 10 = 90 \\%$\n"
            "    *   BETA : $8 / 10 = 80 \\%$\n"
            "*   **Période 2 (Grand échantillon) :**\n"
            "    *   ALPHA : $160 / 200 = 80 \\%$\n"
            "    *   BETA : $170 / 200 = 85 \\%$\n"
            "*Attends, je dois encore ajuster pour que l'inversion soit réelle.*\n"
            "**Tentative finale (Paradoxe Pur) :**\n"
            "*   **Période 1 (Petit échantillon) :**\n"
            "    *   ALPHA : $9 / 10 = 90 \\%$\n"
            "    *   BETA : $8 / 10 = 80 \\%$\n"
            "*   **Période 2 (Grand échantillon) :**\n"
            "    *   ALPHA : $40 / 50 = 80 \\%$\n"
            "Non, là ALPHA gagne toujours. Il faut une inversion de poids.\n"
            "**Le jeu de données correct :**\n"
            "*   ALPHA : $30 / 100 = 30 \\%$ sur les tâches difficiles.\n"
        )
        resultat, cut_line = cut(texte)
        assert cut_line == -1
        assert resultat == texte

    def test_formule_repetee_enonce_puis_calcul_non_coupe(self):
        """Fixture type 1.1 (troncature 19:56) : la même formule apparaît dans
        l'énoncé rappelé puis dans le calcul. Une seule répétition non
        consécutive = pas une boucle."""
        texte = (
            "Jean-Michel, je traite cet exercice avec rigueur et méthode.\n"
            "La formule est : context_factor = 0.5 + 1.5 * min(1.0, relevance)\n"
            "Puisque 0.42 < 1.0, la fonction min renvoie la valeur 0.42 directement.\n"
            "Le résultat du calcul est donc 1.13 pour cette première question.\n"
            "La formule est : context_factor = 0.5 + 1.5 * min(1.0, relevance)\n"
            "Avec relevance = 2.7, le min plafonne à 1.0 et le résultat vaut 2.0.\n"
            "Enseignement : les bornes protègent les systèmes vivants de l'excès.\n"
        )
        resultat, cut_line = cut(texte)
        assert cut_line == -1
        assert resultat == texte

    def test_listes_markdown_repetees_non_coupees(self):
        """Les puces/titres/maths se répètent légitimement — transparents."""
        texte = (
            "Voici la comparaison détaillée des deux périodes étudiées.\n"
            "* **Période 1 :** données initiales du premier échantillon mesuré\n"
            "* **Période 2 :** données complémentaires du second échantillon\n"
            "Et maintenant la même structure pour la seconde routine analysée.\n"
            "* **Période 1 :** données initiales du premier échantillon mesuré\n"
            "* **Période 2 :** données complémentaires du second échantillon\n"
            "Conclusion : les structures se répètent mais le raisonnement avance.\n"
        )
        resultat, cut_line = cut(texte)
        assert cut_line == -1


# --- Vraies boucles : DOIVENT couper ---

class TestVraiesBoucles:

    def test_boucle_prose_deux_lignes_consecutives_repetees(self):
        """Deux lignes de prose consécutives déjà vues = boucle → coupe."""
        texte = (
            "Je vais analyser cette situation avec attention et précision.\n"
            "Le système montre des signes de stabilité remarquable aujourd'hui.\n"
            "Les métriques confirment cette tendance positive et durable.\n"
            "Je vais analyser cette situation avec attention et précision.\n"
            "Le système montre des signes de stabilité remarquable aujourd'hui.\n"
            "Les métriques confirment cette tendance positive et durable.\n"
        )
        resultat, cut_line = cut(texte)
        assert cut_line == 3
        assert resultat.count("Je vais analyser") == 1

    def test_boucle_lignes_identiques_consecutives(self):
        """3 lignes strictement identiques d'affilée = dégénérescence → coupe
        (garde la première occurrence)."""
        texte = (
            "Voici ma réponse structurée à ta question initiale posée.\n"
            "* Je suis prêt à continuer notre travail ensemble.\n"
            "* Je suis prêt à continuer notre travail ensemble.\n"
            "* Je suis prêt à continuer notre travail ensemble.\n"
            "* Je suis prêt à continuer notre travail ensemble.\n"
        )
        resultat, cut_line = cut(texte)
        assert cut_line == 2
        assert resultat.count("Je suis prêt") == 1

    def test_boucle_blocs_entrelaces_avec_puces(self):
        """Bloc prose+puce répété : les puces sont transparentes, les deux
        lignes de prose répétées restent consécutives → coupe."""
        texte = (
            "Premier paragraphe d'analyse contenant des éléments importants.\n"
            "* un détail listé\n"
            "Second paragraphe qui développe la pensée de façon approfondie.\n"
            "Premier paragraphe d'analyse contenant des éléments importants.\n"
            "* un détail listé\n"
            "Second paragraphe qui développe la pensée de façon approfondie.\n"
        )
        resultat, cut_line = cut(texte)
        assert cut_line == 3


# --- Alignement d'index (bug historique) ---

class TestAlignementIndex:

    def test_coupe_dans_les_coordonnees_du_texte_original(self):
        """Bug V1 : index calculé sur la liste filtrée (>20 chars) mais appliqué
        à la liste complète → coupait trop tôt. La coupe doit préserver tout ce
        qui précède la boucle, y compris lignes courtes et vides."""
        texte = (
            "ok\n"
            "\n"
            "court\n"
            "Première ligne de prose suffisamment longue pour être éligible.\n"
            "Deuxième ligne de prose également assez longue pour le filtre.\n"
            "Première ligne de prose suffisamment longue pour être éligible.\n"
            "Deuxième ligne de prose également assez longue pour le filtre.\n"
        )
        resultat, cut_line = cut(texte)
        # La boucle commence ligne 5 (0-based) : tout ce qui précède survit
        assert "ok" in resultat
        assert "court" in resultat
        assert resultat.count("Première ligne") == 1
        assert resultat.count("Deuxième ligne") == 1

    def test_texte_court_jamais_coupe(self):
        texte = "Bonjour Jean-Michel.\nTout va bien aujourd'hui dans mes circuits.\n"
        resultat, cut_line = cut(texte)
        assert cut_line == -1
        assert resultat == texte
