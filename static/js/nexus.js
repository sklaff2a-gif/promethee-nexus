const ws = new WebSocket(`ws://${window.location.host}/ws`);
const dialogueBox = document.getElementById('dialogue-box');
const logsBox = document.getElementById('logs-box');

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
    // 3. Réponse finale d'un agent
    else if (type === "AGENT_RESPONSE") {
        highlightAgent(payload.agent);
        addDialogue(payload.agent.toUpperCase(), payload.content, 'agent');
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
};

function sendMission() {
    const input = document.getElementById('mission-input');
    const mission = input.value.trim();
    if (!mission) return;
    
    // Feedback immédiat UI
    // addDialogue("COMMANDER", mission, 'user'); (Géré par le retour WS USER_COMMAND)
    
    fetch('/api/mission', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mission: mission })
    }).catch(err => addLog("API", "Erreur: " + err, "err"));
    input.value = "";
}

function toggleKillSwitch() {
    fetch('/api/override', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({active: true}) });
    addLog('SYSTEM', 'KILL SWITCH ACTIONNÉ', 'err');
}

// Chart Init (Psyché - Radar)
const ctx = document.getElementById('psycheChart').getContext('2d');
new Chart(ctx, { 
    type: 'radar', 
    data: { 
        labels: ['Logic', 'Creativity', 'Speed', 'Safety', 'Memory'], 
        datasets: [{ 
            data: [90, 70, 95, 100, 80], 
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