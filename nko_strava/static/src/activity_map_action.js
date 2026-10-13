import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { standardActionServiceProps } from "@web/webclient/actions/action_plugin";

import {
    Component,
    onMounted,
    onWillStart,
    onWillUnmount,
    proxy,
    signal,
    useProps,
} from "@odoo/owl";

import { loadLeaflet, OSM_ATTRIBUTION, OSM_TILES, SPORT_COLORS } from "./leaflet_loader";
import { decodePolyline } from "./polyline";

export class StravaActivityMap extends Component {
    static template = "nko_strava.ActivityMap";
    props = useProps(standardActionServiceProps);
    mapRef = signal.ref();

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.map = null;
        this.layer = null;
        this.sportColors = SPORT_COLORS;
        this.state = proxy({
            years: [],
            sports: [],
            year: "",
            sport: "",
            count: 0,
            totalMiles: 0,
            loading: true,
        });
        onWillStart(async () => {
            const [, filters] = await Promise.all([
                loadLeaflet(),
                this.orm.call("strava.activity", "get_map_filters", []),
            ]);
            this.state.years = filters.years;
            this.state.sports = filters.sports;
        });
        onMounted(() => {
            const L = window.L;
            this.map = L.map(this.mapRef(), { preferCanvas: true });
            L.tileLayer(OSM_TILES, { maxZoom: 19, attribution: OSM_ATTRIBUTION }).addTo(this.map);
            this.layer = L.featureGroup().addTo(this.map);
            this.map.setView([42.886, -78.878], 11); // until routes are loaded
            this.loadRoutes();
        });
        onWillUnmount(() => {
            if (this.map) {
                this.map.remove();
                this.map = null;
            }
        });
    }

    get legend() {
        return Object.entries(this.sportColors);
    }

    get domain() {
        const domain = [];
        if (this.state.sport) {
            domain.push(["sport_group", "=", this.state.sport]);
        }
        if (this.state.year) {
            domain.push(["activity_date", ">=", `${this.state.year}-01-01`]);
            domain.push(["activity_date", "<=", `${this.state.year}-12-31`]);
        }
        return domain;
    }

    async loadRoutes() {
        this.state.loading = true;
        const routes = await this.orm.call("strava.activity", "get_map_routes", [this.domain]);
        if (!this.map) {
            return;
        }
        const L = window.L;
        this.layer.clearLayers();
        let miles = 0;
        for (const route of routes) {
            const points = decodePolyline(route.polyline);
            if (points.length < 2) {
                continue;
            }
            miles += route.distance_mi || 0;
            const color = SPORT_COLORS[route.sport_group] || SPORT_COLORS.other;
            const line = L.polyline(points, { color, weight: 3, opacity: 0.75 });
            line.bindTooltip(
                `${escapeHtml(route.name)}<br/>${escapeHtml(route.date)} · ${route.distance_mi} mi` +
                    (route.pace ? ` · ${escapeHtml(route.pace)}` : ""),
                { sticky: true }
            );
            line.on("mouseover", () => line.setStyle({ weight: 6, opacity: 1 }));
            line.on("mouseout", () => line.setStyle({ weight: 3, opacity: 0.75 }));
            line.on("click", () => this.openActivity(route.id));
            line.addTo(this.layer);
        }
        this.state.count = routes.length;
        this.state.totalMiles = Math.round(miles * 10) / 10;
        this.state.loading = false;
        if (this.layer.getLayers().length) {
            this.map.invalidateSize();
            this.map.fitBounds(this.layer.getBounds(), { padding: [30, 30] });
        }
    }

    onYearChange(ev) {
        this.state.year = ev.target.value;
        this.loadRoutes();
    }

    onSportChange(ev) {
        this.state.sport = ev.target.value;
        this.loadRoutes();
    }

    openActivity(id) {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "strava.activity",
            res_id: id,
            views: [[false, "form"]],
            target: "current",
            name: _t("Activity"),
        });
    }
}

function escapeHtml(value) {
    const div = document.createElement("div");
    div.textContent = value == null ? "" : String(value);
    return div.innerHTML;
}

registry.category("actions").add("nko_strava.activity_map", StravaActivityMap);
