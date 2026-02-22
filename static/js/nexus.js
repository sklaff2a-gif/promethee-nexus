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
const activeStreams = {};
const recentlyStreamed = new Set();

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
    // 2. Flux de pensée (Thought Stream)
    else if (type === "THOUGHT_STREAM") {
        highlightAgent(payload.agent);
        // On n'affiche pas tout dans le dialogue pour éviter le spam, juste dans les logs ou si c'est important
        if (payload.type === 'success') {
            addDialogue(payload.agent.toUpperCase(), payload.content, 'agent');
        } else if (payload.type === 'error') {
            addLog(payload.agent, payload.content, 'err');
        } else {
            addLog(payload.agent, payload.content, 'info');
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
    // 13. CARDIAC_BEAT : overlay cardiaque VISION
    else if (type === "CARDIAC_BEAT") {
        if (typeof NeuralVision !== 'undefined') NeuralVision.handleCardiacBeat(payload);
    }
    // 14. SYNAPTIC_UPDATE : mise à jour graphe neural VISION
    else if (type === "SYNAPTIC_UPDATE") {
        if (typeof NeuralVision !== 'undefined') NeuralVision.handleSynapticUpdate(payload);
    }
};

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

function toggleKillSwitch() {
    fetch('/api/override', { method: 'POST', headers: authHeaders(), body: JSON.stringify({active: true}) });
    addLog('SYSTEM', 'KILL SWITCH ACTIONNÉ', 'err');
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