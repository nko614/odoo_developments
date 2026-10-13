from odoo import api, fields, models

from .strava_activity import FT_PER_M, M_PER_MILE, fmt_duration


class StravaAthleteStat(models.Model):
    _name = 'strava.athlete.stat'
    _description = 'Strava Athlete Totals'
    _order = 'sequence, id'

    sequence = fields.Integer()
    period = fields.Selection([('recent', 'Last 4 Weeks'), ('ytd', 'Year to Date'), ('all', 'All Time')], required=True)
    sport = fields.Selection([('run', 'Run'), ('ride', 'Ride'), ('swim', 'Swim')], required=True)
    count = fields.Integer(string='Activities')
    distance_mi = fields.Float(string='Distance (mi)', digits=(16, 1))
    moving_time_display = fields.Char(string='Moving Time')
    moving_hours = fields.Float(string='Moving Time (h)', digits=(16, 1))
    elevation_ft = fields.Float(string='Elevation (ft)', digits=(16, 0))
    is_sample = fields.Boolean(readonly=True)

    _period_sport_unique = models.Constraint('unique(period, sport)', "One row per period and sport.")

    @api.model
    def _store_stats(self, data):
        seq = 0
        for period in ('recent', 'ytd', 'all'):
            for sport in ('run', 'ride', 'swim'):
                seq += 1
                totals = data.get(f'{period}_{sport}_totals') or {}
                vals = {
                    'sequence': seq,
                    'count': totals.get('count') or 0,
                    'distance_mi': (totals.get('distance') or 0) / M_PER_MILE,
                    'moving_hours': (totals.get('moving_time') or 0) / 3600.0,
                    'moving_time_display': fmt_duration(totals.get('moving_time')) or '0:00:00',
                    'elevation_ft': (totals.get('elevation_gain') or 0) * FT_PER_M,
                    'is_sample': False,
                }
                rec = self.search([('period', '=', period), ('sport', '=', sport)], limit=1)
                if rec:
                    rec.write(vals)
                else:
                    self.create(dict(vals, period=period, sport=sport))
        return True

    def action_sync_all(self):
        return self.env['fitness.connection'].action_sync_provider('strava')
