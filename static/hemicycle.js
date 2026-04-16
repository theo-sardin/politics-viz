/* Hemicycle chart — renders deputies as individual dots in a semicircle.
   Depends on: D3 v7, pv.escapeHtml, pv.formatEur (from app.js).
   Usage: pv.hemicycle.render(svgSel, wrapEl, seats, groups)           */

window.pv = window.pv || {};
pv.hemicycle = (function () {
    const esc = pv.escapeHtml;

    // ---- Layout computation ------------------------------------------------

    /** How many concentric rows for N seats (empirically tuned). */
    function rowCount(nSeats) {
        return Math.max(8, Math.min(20, Math.round(Math.sqrt(nSeats / 2.5))));
    }

    /**
     * Distribute N seats across rows proportionally to each row's arc length.
     * Returns an array of integers that sum exactly to nSeats.
     */
    function distributeSeats(nSeats, nRows, rowRadii) {
        const totalArc = rowRadii.reduce((a, b) => a + b, 0);
        const perRow = rowRadii.map(r => Math.max(1, Math.round(nSeats * r / totalArc)));

        // Fix rounding drift so the total matches exactly
        let diff = nSeats - perRow.reduce((a, b) => a + b, 0);
        let idx = diff > 0 ? nRows - 1 : 0;
        while (diff !== 0) {
            perRow[idx] += Math.sign(diff);
            diff -= Math.sign(diff);
            idx = (idx + (diff > 0 ? -1 : 1) + nRows) % nRows;
        }
        return perRow;
    }

    /**
     * Generate {x, y} positions for every seat.
     *
     * Seats sit on concentric semicircular arcs (angle π = left, 0 = right).
     * Positions are sorted by their fractional position along the arc (0 = left
     * edge, 1 = right edge) so that seats at the same "radial column" across
     * different rows are adjacent.  This produces clean radial alignment — party
     * boundaries form straight lines radiating from the centre rather than
     * zigzagging between rows.
     *
     * NOTE: positions are representative (political spectrum left→right),
     * not actual assigned seat numbers — that data isn't in our sources.
     *
     * @returns {{ positions: Array, height: number, seatRadius: number }}
     */
    function computePositions(nSeats, containerWidth) {
        const halfW = containerWidth / 2;
        const nRows = rowCount(nSeats);

        // Fraction of halfW for inner / outer radii
        const innerR = 0.35;
        const outerR = 0.95;
        const rowStep = (outerR - innerR) / nRows;

        // Centre of each row (as fraction of scale)
        const rowRadii = [];
        for (let i = 0; i < nRows; i++) rowRadii.push(innerR + (i + 0.5) * rowStep);

        const seatsPerRow = distributeSeats(nSeats, nRows, rowRadii);

        // Cap pixel height so the hemicycle stays compact
        const maxH = Math.min(halfW * 0.85, 400);
        const scale = maxH / outerR;

        // Angular padding — avoid seats exactly at the horizontal edges
        const aPad = 0.06;
        const aMin = aPad;
        const aMax = Math.PI - aPad;

        const topMargin = 10;
        const botMargin = 5;
        const svgH = maxH + topMargin + botMargin;
        const cy = svgH - botMargin;   // vertical centre of the arcs

        // Each row spaces its own seats evenly along the arc (clean arcs).
        // A virtual column index determines fill order so that parties form
        // wedge-shaped sectors instead of horizontal bands.
        const maxSeats = Math.max(...seatsPerRow);

        const positions = [];
        for (let row = 0; row < nRows; row++) {
            const r = rowRadii[row] * scale;
            const n = seatsPerRow[row];
            for (let j = 0; j < n; j++) {
                const frac = j / Math.max(n - 1, 1);
                // Position: row's own even spacing (clean arcs)
                const angle = aMax - frac * (aMax - aMin);
                // Column: virtual index for sort order only (clean wedges)
                const col = Math.round(frac * (maxSeats - 1));
                positions.push({
                    x: halfW + r * Math.cos(angle),
                    y: cy - r * Math.sin(angle),
                    col,
                    row,
                });
            }
        }

        // Sort by column (left→right), then by row (inner→outer).
        // This determines which deputy sits where — parties form wedges.
        positions.sort((a, b) => a.col - b.col || a.row - b.row);

        const seatRadius = Math.max(2.5, Math.min(6, scale * rowStep * 0.4));

        return { positions, height: svgH, seatRadius };
    }

    // ---- DOM rendering -----------------------------------------------------

    /** Draw one circle per seat on the SVG, with tooltip + click behaviour. */
    function drawSeats(svg, positions, seats, seatR, wrapEl, tooltip) {
        // Pair each position with its seat data
        positions.forEach((p, i) => { p.seat = seats[i]; });

        svg.selectAll('circle')
            .data(positions)
            .enter()
            .append('circle')
            .attr('cx', d => d.x)
            .attr('cy', d => d.y)
            .attr('r', seatR)
            .attr('fill', d => d.seat ? d.seat.party_color : '#999')
            .style('cursor', 'pointer')
            .on('mouseenter', function (e, d) {
                if (!d.seat) return;
                d3.select(this).attr('r', seatR * 1.8).raise();
                const s = d.seat;
                tooltip.innerHTML =
                    `<strong>${esc(s.prenom)} ${esc(s.nom)}</strong><br>`
                    + `<span class="party-pill" style="background:${s.party_color};font-size:0.7rem">${esc(s.party_label)}</span>`
                    + (s.total ? ` · ${pv.formatEur(s.total)}` : '');
                tooltip.style.display = 'block';
                positionTooltip(tooltip, wrapEl, e);
            })
            .on('mousemove', function (e) { positionTooltip(tooltip, wrapEl, e); })
            .on('mouseleave', function () {
                d3.select(this).attr('r', seatR);
                tooltip.style.display = 'none';
            })
            .on('click', (e, d) => { if (d.seat) window.location = '/declarant/' + d.seat.id; });
    }

    function positionTooltip(tooltip, wrapEl, event) {
        const rect = wrapEl.getBoundingClientRect();
        tooltip.style.left = (event.clientX - rect.left + 14) + 'px';
        tooltip.style.top  = (event.clientY - rect.top  - 55) + 'px';
    }

    /** Thin proportional bar showing seat counts per party. */
    function renderPartyBar(container, groups) {
        const total = groups.reduce((s, g) => s + g.n_seats, 0);
        container.innerHTML = '<div class="party-bar">' + groups.map(g => {
            const pct = (100 * g.n_seats / total).toFixed(1);
            const label = g.n_seats / total > 0.06 ? `<span>${g.n_seats}</span>` : '';
            return `<a href="/party/${g.slug}" class="party-bar-seg" `
                + `style="width:${pct}%;background:${g.color}" `
                + `title="${esc(g.label)} — ${g.n_seats} sièges">${label}</a>`;
        }).join('') + '</div>';
    }

    /** Legend row: coloured dot + party name + seat count, one per party. */
    function renderLegend(container, groups) {
        container.innerHTML = groups.map(g =>
            `<a href="/party/${g.slug}" class="comp-legend-item" style="text-decoration:none;color:inherit">`
            + `<span class="comp-dot" style="background:${g.color}"></span>`
            + `${esc(g.label)} <strong>(${g.n_seats})</strong></a>`
        ).join('');
    }

    // ---- Public API --------------------------------------------------------

    /**
     * Render the full hemicycle visualisation.
     *
     * @param {d3.Selection} svg       - D3 selection of the <svg> element
     * @param {HTMLElement}  wrapEl    - wrapper div (for tooltip positioning)
     * @param {Array}        seats     - per-deputy objects, pre-sorted left→right
     * @param {Array}        groups    - per-party aggregates (slug, label, color, n_seats)
     * @param {HTMLElement}  barEl     - container for the party bar
     * @param {HTMLElement}  legendEl  - container for the legend
     */
    function render(svg, wrapEl, seats, groups, barEl, legendEl) {
        svg.selectAll('*').remove();
        if (!seats.length) return;

        const W = wrapEl.clientWidth || 800;
        const { positions, height, seatRadius } = computePositions(seats.length, W);

        svg.attr('width', W).attr('height', height).attr('viewBox', `0 0 ${W} ${height}`);

        const tooltip = wrapEl.querySelector('.tooltip');
        drawSeats(svg, positions, seats, seatRadius, wrapEl, tooltip);

        renderPartyBar(barEl, groups);
        renderLegend(legendEl, groups);
    }

    return { render };
})();
