// static/js/impact.js — IMPACT GRAPH : Dépendances & Santé des Modules
// IIFE identique à vision.js
const ImpactView = (function() {
    "use strict";

    // --- CONFIG ---
    const TYPE_COLORS = {
        core: "#00ff41",
        agent: "#00e5ff",
        capability: "#e040fb",
        grimoire: "#ff9100",
        root: "#ffffff",
    };
    const STATUS_RING = { degraded: "#ffea00", error: "#ff1744" };
    const MIN_ZOOM = 0.3, MAX_ZOOM = 5;

    // --- STATE ---
    let svg, g, simulation, tooltip;
    let nodeEls, linkEls, labelEls;
    let graphData = null;
    let selectedNode = null;
    let initialized = false;

    // --- HELPERS ---
    function nodeRadius(d) {
        return 4 + Math.sqrt(d.imported_by_count || 0) * 2;
    }
    function nodeColor(d) {
        return TYPE_COLORS[d.type] || "#888";
    }

    // --- INIT (lazy, uniquement à l'ouverture) ---
    function init() {
        if (initialized) return;
        initialized = true;

        const container = document.getElementById("impact-container");
        if (!container) return;

        const w = container.clientWidth || 800;
        const h = container.clientHeight || 600;

        svg = d3.select("#impact-svg")
            .attr("width", w)
            .attr("height", h);

        // Defs pour glow
        const defs = svg.append("defs");
        const filter = defs.append("filter").attr("id", "impact-glow");
        filter.append("feGaussianBlur").attr("stdDeviation", "3").attr("result", "blur");
        const feMerge = filter.append("feMerge");
        feMerge.append("feMergeNode").attr("in", "blur");
        feMerge.append("feMergeNode").attr("in", "SourceGraphic");

        g = svg.append("g");

        // Zoom/Pan
        const zoom = d3.zoom()
            .scaleExtent([MIN_ZOOM, MAX_ZOOM])
            .on("zoom", (event) => { g.attr("transform", event.transform); });
        svg.call(zoom);

        // Tooltip
        tooltip = document.getElementById("impact-tooltip");

        // Resize
        const ro = new ResizeObserver(() => {
            const nw = container.clientWidth;
            const nh = container.clientHeight;
            svg.attr("width", nw).attr("height", nh);
            if (simulation) simulation.force("center", d3.forceCenter(nw / 2, nh / 2)).alpha(0.1).restart();
        });
        ro.observe(container);

        fetchGraph();
    }

    function fetchGraph() {
        fetch("/api/health/impact")
            .then(r => r.json())
            .then(data => {
                graphData = data;
                render(data);
                updateStats(data.stats);
            })
            .catch(err => console.warn("[IMPACT] fetch error:", err));
    }

    // --- RENDER ---
    function render(data) {
        if (!g) return;
        g.selectAll("*").remove();

        const container = document.getElementById("impact-container");
        const w = container ? container.clientWidth : 800;
        const h = container ? container.clientHeight : 600;

        simulation = d3.forceSimulation(data.nodes)
            .force("link", d3.forceLink(data.links).id(d => d.id).distance(60))
            .force("charge", d3.forceManyBody().strength(-120))
            .force("center", d3.forceCenter(w / 2, h / 2))
            .force("collision", d3.forceCollide().radius(d => nodeRadius(d) + 4));

        // Links
        linkEls = g.append("g")
            .attr("class", "links")
            .selectAll("line")
            .data(data.links)
            .join("line")
            .attr("stroke", "#003300")
            .attr("stroke-width", 0.5)
            .attr("stroke-opacity", 0.4);

        // Nodes
        nodeEls = g.append("g")
            .attr("class", "nodes")
            .selectAll("g")
            .data(data.nodes)
            .join("g")
            .call(d3.drag()
                .on("start", dragStart)
                .on("drag", dragging)
                .on("end", dragEnd))
            .on("click", onNodeClick)
            .on("mouseover", onNodeHover)
            .on("mouseout", onNodeOut);

        // Cercle principal
        nodeEls.append("circle")
            .attr("r", d => nodeRadius(d))
            .attr("fill", d => nodeColor(d))
            .attr("stroke", d => STATUS_RING[d.status] || "none")
            .attr("stroke-width", d => d.status !== "healthy" ? 2 : 0)
            .attr("filter", d => d.status === "error" ? "url(#impact-glow)" : null)
            .attr("opacity", 0.9);

        // Labels
        labelEls = g.append("g")
            .attr("class", "labels")
            .selectAll("text")
            .data(data.nodes)
            .join("text")
            .text(d => d.display || d.name)
            .attr("font-size", "7px")
            .attr("font-family", "'Courier New', monospace")
            .attr("fill", d => nodeColor(d))
            .attr("fill-opacity", 0.6)
            .attr("dx", d => nodeRadius(d) + 3)
            .attr("dy", 3);

        simulation.on("tick", () => {
            linkEls
                .attr("x1", d => d.source.x)
                .attr("y1", d => d.source.y)
                .attr("x2", d => d.target.x)
                .attr("y2", d => d.target.y);
            nodeEls.attr("transform", d => `translate(${d.x},${d.y})`);
            labelEls.attr("x", d => d.x).attr("y", d => d.y);
        });
    }

    // --- INTERACTIONS ---
    function onNodeClick(event, d) {
        event.stopPropagation();
        if (selectedNode === d.id) {
            // Deselection
            selectedNode = null;
            resetHighlight();
            return;
        }
        selectedNode = d.id;
        highlightCascade(d.id);
    }

    function highlightCascade(moduleId) {
        if (!graphData) return;

        // Trouver tous les modules qui dépendent de moduleId (BFS sur imported_by)
        const affected = new Set([moduleId]);
        const importedBy = {};
        graphData.links.forEach(l => {
            const src = typeof l.source === "object" ? l.source.id : l.source;
            const tgt = typeof l.target === "object" ? l.target.id : l.target;
            if (!importedBy[tgt]) importedBy[tgt] = [];
            importedBy[tgt].push(src);
        });

        const queue = [moduleId];
        while (queue.length > 0) {
            const current = queue.shift();
            (importedBy[current] || []).forEach(dep => {
                if (!affected.has(dep)) {
                    affected.add(dep);
                    queue.push(dep);
                }
            });
        }

        // Dimmer les non-affectés
        nodeEls.select("circle")
            .attr("opacity", d => affected.has(d.id) ? 1.0 : 0.15);
        labelEls
            .attr("fill-opacity", d => affected.has(d.id) ? 0.9 : 0.1);
        linkEls
            .attr("stroke-opacity", d => {
                const src = typeof d.source === "object" ? d.source.id : d.source;
                const tgt = typeof d.target === "object" ? d.target.id : d.target;
                return (affected.has(src) && affected.has(tgt)) ? 0.7 : 0.05;
            })
            .attr("stroke", d => {
                const src = typeof d.source === "object" ? d.source.id : d.source;
                const tgt = typeof d.target === "object" ? d.target.id : d.target;
                return (affected.has(src) && affected.has(tgt)) ? "#ff5252" : "#003300";
            });
    }

    function resetHighlight() {
        if (!nodeEls) return;
        nodeEls.select("circle").attr("opacity", 0.9);
        labelEls.attr("fill-opacity", 0.6);
        linkEls.attr("stroke-opacity", 0.4).attr("stroke", "#003300");
    }

    function onNodeHover(event, d) {
        if (!tooltip) return;
        const lines = [
            `<b>${d.id}</b>`,
            `Type: ${d.type}`,
            `Santé: ${d.status}`,
            d.error_count > 0 ? `Erreurs: ${d.error_count}` : "",
            `Imports: ${d.import_count}`,
            `Importé par: ${d.imported_by_count}`,
            d.last_modified ? `Modifié: ${new Date(d.last_modified * 1000).toLocaleDateString("fr-FR")}` : "",
        ].filter(Boolean);
        tooltip.innerHTML = lines.join("<br>");
        tooltip.style.display = "block";
        tooltip.style.left = (event.clientX + 12) + "px";
        tooltip.style.top = (event.clientY - 10) + "px";
    }

    function onNodeOut() {
        if (tooltip) tooltip.style.display = "none";
    }

    // --- DRAG ---
    function dragStart(event, d) {
        if (!event.active) simulation.alphaTarget(0.3).restart();
        d.fx = d.x; d.fy = d.y;
    }
    function dragging(event, d) {
        d.fx = event.x; d.fy = event.y;
    }
    function dragEnd(event, d) {
        if (!event.active) simulation.alphaTarget(0);
        d.fx = null; d.fy = null;
    }

    // --- STATS ---
    function updateStats(stats) {
        const el = document.getElementById("impact-stats");
        if (!el || !stats) return;
        el.innerHTML =
            `<span style="color:#00ff41">${stats.healthy} OK</span> ` +
            `<span style="color:#ffea00">${stats.degraded} DEG</span> ` +
            `<span style="color:#ff1744">${stats.error} ERR</span> ` +
            `<span style="color:#666">/ ${stats.total_modules}</span>`;
    }

    // --- OPEN / CLOSE ---
    function open() {
        const overlay = document.getElementById("impact-overlay");
        if (!overlay) return;
        overlay.style.display = "flex";
        init();
        // Re-fetch à chaque ouverture
        fetchGraph();
    }

    function close() {
        const overlay = document.getElementById("impact-overlay");
        if (overlay) overlay.style.display = "none";
        selectedNode = null;
    }

    // --- HANDLE UPDATE (via bus) ---
    function handleUpdate(payload) {
        // Invalidation du cache : re-fetch au prochain open
        graphData = null;
        if (document.getElementById("impact-overlay") &&
            document.getElementById("impact-overlay").style.display !== "none") {
            fetchGraph();
        }
    }

    // --- PUBLIC API ---
    return { init: init, open: open, close: close, handleUpdate: handleUpdate };
})();
