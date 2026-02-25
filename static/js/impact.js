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
        planned: "#555555",
    };
    const TYPE_LABELS = {
        core: "CORE",
        agent: "AGENT",
        capability: "CAPABILITY",
        grimoire: "GRIMOIRE",
        root: "ROOT",
        planned: "ROADMAP",
    };
    const PHASE_COLORS = {
        4: "#7986cb",  // CONNECTER — indigo clair
        5: "#4dd0e1",  // COMPRENDRE — cyan
        6: "#ba68c8",  // CRÉER — violet
        7: "#ef5350",  // TRANSCENDER — rouge
    };
    // Positions cibles Y par type (% de la hauteur) pour le clustering
    const TYPE_CLUSTER_Y = {
        root: 0.20,
        core: 0.45,
        agent: 0.35,
        grimoire: 0.30,
        capability: 0.65,
        planned: 0.80,
    };
    // Positions cibles X par type
    const TYPE_CLUSTER_X = {
        root: 0.25,
        core: 0.45,
        agent: 0.70,
        grimoire: 0.72,
        capability: 0.75,
        planned: 0.50,
    };

    const STATUS_RING = { degraded: "#ffea00", error: "#ff1744" };
    const BUS_LINK_COLOR = "#ffd740";  // doré pour les connexions bus
    const IMPORT_LINK_COLOR = "#006622";
    const PLANNED_LINK_COLOR = "#444444";  // gris discret pour les projections
    const MIN_ZOOM = 0.3, MAX_ZOOM = 5;

    // --- STATE ---
    let svg, g, simulation, tooltip;
    let nodeEls, linkEls, labelEls;
    let graphData = null;
    let selectedNode = null;
    let initialized = false;
    let showBusLinks = true;
    let showImportLinks = true;
    let showPlannedLinks = true;

    // --- HELPERS ---
    function nodeRadius(d) {
        if (d.type === "planned") return 5;  // nœuds fantômes : petits
        var total = (d.imported_by_count || 0) + (d.subscribes ? d.subscribes.length : 0);
        return 6 + Math.sqrt(total) * 2.5;
    }
    function nodeColor(d) {
        if (d.type === "planned" && d.phase) return PHASE_COLORS[d.phase] || "#555";
        return TYPE_COLORS[d.type] || "#888";
    }
    function isPlanned(d) { return d.type === "planned"; }

    // --- INIT (lazy, uniquement à l'ouverture) ---
    function init() {
        if (initialized) return;
        initialized = true;

        var container = document.getElementById("impact-container");
        if (!container) return;

        var w = container.clientWidth || 800;
        var h = container.clientHeight || 600;

        svg = d3.select("#impact-svg")
            .attr("width", w)
            .attr("height", h);

        // Defs pour glow + flèches
        var defs = svg.append("defs");

        // Glow filter
        var filter = defs.append("filter").attr("id", "impact-glow");
        filter.append("feGaussianBlur").attr("stdDeviation", "4").attr("result", "blur");
        var feMerge = filter.append("feMerge");
        feMerge.append("feMergeNode").attr("in", "blur");
        feMerge.append("feMergeNode").attr("in", "SourceGraphic");

        // Arrowhead marker — imports (vert)
        defs.append("marker")
            .attr("id", "arrow-import")
            .attr("viewBox", "0 -3 6 6").attr("refX", 14).attr("refY", 0)
            .attr("markerWidth", 5).attr("markerHeight", 5).attr("orient", "auto")
            .append("path").attr("d", "M0,-3L6,0L0,3").attr("fill", IMPORT_LINK_COLOR);

        // Arrowhead marker — bus (doré)
        defs.append("marker")
            .attr("id", "arrow-bus")
            .attr("viewBox", "0 -3 6 6").attr("refX", 14).attr("refY", 0)
            .attr("markerWidth", 5).attr("markerHeight", 5).attr("orient", "auto")
            .append("path").attr("d", "M0,-3L6,0L0,3").attr("fill", BUS_LINK_COLOR);

        // Arrow highlight (cascade)
        defs.append("marker")
            .attr("id", "arrow-highlight")
            .attr("viewBox", "0 -3 6 6").attr("refX", 14).attr("refY", 0)
            .attr("markerWidth", 5).attr("markerHeight", 5).attr("orient", "auto")
            .append("path").attr("d", "M0,-3L6,0L0,3").attr("fill", "#ff5252");

        g = svg.append("g");

        // Zoom/Pan
        var zoom = d3.zoom()
            .scaleExtent([MIN_ZOOM, MAX_ZOOM])
            .on("zoom", function(event) { g.attr("transform", event.transform); });
        svg.call(zoom);

        // Click sur fond = deselection
        svg.on("click", function() {
            if (selectedNode) {
                selectedNode = null;
                resetHighlight();
            }
        });

        // Tooltip
        tooltip = document.getElementById("impact-tooltip");

        // ESC pour fermer
        document.addEventListener("keydown", function(e) {
            if (e.key === "Escape") close();
        });

        // Resize
        var ro = new ResizeObserver(function() {
            var nw = container.clientWidth;
            var nh = container.clientHeight;
            svg.attr("width", nw).attr("height", nh);
            if (simulation) {
                simulation.force("center", d3.forceCenter(nw / 2, nh / 2));
                updateClusterForces(nw, nh);
                simulation.alpha(0.1).restart();
            }
        });
        ro.observe(container);

        fetchGraph();
    }

    function fetchGraph() {
        fetch("/api/health/impact")
            .then(function(r) { return r.json(); })
            .then(function(data) {
                graphData = data;
                render(data);
                updateStats(data.stats);
            })
            .catch(function(err) { console.warn("[IMPACT] fetch error:", err); });
    }

    function updateClusterForces(w, h) {
        if (!simulation) return;
        simulation
            .force("x", d3.forceX(function(d) {
                return (TYPE_CLUSTER_X[d.type] || 0.5) * w;
            }).strength(0.07))
            .force("y", d3.forceY(function(d) {
                return (TYPE_CLUSTER_Y[d.type] || 0.5) * h;
            }).strength(0.07));
    }

    // --- RENDER ---
    function render(data) {
        if (!g) return;
        g.selectAll("*").remove();

        var container = document.getElementById("impact-container");
        var w = container ? container.clientWidth : 800;
        var h = container ? container.clientHeight : 600;

        // Filtrer les liens selon le toggle
        var visibleLinks = data.links.filter(function(l) {
            if (l.type === "bus" && !showBusLinks) return false;
            if (l.type === "imports" && !showImportLinks) return false;
            if (l.type === "planned" && !showPlannedLinks) return false;
            return true;
        });

        // Filtrer les nœuds planned si les liens planned sont masqués
        var visibleNodes = showPlannedLinks ? data.nodes :
            data.nodes.filter(function(n) { return n.type !== "planned"; });

        simulation = d3.forceSimulation(visibleNodes)
            .force("link", d3.forceLink(visibleLinks).id(function(d) { return d.id; })
                .distance(function(d) { return d.type === "planned" ? 150 : d.type === "bus" ? 120 : 80; })
                .strength(function(d) { return d.type === "planned" ? 0.15 : d.type === "bus" ? 0.3 : 0.7; }))
            .force("charge", d3.forceManyBody().strength(-180).distanceMax(500))
            .force("center", d3.forceCenter(w / 2, h / 2))
            .force("collision", d3.forceCollide().radius(function(d) { return nodeRadius(d) + 6; }));

        // Clustering par type
        updateClusterForces(w, h);

        // Links
        linkEls = g.append("g")
            .attr("class", "links")
            .selectAll("line")
            .data(visibleLinks)
            .join("line")
            .attr("stroke", function(d) {
                if (d.type === "planned") return PLANNED_LINK_COLOR;
                return d.type === "bus" ? BUS_LINK_COLOR : IMPORT_LINK_COLOR;
            })
            .attr("stroke-width", function(d) { return d.type === "planned" ? 0.5 : d.type === "bus" ? 0.8 : 1; })
            .attr("stroke-opacity", function(d) { return d.type === "planned" ? 0.25 : d.type === "bus" ? 0.35 : 0.5; })
            .attr("stroke-dasharray", function(d) {
                if (d.type === "planned") return "2,4";
                return d.type === "bus" ? "4,3" : null;
            })
            .attr("marker-end", function(d) {
                if (d.type === "planned") return null;  // pas de flèche pour les planned
                return d.type === "bus" ? "url(#arrow-bus)" : "url(#arrow-import)";
            });

        // Nodes
        nodeEls = g.append("g")
            .attr("class", "nodes")
            .selectAll("g")
            .data(visibleNodes)
            .join("g")
            .call(d3.drag()
                .on("start", dragStart)
                .on("drag", dragging)
                .on("end", dragEnd))
            .on("click", onNodeClick)
            .on("mouseover", onNodeHover)
            .on("mouseout", onNodeOut)
            .style("cursor", "pointer");

        // Halo erreur
        nodeEls.filter(function(d) { return d.status === "error"; })
            .append("circle")
            .attr("r", function(d) { return nodeRadius(d) + 5; })
            .attr("fill", "none")
            .attr("stroke", "#ff1744").attr("stroke-width", 1.5)
            .attr("stroke-opacity", 0.4).attr("filter", "url(#impact-glow)");

        // Halo degraded
        nodeEls.filter(function(d) { return d.status === "degraded"; })
            .append("circle")
            .attr("r", function(d) { return nodeRadius(d) + 4; })
            .attr("fill", "none")
            .attr("stroke", "#ffea00").attr("stroke-width", 1).attr("stroke-opacity", 0.3);

        // Cercle principal
        nodeEls.append("circle")
            .attr("r", nodeRadius)
            .attr("fill", function(d) { return isPlanned(d) ? "transparent" : nodeColor(d); })
            .attr("fill-opacity", function(d) { return isPlanned(d) ? 0 : 0.85; })
            .attr("stroke", nodeColor)
            .attr("stroke-width", function(d) { return isPlanned(d) ? 1.2 : 0.5; })
            .attr("stroke-opacity", function(d) { return isPlanned(d) ? 0.6 : 0.3; })
            .attr("stroke-dasharray", function(d) { return isPlanned(d) ? "2,2" : null; });

        // Labels
        labelEls = g.append("g")
            .attr("class", "labels")
            .selectAll("text")
            .data(visibleNodes)
            .join("text")
            .text(function(d) {
                if (isPlanned(d)) return "P" + d.phase + " " + (d.display || d.name);
                return d.display || d.name;
            })
            .attr("font-size", function(d) { return isPlanned(d) ? "8px" : "9px"; })
            .attr("font-family", "'Courier New', monospace")
            .attr("font-style", function(d) { return isPlanned(d) ? "italic" : "normal"; })
            .attr("fill", nodeColor)
            .attr("fill-opacity", function(d) { return isPlanned(d) ? 0.45 : 0.8; })
            .attr("dx", function(d) { return nodeRadius(d) + 4; })
            .attr("dy", 3)
            .attr("paint-order", "stroke")
            .attr("stroke", "#000")
            .attr("stroke-width", 2.5)
            .attr("stroke-opacity", function(d) { return isPlanned(d) ? 0.4 : 0.8; });

        // Légende
        renderLegend(w, h);

        simulation.on("tick", function() {
            linkEls
                .attr("x1", function(d) { return d.source.x; })
                .attr("y1", function(d) { return d.source.y; })
                .attr("x2", function(d) { return d.target.x; })
                .attr("y2", function(d) { return d.target.y; });
            nodeEls.attr("transform", function(d) { return "translate(" + d.x + "," + d.y + ")"; });
            labelEls.attr("x", function(d) { return d.x; }).attr("y", function(d) { return d.y; });
        });
    }

    // --- LÉGENDE ---
    function renderLegend(w, h) {
        var legend = g.append("g")
            .attr("class", "legend")
            .attr("transform", "translate(20," + (h - 290) + ")");

        // Fond
        legend.append("rect")
            .attr("x", -8).attr("y", -14)
            .attr("width", 165).attr("height", 285)
            .attr("rx", 3)
            .attr("fill", "rgba(0,5,0,0.85)")
            .attr("stroke", "#003300").attr("stroke-width", 0.5);

        // Types existants (sans planned)
        var realTypes = Object.entries(TYPE_COLORS).filter(function(e) { return e[0] !== "planned"; });
        realTypes.forEach(function(entry, i) {
            var type = entry[0], color = entry[1];
            var row = legend.append("g").attr("transform", "translate(0," + (i * 17) + ")");
            row.append("circle").attr("r", 4).attr("fill", color).attr("fill-opacity", 0.85);
            row.append("text").attr("x", 10).attr("dy", 3)
                .attr("font-size", "8px").attr("font-family", "'Courier New', monospace")
                .attr("fill", color).attr("fill-opacity", 0.8)
                .text(TYPE_LABELS[type] || type);
        });

        // Séparateur avant liens
        var sep = realTypes.length * 17 + 4;
        legend.append("line")
            .attr("x1", -4).attr("x2", 150).attr("y1", sep).attr("y2", sep)
            .attr("stroke", "#003300").attr("stroke-width", 0.5);

        // Liens
        var linkY = sep + 14;
        // Import
        legend.append("line")
            .attr("x1", 0).attr("x2", 20).attr("y1", linkY).attr("y2", linkY)
            .attr("stroke", IMPORT_LINK_COLOR).attr("stroke-width", 1);
        legend.append("text").attr("x", 26).attr("y", linkY + 3)
            .attr("font-size", "8px").attr("font-family", "'Courier New', monospace")
            .attr("fill", IMPORT_LINK_COLOR).attr("fill-opacity", 0.8)
            .text("IMPORT")
            .style("cursor", "pointer")
            .on("click", function(e) {
                e.stopPropagation();
                showImportLinks = !showImportLinks;
                if (graphData) render(graphData);
            });

        // Bus
        var busY = linkY + 17;
        legend.append("line")
            .attr("x1", 0).attr("x2", 20).attr("y1", busY).attr("y2", busY)
            .attr("stroke", BUS_LINK_COLOR).attr("stroke-width", 0.8)
            .attr("stroke-dasharray", "4,3");
        legend.append("text").attr("x", 26).attr("y", busY + 3)
            .attr("font-size", "8px").attr("font-family", "'Courier New', monospace")
            .attr("fill", BUS_LINK_COLOR).attr("fill-opacity", 0.8)
            .text("EVENT BUS")
            .style("cursor", "pointer")
            .on("click", function(e) {
                e.stopPropagation();
                showBusLinks = !showBusLinks;
                if (graphData) render(graphData);
            });

        // Séparateur avant roadmap
        var sep2 = busY + 12;
        legend.append("line")
            .attr("x1", -4).attr("x2", 150).attr("y1", sep2).attr("y2", sep2)
            .attr("stroke", "#003300").attr("stroke-width", 0.5);

        // Titre ROADMAP
        var rmY = sep2 + 14;
        legend.append("text").attr("x", 0).attr("y", rmY)
            .attr("font-size", "8px").attr("font-family", "'Courier New', monospace")
            .attr("fill", "#888").attr("font-weight", "bold")
            .text("ROADMAP \u2014 \u00c9veil")
            .style("cursor", "pointer")
            .on("click", function(e) {
                e.stopPropagation();
                showPlannedLinks = !showPlannedLinks;
                if (graphData) render(graphData);
            });

        // Phases
        var phases = [
            [4, "CONNECTER"],
            [5, "COMPRENDRE"],
            [6, "CR\u00c9ER"],
            [7, "TRANSCENDER"],
        ];
        phases.forEach(function(p, i) {
            var py = rmY + 16 + i * 17;
            var pc = PHASE_COLORS[p[0]] || "#555";
            var row = legend.append("g").attr("transform", "translate(4," + py + ")");
            // Cercle pointillé (comme les nœuds planned)
            row.append("circle").attr("r", 4)
                .attr("fill", "transparent")
                .attr("stroke", pc).attr("stroke-width", 1)
                .attr("stroke-dasharray", "2,2").attr("stroke-opacity", 0.6);
            row.append("text").attr("x", 10).attr("dy", 3)
                .attr("font-size", "7px").attr("font-family", "'Courier New', monospace")
                .attr("fill", pc).attr("fill-opacity", 0.7).attr("font-style", "italic")
                .text("P" + p[0] + " " + p[1]);
        });
    }

    // --- INTERACTIONS ---
    function onNodeClick(event, d) {
        event.stopPropagation();
        if (selectedNode === d.id) {
            selectedNode = null;
            resetHighlight();
            return;
        }
        selectedNode = d.id;
        highlightCascade(d.id);
    }

    function highlightCascade(moduleId) {
        if (!graphData) return;

        // BFS sur imported_by + bus subscribers
        var affected = new Set([moduleId]);
        var importedBy = {};
        graphData.links.forEach(function(l) {
            var src = typeof l.source === "object" ? l.source.id : l.source;
            var tgt = typeof l.target === "object" ? l.target.id : l.target;
            // import: source importe target → si target casse, source est affecté
            if (l.type === "imports") {
                if (!importedBy[tgt]) importedBy[tgt] = [];
                importedBy[tgt].push(src);
            }
            // bus: source publie vers target → si source casse, target perd son event
            if (l.type === "bus") {
                if (!importedBy[src]) importedBy[src] = [];
                importedBy[src].push(tgt);
            }
        });

        var queue = [moduleId];
        while (queue.length > 0) {
            var current = queue.shift();
            (importedBy[current] || []).forEach(function(dep) {
                if (!affected.has(dep)) {
                    affected.add(dep);
                    queue.push(dep);
                }
            });
        }

        // Dimmer les non-affectés (avec transitions)
        nodeEls.select("circle:last-of-type")
            .transition().duration(300)
            .attr("fill-opacity", function(d) { return affected.has(d.id) ? 1.0 : 0.08; })
            .attr("stroke-opacity", function(d) { return affected.has(d.id) ? 0.5 : 0.02; });
        labelEls
            .transition().duration(300)
            .attr("fill-opacity", function(d) { return affected.has(d.id) ? 1.0 : 0.06; });
        linkEls
            .transition().duration(300)
            .attr("stroke-opacity", function(d) {
                var src = typeof d.source === "object" ? d.source.id : d.source;
                var tgt = typeof d.target === "object" ? d.target.id : d.target;
                return (affected.has(src) && affected.has(tgt)) ? 0.9 : 0.02;
            })
            .attr("stroke", function(d) {
                var src = typeof d.source === "object" ? d.source.id : d.source;
                var tgt = typeof d.target === "object" ? d.target.id : d.target;
                return (affected.has(src) && affected.has(tgt)) ? "#ff5252" : "#111";
            })
            .attr("stroke-width", function(d) {
                var src = typeof d.source === "object" ? d.source.id : d.source;
                var tgt = typeof d.target === "object" ? d.target.id : d.target;
                return (affected.has(src) && affected.has(tgt)) ? 2 : 0.3;
            })
            .attr("marker-end", function(d) {
                var src = typeof d.source === "object" ? d.source.id : d.source;
                var tgt = typeof d.target === "object" ? d.target.id : d.target;
                return (affected.has(src) && affected.has(tgt)) ? "url(#arrow-highlight)" : "";
            });

        // Info cascade
        var el = document.getElementById("impact-cascade-info");
        if (el) {
            var modNode = graphData.nodes.find(function(n) { return n.id === moduleId; });
            var name = modNode ? modNode.display : moduleId;
            el.innerHTML = '<span style="color:#ff5252">CASCADE: ' + name + ' \u2192 ' + (affected.size - 1) + ' modules affect\u00e9s</span>';
        }
    }

    function resetHighlight() {
        if (!nodeEls) return;
        nodeEls.select("circle:last-of-type")
            .transition().duration(300)
            .attr("fill-opacity", function(d) { return isPlanned(d) ? 0 : 0.85; })
            .attr("stroke-opacity", function(d) { return isPlanned(d) ? 0.6 : 0.3; });
        labelEls
            .transition().duration(300)
            .attr("fill-opacity", function(d) { return isPlanned(d) ? 0.45 : 0.8; });
        linkEls
            .transition().duration(300)
            .attr("stroke-opacity", function(d) {
                if (d.type === "planned") return 0.25;
                return d.type === "bus" ? 0.35 : 0.5;
            })
            .attr("stroke", function(d) {
                if (d.type === "planned") return PLANNED_LINK_COLOR;
                return d.type === "bus" ? BUS_LINK_COLOR : IMPORT_LINK_COLOR;
            })
            .attr("stroke-width", function(d) {
                if (d.type === "planned") return 0.5;
                return d.type === "bus" ? 0.8 : 1;
            })
            .attr("marker-end", function(d) {
                if (d.type === "planned") return null;
                return d.type === "bus" ? "url(#arrow-bus)" : "url(#arrow-import)";
            });

        var el = document.getElementById("impact-cascade-info");
        if (el) el.innerHTML = "";
    }

    function onNodeHover(event, d) {
        if (!tooltip) return;
        var lines = [];

        if (isPlanned(d)) {
            // Tooltip spécial pour les nœuds planifiés
            var pc = PHASE_COLORS[d.phase] || "#555";
            lines.push('<b style="color:' + pc + '">' + d.display + '</b> <span style="color:#666">(planifi\u00e9)</span>');
            lines.push('<span style="color:' + pc + '">Phase ' + d.phase + ' \u2014 ' + (d.phase_name || '') + '</span>');
            if (d.description) {
                lines.push('<span style="color:#aaa;font-style:italic">' + d.description + '</span>');
            }
            lines.push('<span style="color:#666">Connexions pr\u00e9vues: ' + (d.connects_to_count || 0) + ' modules</span>');
        } else {
            // Tooltip normal
            var statusColor = d.status === "error" ? "#ff1744" : d.status === "degraded" ? "#ffea00" : "#00ff41";
            lines.push('<b style="color:' + nodeColor(d) + '">' + d.id + '</b>');
            lines.push('<span style="color:#888">Type:</span> <span style="color:' + nodeColor(d) + '">' + (TYPE_LABELS[d.type] || d.type) + '</span>');
            lines.push('<span style="color:#888">Sant\u00e9:</span> <span style="color:' + statusColor + '">' + d.status.toUpperCase() + '</span>');
            if (d.error_count > 0) {
                lines.push('<span style="color:#888">Erreurs:</span> <span style="color:#ff5252">' + d.error_count + '</span>');
            }
            lines.push('<span style="color:#888">Imports top-level:</span> ' + d.import_count);
            if (d.local_import_count > 0) {
                lines.push('<span style="color:#888">Imports locaux:</span> <span style="color:#aaa">' + d.local_import_count + '</span>');
            }
            lines.push('<span style="color:#888">Import\u00e9 par:</span> ' + d.imported_by_count);
            if (d.publishes && d.publishes.length > 0) {
                lines.push('<span style="color:' + BUS_LINK_COLOR + '">Publie:</span> ' + d.publishes.join(', '));
            }
            if (d.subscribes && d.subscribes.length > 0) {
                lines.push('<span style="color:' + BUS_LINK_COLOR + '">\u00c9coute:</span> ' + d.subscribes.join(', '));
            }
            if (d.last_modified) {
                lines.push('<span style="color:#888">Modifi\u00e9:</span> ' + new Date(d.last_modified * 1000).toLocaleDateString("fr-FR"));
            }
        }
        tooltip.innerHTML = lines.join("<br>");
        tooltip.style.display = "block";
        tooltip.style.left = (event.clientX + 14) + "px";
        tooltip.style.top = (event.clientY - 12) + "px";
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
        var el = document.getElementById("impact-stats");
        if (!el || !stats) return;
        el.innerHTML =
            '<span style="color:#00ff41">' + stats.healthy + ' OK</span> ' +
            '<span style="color:#ffea00">' + stats.degraded + ' DEG</span> ' +
            '<span style="color:#ff1744">' + stats.error + ' ERR</span> ' +
            '<span style="color:#666">/ ' + stats.total_modules + '</span> ' +
            '<span style="color:' + IMPORT_LINK_COLOR + '">' + stats.import_links + ' imp</span> ' +
            '<span style="color:' + BUS_LINK_COLOR + '">' + stats.bus_links + ' bus</span> ' +
            '<span style="color:#555">' + (stats.planned_modules || 0) + ' roadmap</span>';
    }

    // --- OPEN / CLOSE ---
    function open() {
        var overlay = document.getElementById("impact-overlay");
        if (!overlay) return;
        overlay.style.display = "flex";
        init();
        fetchGraph();
    }

    function close() {
        var overlay = document.getElementById("impact-overlay");
        if (overlay) overlay.style.display = "none";
        selectedNode = null;
    }

    // --- HANDLE UPDATE (via bus) ---
    function handleUpdate(payload) {
        graphData = null;
        if (document.getElementById("impact-overlay") &&
            document.getElementById("impact-overlay").style.display !== "none") {
            fetchGraph();
        }
    }

    // --- PUBLIC API ---
    return { init: init, open: open, close: close, handleUpdate: handleUpdate };
})();
