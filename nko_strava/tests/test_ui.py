"""Browser smoke tests: the OWL map widget, the Map client action and the settings page
render without JS errors (runs headless Chrome; OSM tile loading errors are ignored)."""
from odoo.tests import HttpCase, tagged

WAIT_FOR = """
const waitFor = async (check, what) => {
    for (let i = 0; i < 300; i++) {
        const res = check();
        if (res) { return res; }
        await new Promise((r) => setTimeout(r, 100));
    }
    throw new Error('timeout waiting for ' + what);
};
"""


def ignore_tiles(message):
    return 'openstreetmap' not in message and 'net::ERR' not in message


@tagged('post_install', '-at_install', 'nko_strava')
class TestNkoStravaUi(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Act = cls.env['strava.activity']
        Act._nko_load_sample_data()
        cls.route_act = Act.search([('is_sample', '=', True), ('has_route', '=', True)], limit=1)
        cls.indoor_act = Act.search([('is_sample', '=', True), ('has_route', '=', False)], limit=1)

    def test_activity_form_route_map(self):
        code = WAIT_FOR + """
        (async () => {
            await waitFor(() => document.querySelector('.o_nko_strava_route_map .leaflet-overlay-pane path.leaflet-interactive'), 'route polyline');
            const markers = document.querySelectorAll('.o_nko_strava_route_map path.leaflet-interactive');
            if (markers.length < 3) { throw new Error('expected route + start/end markers, got ' + markers.length); }
            const box = document.querySelector('.o_nko_strava_route_map').getBoundingClientRect();
            if (box.width < 300 || box.height < 200) { throw new Error('map container too small: ' + box.width + 'x' + box.height); }
            console.log('test successful');
        })().catch((e) => console.error('nko_strava UI failure: ' + e.message));
        """
        self.browser_js(f'/odoo/action-nko_strava.strava_activity_action/{self.route_act.id}', code,
                        login='admin', error_checker=ignore_tiles)

    def test_activity_form_without_route(self):
        code = WAIT_FOR + """
        (async () => {
            await waitFor(() => document.querySelector('.o_nko_strava_route_empty'), 'no-route placeholder');
            if (document.querySelector('.o_nko_strava_route_map')) { throw new Error('map shown for indoor activity'); }
            console.log('test successful');
        })().catch((e) => console.error('nko_strava UI failure: ' + e.message));
        """
        self.browser_js(f'/odoo/action-nko_strava.strava_activity_action/{self.indoor_act.id}', code,
                        login='admin', error_checker=ignore_tiles)

    def test_map_client_action(self):
        code = WAIT_FOR + """
        (async () => {
            const count = () => {
                const el = document.querySelector('.o_nko_strava_map_count');
                return el ? el.dataset.count : '';
            };
            await waitFor(() => Number(count()) > 0, 'routes loaded');
            const bar = document.querySelector('.o_nko_strava_map_toolbar');
            await waitFor(() => document.querySelector('.o_nko_strava_all_map canvas'), 'canvas renderer');
            const box = document.querySelector('.o_nko_strava_all_map').getBoundingClientRect();
            if (box.width < 300 || box.height < 200) { throw new Error('map too small: ' + box.width + 'x' + box.height); }
            // filter by sport: only rides
            const select = bar.querySelectorAll('select')[0];
            select.value = 'ride';
            select.dispatchEvent(new Event('change', { bubbles: true }));
            try {
                await waitFor(() => count() === '3', 'ride filter');
            } catch (e) {
                throw new Error('ride filter (3 sample rides) not applied, toolbar: ' + bar.textContent.replace(/ +/g, ' '));
            }
            console.log('test successful');
        })().catch((e) => console.error('nko_strava UI failure: ' + e.message));
        """
        self.browser_js('/odoo/action-nko_strava.strava_activity_map_action', code,
                        login='admin', error_checker=ignore_tiles)

    def test_settings_page(self):
        code = WAIT_FOR + """
        (async () => {
            await waitFor(() => document.querySelector('button[name="action_nko_strava_connect"]'), 'Connect Strava button');
            await waitFor(() => document.querySelector('button[name="action_nko_whoop_connect"]'), 'Connect WHOOP button');
            const text = document.body.textContent;
            if (!text.includes('/nko_strava/whoop/callback')) { throw new Error('redirect URI not shown'); }
            console.log('test successful');
        })().catch((e) => console.error('nko_strava UI failure: ' + e.message));
        """
        self.browser_js('/odoo/action-nko_strava.nko_strava_settings_action', code, login='admin')
