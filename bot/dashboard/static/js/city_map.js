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
                scrollWheelZoom: false,
            });
        } catch (err) {
            console.error("[city_map] Leaflet failed to initialize:", err);
            renderEmptyState(container, "Map failed to load.");
            return;
        }

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
                weight: c.kev_count > 0 ? 3 : 1,
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

        setTimeout(() => map.invalidateSize(), 250);

        window.addEventListener("resize", () => map.invalidateSize());
    }

    function markerRadius(assetCount, maxAssetCount) {
        const minR = 8, maxR = 28;
        const ratio = Math.min(1, (assetCount || 0) / maxAssetCount);
        return minR + ratio * (maxR - minR);
    }

    function buildPopupHtml(c) {
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
