"""Sample data (Buffalo, NY routes + a few weeks of WHOOP data) for demos.

Load:   env['strava.activity']._nko_load_sample_data(); env.cr.commit()
Remove: env['strava.activity']._nko_remove_sample_data(); env.cr.commit()
Every sample row has is_sample=True (activity names start with "[sample]").
"""
import math
import random
from datetime import timedelta

from odoo import api, models

from .fitness_connection import utcnow

SAMPLE_PREFIX = '[sample] '

# waypoints (lat, lng); densified and encoded below
ROUTES = {
    'delaware_park': [
        (42.93290, -78.87560), (42.93520, -78.87330), (42.93700, -78.87050), (42.93830, -78.86700),
        (42.93850, -78.86350), (42.93700, -78.85980), (42.93420, -78.85760), (42.93080, -78.85800),
        (42.92820, -78.86050), (42.92720, -78.86450), (42.92800, -78.86900), (42.93020, -78.87300),
        (42.93290, -78.87560),
    ],
    'waterfront_ride': [
        (42.87700, -78.87800), (42.88070, -78.88160), (42.88540, -78.88820), (42.88910, -78.89420),
        (42.89520, -78.89900), (42.90250, -78.90360), (42.91040, -78.90600), (42.91900, -78.90650),
        (42.92820, -78.90580), (42.93700, -78.90400), (42.94520, -78.90560), (42.95400, -78.90860),
        (42.96300, -78.91200), (42.95400, -78.90860), (42.94520, -78.90560), (42.93700, -78.90400),
        (42.92820, -78.90580), (42.91900, -78.90650), (42.91040, -78.90600), (42.90250, -78.90360),
        (42.89520, -78.89900), (42.88910, -78.89420), (42.88540, -78.88820), (42.88070, -78.88160),
        (42.87700, -78.87800),
    ],
    'tifft_hike': [
        (42.84560, -78.85860), (42.84420, -78.85620), (42.84170, -78.85480), (42.83880, -78.85520),
        (42.83700, -78.85780), (42.83740, -78.86120), (42.83960, -78.86340), (42.84260, -78.86320),
        (42.84480, -78.86120), (42.84560, -78.85860),
    ],
    'outer_harbor_run': [
        (42.86880, -78.87420), (42.86520, -78.87660), (42.86020, -78.87980), (42.85480, -78.88180),
        (42.84920, -78.88320), (42.84400, -78.88380), (42.84920, -78.88320), (42.85480, -78.88180),
        (42.86020, -78.87980), (42.86520, -78.87660), (42.86880, -78.87420),
    ],
    'elmwood_walk': [
        (42.91240, -78.87720), (42.91700, -78.87740), (42.92180, -78.87760), (42.92640, -78.87780),
        (42.92660, -78.87300), (42.92200, -78.87280), (42.91720, -78.87260), (42.91260, -78.87240),
        (42.91240, -78.87720),
    ],
}


def haversine(a, b):
    r = 6371008.8
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2)
    return 2 * r * math.asin(math.sqrt(h))


def densify(points, step=40.0):
    out = [points[0]]
    for a, b in zip(points, points[1:]):
        n = max(1, int(haversine(a, b) // step))
        for i in range(1, n + 1):
            out.append((a[0] + (b[0] - a[0]) * i / n, a[1] + (b[1] - a[1]) * i / n))
    return out


def encode_polyline(points):
    """Google encoded polyline algorithm (precision 5)."""
    result = []
    prev_lat = prev_lng = 0
    for lat, lng in points:
        ilat, ilng = int(round(lat * 1e5)), int(round(lng * 1e5))
        for delta in (ilat - prev_lat, ilng - prev_lng):
            value = ~(delta << 1) if delta < 0 else delta << 1
            while value >= 0x20:
                result.append(chr((0x20 | (value & 0x1f)) + 63))
                value >>= 5
            result.append(chr(value + 63))
        prev_lat, prev_lng = ilat, ilng
    return ''.join(result)


def decode_polyline(encoded):
    coords, index, lat, lng = [], 0, 0, 0
    while index < len(encoded):
        for which in (0, 1):
            shift = result = 0
            while True:
                b = ord(encoded[index]) - 63
                index += 1
                result |= (b & 0x1f) << shift
                shift += 5
                if b < 0x20:
                    break
            delta = ~(result >> 1) if result & 1 else result >> 1
            if which == 0:
                lat += delta
            else:
                lng += delta
        coords.append((lat / 1e5, lng / 1e5))
    return coords


def route_length(points):
    return sum(haversine(a, b) for a, b in zip(points, points[1:]))


class StravaActivitySample(models.Model):
    _inherit = 'strava.activity'

    @api.model
    def _nko_sample_models(self):
        return ['strava.activity', 'strava.athlete.stat', 'whoop.recovery', 'whoop.sleep',
                'whoop.cycle', 'whoop.workout']

    @api.model
    def _nko_remove_sample_data(self):
        counts = {}
        for model in self._nko_sample_models():
            recs = self.env[model].sudo().search([('is_sample', '=', True)])
            counts[model] = len(recs)
            recs.unlink()
        return counts

    @api.model
    def _nko_load_sample_data(self):
        self._nko_remove_sample_data()
        rnd = random.Random(42)
        today = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        specs = [
            # days ago, local hour, name, sport, route, avg speed m/s, hr
            (1, 7, 'Morning loop around Delaware Park', 'Run', 'delaware_park', 3.05, 152),
            (2, 17, 'Waterfront to Black Rock', 'Ride', 'waterfront_ride', 6.9, 138),
            (4, 9, 'Tifft Nature Preserve hike', 'Hike', 'tifft_hike', 1.35, 108),
            (6, 6, 'Outer Harbor out-and-back', 'Run', 'outer_harbor_run', 3.2, 158),
            (8, 18, 'Elmwood evening walk', 'Walk', 'elmwood_walk', 1.5, 96),
            (11, 7, 'Delaware Park tempo', 'Run', 'delaware_park', 3.45, 166),
            (15, 16, 'Sunday waterfront spin', 'Ride', 'waterfront_ride', 7.4, 142),
            (40, 8, 'Delaware Park easy', 'Run', 'delaware_park', 2.85, 145),
            (75, 10, 'Tifft birding walk', 'Walk', 'tifft_hike', 1.2, 92),
            (120, 17, 'Waterfront ride', 'Ride', 'waterfront_ride', 7.1, 140),
        ]
        acts = self.browse()
        for i, (days, hour, name, sport, route, speed, hr) in enumerate(specs, start=1):
            pts = densify(ROUTES[route])
            dist = route_length(pts)
            moving = int(dist / speed)
            local = today - timedelta(days=days) + timedelta(hours=hour, minutes=rnd.randint(0, 50))
            start_utc = local + timedelta(hours=4)  # America/New_York (EDT)
            acts |= self.create({
                'strava_id': f'sample-{i}',
                'is_sample': True,
                'details_fetched': True,
                'name': SAMPLE_PREFIX + name,
                'sport_type': sport,
                'start_date': start_utc,
                'start_date_local': local.strftime('%Y-%m-%d %H:%M'),
                'activity_date': local.date(),
                'timezone': '(GMT-05:00) America/New_York',
                'distance': dist,
                'moving_time': moving,
                'elapsed_time': int(moving * 1.08),
                'total_elevation_gain': round(dist / 1000 * rnd.uniform(2.0, 6.0), 1),
                'average_speed': speed,
                'max_speed': speed * 1.6,
                'average_heartrate': hr,
                'max_heartrate': hr + 22,
                'average_watts': 185.0 if sport == 'Ride' else 0.0,
                'kilojoules': round(185.0 * moving / 1000, 0) if sport == 'Ride' else 0.0,
                'calories': round(dist / 1000 * (35 if sport == 'Ride' else 70)),
                'suffer_score': round(moving / 60 * (hr - 90) / 40),
                'kudos_count': rnd.randint(0, 12),
                'achievement_count': rnd.randint(0, 4),
                'pr_count': rnd.randint(0, 2),
                'gear_name': 'Sample bike' if sport == 'Ride' else 'Sample shoes',
                'device_name': 'Garmin Forerunner 965 (sample)',
                'summary_polyline': encode_polyline(pts),
                'start_lat': pts[0][0],
                'start_lng': pts[0][1],
                'description': 'Sample activity generated by the nko_strava module.',
            })
        # an indoor activity without any route
        acts |= self.create({
            'strava_id': 'sample-indoor',
            'is_sample': True,
            'details_fetched': True,
            'name': SAMPLE_PREFIX + 'Zwift: Watopia flat',
            'sport_type': 'VirtualRide',
            'trainer': True,
            'start_date': today - timedelta(days=3) + timedelta(hours=23),
            'start_date_local': (today - timedelta(days=3) + timedelta(hours=19)).strftime('%Y-%m-%d %H:%M'),
            'activity_date': (today - timedelta(days=3)).date(),
            'distance': 24140.0,
            'moving_time': 3600,
            'elapsed_time': 3650,
            'average_speed': 6.7,
            'max_speed': 11.0,
            'average_heartrate': 141,
            'max_heartrate': 168,
            'average_watts': 196,
            'kilojoules': 705,
            'calories': 690,
        })

        # athlete totals
        self.env['strava.athlete.stat']._store_stats({
            'recent_run_totals': {'count': 6, 'distance': 31000, 'moving_time': 9800, 'elevation_gain': 120},
            'ytd_run_totals': {'count': 88, 'distance': 610000, 'moving_time': 196000, 'elevation_gain': 2900},
            'all_run_totals': {'count': 640, 'distance': 4700000, 'moving_time': 1520000, 'elevation_gain': 21000},
            'recent_ride_totals': {'count': 3, 'distance': 61000, 'moving_time': 8900, 'elevation_gain': 210},
            'ytd_ride_totals': {'count': 41, 'distance': 1380000, 'moving_time': 196000, 'elevation_gain': 5200},
            'all_ride_totals': {'count': 310, 'distance': 9900000, 'moving_time': 1400000, 'elevation_gain': 41000},
            'recent_swim_totals': {}, 'ytd_swim_totals': {'count': 4, 'distance': 6000, 'moving_time': 7200},
            'all_swim_totals': {'count': 22, 'distance': 31000, 'moving_time': 39000},
        })
        self.env['strava.athlete.stat'].search([]).write({'is_sample': True})

        # WHOOP: 28 days of cycles / recovery / sleep, plus workouts matching the activities
        Cycle, Recovery, Sleep, Workout = (self.env[m] for m in (
            'whoop.cycle', 'whoop.recovery', 'whoop.sleep', 'whoop.workout'))
        for d in range(28, 0, -1):
            day = today - timedelta(days=d)
            sleep_start = day - timedelta(hours=1, minutes=rnd.randint(0, 70)) + timedelta(hours=4)  # ~11pm EDT
            asleep = rnd.uniform(6.0, 8.4)
            in_bed = asleep + rnd.uniform(0.3, 0.9)
            sleep_end = sleep_start + timedelta(hours=in_bed)
            rec_score = max(5, min(99, rnd.gauss(62, 20)))
            strain = round(rnd.uniform(6.0, 17.5), 1)
            kj = rnd.uniform(9000, 14500)
            cid = f'sample-cycle-{d}'
            Cycle._upsert_whoop({
                'id': cid, 'start': sleep_start.isoformat() + 'Z',
                'end': (sleep_start + timedelta(days=1)).isoformat() + 'Z',
                'timezone_offset': '-04:00', 'score_state': 'SCORED',
                'score': {'strain': strain, 'kilojoule': kj,
                          'average_heart_rate': rnd.randint(62, 75), 'max_heart_rate': rnd.randint(150, 182)},
            })
            Recovery._upsert_whoop({
                'cycle_id': cid, 'sleep_id': f'sample-sleep-{d}',
                'created_at': (sleep_end + timedelta(minutes=20)).isoformat() + 'Z',
                'score_state': 'SCORED',
                'score': {'user_calibrating': False, 'recovery_score': round(rec_score),
                          'resting_heart_rate': round(58 - rec_score / 20 + rnd.uniform(-2, 2)),
                          'hrv_rmssd_milli': round(35 + rec_score * 0.55 + rnd.uniform(-6, 6), 1),
                          'spo2_percentage': round(rnd.uniform(94.5, 98.5), 1),
                          'skin_temp_celsius': round(rnd.uniform(33.2, 34.6), 1)},
            })
            light, deep = asleep * rnd.uniform(0.45, 0.55), asleep * rnd.uniform(0.18, 0.24)
            rem = asleep - light - deep
            ms = 3600000
            Sleep._upsert_whoop({
                'id': f'sample-sleep-{d}', 'cycle_id': cid,
                'start': sleep_start.isoformat() + 'Z', 'end': sleep_end.isoformat() + 'Z',
                'timezone_offset': '-04:00', 'nap': False, 'score_state': 'SCORED',
                'score': {
                    'stage_summary': {
                        'total_in_bed_time_milli': int(in_bed * ms),
                        'total_awake_time_milli': int((in_bed - asleep) * ms),
                        'total_light_sleep_time_milli': int(light * ms),
                        'total_slow_wave_sleep_time_milli': int(deep * ms),
                        'total_rem_sleep_time_milli': int(rem * ms),
                        'sleep_cycle_count': rnd.randint(3, 6),
                        'disturbance_count': rnd.randint(4, 16),
                    },
                    'sleep_needed': {'baseline_milli': int(7.6 * ms),
                                     'need_from_sleep_debt_milli': int(rnd.uniform(0, 0.8) * ms),
                                     'need_from_recent_strain_milli': int(rnd.uniform(0, 0.4) * ms),
                                     'need_from_recent_nap_milli': 0},
                    'respiratory_rate': round(rnd.uniform(14.2, 16.0), 1),
                    'sleep_performance_percentage': round(min(100, asleep / 8.0 * 100)),
                    'sleep_consistency_percentage': rnd.randint(55, 92),
                    'sleep_efficiency_percentage': round(asleep / in_bed * 100, 1),
                },
            })
        for act in acts.filtered(lambda a: a.activity_date and a.activity_date >= (today - timedelta(days=28)).date()):
            Workout._upsert_whoop({
                'id': f'sample-workout-{act.strava_id}',
                'sport_name': {'Run': 'running', 'Ride': 'cycling', 'VirtualRide': 'spin',
                               'Hike': 'hiking-rucking', 'Walk': 'walking'}.get(act.sport_type, 'activity'),
                'start': act.start_date.isoformat() + 'Z',
                'end': act.end_date.isoformat() + 'Z',
                'timezone_offset': '-04:00', 'score_state': 'SCORED',
                'score': {
                    'strain': round(min(20.0, 4 + act.moving_time / 600), 1),
                    'average_heart_rate': int(act.average_heartrate), 'max_heart_rate': int(act.max_heartrate),
                    'kilojoule': act.calories / 0.239006, 'percent_recorded': 100,
                    'distance_meter': act.distance, 'altitude_gain_meter': act.total_elevation_gain,
                    'zone_durations': {f'zone_{n}_milli': int(act.moving_time * 1000 * w) for n, w in zip(
                        ('zero', 'one', 'two', 'three', 'four', 'five'), (0.02, 0.12, 0.3, 0.33, 0.18, 0.05))},
                },
            })
        for model in ('whoop.cycle', 'whoop.recovery', 'whoop.sleep', 'whoop.workout'):
            self.env[model].search([('whoop_id', '=like', 'sample-%')]).write({'is_sample': True})
        self.env['whoop.recovery'].search([('cycle_id', '=like', 'sample-%')]).write({'is_sample': True})
        return len(acts)
