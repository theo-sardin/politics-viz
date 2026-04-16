/* Hemicycle chart — renders deputies as individual dots in a semicircle.
   Depends on: D3 v7, pv.escapeHtml, pv.formatEur (from app.js).
   Usage: pv.hemicycle.render(svgSel, wrapEl, seats, groups)           */

window.pv = window.pv || {};
pv.hemicycle = (function () {
    const esc = pv.escapeHtml;

    // ---- Layout helpers ----------------------------------------------------

    function rowCount(nSeats) {
        return Math.max(8, Math.min(20, Math.round(Math.sqrt(nSeats / 2.5))));
    }

    /** Distribute N items across rows proportionally to arc length. */
    function distributeToRows(nSeats, nRows, rowRadii) {
        const totalArc = rowRadii.reduce((a, b) => a + b, 0);
        const perRow = rowRadii.map(r => Math.max(1, Math.round(nSeats * r / totalArc)));
        let diff = nSeats - perRow.reduce((a, b) => a + b, 0);
        let idx = diff > 0 ? nRows - 1 : 0;
        while (diff !== 0) {
            perRow[idx] += Math.sign(diff);
            diff -= Math.sign(diff);
            idx = (idx + (diff > 0 ? -1 : 1) + nRows) % nRows;
        }
        return perRow;
    }

    /** Largest-remainder apportionment of `total` across groups. */
    function apportion(groupSizes, total) {
        const grand = groupSizes.reduce((a, b) => a + b, 0);
        if (grand === 0) return groupSizes.map(() => 0);
        const ideals = groupSizes.map(s => s / grand * total);
        const floors = ideals.map(Math.floor);
        let rem = total - floors.reduce((a, b) => a + b, 0);
        const order = ideals.map((v, i) => ({ i, r: v - floors[i] })).sort((a, b) => b.r - a.r);
        for (let k = 0; k < rem; k++) floors[order[k].i]++;
        return floors;
    }

    // ---- Core layout -------------------------------------------------------

    /**
     * Build positioned seats for the hemicycle.
     *
     * Party boundary angles are fixed globally (based on total seat fractions)
     * so they form perfectly straight radial lines across all rows.  Within
     * each party's angular wedge, that row's seats are evenly spaced.
     */
    function computePositions(seats, containerWidth) {
        const N = seats.length;
        const halfW = containerWidth / 2;
        const nRows = rowCount(N);

        const innerR = 0.35, outerR = 0.95;
        const rowStep = (outerR - innerR) / nRows;
        const rowRadii = [];
        for (let i = 0; i < nRows; i++) rowRadii.push(innerR + (i + 0.5) * rowStep);

        const seatsPerRow = distributeToRows(N, nRows, rowRadii);

        const maxH = Math.min(halfW * 0.85, 400);
        const scale = maxH / outerR;
        const aPad = 0.06, aMin = aPad, aMax = Math.PI - aPad;
        const aRange = aMax - aMin;
        const topMargin = 10, botMargin = 5;
        const svgH = maxH + topMargin + botMargin;
        const cy = svgH - botMargin;

        // Identify party groups in the pre-sorted seat list.
        const groups = [];
        let gStart = 0;
        for (let i = 1; i <= N; i++) {
            if (i === N || seats[i].party_slug !== seats[gStart].party_slug) {
                groups.push({ start: gStart, count: i - gStart });
                gStart = i;
            }
        }
        const groupSizes = groups.map(g => g.count);
        const groupUsed = groups.map(() => 0);
        const positions = [];

        for (let row = 0; row < nRows; row++) {
            const r = rowRadii[row] * scale;
            const n = seatsPerRow[row];

            // Decide which deputies sit in this row (proportional per party)
            const rowAlloc = apportion(groupSizes, n);
            const rowSeats = [];
            for (let gi = 0; gi < groups.length; gi++) {
                for (let k = 0; k < rowAlloc[gi]; k++) {
                    rowSeats.push(seats[groups[gi].start + groupUsed[gi]++]);
                }
            }

            // Place ALL seats evenly along the full arc (clean arcs).
            // Party boundaries emerge from the proportional allocation —
            // no angular wedges needed.
            for (let j = 0; j < n; j++) {
                const frac = j / Math.max(n - 1, 1);
                const angle = aMax - frac * aRange;
                positions.push({
                    x: halfW + r * Math.cos(angle),
                    y: cy - r * Math.sin(angle),
                    seat: rowSeats[j],
                });
            }
        }

        const seatRadius = Math.max(2.5, Math.min(6, scale * rowStep * 0.4));
        return { positions, height: svgH, seatRadius };
    }

    // ---- DOM rendering -----------------------------------------------------

    function drawSeats(svg, positions, seatR, wrapEl, tooltip) {
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

    function renderLegend(container, groups) {
        container.innerHTML = groups.map(g =>
            `<a href="/party/${g.slug}" class="comp-legend-item" style="text-decoration:none;color:inherit">`
            + `<span class="comp-dot" style="background:${g.color}"></span>`
            + `${esc(g.label)} <strong>(${g.n_seats})</strong></a>`
        ).join('');
    }

    // ---- Public API --------------------------------------------------------

    function render(svg, wrapEl, seats, groups, barEl, legendEl) {
        svg.selectAll('*').remove();
        if (!seats.length) return;

        const W = wrapEl.clientWidth || 800;
        const { positions, height, seatRadius } = computePositions(seats, W);

        svg.attr('width', W).attr('height', height).attr('viewBox', `0 0 ${W} ${height}`);

        const tooltip = wrapEl.querySelector('.tooltip');
        drawSeats(svg, positions, seatRadius, wrapEl, tooltip);

        renderPartyBar(barEl, groups);
        renderLegend(legendEl, groups);
    }

    return { render };
})();
