"""V4.3 (2026-04-23) — Scope restreint aux blocs de code executable.

Le Bloom Filter scanne desormais UNIQUEMENT les blocs ```python ... ```
(ou ``` ... ```) au lieu du prompt entier. Fix de la contamination
memoire auto-renforcee observee 22-23/04 :

- Avant V4.3 : les vetos passes (ex: 'extract_code_snippets') stockes
  dans collective_wisdom re-contaminaient le prompt via le recall RAG.
  Le Bloom les detectait dans la prose des souvenirs et re-rejetait,
  creant un cercle vicieux auto-entretenu. Un concept hallucine devenait
  un noeud synaptique permanent.

- Apres V4.3 : Le Bloom ne s'applique qu'aux Actes (code livre),
  pas aux Pensees (souvenirs narratifs, reasoning du LLM). La prose
  est libre, seul le code executable est sanctionne.

Principe : sanctionner la pensee est interdire la reflexion.
"""
import pytest

from core.bloom_filter import BloomIndexManager


@pytest.fixture
def built_manager():
    BloomIndexManager.reset_singleton()
    m = BloomIndexManager()
    m.functions.add("dispatch_task")
    m.functions.add("verify_code_review")
    m.classes.add("BaseAgent")
    m.files.add("core/base_agent.py")
    m._built = True
    return m


class TestV43ProseImmune:
    """La prose ne declenche plus aucun veto (le fix central)."""

    def test_rag_memory_with_phantom_no_veto(self, built_manager):
        """Souvenir RAG mentionnant une fonction fantome -> pas de veto.

        Ceci est le cas reel observe en prod : collective_wisdom ramenait
        des traces comme 'J'ai essaye extract_code_snippets() mais veto',
        et ce souvenir contaminait le prompt suivant.
        """
        prompt = (
            "[SOUVENIRS]\n"
            "J'ai essaye extract_code_snippets() hier, sans succes. "
            "Le veto Bloom a bloque.\n"
            "---\n"
            "Mission actuelle : ameliorer core/prefrontal.py."
        )
        veto = built_manager.check_prompt("test", prompt)
        assert veto is None, \
            "La prose des souvenirs ne doit plus declencher de veto (V4.3)"

    def test_reasoning_with_phantom_function_no_veto(self, built_manager):
        """Prose narrative mentionnant une fonction fantome -> pas de veto."""
        prompt = (
            "Je vais utiliser ghost_function() pour extraire les donnees, "
            "puis appeler another_phantom_method() pour les analyser. "
            "En fait non, c'est pas une bonne idee."
        )
        veto = built_manager.check_prompt("test", prompt)
        assert veto is None

    def test_french_words_never_trigger(self, built_manager):
        """Mots francais courants (cours, classe, contenu) -> jamais de veto."""
        prompts = [
            "Pendant le cours aujourd'hui, on a vu la classe des mammiferes.",
            "Le contenu du message est important.",
            "Si tu detectes un probleme, signale-le.",
        ]
        for p in prompts:
            assert built_manager.check_prompt("test", p) is None


class TestV43CodeBlocksActive:
    """Les blocs de code sont scannes et peuvent declencher des vetos."""

    def test_phantom_function_in_code_block_veto(self, built_manager):
        """Fonction fantome DANS bloc code -> veto."""
        prompt = (
            "Voici le code :\n"
            "```python\n"
            "result = extract_code_snippets(file)\n"
            "```"
        )
        veto = built_manager.check_prompt("test", prompt)
        assert veto is not None
        assert veto.ref_name == "extract_code_snippets"

    def test_valid_function_in_code_block_no_veto(self, built_manager):
        """Fonction valide dans bloc code -> pas de veto."""
        prompt = (
            "```python\n"
            "dispatch_task(payload)\n"
            "```"
        )
        assert built_manager.check_prompt("test", prompt) is None

    def test_code_block_without_python_tag(self, built_manager):
        """```...``` sans tag langue est aussi scanne."""
        prompt = (
            "```\n"
            "phantom_func_xyz()\n"
            "```"
        )
        veto = built_manager.check_prompt("test", prompt)
        assert veto is not None

    def test_mixed_prose_and_code(self, built_manager):
        """Prose avec phantom + code propre -> pas de veto.

        La prose mentionne une fonction fantome mais le code livre
        est propre. Seul le code compte.
        """
        prompt = (
            "Je pensais utiliser a_made_up_function() mais en fait j'utilise\n"
            "```python\n"
            "dispatch_task(payload)\n"
            "```\n"
            "qui existe vraiment."
        )
        assert built_manager.check_prompt("test", prompt) is None

    def test_mixed_clean_prose_dirty_code(self, built_manager):
        """Prose propre + code fantome -> veto sur le code."""
        prompt = (
            "Je vais utiliser dispatch_task qui existe.\n"
            "```python\n"
            "phantom_xyz_fn()\n"
            "```"
        )
        veto = built_manager.check_prompt("test", prompt)
        assert veto is not None
        assert veto.ref_name == "phantom_xyz_fn"


class TestV43ExtractReferences:
    """extract_references retourne vide pour la prose pure."""

    def test_pure_prose_returns_empty(self):
        BloomIndexManager.reset_singleton()
        m = BloomIndexManager()
        refs = m.extract_references(
            "Voici une longue prose avec ma_fonction() et `MaClasse` "
            "mentionnes mais pas dans un bloc de code."
        )
        assert refs["functions"] == []
        assert refs["classes"] == []
        assert refs["files"] == []

    def test_code_block_extracts_normally(self):
        BloomIndexManager.reset_singleton()
        m = BloomIndexManager()
        refs = m.extract_references(
            "```python\nma_fonction(args)\n```"
        )
        assert "ma_fonction" in refs["functions"]

    def test_empty_code_block_returns_empty(self):
        BloomIndexManager.reset_singleton()
        m = BloomIndexManager()
        refs = m.extract_references("```python\n\n```")
        assert refs["functions"] == []
        assert refs["classes"] == []


class TestV43RegressionScenarioProduction:
    """Scenario reel reproduisant le bug du 22-23/04 (contamination RAG)."""

    def test_rag_contamination_scenario(self, built_manager):
        """Reconstitue le prompt probable qui contaminait SCHOOL_CREATION.

        Contexte reel : mission 'ameliorer core/prefrontal.py', et le
        recall RAG ramenait un souvenir mentionnant extract_code_snippets
        comme ayant ete tentee et veto. V4.3 doit laisser passer ce prompt
        car il contient uniquement de la prose (pas de bloc code).
        """
        contaminated_prompt = (
            "Mission : ameliorer core/prefrontal.py\n\n"
            "[SOUVENIRS RAG]\n"
            "- Hier 03:36, cours CREATION, note 4.0/10. J'avais essaye "
            "d'utiliser extract_code_snippets() mais le veto Bloom V4.2 "
            "s'est declenche car la fonction n'est pas dans l'index.\n"
            "- Avant-hier, meme probleme : appel a extract_code_snippets "
            "bloque.\n\n"
            "Consigne : propose 3 ameliorations concretes au fichier."
        )
        veto = built_manager.check_prompt("test", contaminated_prompt)
        assert veto is None, (
            "V4.3 doit ignorer les mentions de fonctions dans les souvenirs "
            "RAG (prose), sinon la boucle de contamination se perpetue."
        )
