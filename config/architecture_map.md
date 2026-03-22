# Carte Architecturale de Promethee
# Ce fichier est lisible par Promethee via !read config/architecture_map.md
# Il decrit les modules, singletons, evenements bus et patterns cles.

## Modules principaux et singletons

| Module | Singleton | Role | Fichier |
|--------|-----------|------|---------|
| CardiacEngine | `heart` | Battement cardiaque, emotions, BPM, coherence | core/cardiac_engine.py |
| DesireEngine | `desires` | 7 pulsions homeostatiques, deprivation, satisfaction | core/desire_engine.py |
| DopamineSystem | `dopamine` | Recompense, RPE, baseline adaptatif | core/dopamine_system.py |
| PrefrontalCortex | `prefrontal` | Goals, deliberation, inhibition, veto proactif | core/prefrontal.py |
| ReptilianCore | `reptile` | Reflexes survie, threat level, freeze/fight/shed | core/reptilian_core.py |
| CorpusCallosum | `callosum` | Pont inter-organes, etat cognitif, coherence globale | core/corpus_callosum.py |
| Thalamus | `thalamus` | Filtrage attentionnel, focus, regles apprises | core/thalamus.py |
| Hippocampus | `hippocampus` | Memoire episodique, arcs narratifs, recall | core/hippocampus.py |
| SynapticNetwork | `cortex` | Reseau associatif, LIF neurones, plasticite Hebbienne | core/synaptic_network.py |
| BrainVM | `brain` | Tick unifie 30s, snapshot organes, Phi IIT | core/brain_vm.py |
| GlobalWorkspace | `workspace` | Competition pour la conscience (Baars), 7 slots max | core/global_workspace.py |
| CodeletSystem | `codelet_system` | 10 codelets d'attention LIDA, detection patterns | core/attention_codelets.py |
| ConnectivityMatrix | `matrix` | Connexions inter-organes, plasticite structurelle | core/connectivity_matrix.py |
| AutonomyEngine | `autonomy` | Routines autonomes, scoring, budget, signaux descendants | core/autonomy_engine.py |
| PSYCHE | `psyche` | Traits personnalite (7 axes), EMA audace | core/psyche.py |
| SelfAwareness | `awareness` | Snapshots periodiques, meta-reflexion | core/self_awareness.py |
| ChatEngine | `chat_engine` | Interface chat, 25+ commandes !, boucle agentique | core/chat_engine.py |
| Orchestrator | `orchestrator` | Dispatch taches vers agents, Summoner grimoire | core/orchestrator.py |
| RouterAgent | `RouterAgent` | Routage N0-N2, grimoire index, regles apprises | core/router.py |
| Hypothalamus | `hypothalamus` | Homeostasie, alarmes energie/stress/temperature | core/hypothalamus.py |
| CingulateCortex | `cingulate` | Detection conflits, monitoring erreurs | core/cingulate_cortex.py |
| BasalGanglia | `ganglia` | Habitudes, selection action, apprentissage procedural | core/basal_ganglia.py |
| Insula | `insula` | Conscience corporelle, interoception | core/insula.py |
| DefaultModeNetwork | `dmn` | Pensee vagabonde, creativite spontanee | core/default_mode_network.py |
| CircadianRhythm | `circadian` | Rythme jour/nuit, phases actives | core/circadian_rhythm.py |
| Neurochemistry | `neurochemistry` | 3 pools : serotonine (patience), noradrenaline (vigilance), acetylcholine (plasticite) | core/neurochemistry.py |
| BugAntibodies | `antibody_registry` | Systeme immunitaire — anticorps anti-bugs, scan deterministe | core/bug_antibodies.py |
| InnerVoice | `voice` | Voix interieure, soliloque, stream de conscience | core/inner_voice.py |

## Evenements bus principaux

| Evenement | Emetteur | Ecouteurs | Frequence |
|-----------|----------|-----------|-----------|
| CARDIAC_BEAT | CardiacEngine | BrainVM, tous les organes | 30s |
| BRAIN_TICK | BrainVM | GlobalWorkspace, CodeletSystem | 30s |
| AUTONOMY_ROUTINE_COMPLETE | AutonomyEngine | Hippocampus, Dopamine, Psyche, SynapticNetwork | par routine |
| SENSORIUM_FEEDBACK | SensoriumLoop | ConnectivityMatrix, Psyche, InnerVoice | post-routine |
| AGENT_RESPONSE | Orchestrator | QualityControl chain | par dispatch |
| USER_CHAT | ChatEngine | DesireEngine (CONNEXION) | par message |
| CHAT_RESPONSE | ChatEngine | Cardiac (stimulation) | par reponse |
| COUNCIL_RULE_LEARNED | Council | RouterAgent (Chunking SOAR) | sur consensus |
| AGENT_VOTE | VotingLateral | AutonomyEngine | post-succes |

## Flux principal : routine autonome

```
CARDIAC_BEAT (30s)
  -> BrainVM._on_cardiac_beat() : snapshot organes, Phi, signaux
  -> BRAIN_TICK publie
     -> GlobalWorkspace.collect_from_organs() + compete()
     -> CodeletSystem.run_all() : 10 codelets scannent patterns

AutonomyEngine._check_idle() : si idle > 5min
  -> _build_routine_pool() : 20+ routines possibles
  -> _score_routines() : scoring LIF + desire + personality + recency
  -> _select_next_routine() : top score (avec anti-stagnation)
  -> dispatch_task(agent, payload) via Orchestrator
  -> AUTONOMY_ROUTINE_COMPLETE publie
     -> SensoriumLoop feedback
     -> Hippocampus encode episode
     -> Dopamine RPE
     -> Psyche ajuste traits
```

## Flux chat (avec boucle agentique)

```
POST /api/chat : user_message
  -> ChatEngine.chat()
  -> _parse_command() : si !commande -> _execute_command()
  -> _build_system_prompt() : memoire + organes + purpose
  -> Ollama streaming -> full_response
  -> _clean_response_commands() : anti-hallucination
  -> _scan_response_actions() : execute les !commandes dans la reponse
  -> BOUCLE AGENTIQUE (max 3 iterations) :
     si auto-actions executees -> relancer LLM -> scanner -> boucler
  -> CHAT_RESPONSE publie
```

## Les 10 agents (Agents/)

| Agent | Slug | Role |
|-------|------|------|
| Strategist | strategist | COO, planification, apprentissage |
| Coder | coder | Generation de code, guardrails anti-hallucination |
| Architect | architect | Validation structurelle, audit |
| Factory | factory | Ecriture fichiers (PROTECTED_FILES) |
| Formatter | formatter | Formatage, nettoyage |
| Researcher | researcher | Recherche web, veille |
| Writer | writer | Redaction |
| Security | security | Audit securite |
| Infra | infra | Infrastructure |
| Evolution | evolution | R&D, pipeline evolution, synthese connaissance |

## Grimoire : 11+ specialistes ephemeres

Charges dynamiquement par le Summoner depuis core/grimoire/.
Index dans core/grimoire/grimoire_index.json.
Invocables via !invoke <slug> <mission>.
Creables via !craft <nom> <description>.

## Scoring des routines (autonomy_engine)

```
score = base_score (desirs + territory + personality)
      + LIF_bonus (integrate-and-fire contextuel)
      + desire_bonus (pulsion dominante [0, +3])
      + reactivity_bonus (dropzone, photos)
      - recency_penalty (repetition window 10 + cooldown temporel)
      - anti_stagnation (2+ meme intent sur 5 -> -8.0)
      - health_penalty (si DEGRADED)
      + jitter (aleatoire [-0.3, +0.3])
      CLAMP [-10, +5]
```

## Patterns cles a respecter

- Imports locaux avec try/except dans les organes (eviter import circulaire)
- Singletons avec reset_singleton() pour les tests
- Evenements bus async (await bus.publish)
- Guardrails anti-hallucination en FIN de prompt (biais de recence LLM 8B)
- _detect_alien_imports() AST pour rejeter les imports etrangers
- _PROTECTED_FILES dans Factory (11 fichiers intouchables)
- Cooldown 60s entre dispatch chat
- Max 4 auto-actions par reponse, max 3 boucles agentiques
