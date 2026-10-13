/**
 * Decode a Google encoded polyline (precision 5, as used by Strava's
 * map.summary_polyline) into an array of [lat, lng] pairs.
 */
export function decodePolyline(encoded, precision = 5) {
    const coords = [];
    if (!encoded) {
        return coords;
    }
    const factor = Math.pow(10, precision);
    let index = 0;
    let lat = 0;
    let lng = 0;
    while (index < encoded.length) {
        const deltas = [0, 0];
        for (let k = 0; k < 2; k++) {
            let shift = 0;
            let result = 0;
            let byte;
            do {
                if (index >= encoded.length) {
                    return coords; // truncated input: keep what we have
                }
                byte = encoded.charCodeAt(index++) - 63;
                result |= (byte & 0x1f) << shift;
                shift += 5;
            } while (byte >= 0x20);
            deltas[k] = result & 1 ? ~(result >> 1) : result >> 1;
        }
        lat += deltas[0];
        lng += deltas[1];
        coords.push([lat / factor, lng / factor]);
    }
    return coords;
}
