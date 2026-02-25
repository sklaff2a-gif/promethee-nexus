// static/js/vision.js — VISION : Carte Neurale Synaptique en Temps Reel
// Module D3.js force-directed graph pour visualiser le cortex associatif de Promethee.

const NeuralVision = (function() {
    "use strict";

    // --- Constantes ---
    const MAX_VISIBLE_NODES = 120;
    const REFRESH_INTERVAL = 60000; // 60s filet de securite
    const NODE_TYPE_COLORS = {
        memory:    "#00ff41",
        desire:    "#00e5ff",
        trait:     "#ffb300",
        event:     "#448aff",
        eureka:    "#e040fb",
        meta:      "#ffffff",
        objective: "#ff5252",
    };
    const SYNAPSE_TYPE_COLORS = {
        hebbian:      "#00ff41",
        anti_hebbian: "#ff1744",
        temporal:     "#448aff",
        emotional:    "#e040fb",
        eureka:       "#ffea00",
    };
    const EMOTION_COLORS = {
        serenite:      "#00ff41",
        curiosite:     "#00e5ff",
        enthousiasme:  "#ffea00",
        flow:          "#e040fb",
        frustration:   "#ff5252",
        inquietude:    "#ff9100",
        fatigue:       "#78909c",
        determination: "#448aff",
        alerte:        "#ff1744",
    };

    // --- Etat ---
    let svg, g, simulation;
    let nodeData = [], linkData = [];
    let nodeMap = {};  // id -> node object
    let linkElements, nodeElements;
    let width = 300, height = 200;
    let tooltip, statsEl, offlineEl, container;
    let initialized = false;
    let refreshTimer = null;

    // --- Init ---
    function init() {
        container = document.getElementById("vision-container");
        tooltip = document.getElementById("vision-tooltip");
        statsEl = document.getElementById("vision-stats");
        offlineEl = document.getElementById("vision-offline");

        if (!container || !document.getElementById("vision-svg")) return;

        const rect = container.getBoundingClientRect();
        width = rect.width || 300;
        height = rect.height || 200;

        setupSVG();
        setupSimulation();
        fetchGraph();

        // Refresh periodique (filet de securite)
        refreshTimer = setInterval(fetchGraph, REFRESH_INTERVAL);

        // ResizeObserver pour adaptation dynamique
        if (typeof ResizeObserver !== "undefined") {
            new ResizeObserver(function(entries) {
                const r = entries[0].contentRect;
                if (r.width > 0 && r.height > 0) {
                    width = r.width;
                    height = r.height;
                    svg.attr("viewBox", "0 0 " + width + " " + height);
                    if (simulation) {
                        simulation.force("center", d3.forceCenter(width / 2, height / 2));
                        simulation.alpha(0.3).restart();
                    }
                }
            }).observe(container);
        }

        initialized = true;
    }

    // --- Setup SVG ---
    function setupSVG() {
        svg = d3.select("#vision-svg")
            .attr("viewBox", "0 0 " + width + " " + height);

        // Defs : filtres glow
        const defs = svg.append("defs");

        // Glow vert standard
        const filterGreen = defs.append("filter").attr("id", "glow-green");
        filterGreen.append("feGaussianBlur").attr("stdDeviation", "2").attr("result", "blur");
        const feMergeGreen = filterGreen.append("feMerge");
        feMergeGreen.append("feMergeNode").attr("in", "blur");
        feMergeGreen.append("feMergeNode").attr("in", "SourceGraphic");

        // Glow bright (pour activation)
        const filterBright = defs.append("filter").attr("id", "glow-bright");
        filterBright.append("feGaussianBlur").attr("stdDeviation", "4").attr("result", "blur");
        const feMergeBright = filterBright.append("feMerge");
        feMergeBright.append("feMergeNode").attr("in", "blur");
        feMergeBright.append("feMergeNode").attr("in", "SourceGraphic");

        // Glow pulse cardiaque
        const filterPulse = defs.append("filter").attr("id", "glow-pulse");
        filterPulse.append("feGaussianBlur").attr("stdDeviation", "6").attr("result", "blur");
        const feMergePulse = filterPulse.append("feMerge");
        feMergePulse.append("feMergeNode").attr("in", "blur");
        feMergePulse.append("feMergeNode").attr("in", "SourceGraphic");

        // Groupe principal (zoom/pan)
        g = svg.append("g").attr("class", "vision-graph");

        // Cercle pulse cardiaque (en arriere plan)
        g.append("circle")
            .attr("id", "cardiac-pulse")
            .attr("cx", width / 2)
            .attr("cy", height / 2)
            .attr("r", 0)
            .attr("fill", "none")
            .attr("stroke", "#00ff41")
            .attr("stroke-width", 1)
            .attr("opacity", 0);

        // Groupes pour liens et noeuds (liens en dessous)
        g.append("g").attr("class", "links");
        g.append("g").attr("class", "nodes");

        // Zoom/Pan
        const zoom = d3.zoom()
            .scaleExtent([0.3, 5])
            .on("zoom", function(event) {
                g.attr("transform", event.transform);
            });
        svg.call(zoom);
    }

    // --- Setup Simulation ---
    function setupSimulation() {
        simulation = d3.forceSimulation()
            .force("link", d3.forceLink().id(function(d) { return d.id; }).distance(40).strength(0.3))
            .force("charge", d3.forceManyBody().strength(-30).distanceMax(150))
            .force("center", d3.forceCenter(width / 2, height / 2))
            .force("collision", d3.forceCollide().radius(function(d) { return nodeRadius(d) + 2; }))
            .alphaDecay(0.02)
            .on("tick", tick);
        simulation.stop();
    }

    // --- Fetch API ---
    function fetchGraph() {
        const headers = {};
        const token = localStorage.getItem('api_token');
        if (token) headers['Authorization'] = 'Bearer ' + token;

        fetch("/api/synaptic/graph", { headers: headers })
            .then(function(r) { return r.json(); })
            .then(function(data) {
                if (offlineEl) offlineEl.style.display = "none";
                processGraphData(data);
                updateStats(data.stats, data.cardiac);
            })
            .catch(function(err) {
                console.warn("VISION: fetch error", err);
                if (offlineEl) {
                    offlineEl.textContent = "[DECONNECTE]";
                    offlineEl.style.display = "flex";
                }
            });
    }

    // --- Process Data ---
    function processGraphData(data) {
        var nodes = data.nodes || [];
        var links = data.links || [];

        // Limiter aux top nodes par energie
        if (nodes.length > MAX_VISIBLE_NODES) {
            nodes.sort(function(a, b) { return b.energy - a.energy; });
            nodes = nodes.slice(0, MAX_VISIBLE_NODES);
            var visibleIds = new Set(nodes.map(function(n) { return n.id; }));
            links = links.filter(function(l) {
                return visibleIds.has(l.source) && visibleIds.has(l.target);
            });
        }

        // Preserver positions existantes
        nodeMap = {};
        nodeData.forEach(function(n) { nodeMap[n.id] = n; });

        var newNodeData = nodes.map(function(n) {
            var existing = nodeMap[n.id];
            return {
                id: n.id,
                concept: n.concept,
                type: n.type,
                energy: n.energy,
                activation: n.activation,
                valence: n.valence,
                x: existing ? existing.x : width / 2 + (Math.random() - 0.5) * 100,
                y: existing ? existing.y : height / 2 + (Math.random() - 0.5) * 100,
                vx: existing ? existing.vx : 0,
                vy: existing ? existing.vy : 0,
            };
        });

        nodeData = newNodeData;
        linkData = links.map(function(l) {
            return {
                source: l.source,
                target: l.target,
                weight: l.weight,
                type: l.type,
            };
        });

        // Rebuild nodeMap
        nodeMap = {};
        nodeData.forEach(function(n) { nodeMap[n.id] = n; });

        rebindGraph();
    }

    // --- Rebind D3 data join ---
    function rebindGraph() {
        var linkGroup = g.select(".links");
        var nodeGroup = g.select(".nodes");

        // Links
        linkElements = linkGroup.selectAll("line")
            .data(linkData, function(d) {
                var sid = typeof d.source === "object" ? d.source.id : d.source;
                var tid = typeof d.target === "object" ? d.target.id : d.target;
                return sid + "->" + tid;
            });

        linkElements.exit().remove();

        var linkEnter = linkElements.enter().append("line");

        linkElements = linkEnter.merge(linkElements)
            .attr("stroke", function(d) { return SYNAPSE_TYPE_COLORS[d.type] || "#00ff41"; })
            .attr("stroke-width", function(d) { return Math.max(0.3, d.weight * 3); })
            .attr("stroke-opacity", function(d) { return d.weight < 0.1 ? 0.15 : 0.2 + d.weight * 0.4; })
            .attr("stroke-dasharray", function(d) { return d.weight < 0.08 ? "2,3" : null; });

        // Nodes
        nodeElements = nodeGroup.selectAll("circle")
            .data(nodeData, function(d) { return d.id; });

        nodeElements.exit().remove();

        var nodeEnter = nodeElements.enter().append("circle")
            .attr("class", "node-new")
            .call(d3.drag()
                .on("start", dragStarted)
                .on("drag", dragged)
                .on("end", dragEnded))
            .on("mouseover", showTooltip)
            .on("mousemove", moveTooltip)
            .on("mouseout", hideTooltip);

        nodeElements = nodeEnter.merge(nodeElements)
            .attr("r", function(d) { return nodeRadius(d); })
            .attr("fill", function(d) { return NODE_TYPE_COLORS[d.type] || "#00ff41"; })
            .attr("opacity", function(d) { return 0.3 + 0.7 * d.energy; })
            .attr("filter", function(d) { return d.energy > 0.7 ? "url(#glow-green)" : null; })
            .attr("stroke", function(d) { return d.energy > 0.8 ? NODE_TYPE_COLORS[d.type] || "#00ff41" : "none"; })
            .attr("stroke-width", function(d) { return d.energy > 0.8 ? 0.5 : 0; });

        // Restart simulation
        simulation.nodes(nodeData);
        simulation.force("link").links(linkData);
        simulation.alpha(0.5).restart();
    }

    // --- Helpers ---
    function nodeRadius(d) {
        // Taille basee sur activation_count : 2.5 - 10px
        return Math.max(2.5, Math.min(10, 2.5 + Math.log2(d.activation + 1) * 1.5));
    }

    function tick() {
        if (linkElements) {
            linkElements
                .attr("x1", function(d) { return d.source.x; })
                .attr("y1", function(d) { return d.source.y; })
                .attr("x2", function(d) { return d.target.x; })
                .attr("y2", function(d) { return d.target.y; });
        }
        if (nodeElements) {
            nodeElements
                .attr("cx", function(d) { return d.x; })
                .attr("cy", function(d) { return d.y; });
        }
    }

    // --- Drag ---
    function dragStarted(event, d) {
        if (!event.active) simulation.alphaTarget(0.3).restart();
        d.fx = d.x;
        d.fy = d.y;
    }

    function dragged(event, d) {
        d.fx = event.x;
        d.fy = event.y;
    }

    function dragEnded(event, d) {
        if (!event.active) simulation.alphaTarget(0);
        d.fx = null;
        d.fy = null;
    }

    // --- Tooltip ---
    function showTooltip(event, d) {
        if (!tooltip) return;
        var energyBar = "";
        var blocks = Math.round(d.energy * 10);
        for (var i = 0; i < 10; i++) {
            energyBar += i < blocks ? "\u2588" : "\u2591";
        }
        tooltip.innerHTML =
            "<b>" + d.concept + "</b><br>" +
            "type: " + d.type + "<br>" +
            "activations: " + d.activation + "<br>" +
            "energie: " + energyBar + " " + (d.energy * 100).toFixed(0) + "%";
        tooltip.style.display = "block";
    }

    function moveTooltip(event) {
        if (!tooltip) return;
        var containerRect = container.getBoundingClientRect();
        tooltip.style.left = (event.clientX - containerRect.left + 10) + "px";
        tooltip.style.top = (event.clientY - containerRect.top - 10) + "px";
    }

    function hideTooltip() {
        if (tooltip) tooltip.style.display = "none";
    }

    // --- Stats header ---
    function updateStats(stats, cardiac) {
        if (!statsEl) return;
        var text = (stats.total_nodes || 0) + "N " + (stats.total_synapses || 0) + "S";
        if (cardiac && cardiac.bpm) {
            var emotion = cardiac.emotion || "---";
            var color = EMOTION_COLORS[emotion] || "#00ff41";
            text += " | " + Math.round(cardiac.bpm) + " BPM";
            statsEl.style.color = color;
        }
        statsEl.textContent = text;
    }

    // --- Handlers temps reel ---

    function handleSynapticUpdate(payload) {
        if (!initialized || !payload) return;
        var change = payload.change;

        if (change === "node_new") {
            // Nouveau noeud
            var newNode = {
                id: payload.id,
                concept: payload.concept || "?",
                type: payload.type || "memory",
                energy: payload.energy || 0.5,
                activation: 1,
                valence: 0,
                x: width / 2 + (Math.random() - 0.5) * 80,
                y: height / 2 + (Math.random() - 0.5) * 80,
            };
            if (!nodeMap[newNode.id]) {
                nodeData.push(newNode);
                nodeMap[newNode.id] = newNode;
                rebindGraph();
            }
        }
        else if (change === "node_activate") {
            // Reactivation
            var node = nodeMap[payload.id];
            if (node) {
                node.energy = payload.energy || node.energy;
                node.activation = payload.activation || node.activation;
                // Animation pulse
                g.select(".nodes").selectAll("circle")
                    .filter(function(d) { return d.id === payload.id; })
                    .attr("class", "node-activated")
                    .attr("filter", "url(#glow-bright)");
                // Reset apres animation
                setTimeout(function() {
                    g.select(".nodes").selectAll("circle")
                        .filter(function(d) { return d.id === payload.id; })
                        .attr("class", "")
                        .attr("filter", function(d) { return d.energy > 0.7 ? "url(#glow-green)" : null; });
                }, 3000);
                // Update visuel
                if (nodeElements) {
                    nodeElements
                        .attr("r", function(d) { return nodeRadius(d); })
                        .attr("opacity", function(d) { return 0.3 + 0.7 * d.energy; });
                }
            }
        }
        else if (change === "synapse_new") {
            // Nouvelle synapse
            if (nodeMap[payload.source] && nodeMap[payload.target]) {
                linkData.push({
                    source: payload.source,
                    target: payload.target,
                    weight: payload.weight || 0.1,
                    type: payload.type || "hebbian",
                });
                rebindGraph();
                // Animation glow sur la nouvelle synapse
                g.select(".links").selectAll("line")
                    .filter(function(d) {
                        var sid = typeof d.source === "object" ? d.source.id : d.source;
                        var tid = typeof d.target === "object" ? d.target.id : d.target;
                        return sid === payload.source && tid === payload.target;
                    })
                    .attr("class", "synapse-new");
            }
        }
        else if (change === "node_removed") {
            // Noeud pruné par _enforce_node_limit
            var idx = nodeData.findIndex(function(n) { return n.id === payload.id; });
            if (idx !== -1) {
                nodeData.splice(idx, 1);
                delete nodeMap[payload.id];
                // Supprimer les liens associés
                linkData = linkData.filter(function(l) {
                    var sid = typeof l.source === "object" ? l.source.id : l.source;
                    var tid = typeof l.target === "object" ? l.target.id : l.target;
                    return sid !== payload.id && tid !== payload.id;
                });
                rebindGraph();
            }
        }
        else if (change === "synapse_strengthen") {
            // Renforcement
            var found = false;
            linkData.forEach(function(l) {
                var sid = typeof l.source === "object" ? l.source.id : l.source;
                var tid = typeof l.target === "object" ? l.target.id : l.target;
                if (sid === payload.source && tid === payload.target) {
                    l.weight = payload.weight || l.weight;
                    found = true;
                }
            });
            if (found && linkElements) {
                linkElements
                    .attr("stroke-width", function(d) { return Math.max(0.3, d.weight * 3); })
                    .attr("stroke-opacity", function(d) { return d.weight < 0.1 ? 0.15 : 0.2 + d.weight * 0.4; });
            }
        }
    }

    function handleCardiacBeat(payload) {
        if (!initialized || !g) return;

        var bpm = payload.bpm || 60;
        var emotion = payload.emotion || "serenite";
        var coherence = payload.coherence || 0.5;
        var color = EMOTION_COLORS[emotion] || "#00ff41";

        // Pulse cardiaque : anneau qui s'etend depuis le centre
        var pulse = g.select("#cardiac-pulse");
        pulse
            .attr("cx", width / 2)
            .attr("cy", height / 2)
            .attr("stroke", color)
            .attr("stroke-width", 1.5)
            .attr("opacity", 0.6)
            .attr("r", 5)
            .transition()
            .duration(1500)
            .ease(d3.easeQuadOut)
            .attr("r", Math.min(width, height) * 0.4)
            .attr("opacity", 0)
            .attr("stroke-width", 0.3);

        // Update stats header avec couleur emotion
        if (statsEl) {
            var text = statsEl.textContent.split("|")[0].trim();
            text += " | " + Math.round(bpm) + " BPM " + emotion;
            statsEl.textContent = text;
            statsEl.style.color = color;
        }

        // Modulation du fond SVG selon l'emotion (subtile)
        var svgEl = document.getElementById("vision-svg");
        if (svgEl) {
            var r = parseInt(color.slice(1, 3), 16);
            var gv = parseInt(color.slice(3, 5), 16);
            var b = parseInt(color.slice(5, 7), 16);
            var intensity = Math.round(coherence * 8);
            var bgColor = "rgb(" + Math.round(r * intensity / 255) + "," +
                          Math.round(gv * intensity / 255) + "," +
                          Math.round(b * intensity / 255) + ")";
            svgEl.style.background = "radial-gradient(ellipse at center, " + bgColor + " 0%, #000 100%)";
        }
    }

    // --- API publique ---
    return {
        init: init,
        handleSynapticUpdate: handleSynapticUpdate,
        handleCardiacBeat: handleCardiacBeat,
    };
})();

// Auto-init au chargement
document.addEventListener("DOMContentLoaded", function() {
    // Petit delai pour laisser le DOM se stabiliser
    setTimeout(function() { NeuralVision.init(); }, 500);
});
