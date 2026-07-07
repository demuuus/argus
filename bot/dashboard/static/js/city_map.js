/**
 * city_map.js - City Exposure Overview map (Phase 7).
 *
 * Renders one Leaflet marker per MAPPED city (cities without a configured
 * centroid are shown in the exposure table only, never on the map - they
 * are intentionally excluded here, not a bug).
 *
 * Data comes from the data-cities attribute already rendered server-side
 * by index.html (no extra fetch needed on initial load - the same
 * aggregate data used for the table is reused for the map, avoiding a
 * second round trip). The /api/dashboard/city-exposure endpoint exists
 * for any other consumer that needs the same data independently of the
 * dashboard page itself.
 *
 * Per the feature spec: city-centroid markers ONLY, sized by asset_count,
 * colored by risk_level, no per-asset pins, no exact coordinates.
 */

(function () {
    "use strict";

    function initCityMap() {
        const container = document.getElementById("city-map");
        if (!container) return; // No map on this page — nothing to do.

        let cities = [];
        try {
            cities = JSON.parse(container.dataset.cities || "[]");
        } catch (err) {
            console.error("[city_map] Failed to parse city data:", err);
            renderEmptyState(container, "Unable to load city exposure data.");
            return;
        }

        // Only mapped cities get a marker; unmapped ones are intentionally
        // table-only (see panel-header note rendered server-side).
        const mappedCities = cities.filter(c => c.mapped && c.lat != null && c.lng != null);

        if (mappedCities.length === 0) {
            renderEmptyState(
                container,
                cities.length === 0
                    ? "No assets have a city assigned yet."
                    : "No cities have a map location configured yet."
            );
            return;
        }

        let map;
        try {
            map = L.map(container, {
                scrollWheelZoom: false, // avoid hijacking page scroll inside a dashboard panel
            });
        } catch (err) {
            // A Leaflet init failure (e.g. the library failed to load) must
            // never break the rest of the dashboard — fail visibly inside
            // the map panel only.
            console.error("[city_map] Leaflet failed to initialize:", err);
            renderEmptyState(container, "Map failed to load.");
            return;
        }

        // OpenStreetMap tiles — free, no API key required. If tiles fail
        // to load (offline, blocked, rate-limited), Leaflet just shows
        // blank/gray tiles under the markers; it does not throw or break
        // the page, so no extra handling is needed here beyond logging.
        const tileLayer = L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
            maxZoom: 18,
            attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
        });
        tileLayer.on("tileerror", () => {
            console.warn("[city_map] A map tile failed to load; map remains usable.");
        });
        tileLayer.addTo(map);

        const bounds = [];
        const maxAssetCount = Math.max(...mappedCities.map(c => c.asset_count || 0), 1);

        mappedCities.forEach((c) => {
            const radius = markerRadius(c.asset_count, maxAssetCount);
            const color = c.risk_color || "#7a869a";

            const marker = L.circleMarker([c.lat, c.lng], {
                radius: radius,
                color: color,
                weight: c.kev_count > 0 ? 3 : 1,   // thicker outline = KEV present, per spec's "visually obvious" requirement
                fillColor: color,
                fillOpacity: 0.55,
            }).addTo(map);

            marker.bindPopup(buildPopupHtml(c));
            bounds.push([c.lat, c.lng]);
        });

        if (bounds.length === 1) {
            map.setView(bounds[0], 4);
        } else {
            map.fitBounds(bounds, { padding: [30, 30] });
        }

        // Leaflet sizes itself based on the container's dimensions at the
        // moment of creation. If the panel was hidden/animating or fonts
        // were still loading, the map can render at the wrong size. A
        // short delayed invalidateSize() call (the official Leaflet fix
        // for this) ensures the map fills its container correctly.
        setTimeout(() => map.invalidateSize(), 250);

        // Re-check sizing whenever the window resizes, so the map never
        // causes horizontal overflow on narrow screens (spec requirement).
        window.addEventListener("resize", () => map.invalidateSize());
    }

    function markerRadius(assetCount, maxAssetCount) {
        // Linear scale between a sensible minimum and maximum pixel
        // radius, so a city with very few assets is still visible and a
        // city with many assets doesn't dominate the whole map.
        const minR = 8, maxR = 28;
        const ratio = Math.min(1, (assetCount || 0) / maxAssetCount);
        return minR + ratio * (maxR - minR);
    }

    function buildPopupHtml(c) {
        // All values come from server-rendered, already-escaped JSON data
        // (Jinja2's |tojson filter HTML-escapes by default) — no further
        // escaping needed here, but city names are still treated as plain
        // text via textContent-equivalent string building, not innerHTML
        // concatenation of unknown origin.
        const kevLine = c.kev_count > 0
            ? `<div style="color:#f75f5f;font-weight:600">⚠ ${c.kev_count} KEV finding${c.kev_count === 1 ? "" : "s"}</div>`
            : "";

        return `
            <div style="font-size:13px;line-height:1.5;min-width:180px">
                <div style="font-weight:700;margin-bottom:4px">${escapeHtml(c.city)} (${escapeHtml(c.country_code)})</div>
                <div>Assets: <strong>${c.asset_count}</strong></div>
                <div>Findings: <strong>${c.finding_count}</strong></div>
                <div>Unique CVEs: <strong>${c.unique_cve_count}</strong></div>
                ${kevLine}
                <div>Highest Risk: <strong>${c.max_risk_score}</strong> (${escapeHtml(c.risk_level)})</div>
                <div style="margin-top:8px;display:flex;gap:6px">
                    <a href="${c.assets_url}" style="font-size:12px">View Assets</a>
                    <span>·</span>
                    <a href="${c.findings_url}" style="font-size:12px">View Findings</a>
                </div>
            </div>
        `;
    }

    function escapeHtml(str) {
        const div = document.createElement("div");
        div.textContent = String(str ?? "");
        return div.innerHTML;
    }

    function renderEmptyState(container, message) {
        container.innerHTML = `
            <div style="display:flex;align-items:center;justify-content:center;height:100%;color:var(--argus-muted);font-size:.85rem;text-align:center;padding:1rem">
                ${escapeHtml(message)}
            </div>
        `;
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", initCityMap);
    } else {
        initCityMap();
    }
})();
