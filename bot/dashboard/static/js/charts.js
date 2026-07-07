const ACCENT      = "#4f8ef7";
const PURPLE      = "#a78bfa";
const RISK_COLORS = ["#4fcf8e", "#0dcaf0", "#ffc107", "#fd7e14", "#dc3545"];
const KEV_COLORS  = ["#dc3545", "#4fcf8e"];

document.addEventListener("DOMContentLoaded", () => {
    createAssetsChart();
    createRiskChart();
    createKEVChart();
    createVendorChart();
    createFindingsHistoryChart();
});

function chartDefaults() {
    return {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
            legend: { display: false },
            tooltip: { mode: "index", intersect: false }
        },
        scales: {
            x: {
                ticks: { color: "#8892a4", font: { size: 11 } },
                grid:  { color: "rgba(255,255,255,0.05)" }
            },
            y: {
                ticks: { color: "#8892a4", font: { size: 11 } },
                grid:  { color: "rgba(255,255,255,0.05)" },
                beginAtZero: true
            }
        }
    };
}

function createAssetsChart() {
    const canvas = document.getElementById("chartAssets");
    if (!canvas) return;

    fetch("/api/chart/assets")
    .then(r => r.json())
    .then(data => {
        const opts = chartDefaults();
        opts.plugins.legend = { display: false };
        // Click → asset detail page, with ref=charts so back button returns here
        opts.onClick = (event, elements) => {
            if (!elements.length) return;
            window.location = "/asset/" + data.asset_ids[elements[0].index] + "?ref=charts";
        };

        new Chart(canvas, {
            type: "bar",
            data: {
                labels: data.labels,
                datasets: [{
                    label: "Findings",
                    data: data.values,
                    backgroundColor: ACCENT,
                    borderRadius: 6
                }]
            },
            options: opts
        });
    })
    .catch(console.error);
}

function createRiskChart() {
    const canvas = document.getElementById("chartRisk");
    if (!canvas) return;

    fetch("/api/chart/risk")
    .then(r => r.json())
    .then(data => {
        const opts = chartDefaults();
        opts.plugins.legend = { display: false };
        // Click → findings page filtered by risk level, ref=charts for back button
        opts.onClick = (event, elements) => {
            if (!elements.length) return;
            const label = data.labels[elements[0].index];
            window.location = "/findings?risk=" + encodeURIComponent(label) + "&ref=charts";
        };

        new Chart(canvas, {
            type: "bar",
            data: {
                labels: data.labels,
                datasets: [{
                    label: "Count",
                    data: data.values,
                    backgroundColor: RISK_COLORS.slice(0, data.labels.length),
                    borderRadius: 6
                }]
            },
            options: opts
        });
    })
    .catch(console.error);
}

function createKEVChart() {
    const canvas = document.getElementById("chartKEV");
    if (!canvas) return;

    fetch("/api/chart/kev")
    .then(r => r.json())
    .then(data => {
        new Chart(canvas, {
            type: "doughnut",
            data: {
                labels: data.labels,
                datasets: [{
                    data: data.values,
                    backgroundColor: KEV_COLORS,
                    borderWidth: 0,
                    hoverOffset: 8
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                cutout: "60%",
                plugins: {
                    legend: {
                        display: true,
                        position: "bottom",
                        labels: { color: "#8892a4", padding: 16, font: { size: 12 } }
                    }
                },
                // Click → findings filtered by kev=true or kev=false, ref=charts
                onClick: (event, elements) => {
                    if (!elements.length) return;
                    const label = data.labels[elements[0].index];
                    const kevVal = label === "KEV" ? "true" : "false";
                    window.location = "/findings?kev=" + kevVal + "&ref=charts";
                }
            }
        });
    })
    .catch(console.error);
}

function createVendorChart() {
    const canvas = document.getElementById("chartVendor");
    if (!canvas) return;

    fetch("/api/chart/vendors")
    .then(r => r.json())
    .then(data => {
        const opts = chartDefaults();
        opts.indexAxis = "y";
        opts.plugins.legend = { display: false };
        // Click → findings filtered by vendor, ref=charts for back button
        opts.onClick = (event, elements) => {
            if (!elements.length) return;
            const vendor = data.labels[elements[0].index];
            window.location = "/findings?vendor=" + encodeURIComponent(vendor) + "&ref=charts";
        };

        new Chart(canvas, {
            type: "bar",
            data: {
                labels: data.labels,
                datasets: [{
                    label: "Findings",
                    data: data.values,
                    backgroundColor: PURPLE,
                    borderRadius: 6
                }]
            },
            options: opts
        });
    })
    .catch(console.error);
}

function createFindingsHistoryChart() {
    const canvas = document.getElementById("chartHistory");
    if (!canvas) {
        return;
    }

    fetch("/api/chart/findings_history")
    .then(r => r.json())
    .then(data => {
        new Chart(canvas, {
            type: "line",
            data: {
                labels: data.labels,
                datasets: [{
                    label: "New Findings",
                    data: data.values,
                    tension: 0.3
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false
            }
        });
    })
    .catch(console.error);
}