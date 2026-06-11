/**
 * TissueView — Heatmap canvas du substrat cellulaire neuronal 16x16.
 * Overlay plein ecran avec auto-refresh 5s.
 */
const TissueView = (function() {
    "use strict";

    const GRID = 16;
    const CELL_PX = 28;          // taille d'une cellule en pixels
    const CANVAS_PX = GRID * CELL_PX;
    const REFRESH_MS = 5000;

    let _canvas, _ctx, _overlay, _panel, _timer = null;

    // Couleurs par zone (bordures)
    const ZONE_COLORS = {
        emotion:    "#ff4081",
        threat:     "#ff1744",
        dopamine:   "#00e676",
        goals:      "#ffea00",
        desire:     "#e040fb",
        memory:     "#40c4ff",
        stability:  "#78909c",
        creativity: "#ff9100",
    };

    function _heatColor(v) {
        // 0 = noir, 1+ = vert→jaune→rouge
        v = Math.min(v, 3.0);
        if (v < 0.01) return "rgb(5,10,5)";
        if (v < 0.5)  return `rgb(0,${Math.floor(v*200)},0)`;
        if (v < 1.5)  return `rgb(${Math.floor((v-0.5)*255)},${Math.floor(200-v*60)},0)`;
        return `rgb(255,${Math.floor(Math.max(0,200-v*80))},0)`;
    }

    function _render(data) {
        if (!_ctx) return;
        const grid = data.grid;
        const cells = data.cells;
        const zones = data.zones;
        const zoneSignals = data.zone_signals || {};

        // 1. Heatmap fond
        for (let y = 0; y < GRID; y++) {
            for (let x = 0; x < GRID; x++) {
                _ctx.fillStyle = _heatColor(grid[y][x]);
                _ctx.fillRect(x * CELL_PX, y * CELL_PX, CELL_PX, CELL_PX);
            }
        }

        // 2. Grille subtile
        _ctx.strokeStyle = "rgba(0,255,65,0.08)";
        _ctx.lineWidth = 0.5;
        for (let i = 0; i <= GRID; i++) {
            _ctx.beginPath(); _ctx.moveTo(i*CELL_PX, 0); _ctx.lineTo(i*CELL_PX, CANVAS_PX); _ctx.stroke();
            _ctx.beginPath(); _ctx.moveTo(0, i*CELL_PX); _ctx.lineTo(CANVAS_PX, i*CELL_PX); _ctx.stroke();
        }

        // 3. Bordures de zones
        _ctx.lineWidth = 2;
        for (const [name, z] of Object.entries(zones)) {
            _ctx.strokeStyle = ZONE_COLORS[name] || "#00ff41";
            _ctx.strokeRect(z.x1*CELL_PX, z.y1*CELL_PX, (z.x2-z.x1)*CELL_PX, (z.y2-z.y1)*CELL_PX);
            // Label
            _ctx.fillStyle = ZONE_COLORS[name] || "#00ff41";
            _ctx.font = "bold 9px 'Courier New', monospace";
            _ctx.fillText(name.toUpperCase(), z.x1*CELL_PX+3, z.y1*CELL_PX+10);
        }

        // 4. Cellules (points blancs, taille = energie)
        for (const c of cells) {
            const r = Math.max(1.5, Math.min(c.energy / 40, CELL_PX/3));
            const cx = c.x * CELL_PX + CELL_PX/2;
            const cy = c.y * CELL_PX + CELL_PX/2;
            _ctx.beginPath();
            _ctx.arc(cx, cy, r, 0, Math.PI*2);
            _ctx.fillStyle = c.generation > 5 ? "#00e5ff" : "#ffffff";
            _ctx.globalAlpha = 0.85;
            _ctx.fill();
            _ctx.globalAlpha = 1.0;
        }

        // 5. Panneau lateral droit : metriques par zone
        _renderPanel(zoneSignals, data);
    }

    function _bar(val, max, len) {
        const filled = Math.round((val / max) * len);
        return "\u2588".repeat(Math.min(filled, len)) + "\u2591".repeat(Math.max(0, len - filled));
    }

    function _renderPanel(zoneSignals, data) {
        if (!_panel) return;
        let html = `<div style="font-size:10px;color:#00ff41;font-family:'Courier New',monospace;padding:8px;">`;
        html += `<div style="color:#666;margin-bottom:6px;">TICK #${data.tick_count} | ${data.tick_ms}ms | ${data.cells.length} cellules</div>`;

        for (const [name, sig] of Object.entries(zoneSignals)) {
            const col = ZONE_COLORS[name] || "#00ff41";
            html += `<div style="margin-bottom:6px;">`;
            html += `<div style="color:${col};font-weight:bold;font-size:11px;">${name.toUpperCase()}</div>`;
            html += `<div>ACT  ${_bar(sig.activity, 2.0, 12)} ${sig.activity.toFixed(2)}</div>`;
            html += `<div>DENS ${_bar(sig.density, 0.5, 12)} ${sig.density.toFixed(3)}</div>`;
            html += `<div>NRJ  ${_bar(sig.energy, 200, 12)} ${sig.energy.toFixed(0)}</div>`;
            html += `<div>DIV  ${_bar(sig.diversity, 1.0, 12)} ${sig.diversity.toFixed(2)}</div>`;
            html += `</div>`;
        }
        html += `</div>`;
        _panel.innerHTML = html;
    }

    async function _fetch() {
        try {
            const r = await fetch("/api/tissue/grid", { headers: authHeaders() });
            if (!r.ok) return;
            const data = await r.json();
            _render(data);
        } catch(e) {
            console.warn("TissueView fetch error:", e);
        }
    }

    function open() {
        if (!_overlay) _buildOverlay();
        _overlay.style.display = "flex";
        _fetch();
        if (_timer) clearInterval(_timer);  // garde anti double-open (fuite d'interval)
        _timer = setInterval(_fetch, REFRESH_MS);
        document.addEventListener("keydown", _onKey);
    }

    function close() {
        if (_overlay) _overlay.style.display = "none";
        if (_timer) { clearInterval(_timer); _timer = null; }
        document.removeEventListener("keydown", _onKey);
    }

    function _onKey(e) {
        if (e.key === "Escape") close();
    }

    function _buildOverlay() {
        _overlay = document.createElement("div");
        _overlay.id = "tissue-overlay";
        Object.assign(_overlay.style, {
            display: "none", position: "fixed", inset: "0", zIndex: "1000",
            background: "#000", flexDirection: "column",
        });

        // Header
        const header = document.createElement("div");
        Object.assign(header.style, {
            display: "flex", justifyContent: "space-between", alignItems: "center",
            padding: "6px 12px", background: "#050a05", borderBottom: "1px solid #00441b",
        });
        header.innerHTML = `
            <span style="color:#00ff41;font-size:12px;font-weight:bold;font-family:'Courier New',monospace;letter-spacing:0.15em;">NEURAL TISSUE — SUBSTRAT CELLULAIRE</span>
            <button onclick="TissueView.close()" style="color:#ff5252;border:1px solid #441111;background:none;padding:2px 10px;font-size:10px;font-family:'Courier New',monospace;cursor:pointer;">FERMER [ESC]</button>
        `;
        _overlay.appendChild(header);

        // Body : canvas + panel
        const body = document.createElement("div");
        Object.assign(body.style, {
            flex: "1", display: "flex", overflow: "hidden", background: "#000",
        });

        // Canvas container (centre)
        const canvasWrap = document.createElement("div");
        Object.assign(canvasWrap.style, {
            flex: "1", display: "flex", alignItems: "center", justifyContent: "center",
        });
        _canvas = document.createElement("canvas");
        _canvas.width = CANVAS_PX;
        _canvas.height = CANVAS_PX;
        Object.assign(_canvas.style, {
            border: "1px solid #00441b", imageRendering: "pixelated",
        });
        _ctx = _canvas.getContext("2d");
        canvasWrap.appendChild(_canvas);
        body.appendChild(canvasWrap);

        // Panel droit
        _panel = document.createElement("div");
        Object.assign(_panel.style, {
            width: "280px", overflowY: "auto", borderLeft: "1px solid #00441b",
            background: "#050a05",
        });
        body.appendChild(_panel);

        _overlay.appendChild(body);
        document.body.appendChild(_overlay);
    }

    return { open, close };
})();
