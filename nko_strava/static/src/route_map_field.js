import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

import {
    Component,
    onMounted,
    onPatched,
    onWillStart,
    onWillUnmount,
    signal,
    useProps,
} from "@odoo/owl";

import { loadLeaflet, OSM_ATTRIBUTION, OSM_TILES } from "./leaflet_loader";
import { decodePolyline } from "./polyline";

export class StravaRouteMapField extends Component {
    static template = "nko_strava.StravaRouteMapField";
    props = useProps(standardFieldProps);
    mapRef = signal.ref();

    setup() {
        this.map = null;
        this.drawnPolyline = null;
        onWillStart(() => loadLeaflet());
        onMounted(() => this.renderMap());
        onPatched(() => this.renderMap());
        onWillUnmount(() => this.destroyMap());
        // the form keeps laying out after mount (sheet width, fonts): refit on resize
        this.resizeObserver = new ResizeObserver(() => this.fit());
    }

    get encoded() {
        return this.props.record.data[this.props.name] || "";
    }

    get points() {
        return decodePolyline(this.encoded);
    }

    fit() {
        if (this.map && this.routeLine) {
            this.map.invalidateSize();
            this.map.fitBounds(this.routeLine.getBounds(), { padding: [24, 24] });
        }
    }

    destroyMap() {
        this.resizeObserver?.disconnect();
        this.routeLine = null;
        if (this.map) {
            this.map.remove();
            this.map = null;
        }
        this.drawnPolyline = null;
    }

    renderMap() {
        const el = this.mapRef();
        const L = window.L;
        if (!el || !L) {
            this.destroyMap();
            return;
        }
        if (this.map && this.map.getContainer() !== el) {
            this.destroyMap();
        }
        if (this.map && this.drawnPolyline === this.encoded) {
            return;
        }
        const points = this.points;
        if (points.length < 2) {
            this.destroyMap();
            return;
        }
        if (!this.map) {
            this.map = L.map(el, { scrollWheelZoom: false, zoomSnap: 0.25 });
            L.tileLayer(OSM_TILES, { maxZoom: 19, attribution: OSM_ATTRIBUTION }).addTo(this.map);
            this.layer = L.layerGroup().addTo(this.map);
        }
        this.layer.clearLayers();
        this.routeLine = L.polyline(points, { color: "#FC4C02", weight: 4, opacity: 0.9 }).addTo(this.layer);
        L.circleMarker(points[0], {
            radius: 7, color: "#fff", weight: 2, fillColor: "#2E7D32", fillOpacity: 1,
        }).bindTooltip(_t("Start")).addTo(this.layer);
        L.circleMarker(points[points.length - 1], {
            radius: 7, color: "#fff", weight: 2, fillColor: "#C62828", fillOpacity: 1,
        }).bindTooltip(_t("Finish")).addTo(this.layer);
        this.drawnPolyline = this.encoded;
        this.fit();
        this.resizeObserver.disconnect();
        this.resizeObserver.observe(el);
    }
}

export const stravaRouteMapField = {
    component: StravaRouteMapField,
    displayName: _t("Strava Route Map"),
    supportedTypes: ["text", "char"],
};

registry.category("fields").add("strava_route_map", stravaRouteMapField);
