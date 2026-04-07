/**
 * GAMES — Onglet jeux de Promethee
 * Morpion + Puissance 4 jouables via l'API /api/games/*
 */

const GamesView = {
    currentGame: null,
    polling: null,

    open() {
        document.getElementById('games-overlay').style.display = 'flex';
        this.loadStatus();
    },
    close() {
        document.getElementById('games-overlay').style.display = 'none';
        if (this.polling) { clearInterval(this.polling); this.polling = null; }
    },

    async loadStatus() {
        try {
            const res = await fetch('/api/games/status');
            const data = await res.json();
            this.renderHub(data);
        } catch (e) {
            document.getElementById('games-content').innerHTML =
                `<div style="color:#ff5050; text-align:center; padding:40px;">Erreur: ${e.message}</div>`;
        }
    },

    renderHub(data) {
        const el = document.getElementById('games-content');
        const stats = data.stats || {};
        const competences = data.competences || {};
        const active = data.active_game;

        // Si une partie est en cours, afficher la partie
        if (active) {
            this.currentGame = active.game;
            if (active.game === 'morpion') this.renderMorpion(active.state, active.render);
            else if (active.game === 'puissance4') this.renderPuissance4(active.state, active.render);
            return;
        }

        // Hub principal
        let html = '<div style="max-width:600px; margin:0 auto; padding:20px;">';

        // Stats globales + score adaptatif
        const scores = data.opponent_scores || {};
        const humanScore = scores.human || 50;
        const humanDiff = humanScore < 30 ? 'EASY' : humanScore < 60 ? 'MEDIUM' : 'HARD';
        const diffColor = humanScore < 30 ? '#4dff88' : humanScore < 60 ? '#ffb860' : '#ff5050';
        html += `<div style="text-align:center; margin-bottom:24px;">
            <div style="color:#4dff88; font-size:16px; font-weight:bold; letter-spacing:0.2em;">SALLE DE JEUX</div>
            <div style="color:#666; font-size:10px; margin-top:4px;">${stats.total_games_played || 0} parties jouees</div>
            <div style="color:#888; font-size:9px; margin-top:4px;">Ton score : <strong style="color:${diffColor}">${humanScore.toFixed(0)}/100</strong> — Promethee joue <strong style="color:${diffColor}">${humanDiff}</strong></div>
        </div>`;

        // Cartes des jeux
        const games = [
            { id: 'morpion', name: 'MORPION', icon: '#', desc: 'Tic-Tac-Toe 3x3',
              w: stats.morpion_wins||0, l: stats.morpion_losses||0, d: stats.morpion_draws||0,
              color: '#00e5c8' },
            { id: 'puissance4', name: 'PUISSANCE 4', icon: '●', desc: 'Connect Four 7x6',
              w: stats.puissance4_wins||0, l: stats.puissance4_losses||0, d: stats.puissance4_draws||0,
              color: '#ffb860' },
            { id: 'echecs', name: 'ECHECS', icon: '♟', desc: 'Debloquer les 3 premiers jeux',
              locked: !stats.chess_unlocked, color: '#a0a0ff' },
        ];

        html += '<div style="display:grid; grid-template-columns:1fr 1fr 1fr; gap:12px; margin-bottom:24px;">';
        for (const g of games) {
            const border = g.locked ? 'rgba(100,100,100,0.3)' : g.color;
            const opacity = g.locked ? '0.4' : '1';
            html += `<div style="border:1px solid ${border}; border-radius:8px; padding:16px; text-align:center; opacity:${opacity}; background:rgba(0,0,0,0.3);">
                <div style="font-size:28px; margin-bottom:8px;">${g.icon}</div>
                <div style="color:${g.color}; font-size:12px; font-weight:bold; letter-spacing:0.1em;">${g.name}</div>
                <div style="color:#666; font-size:9px; margin-top:4px;">${g.desc}</div>
                ${g.locked ? '<div style="color:#883333; font-size:9px; margin-top:8px;">VERROUILLE</div>' :
                  g.id === 'echecs' ? '<div style="color:#4dff88; font-size:9px; margin-top:8px;">BIENTOT</div>' :
                  `<div style="color:#888; font-size:10px; margin-top:8px;">${g.w}V ${g.l}D ${g.d}N</div>
                   <div style="margin-top:8px; display:flex; gap:4px; justify-content:center; flex-wrap:wrap;">
                       <button onclick="GamesView.newGame('${g.id}', true, 'adaptive')" style="background:${g.color}; color:#000; border:none; padding:3px 8px; font-size:9px; font-weight:bold; cursor:pointer; border-radius:3px;">JOUER</button>
                       <button onclick="GamesView.newGame('${g.id}', false, 'adaptive')" style="background:none; color:${g.color}; border:1px solid ${g.color}; padding:3px 8px; font-size:9px; cursor:pointer; border-radius:3px;">2e</button>
                   </div>`
                }
            </div>`;
        }
        html += '</div>';

        // Competences pour les echecs
        html += '<div style="border:1px solid rgba(160,160,255,0.2); border-radius:8px; padding:12px; background:rgba(0,0,0,0.2);">';
        html += '<div style="color:#a0a0ff; font-size:10px; font-weight:bold; margin-bottom:8px; letter-spacing:0.1em;">PROGRESSION ECHECS</div>';
        for (const [key, comp] of Object.entries(competences)) {
            const ok = comp.validated;
            html += `<div style="display:flex; justify-content:space-between; align-items:center; padding:4px 0; border-bottom:1px solid rgba(100,100,100,0.1);">
                <span style="color:#888; font-size:9px;">${comp.description}</span>
                <span style="color:${ok ? '#4dff88' : '#883333'}; font-size:10px; font-weight:bold;">${ok ? '✓' : '✗'}</span>
            </div>`;
        }
        html += '</div>';

        // Historique recent
        const history = data.recent_history || [];
        if (history.length > 0) {
            html += '<div style="margin-top:16px; border:1px solid rgba(100,100,100,0.2); border-radius:8px; padding:12px; background:rgba(0,0,0,0.2);">';
            html += '<div style="color:#666; font-size:10px; font-weight:bold; margin-bottom:8px;">HISTORIQUE RECENT</div>';
            for (const h of history.slice(-5).reverse()) {
                const won = h.promethee_won;
                const icon = won ? '🏆' : h.winner ? '💀' : '🤝';
                const date = new Date(h.timestamp * 1000).toLocaleString('fr-FR', {hour:'2-digit',minute:'2-digit'});
                html += `<div style="display:flex; justify-content:space-between; font-size:9px; color:#888; padding:2px 0;">
                    <span>${icon} ${h.game} vs ${h.opponent} (${h.moves} coups)</span>
                    <span>${date}</span>
                </div>`;
            }
            html += '</div>';
        }

        html += '</div>';
        el.innerHTML = html;
    },

    async newGame(gameType, prometheeStarts, difficulty = 'adaptive') {
        try {
            const res = await fetch('/api/games/new', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({game: gameType, opponent: 'human', promethee_starts: !prometheeStarts, difficulty: difficulty})
            });
            const data = await res.json();
            if (data.error) {
                alert(data.error);
                return;
            }
            this.currentGame = gameType;
            if (gameType === 'morpion') this.renderMorpion(data.state, data.render);
            else this.renderPuissance4(data.state, data.render);
        } catch (e) {
            alert('Erreur: ' + e.message);
        }
    },

    // ─── MORPION ─────────────────────────────────────────────

    renderMorpion(state, textRender) {
        const el = document.getElementById('games-content');
        const board = state.board;
        const gameOver = state.game_over;
        const winner = state.winner;
        const current = state.current_player;

        let html = '<div style="max-width:400px; margin:0 auto; padding:20px; text-align:center;">';
        html += '<div style="color:#00e5c8; font-size:14px; font-weight:bold; letter-spacing:0.15em; margin-bottom:16px;">MORPION</div>';

        // Status
        if (gameOver) {
            html += `<div style="color:${winner ? '#4dff88' : '#ffb860'}; font-size:12px; margin-bottom:12px; font-weight:bold;">
                ${winner ? winner + ' GAGNE!' : 'MATCH NUL!'}</div>`;
        } else {
            html += `<div style="color:#888; font-size:11px; margin-bottom:12px;">Tour de <strong style="color:#00e5c8">${current}</strong></div>`;
        }

        // Grille 3x3
        html += '<div style="display:inline-grid; grid-template-columns:repeat(3,80px); gap:4px; margin-bottom:16px;">';
        for (let r = 0; r < 3; r++) {
            for (let c = 0; c < 3; c++) {
                const cell = board[r][c];
                const isEmpty = cell === ' ';
                const color = cell === 'X' ? '#00e5c8' : cell === 'O' ? '#ffb860' : '#333';
                const cursor = isEmpty && !gameOver ? 'pointer' : 'default';
                const hover = isEmpty && !gameOver ? 'onmouseenter="this.style.background=\'rgba(0,229,200,0.1)\'" onmouseleave="this.style.background=\'rgba(0,0,0,0.4)\'"' : '';
                const click = isEmpty && !gameOver ? `onclick="GamesView.playMorpion(${r},${c})"` : '';
                html += `<div style="width:80px; height:80px; border:1px solid rgba(0,229,200,0.3); background:rgba(0,0,0,0.4); display:flex; align-items:center; justify-content:center; font-size:32px; font-weight:bold; color:${color}; cursor:${cursor}; border-radius:4px; transition:background 0.2s;" ${hover} ${click}>
                    ${isEmpty ? '' : cell}
                </div>`;
            }
        }
        html += '</div>';

        // Boutons
        html += '<div style="margin-top:12px; display:flex; gap:8px; justify-content:center;">';
        if (gameOver) {
            html += `<button onclick="GamesView.loadStatus()" style="background:#00e5c8; color:#000; border:none; padding:6px 16px; font-size:11px; font-weight:bold; cursor:pointer; border-radius:4px;">NOUVELLE PARTIE</button>`;
        } else {
            html += `<button onclick="GamesView.forfeit()" style="background:none; color:#ff5050; border:1px solid #ff5050; padding:4px 12px; font-size:9px; cursor:pointer; border-radius:3px;">ABANDONNER</button>`;
        }
        html += `<button onclick="GamesView.backToHub()" style="background:none; color:#888; border:1px solid #555; padding:4px 12px; font-size:9px; cursor:pointer; border-radius:3px;">RETOUR HUB</button>`;
        html += '</div>';

        // Zone de chat en jeu
        html += this.renderGameChat();

        html += '</div>';
        el.innerHTML = html;
    },

    async playMorpion(row, col) {
        try {
            const res = await fetch('/api/games/move', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({move: [row, col], player: 'human'})
            });
            const data = await res.json();
            if (data.error) { alert(data.error); return; }
            if (!data.move_result || !data.move_result.valid) {
                alert(data.move_result ? data.move_result.reason : 'Coup invalide');
                return;
            }
            this.updateChatFromResponse(data);
            this.renderMorpion(data.state, data.render);
        } catch (e) { alert('Erreur: ' + e.message); }
    },

    // ─── PUISSANCE 4 ────────────────────────────────────────

    renderPuissance4(state, textRender) {
        const el = document.getElementById('games-content');
        const board = state.board;
        const gameOver = state.game_over;
        const winner = state.winner;
        const current = state.current_player;
        const validCols = state.valid_columns || [];
        const ROWS = board.length;
        const COLS = board[0].length;

        let html = '<div style="max-width:500px; margin:0 auto; padding:20px; text-align:center;">';
        html += '<div style="color:#ffb860; font-size:14px; font-weight:bold; letter-spacing:0.15em; margin-bottom:16px;">PUISSANCE 4</div>';

        if (gameOver) {
            html += `<div style="color:${winner ? '#4dff88' : '#ffb860'}; font-size:12px; margin-bottom:12px; font-weight:bold;">
                ${winner ? (winner === 'R' ? 'ROUGE' : 'JAUNE') + ' GAGNE!' : 'MATCH NUL!'}</div>`;
        } else {
            const pName = current === 'R' ? 'ROUGE' : 'JAUNE';
            const pColor = current === 'R' ? '#ff5050' : '#ffb860';
            html += `<div style="color:#888; font-size:11px; margin-bottom:8px;">Tour de <strong style="color:${pColor}">${pName}</strong></div>`;
        }

        // Boutons colonnes (cliquer pour jouer)
        if (!gameOver) {
            html += '<div style="display:inline-grid; grid-template-columns:repeat(' + COLS + ',50px); gap:2px; margin-bottom:4px;">';
            for (let c = 0; c < COLS; c++) {
                const canPlay = validCols.includes(c);
                html += `<div style="height:24px; display:flex; align-items:center; justify-content:center; font-size:16px; cursor:${canPlay ? 'pointer' : 'default'}; color:${canPlay ? '#4dff88' : '#333'};"
                    ${canPlay ? `onclick="GamesView.playPuissance4(${c})" onmouseenter="this.style.color='#00e5c8'" onmouseleave="this.style.color='#4dff88'"` : ''}>▼</div>`;
            }
            html += '</div>';
        }

        // Grille
        html += '<div style="display:inline-grid; grid-template-columns:repeat(' + COLS + ',50px); gap:2px; background:rgba(0,50,150,0.3); padding:4px; border-radius:8px;">';
        for (let r = 0; r < ROWS; r++) {
            for (let c = 0; c < COLS; c++) {
                const cell = board[r][c];
                let bg = 'rgba(0,0,0,0.5)';
                if (cell === 'R') bg = '#ff5050';
                else if (cell === 'J') bg = '#ffb860';
                html += `<div style="width:50px; height:50px; border-radius:50%; background:${bg}; border:2px solid rgba(0,50,150,0.5);"></div>`;
            }
        }
        html += '</div>';

        html += '<div style="margin-top:12px; display:flex; gap:8px; justify-content:center;">';
        if (gameOver) {
            html += `<button onclick="GamesView.loadStatus()" style="background:#ffb860; color:#000; border:none; padding:6px 16px; font-size:11px; font-weight:bold; cursor:pointer; border-radius:4px;">NOUVELLE PARTIE</button>`;
        } else {
            html += `<button onclick="GamesView.forfeit()" style="background:none; color:#ff5050; border:1px solid #ff5050; padding:4px 12px; font-size:9px; cursor:pointer; border-radius:3px;">ABANDONNER</button>`;
        }
        html += `<button onclick="GamesView.backToHub()" style="background:none; color:#888; border:1px solid #555; padding:4px 12px; font-size:9px; cursor:pointer; border-radius:3px;">RETOUR HUB</button>`;
        html += '</div>';

        // Zone de chat en jeu
        html += this.renderGameChat();

        html += '</div>';
        el.innerHTML = html;
    },

    async playPuissance4(col) {
        try {
            const res = await fetch('/api/games/move', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({move: col, player: 'human'})
            });
            const data = await res.json();
            if (data.error) { alert(data.error); return; }
            if (!data.move_result || !data.move_result.valid) {
                alert(data.move_result ? data.move_result.reason : 'Coup invalide');
                return;
            }
            this.updateChatFromResponse(data);
            this.renderPuissance4(data.state, data.render);
        } catch (e) { alert('Erreur: ' + e.message); }
    },

    gameChatMessages: [],

    renderGameChat() {
        let html = '<div style="margin-top:16px; border:1px solid rgba(0,229,200,0.15); border-radius:8px; padding:8px; background:rgba(0,0,0,0.3); max-width:400px; margin-left:auto; margin-right:auto;">';
        html += '<div id="game-chat-messages" style="max-height:120px; overflow-y:auto; margin-bottom:8px; font-size:10px;">';
        for (const msg of this.gameChatMessages.slice(-8)) {
            const isP = msg.player === 'promethee';
            const color = isP ? '#00e5c8' : '#ffb860';
            const name = isP ? 'Promethee' : 'Toi';
            if (msg.message) {
                html += `<div style="margin:2px 0;"><span style="color:${color}; font-weight:bold;">${name}:</span> <span style="color:#aaa;">${msg.message}</span></div>`;
            }
        }
        html += '</div>';
        html += `<div style="display:flex; gap:4px;">
            <input type="text" id="game-chat-input" placeholder="Dis quelque chose..."
                style="flex:1; background:rgba(0,0,0,0.5); border:1px solid rgba(0,229,200,0.2); color:#ccc; padding:4px 8px; font-size:10px; border-radius:3px; font-family:'Courier New',monospace;"
                onkeydown="if(event.key==='Enter') GamesView.sendGameMessage()">
            <button onclick="GamesView.sendGameMessage()"
                style="background:rgba(0,229,200,0.2); color:#00e5c8; border:1px solid rgba(0,229,200,0.3); padding:4px 8px; font-size:9px; cursor:pointer; border-radius:3px;">ENVOYER</button>
        </div>`;
        html += '</div>';
        return html;
    },

    async sendGameMessage() {
        const input = document.getElementById('game-chat-input');
        if (!input) return;
        const msg = input.value.trim();
        if (!msg) return;
        input.value = '';
        try {
            const res = await fetch('/api/games/say', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({message: msg})
            });
            const data = await res.json();
            if (data.chat) {
                this.gameChatMessages = data.chat;
                const chatEl = document.getElementById('game-chat-messages');
                if (chatEl) {
                    let html = '';
                    for (const m of this.gameChatMessages.slice(-8)) {
                        const isP = m.player === 'promethee';
                        const color = isP ? '#00e5c8' : '#ffb860';
                        const name = isP ? 'Promethee' : 'Toi';
                        if (m.message) {
                            html += `<div style="margin:2px 0;"><span style="color:${color}; font-weight:bold;">${name}:</span> <span style="color:#aaa;">${m.message}</span></div>`;
                        }
                    }
                    chatEl.innerHTML = html;
                    chatEl.scrollTop = chatEl.scrollHeight;
                }
            }
        } catch (e) { /* silencieux */ }
    },

    updateChatFromResponse(data) {
        if (data.chat) this.gameChatMessages = data.chat;
        if (data.promethee_comment) {
            // Ajouter le commentaire s'il n'est pas deja dans la liste
            const last = this.gameChatMessages[this.gameChatMessages.length - 1];
            if (!last || last.message !== data.promethee_comment) {
                this.gameChatMessages.push({player: 'promethee', message: data.promethee_comment});
            }
        }
    },

    backToHub() {
        this.currentGame = null;
        this.loadStatus();
    },

    async forfeit() {
        if (!confirm('Abandonner la partie ?')) return;
        try {
            await fetch('/api/games/forfeit', {method: 'POST'});
            this.loadStatus();
        } catch (e) { alert('Erreur: ' + e.message); }
    }
};

document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && document.getElementById('games-overlay').style.display !== 'none')
        GamesView.close();
});
