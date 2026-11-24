/* Bipartite force graph: people (circles, group colour) and the companies
   several of them hold (squares). Depends on d3 v7. */
(async function () {
    "use strict";
    const script = document.currentScript || document.querySelector("script[data-url]");
    const svgEl = document.getElementById("net");
    const count = document.getElementById("net-count");
    if (!svgEl || !window.d3) return;

    const BASE = document.documentElement.dataset.base || "";
    const form = document.getElementById("net-filters");
    const params = new URLSearchParams(location.search);
    ["secteur", "min"].forEach((name) => { if (params.has(name)) form.elements[name].value = params.get(name); });
    // Changing a filter reloads the page with the choice in the query string: the layout is recomputed anyway.
    form.addEventListener("change", () => {
        const next = new URLSearchParams();
        if (form.elements.secteur.value) next.set("secteur", form.elements.secteur.value);
        next.set("min", form.elements.min.value);
        location.search = next.toString();
    });

    let full;
    try {
        full = await (await fetch(BASE + script.dataset.url)).json();
    } catch (err) {
        count.textContent = "Chargement impossible.";
        return;
    }
    // The file holds every company with two holders or more; narrow it here.
    const sector = form.elements.secteur.value;
    const min = parseInt(form.elements.min.value, 10) || 2;
    const kept = new Set(full.nodes
        .filter((n) => n.type === "company" && n.n >= min && (!sector || n.sectors.includes(sector)))
        .map((n) => n.id));
    const links = full.links.filter((l) => kept.has(l.target));
    const linked = new Set(links.map((l) => l.source));
    const data = { nodes: full.nodes.filter((n) => kept.has(n.id) || linked.has(n.id)), links };

    const people = data.nodes.filter((n) => n.type === "person");
    const companies = data.nodes.filter((n) => n.type === "company");
    count.textContent = `${people.length} élus · ${companies.length} sociétés · ${data.links.length} participations`;
    if (!data.nodes.length) {
        count.textContent = "Aucune société ne remplit ces critères : baissez le seuil.";
        return;
    }

    const chosen = document.documentElement.dataset.theme;
    const dark = chosen ? chosen === "dark" : window.matchMedia("(prefers-color-scheme: dark)").matches;
    const hue = (n) => (dark ? n.color_dark : n.color) || "var(--neutral)";
    const width = svgEl.clientWidth, height = svgEl.clientHeight;
    const svg = d3.select(svgEl).attr("viewBox", [0, 0, width, height]);
    const root = svg.append("g");
    const zoom = d3.zoom().scaleExtent([0.2, 4]).on("zoom", (e) => root.attr("transform", e.transform));
    svg.call(zoom);

    const side = (n) => 9 + Math.sqrt(n.n) * 3.2;
    const link = root.append("g").selectAll("line").data(data.links).join("line").attr("stroke-width", 1);
    const node = root.append("g").selectAll("g").data(data.nodes).join("g")
        .attr("class", (d) => "node " + (d.type === "company" ? "co" : "pe"))
        .attr("tabindex", 0)
        .attr("data-tip", (d) => d.type === "company"
            ? `${d.label}\n${d.n} détenteurs dans ce périmètre`
            : `${d.label}\n${d.code || "Sans groupe"}`);

    node.filter((d) => d.type === "company").append("rect")
        .attr("width", (d) => side(d) * 2).attr("height", (d) => side(d) * 2)
        .attr("x", (d) => -side(d)).attr("y", (d) => -side(d)).attr("rx", 4);
    node.filter((d) => d.type === "person").append("circle").attr("r", 6).style("--g", hue);
    // Only companies are labelled permanently: there are few of them, and they are the hubs.
    node.filter((d) => d.type === "company").append("text")
        .attr("text-anchor", "middle").attr("dy", (d) => side(d) + 13)
        .text((d) => (d.label.length > 24 ? d.label.slice(0, 23) + "…" : d.label));

    const neighbours = new Map(data.nodes.map((n) => [n.id, new Set([n.id])]));
    const sim = d3.forceSimulation(data.nodes)
        .force("link", d3.forceLink(data.links).id((d) => d.id).distance(70).strength(0.3))
        .force("charge", d3.forceManyBody().strength((d) => (d.type === "company" ? -620 : -70)))
        .force("x", d3.forceX(width / 2).strength(0.03))
        .force("y", d3.forceY(height / 2).strength(0.09))
        .force("collide", d3.forceCollide((d) => (d.type === "company" ? side(d) + 26 : 10)))
        .stop();
    // Settle the layout before the first paint, then frame it.
    for (let i = 0; i < 320; i++) sim.tick();
    data.links.forEach((l) => {
        neighbours.get(l.source.id).add(l.target.id);
        neighbours.get(l.target.id).add(l.source.id);
    });

    function draw() {
        link.attr("x1", (d) => d.source.x).attr("y1", (d) => d.source.y)
            .attr("x2", (d) => d.target.x).attr("y2", (d) => d.target.y);
        node.attr("transform", (d) => `translate(${d.x},${d.y})`);
    }
    draw();
    sim.on("tick", draw);
    const xs = d3.extent(data.nodes, (d) => d.x), ys = d3.extent(data.nodes, (d) => d.y);
    const k = Math.min(1.5, 0.92 * Math.min(width / (xs[1] - xs[0] + 80), height / (ys[1] - ys[0] + 80)));
    svg.call(zoom.transform, d3.zoomIdentity
        .translate(width / 2 - k * (xs[0] + xs[1]) / 2, height / 2 - k * (ys[0] + ys[1]) / 2).scale(k));

    function focus(d) {
        const keep = neighbours.get(d.id);
        node.classed("dim", (n) => !keep.has(n.id));
        link.classed("dim", (l) => l.source.id !== d.id && l.target.id !== d.id);
    }
    function clear() { node.classed("dim", false); link.classed("dim", false); }
    node.on("pointerenter", (e, d) => focus(d)).on("pointerleave", clear)
        .on("focus", (e, d) => focus(d)).on("blur", clear)
        .on("click", (e, d) => { window.location = BASE + d.url; })
        .on("keydown", (e, d) => { if (e.key === "Enter") window.location = BASE + d.url; });
    node.call(d3.drag()
        .on("start", (e, d) => { if (!e.active) sim.alphaTarget(0.25).restart(); d.fx = d.x; d.fy = d.y; })
        .on("drag", (e, d) => { d.fx = e.x; d.fy = e.y; })
        .on("end", (e, d) => { if (!e.active) sim.alphaTarget(0); d.fx = null; d.fy = null; }));

    // Legend: the groups actually present, in a stable order.
    const legend = document.getElementById("net-legend");
    const seen = new Map();
    people.forEach((p) => { if (p.code && !seen.has(p.code)) seen.set(p.code, p); });
    [...seen.values()].sort((a, b) => a.code.localeCompare(b.code)).forEach((p) => {
        const item = document.createElement("span");
        item.className = "grp";
        const dot = document.createElement("span");
        dot.className = "dot";
        dot.style.setProperty("--g", hue(p));
        item.append(dot, document.createTextNode(p.code));
        legend.appendChild(item);
    });
    const sq = document.createElement("span");
    sq.className = "grp";
    sq.textContent = "□ société (taille : nombre de détenteurs)";
    legend.appendChild(sq);
})();
