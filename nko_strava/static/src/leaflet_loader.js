import { loadCSS, loadJS } from "@web/core/assets";

const LIB = "/nko_strava/static/lib/leaflet";

/**
 * Load the vendored Leaflet 1.9.4 (served from this module, never from a CDN).
 * If another addon (e.g. web_map) already loaded Leaflet, reuse it.
 */
export async function loadLeaflet() {
    await loadCSS(`${LIB}/leaflet.css`);
    if (!window.L) {
        await loadJS(`${LIB}/leaflet.js`);
    }
    return window.L;
}

export const OSM_TILES = "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
export const OSM_ATTRIBUTION =
    '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank">OpenStreetMap</a> contributors';

export const SPORT_COLORS = {
    run: "#FC4C02",
    ride: "#1E88E5",
    walk: "#2E7D32",
    swim: "#00ACC1",
    other: "#8E24AA",
};
