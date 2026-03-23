// Token API (lire depuis meta tag ou localStorage)
const API_TOKEN = localStorage.getItem('api_token') || '';
const wsUrl = API_TOKEN
    ? `ws://${window.location.host}/ws?token=${API_TOKEN}`
    : `ws://${window.location.host}/ws`;
const ws = new WebSocket(wsUrl);

function authHeaders() {
    const h = { 'Content-Type': 'application/json' };
    if (API_TOKEN) h['Authorization'] = `Bearer ${API_TOKEN}`;
    return h;
}
const dialogueBox = document.getElementById('dialogue-box');
const logsBox = document.getElementById('logs-box');
const chatMessages = document.getElementById('chat-messages');
const activeStreams = {};
const activeChatStreams = {};
const recentlyStreamed = new Set();
let chatPanelOpen = false;

// FONCTION: Formater le code pour l'affichage
function formatMessage(text) {
    if (typeof text === 'object') text = JSON.stringify(text, null, 2);
    if (text.includes('```')) {
        text = text.replace(/```(?:python|json|html)?\s*([\s\S]*?)```/g, '<pre>$1</pre>');
    }
    return text.replace(/\n/g, '<br>');
}

// FONCTION: Ajouter un message au dialogue principal
function addDialogue(sender, text, type) {
    const div = document.createElement('div');
    
    // Traitement du code
    let htmlContent = "";
    if (text && text.includes('```')) {
       const parts = text.split('```');
       parts.forEach((part, index) => {
           if (index % 2 === 1) htmlContent += `<pre>${part}</pre>`;
           else htmlContent += part.replace(/\n/g, '<br>');
       });
    } else {
       htmlContent = text ? text.replace(/\n/g, '<br>') : "";
    }

    if (type === 'user') {
        div.className = 'msg-user';
        div.innerHTML = `<div>${htmlContent}</div>`;
    } else if (type === 'system') {
        div.className = 'msg-system';
        div.innerHTML = `<div>${htmlContent}</div>`;
    } else {
        div.className = 'msg-agent';
        div.innerHTML = `<span class="font-bold text-xs mb-1 block opacity-50">[${sender}]</span><div>${htmlContent}</div>`;
    }
    dialogueBox.appendChild(div);
    dialogueBox.scrollTop = dialogueBox.scrollHeight;
}

// FONCTION: Ajouter une ligne aux logs système (colonne centrale bas)
function addLog(source, text, level) {
    const div = document.createElement('div');
    div.className = 'log-entry';
    
    if (level === 'err') div.classList.add('log-err');
    else if (level === 'sys') div.classList.add('log-sys');
    else if (level === 'success') div.classList.add('log-success');
    
    const time = new Date().toLocaleTimeString('fr-FR');
    if (text.length > 150) text = text.substring(0, 150) + "...";
    
    div.innerHTML = `<span class="text-gray-600">[${time}]</span> <span class="opacity-70">[${source}]</span> <span>${text}</span>`;
    
    logsBox.appendChild(div);
    logsBox.scrollTop = logsBox.scrollHeight;
}

// FONCTION: Allumer l'agent dans la barre latérale
function highlightAgent(name) {
    if (!name) return;
    const slug = name.toLowerCase();
    const el = document.getElementById(`agent-${slug}`);
    if (el) {
        // Active le style
        el.querySelector('span:first-child').classList.add('agent-active');
        const dot = el.querySelector('span:last-child');
        dot.classList.remove('dot-inactive');
        dot.classList.add('dot-active');

        // Éteint après 2 secondes
        setTimeout(() => {
            el.querySelector('span:first-child').classList.remove('agent-active');
            dot.classList.remove('dot-active');
            dot.classList.add('dot-inactive');
        }, 2000);
    }
}

// GESTIONNAIRE D'ÉVÉNEMENTS WEBSOCKET (LE CŒUR VISUEL)
ws.onmessage = (event) => {
    const data = JSON.parse(event.data);
    const type = data.type;
    const payload = data.payload || {};

    // 1. Démarrage de tâche (Allumage Agent)
    if (type === "AGENT_TASK_DISPATCH") {
        highlightAgent(payload.target);
        addLog('ROUTER', `Mission envoyée vers -> ${payload.target.toUpperCase()}`, 'sys');
    }
    // 2. Flux de pensée (Thought Stream) — affiché dans le panneau flux
    else if (type === "THOUGHT_STREAM") {
        highlightAgent(payload.agent);
        if (payload.type === 'error') {
            addDialogue(payload.agent.toUpperCase(), payload.content, 'system');
            addLog(payload.agent, payload.content, 'err');
        } else {
            addDialogue(payload.agent.toUpperCase(), payload.content, 'system');
        }
    } 
    // 2b. Streaming temps réel (tokens progressifs)
    else if (type === "AGENT_STREAM") {
        highlightAgent(payload.agent);
        const sid = payload.stream_id;

        if (payload.status === "start") {
            // Créer la bulle de streaming
            const div = document.createElement('div');
            div.className = 'msg-agent';
            div.id = `stream-${sid}`;
            div.innerHTML = `<span class="font-bold text-xs mb-1 block opacity-50">[${payload.agent.toUpperCase()}]</span><div class="stream-content"></div><span class="cursor">|</span>`;
            dialogueBox.appendChild(div);
            activeStreams[sid] = div;
        } else if (payload.done) {
            // Fin du stream : retirer le curseur, reformater le contenu
            const div = activeStreams[sid];
            if (div) {
                const cursorEl = div.querySelector('.cursor');
                if (cursorEl) cursorEl.remove();
                const contentEl = div.querySelector('.stream-content');
                if (contentEl) {
                    const raw = contentEl.textContent;
                    let html = "";
                    if (raw.includes('```')) {
                        const parts = raw.split('```');
                        parts.forEach((part, index) => {
                            if (index % 2 === 1) html += `<pre>${part}</pre>`;
                            else html += part.replace(/\n/g, '<br>');
                        });
                    } else {
                        html = raw.replace(/\n/g, '<br>');
                    }
                    contentEl.innerHTML = html;
                }
                delete activeStreams[sid];
            }
            // Marquer l'agent comme récemment streamé (TTL 3s)
            recentlyStreamed.add(payload.agent);
            setTimeout(() => recentlyStreamed.delete(payload.agent), 3000);
        } else if (payload.chunk) {
            // Chunk intermédiaire : appender le texte
            const div = activeStreams[sid];
            if (div) {
                const contentEl = div.querySelector('.stream-content');
                if (contentEl) contentEl.textContent += payload.chunk;
                dialogueBox.scrollTop = dialogueBox.scrollHeight;
            }
        }
    }
    // 3. Réponse finale d'un agent
    else if (type === "AGENT_RESPONSE") {
        highlightAgent(payload.agent);
        // Si l'agent vient de streamer, ne pas re-créer la bulle (contenu déjà affiché)
        if (!recentlyStreamed.has(payload.agent)) {
            addDialogue(payload.agent.toUpperCase(), payload.content, 'agent');
        }
        addLog(payload.agent, "Tâche terminée avec succès", 'success');
    }
    // 4. Création de fichier (FACTORY SUCCESS)
    else if (type === "ARTIFACT_CREATED") {
        highlightAgent('factory');
        addDialogue('SYSTEM', `✨ NOUVEAU FICHIER CRÉÉ : ${payload.filename}`, 'system');
        addLog('FACTORY', `Ecriture disque: ${payload.filepath}`, 'success');
    }
    // 5. Commande Utilisateur
    else if (type === "USER_COMMAND") {
        addDialogue("COMMANDER", payload.mission, 'user');
    }
    // 6. COUNCIL_START : ouverture du débat
    else if (type === "COUNCIL_START") {
        const agents = payload.participants.map(a => a.toUpperCase()).join(", ");
        addDialogue("SYSTEM",
            `CONSEIL OUVERT [${agents}] - "${payload.mission}" (max ${payload.max_rounds} tours)`,
            'system');
        payload.participants.forEach(a => highlightAgent(a));
    }
    // 7. COUNCIL_TURN : tour de parole
    else if (type === "COUNCIL_TURN") {
        highlightAgent(payload.agent);
        if (!recentlyStreamed.has(payload.agent)) {
            addDialogue(payload.agent.toUpperCase(),
                `[CONSEIL Tour ${payload.round}/${payload.max_rounds}]\n${payload.content}`,
                'agent');
        }
        addLog(payload.agent, `Conseil T${payload.round}: contribution envoyée`, 'info');
    }
    // 8. COUNCIL_END : fermeture du débat
    else if (type === "COUNCIL_END") {
        const verdict = payload.status === "consensus" ? "CONSENSUS ATTEINT" : "LIMITE DE TOURS";
        addDialogue("SYSTEM",
            `CONSEIL FERME [${verdict}] après ${payload.rounds_used} tour(s).`,
            'system');
    }
    // 9. CI_PIPELINE_START : démarrage pipeline CI/CD
    else if (type === "CI_PIPELINE_START") {
        addDialogue("SYSTEM",
            `CI/CD Pipeline démarré pour : ${payload.filename}`,
            'system');
        addLog("CI/CD", `Pipeline démarré : ${payload.filename}`, "sys");
    }
    // 10. CI_PIPELINE_STEP : étape intermédiaire
    else if (type === "CI_PIPELINE_STEP") {
        const level = payload.status === "error" ? "err" : payload.status === "success" ? "success" : "sys";
        addLog("CI/CD", `[${payload.step}] ${payload.status.toUpperCase()} - ${payload.filename} : ${payload.detail || ''}`, level);
    }
    // 11. CI_PIPELINE_RESULT : résultat final
    else if (type === "CI_PIPELINE_RESULT") {
        const verdict = payload.success ? "DÉPLOYÉ" : "ROLLBACK";
        const level = payload.success ? "success" : "err";
        addDialogue("SYSTEM",
            `CI/CD [${verdict}] ${payload.filename} : ${payload.detail || ''}`,
            'system');
        addLog("CI/CD", `RÉSULTAT [${verdict}] ${payload.filename}`, level);
    }
    // 12. PSYCHE_UPDATE : mise à jour du radar de personnalité
    else if (type === "PSYCHE_UPDATE") {
        if (payload.system_average && typeof psycheChartInstance !== 'undefined') {
            const avg = payload.system_average;
            psycheChartInstance.data.datasets[0].data = [
                avg.curiosite, avg.creativite, avg.audace, avg.savoir, avg.survie, avg.respect
            ];
            psycheChartInstance.update();
        }
    }
    // 13. CARDIAC_BEAT : overlay cardiaque VISION + télémétrie BPM + avatar émotion
    else if (type === "CARDIAC_BEAT") {
        if (typeof NeuralVision !== 'undefined') NeuralVision.handleCardiacBeat(payload);
        // Télémétrie BPM
        updateTelemBpm(payload);
        // Avatar dynamique selon émotion
        if (payload.emotion) updateAvatar(payload.emotion);
    }
    // 14. SYNAPTIC_UPDATE : mise à jour graphe neural VISION
    else if (type === "SYNAPTIC_UPDATE") {
        if (typeof NeuralVision !== 'undefined') NeuralVision.handleSynapticUpdate(payload);
    }
    // 15. REPTILIAN_STATE : télémétrie CPU/RAM/Ollama/Threat
    else if (type === "REPTILIAN_STATE") {
        updateTelemReptilian(payload);
    }
    // 16. IMPACT_UPDATE : invalidation graphe dépendances
    else if (type === "IMPACT_UPDATE") {
        if (typeof ImpactView !== 'undefined') ImpactView.handleUpdate(payload);
    }
    // 17. CHAT_STREAM : streaming tokens du chat direct
    else if (type === "CHAT_STREAM") {
        const sid = payload.stream_id;

        if (payload.status === "start") {
            // Detecter si la reponse est emergente (organes actifs)
            const emergent = payload.emergent_sources && payload.emergent_sources.length > 0;
            const div = document.createElement('div');

            if (emergent) {
                // Style manuscrit pour les pensees emergentes
                div.className = 'msg-chat-emergent';
                div.id = `chat-stream-${sid}`;
                const sourcesText = payload.emergent_sources.join(', ');
                div.innerHTML = `<span class="emergent-badge">[PROMETHEE — ${sourcesText}]</span><div class="stream-content"></div><span class="emergent-cursor">|</span>`;
            } else {
                // Style standard pour les reponses LLM
                div.className = 'msg-chat-bot';
                div.id = `chat-stream-${sid}`;
                div.innerHTML = `<span class="font-bold text-xs mb-1 block opacity-50" style="color:#ffa500;">[PROMETHEE]</span><div class="stream-content"></div><span class="chat-cursor">|</span>`;
            }
            chatMessages.appendChild(div);
            activeChatStreams[sid] = div;
            chatMessages.scrollTop = chatMessages.scrollHeight;
        } else if (payload.done) {
            // Fin du stream
            const div = activeChatStreams[sid];
            if (div) {
                const cursorEl = div.querySelector('.chat-cursor') || div.querySelector('.emergent-cursor');
                if (cursorEl) cursorEl.remove();
                const contentEl = div.querySelector('.stream-content');
                if (contentEl) {
                    const raw = contentEl.textContent;
                    let html = "";
                    if (raw.includes('```')) {
                        const parts = raw.split('```');
                        parts.forEach((part, index) => {
                            if (index % 2 === 1) html += `<pre>${part}</pre>`;
                            else html += part.replace(/\n/g, '<br>');
                        });
                    } else {
                        html = raw.replace(/\n/g, '<br>');
                    }
                    contentEl.innerHTML = html;
                }
                delete activeChatStreams[sid];
            }
        } else if (payload.chunk) {
            // Chunk intermediaire
            const div = activeChatStreams[sid];
            if (div) {
                const contentEl = div.querySelector('.stream-content');
                if (contentEl) contentEl.textContent += payload.chunk;
                chatMessages.scrollTop = chatMessages.scrollHeight;
            }
        }
    }
    // 18. CHAT_RESPONSE : log dans les logs systeme
    else if (type === "CHAT_RESPONSE") {
        var connBefore = payload.connexion_before ? Math.round(payload.connexion_before) : '?';
        var connAfter = payload.connexion_after ? Math.round(payload.connexion_after) : '?';
        addLog("CHAT", `Reponse envoyee (CONNEXION ${connBefore} -> ${connAfter})`, "success");
    }
};

// --- CHAT DIRECT ---

function toggleChatPanel() {
    chatPanelOpen = !chatPanelOpen;
    const fluxPanel = document.getElementById('flux-panel');
    const chatPanel = document.getElementById('chat-panel');
    const missionBar = document.getElementById('mission-bar');
    const chatBar = document.getElementById('chat-bar');
    const btn = document.getElementById('btn-chat-toggle');

    if (chatPanelOpen) {
        fluxPanel.classList.add('hidden');
        chatPanel.classList.remove('hidden');
        missionBar.classList.add('hidden');
        chatBar.classList.remove('hidden');
        btn.textContent = 'FLUX';
        btn.className = 'border px-3 py-0 text-[10px] font-bold hover:opacity-80 transition h-6';
        btn.style.borderColor = '#ffa500';
        btn.style.color = '#ffa500';
        btn.style.background = '#1a0a00';
        document.getElementById('chat-input').focus();
        // Charger l'historique au premier ouverture
        loadChatHistory();
        // Charger les credits salary
        refreshSalaryCredits();
    } else {
        fluxPanel.classList.remove('hidden');
        chatPanel.classList.add('hidden');
        missionBar.classList.remove('hidden');
        chatBar.classList.add('hidden');
        btn.textContent = 'CHAT';
        btn.className = 'border border-orange-700 text-orange-400 px-3 py-0 text-[10px] font-bold hover:bg-orange-800 hover:text-white transition h-6';
        btn.style.background = '';
    }
}

function addChatMessage(sender, text, type, emergentSources) {
    const div = document.createElement('div');
    let htmlContent = "";
    if (text && text.includes('```')) {
        const parts = text.split('```');
        parts.forEach((part, index) => {
            if (index % 2 === 1) htmlContent += `<pre>${part}</pre>`;
            else htmlContent += part.replace(/\n/g, '<br>');
        });
    } else {
        htmlContent = text ? text.replace(/\n/g, '<br>') : "";
    }

    if (type === 'user') {
        div.className = 'msg-chat-user';
        div.innerHTML = `<div>${htmlContent}</div>`;
    } else if (emergentSources && emergentSources.length > 0) {
        // Style emergent — pensees authentiques de Promethee (organes actifs)
        div.className = 'msg-chat-emergent';
        const sourcesText = emergentSources.join(', ');
        div.innerHTML = `<span class="emergent-badge">[PROMETHEE — ${sourcesText}]</span><div>${htmlContent}</div>`;
    } else {
        div.className = 'msg-chat-bot';
        div.innerHTML = `<span class="font-bold text-xs mb-1 block opacity-50" style="color:#ffa500;">[PROMETHEE]</span><div>${htmlContent}</div>`;
    }
    chatMessages.appendChild(div);
    chatMessages.scrollTop = chatMessages.scrollHeight;
}

function sendChat() {
    const input = document.getElementById('chat-input');
    const msg = input.value.trim();
    if (!msg) return;

    addChatMessage("VOUS", msg, "user");
    input.value = "";

    fetch('/api/chat', {
        method: 'POST',
        headers: authHeaders(),
        body: JSON.stringify({ message: msg })
    }).catch(err => addLog("CHAT", "Erreur: " + err, "err"));
}

function loadChatHistory() {
    // Ne charger qu'une fois si des messages existent deja
    if (chatMessages.children.length > 1) return;

    fetch('/api/chat/history', { headers: authHeaders() })
        .then(r => r.json())
        .then(data => {
            if (!data.messages || data.messages.length === 0) return;
            // Vider le placeholder
            chatMessages.innerHTML = '';
            // Afficher les 30 derniers messages
            const recent = data.messages.slice(-30);
            recent.forEach(msg => {
                addChatMessage(
                    msg.role === 'user' ? 'VOUS' : 'PROMETHEE',
                    msg.content,
                    msg.role === 'user' ? 'user' : 'bot',
                    msg.emergent_sources || null
                );
            });
        })
        .catch(() => {});
}

// --- WISHLIST & SALARY ---

let wishlistOpen = false;

function toggleWishlistPanel() {
    wishlistOpen = !wishlistOpen;
    const panel = document.getElementById('wishlist-panel');
    if (wishlistOpen) {
        panel.classList.remove('hidden');
        loadSalaryStatus();
    } else {
        panel.classList.add('hidden');
    }
}

function loadSalaryStatus() {
    fetch('/api/salary/status', { headers: authHeaders() })
        .then(r => r.json())
        .then(data => {
            if (data.error) return;
            // Mettre a jour les credits dans le header
            const creditsEl = document.getElementById('salary-credits');
            if (creditsEl) creditsEl.textContent = `${data.credits || 0} credits`;
            // Mettre a jour les details
            const detailEl = document.getElementById('salary-detail');
            if (detailEl && data.salary_projection) {
                const sp = data.salary_projection;
                detailEl.innerHTML = `
                    Base: ${sp.base} | Bonus: +${sp.bonus_quality} | Malus: -${sp.malus_echecs}<br>
                    Taches: ${sp.tasks_completed} OK, ${sp.tasks_failed} echecs<br>
                    Photos vues: ${sp.photos_consumed} cette semaine<br>
                    Total gagne: ${data.total_earned || 0} | Depense: ${data.total_spent || 0}
                `;
            }
            // Rendre la wishlist (utiliser la version detaillee si disponible)
            renderWishlist(data.wishlist_detailed || [], data.salary_history || []);
        })
        .catch(() => {});
}

function renderWishlist(wishlist, history) {
    const container = document.getElementById('wishlist-items');
    if (!container) return;
    container.innerHTML = '';
    if (wishlist.length === 0) {
        container.innerHTML = '<div class="text-[9px]" style="color:#666;">Aucun souhait. Tape !souhait nature dans le chat.</div>';
        return;
    }
    wishlist.forEach(wish => {
        const div = document.createElement('div');
        const category = wish.category || wish;
        const status = wish.status || 'en_attente';
        let statusClass, statusText;
        if (status === 'visualiser') {
            statusClass = 'wish-fulfilled';
            statusText = 'VISUALISER';
        } else if (status === 'relance') {
            statusClass = 'wish-relance';
            statusText = 'RELANCE';
        } else {
            statusClass = 'wish-pending';
            statusText = 'EN ATTENTE';
        }
        div.className = `wish-item ${statusClass}`;
        div.innerHTML = `<span>${category}</span><span class="text-[8px]">${statusText}</span>`;
        container.appendChild(div);
    });
}

function addWish() {
    const input = document.getElementById('wish-input');
    const category = input.value.trim();
    if (!category) return;
    fetch('/api/salary/wish', {
        method: 'POST',
        headers: authHeaders(),
        body: JSON.stringify({ category: category })
    })
        .then(r => r.json())
        .then(data => {
            if (data.wishlist) {
                renderWishlist(data.wishlist, []);
            }
            input.value = '';
        })
        .catch(() => {});
}

// Charger les credits au demarrage du chat
function refreshSalaryCredits() {
    fetch('/api/salary/status', { headers: authHeaders() })
        .then(r => r.json())
        .then(data => {
            const el = document.getElementById('salary-credits');
            if (el && data.credits !== undefined) el.textContent = `${data.credits} credits`;
        })
        .catch(() => {});
}

function sendMission() {
    const input = document.getElementById('mission-input');
    const mission = input.value.trim();
    if (!mission) return;
    
    // Feedback immédiat UI
    // addDialogue("COMMANDER", mission, 'user'); (Géré par le retour WS USER_COMMAND)
    
    fetch('/api/mission', {
        method: 'POST',
        headers: authHeaders(),
        body: JSON.stringify({ mission: mission })
    }).catch(err => addLog("API", "Erreur: " + err, "err"));
    input.value = "";
}

function triggerReboot() {
    if (!confirm('Redémarrer Prométhée ?')) return;
    fetch('/api/reboot', { method: 'POST', headers: authHeaders(), body: JSON.stringify({}) });
    addLog('SYSTEM', 'REBOOT EN COURS...', 'sys');
    // Poll /health jusqu'à ce que le serveur revienne, puis reload
    setTimeout(() => {
        const poll = setInterval(() => {
            fetch('/health').then(r => {
                if (r.ok) { clearInterval(poll); location.reload(); }
            }).catch(() => {});
        }, 2000);
    }, 3000);
}

// --- Mode Sieste (hibernation 0-GPU) ---
let napModeActive = false;

function toggleNapMode() {
    const newState = !napModeActive;
    fetch('/api/sieste', {
        method: 'POST',
        headers: authHeaders(),
        body: JSON.stringify({ enabled: newState })
    }).then(r => r.json()).then(data => {
        napModeActive = data.is_napping;
        updateNapButton();
        addLog('SYSTEM', napModeActive ? 'MODE SIESTE ACTIVÉ — GPU libéré' : 'RÉVEIL — reprise normale', napModeActive ? 'sys' : 'success');
    }).catch(err => addLog('API', 'Erreur sieste: ' + err, 'err'));
}

function updateNapButton() {
    const btn = document.getElementById('nap-button');
    const banner = document.getElementById('nap-banner');
    if (!btn) return;
    if (napModeActive) {
        btn.textContent = 'RÉVEIL';
        btn.className = 'bg-indigo-700 border border-indigo-400 text-white px-3 py-0 text-[10px] font-bold hover:bg-indigo-600 transition h-6 shadow-[0_0_8px_rgba(99,102,241,0.6)]';
        if (banner) banner.classList.remove('hidden');
    } else {
        btn.textContent = 'SIESTE';
        btn.className = 'border border-indigo-700 text-indigo-400 px-3 py-0 text-[10px] font-bold hover:bg-indigo-800 hover:text-white transition h-6';
        if (banner) banner.classList.add('hidden');
    }
}

// Chart Init (Psyché - Radar) — 6 dimensions dynamiques
const psycheLabels = ['Curiosite', 'Creativite', 'Audace', 'Savoir', 'Survie', 'Respect'];
const ctx = document.getElementById('psycheChart').getContext('2d');
const psycheChartInstance = new Chart(ctx, {
    type: 'radar',
    data: {
        labels: psycheLabels,
        datasets: [{
            data: [50, 50, 50, 50, 60, 55],
            backgroundColor: 'rgba(0, 255, 65, 0.2)',
            borderColor: '#00ff41',
            borderWidth: 1,
            pointRadius: 0
        }]
    },
    options: {
        plugins: { legend: { display: false } },
        scales: {
            r: {
                ticks: { display: false },
                grid: { color: '#00220a' },
                angleLines: { color: '#00441b' },
                suggestedMin: 0,
                suggestedMax: 100
            }
        },
        maintainAspectRatio: false
    }
});

// Chargement initial des traits PSYCHE
fetch('/api/psyche/status').then(r => r.json()).then(data => {
    if (data && data.system_average) {
        const avg = data.system_average;
        psycheChartInstance.data.datasets[0].data = [
            avg.curiosite, avg.creativite, avg.audace, avg.savoir, avg.survie, avg.respect
        ];
        psycheChartInstance.update();
    }
}).catch(() => {});

// --- Télémétrie : fonctions de mise à jour ---

function updateTelemReptilian(p) {
    // CPU
    var cpuVal = document.getElementById('telem-cpu-val');
    var cpuBar = document.getElementById('telem-cpu-bar');
    if (cpuVal && p.cpu_percent !== undefined) {
        var cpu = Math.round(p.cpu_percent);
        cpuVal.textContent = cpu + '%';
        cpuBar.style.width = cpu + '%';
        cpuBar.className = 'h-full transition-all duration-500 ' +
            (cpu > 80 ? 'bg-red-600' : cpu > 50 ? 'bg-yellow-600' : 'bg-green-600');
    }
    // RAM
    var ramVal = document.getElementById('telem-ram-val');
    var ramBar = document.getElementById('telem-ram-bar');
    if (ramVal && p.ram_percent !== undefined) {
        var ram = Math.round(p.ram_percent);
        ramVal.textContent = ram + '%';
        ramBar.style.width = ram + '%';
        ramBar.className = 'h-full transition-all duration-500 ' +
            (ram > 80 ? 'bg-red-600' : ram > 60 ? 'bg-yellow-600' : 'bg-blue-600');
    }
    // Ollama
    var olVal = document.getElementById('telem-ollama-val');
    var olBar = document.getElementById('telem-ollama-bar');
    if (olVal) {
        var ok = p.ollama_ok;
        olVal.textContent = ok ? 'OK' : 'DOWN';
        olVal.className = 'text-[9px] ' + (ok ? 'text-green-500' : 'text-red-500');
        olBar.style.width = ok ? '100%' : '0%';
        olBar.className = 'h-full transition-all duration-500 ' + (ok ? 'bg-purple-600' : 'bg-red-600');
    }
    // Budget
    var budVal = document.getElementById('telem-budget-val');
    var budBar = document.getElementById('telem-budget-bar');
    if (budVal && p.budget_ratio !== undefined) {
        var pct = Math.round(p.budget_ratio * 100);
        var remaining = Math.max(0, 100 - pct);
        budVal.textContent = remaining + '% restant';
        budBar.style.width = pct + '%';
        budBar.className = 'h-full transition-all duration-500 ' +
            (pct > 90 ? 'bg-red-600' : pct > 70 ? 'bg-yellow-600' : 'bg-yellow-600');
    }
    // Threat level
    var threatEl = document.getElementById('telem-threat');
    if (threatEl && p.threat_level !== undefined) {
        var tl = p.threat_level;
        threatEl.textContent = 'THREAT ' + tl.toFixed(1);
        threatEl.style.color = tl >= 7 ? '#ff1744' : tl >= 4 ? '#ff9100' : tl >= 2 ? '#ffea00' : '#00ff41';
    }
    // Adrenaline
    var adrVal = document.getElementById('telem-adrenaline-val');
    if (adrVal && p.adrenaline !== undefined) {
        adrVal.textContent = Math.round(p.adrenaline * 100) + '%';
        adrVal.style.color = p.adrenaline > 0.5 ? '#ff5252' : p.adrenaline > 0 ? '#ff9100' : '#4b5563';
    }
    // Reflexes
    var refVal = document.getElementById('telem-reflexes-val');
    if (refVal && p.reflexes_triggered) {
        var parts = [];
        for (var k in p.reflexes_triggered) {
            if (p.reflexes_triggered[k] > 0) parts.push(k + ':' + p.reflexes_triggered[k]);
        }
        refVal.textContent = parts.length > 0 ? parts.join(' ') : 'aucun';
    }
}

function updateTelemBpm(p) {
    var bpmVal = document.getElementById('telem-bpm-val');
    var bpmBar = document.getElementById('telem-bpm-bar');
    if (!bpmVal || !p.bpm) return;
    var bpm = Math.round(p.bpm);
    var emotion = p.emotion || 'serenite';
    var emotionColors = {
        serenite: '#00ff41', curiosite: '#00e5ff', enthousiasme: '#ffea00',
        flow: '#e040fb', frustration: '#ff5252', inquietude: '#ff9100',
        fatigue: '#78909c', determination: '#448aff', alerte: '#ff1744'
    };
    var color = emotionColors[emotion] || '#00ff41';
    bpmVal.textContent = bpm + ' ' + emotion;
    bpmVal.style.color = color;
    // BPM 40-180 → barre 0-100%
    var pct = Math.max(0, Math.min(100, ((bpm - 40) / 140) * 100));
    bpmBar.style.width = pct + '%';
    bpmBar.style.backgroundColor = color;
}

// --- AVATAR EMOTION MAPPING ---
var avatarEmotionMap = {
    'neutre': 'avatar_visage5.png',
    'serenite': 'avatar_visage5.png',
    'curiosite': 'avatar_visage5_souriant.png',
    'satisfaction': 'avatar_visage5_souriant.png',
    'enthousiasme': 'avatar_visage5_souriant.png',
    'flow': 'avatar_visage5_souriant.png',
    'determination': 'avatar_visage5_la_colere.png',
    'frustration': 'avatar_visage5_la_colere.png',
    'inquietude': 'avatar_visage5_la_peur.png',
    'peur': 'avatar_visage5_la_peur.png',
    'panique': 'avatar_visage5_la_peur.png',
    'alerte': 'avatar_visage5_surpris.png',
    'fatigue': 'avatar_visage5_triste.png',
    'ennui': 'avatar_visage5_triste.png',
};

function updateAvatar(emotion) {
    var img = document.getElementById('avatar-img');
    var label = document.getElementById('avatar-emotion');
    var panel = document.getElementById('avatar-panel');
    if (!img) return;
    var file = avatarEmotionMap[emotion] || 'avatar_visage5.png';
    var newSrc = '/static/assets/bio/' + file;
    if (img.src.indexOf(file) === -1) {
        img.style.opacity = '0.3';
        setTimeout(function() {
            img.src = newSrc;
            img.style.opacity = '1';
        }, 400);
    }
    if (label) {
        label.textContent = emotion || '--';
        var emotionColors = {
            serenite: '#00e5c8', curiosite: '#00e5ff', enthousiasme: '#ffea00',
            flow: '#e040fb', frustration: '#ff5252', inquietude: '#ff9100',
            fatigue: '#78909c', determination: '#448aff', alerte: '#ff1744',
            satisfaction: '#4dff88', neutre: '#00e5c8', ennui: '#6a7a8a',
            peur: '#ff6600', panique: '#ff0000'
        };
        label.style.color = emotionColors[emotion] || '#00e5c8';
    }
    // Pulse cardiaque sur l'avatar
    if (img) {
        img.style.filter = 'drop-shadow(0 0 30px rgba(0, 229, 200, 0.6))';
        setTimeout(function() {
            img.style.filter = 'drop-shadow(0 0 15px rgba(0, 229, 200, 0.3))';
        }, 500);
    }
}

// --- NEUROCHIMIE TELEMETRIE ---
function updateTelemNeurochemistry() {
    fetch('/api/psyche/status').then(function(r) { return r.json(); }).then(function(data) {
        // Les pools neurochimiques ne sont pas dans psyche, cherchons dans brain
    }).catch(function() {});
    // Fallback : lire directement le brain status pour les neurochimiques
    fetch('/api/brain/status').then(function(r) { return r.json(); }).then(function(data) {
        var cs = data && data.current_state;
        if (!cs || !cs.organ_states) return;
        var neuro = cs.organ_states;
        // Dopamine
        var dopa = neuro.dopamine;
        if (dopa) {
            var dopaVal = document.getElementById('telem-dopamine-val');
            var dopaBar = document.getElementById('telem-dopamine-bar');
            if (dopaVal) dopaVal.textContent = dopa.level.toFixed(2);
            if (dopaBar) dopaBar.style.width = Math.round(dopa.level * 100) + '%';
        }
        // Neurochemistry
        var nc = neuro.neurochemistry;
        if (nc) {
            var pools = [
                {key: 'serotonin', id: 'serotonin'},
                {key: 'noradrenaline', id: 'noradrenaline'},
                {key: 'acetylcholine', id: 'acetylcholine'},
            ];
            pools.forEach(function(p) {
                var val = nc[p.key];
                if (val !== undefined) {
                    var el = document.getElementById('telem-' + p.id + '-val');
                    var bar = document.getElementById('telem-' + p.id + '-bar');
                    if (el) el.textContent = val.toFixed(2);
                    if (bar) bar.style.width = Math.round(val * 100) + '%';
                }
            });
        }
    }).catch(function() {});
}

// Mise a jour periodique neurochimie (30s)
setInterval(updateTelemNeurochemistry, 30000);
updateTelemNeurochemistry();

// Chargement initial télémétrie
fetch('/api/reptilian/status').then(function(r) { return r.json(); }).then(function(data) {
    updateTelemReptilian(data);
}).catch(function() {});

// Chargement initial statut sieste + polling
fetch('/api/autonomy/status').then(r => r.json()).then(data => {
    if (data && typeof data.is_napping === 'boolean') {
        napModeActive = data.is_napping;
        updateNapButton();
    }
}).catch(() => {});
setInterval(() => {
    fetch('/api/autonomy/status').then(r => r.json()).then(data => {
        if (data && typeof data.is_napping === 'boolean') {
            napModeActive = data.is_napping;
            updateNapButton();
        }
    }).catch(() => {});
}, 30000);

// Chargement version + état sieste depuis /health
fetch('/health').then(r => r.json()).then(data => {
    if (data && data.version) {
        const el = document.getElementById('version-display');
        if (el) el.textContent = 'V' + data.version;
    }
    if (data && data.is_napping) {
        napModeActive = true;
        updateNapButton();
    }
}).catch(() => {});