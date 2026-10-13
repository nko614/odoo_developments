from datetime import timedelta

from odoo import api, fields, models

from .nfl_team import ESPN_SITE, POSTSEASON_WEEKS, REGULAR_SEASON_WEEKS, _espn_get, _parse_espn_date

GAME_LENGTH = timedelta(hours=3, minutes=30)


class NflGame(models.Model):
    _name = 'nfl.game'
    _description = 'NFL Game'
    _order = 'kickoff, id'

    name = fields.Char(required=True)
    espn_id = fields.Char(string='ESPN ID', required=True, index=True)
    season = fields.Integer()
    season_type = fields.Selection([
        ('1', 'Preseason'),
        ('2', 'Regular Season'),
        ('3', 'Postseason'),
    ])
    week = fields.Integer()
    kickoff = fields.Datetime(index=True)
    kickoff_end = fields.Datetime(compute='_compute_kickoff_end', store=True)

    home_team_id = fields.Many2one('nfl.team', string='Home', index=True, ondelete='set null')
    away_team_id = fields.Many2one('nfl.team', string='Away', index=True, ondelete='set null')
    home_score = fields.Integer()
    away_score = fields.Integer()
    score = fields.Char(compute='_compute_score', store=True)

    venue = fields.Char(string='Stadium')
    city = fields.Char()
    state = fields.Char()
    country = fields.Char()
    location = fields.Char(compute='_compute_location', store=True)
    neutral_site = fields.Boolean()
    broadcast = fields.Char(string='TV')

    status = fields.Selection([
        ('scheduled', 'Scheduled'),
        ('in_progress', 'In Progress'),
        ('final', 'Final'),
        ('postponed', 'Postponed'),
        ('canceled', 'Canceled'),
    ], default='scheduled', index=True)
    status_detail = fields.Char(string='Status Detail')

    _espn_id_unique = models.Constraint('unique(espn_id)', "This ESPN game already exists.")

    @api.depends('kickoff')
    def _compute_kickoff_end(self):
        for game in self:
            game.kickoff_end = game.kickoff and game.kickoff + GAME_LENGTH

    @api.depends('status', 'home_score', 'away_score', 'home_team_id', 'away_team_id')
    def _compute_score(self):
        for game in self:
            if game.status in ('in_progress', 'final'):
                game.score = (f"{game.away_team_id.abbreviation} {game.away_score} - "
                              f"{game.home_score} {game.home_team_id.abbreviation}")
            else:
                game.score = False

    @api.depends('city', 'state', 'country')
    def _compute_location(self):
        for game in self:
            region = game.state or (game.country if game.country != 'USA' else False)
            game.location = ", ".join(p for p in (game.city, region) if p) or False

    def action_sync_all(self):
        return self.env['nfl.team'].action_sync_all()

    @api.model
    def _sync_schedule(self, season):
        teams = {t.espn_id: t.id for t in self.env['nfl.team'].search([])}
        games = self.browse()
        weeks = [('2', w) for w in range(1, REGULAR_SEASON_WEEKS + 1)]
        weeks += [('3', w) for w in range(1, POSTSEASON_WEEKS + 1)]
        for season_type, week in weeks:
            data = _espn_get(f'{ESPN_SITE}/scoreboard', dates=season, seasontype=season_type, week=week)
            for event in data.get('events', []):
                games |= self._upsert_game(event, season, season_type, week, teams)
        return games

    @api.model
    def _upsert_game(self, event, season, season_type, week, teams):
        comp = event['competitions'][0]
        sides = {c['homeAway']: c for c in comp.get('competitors', [])}
        venue = comp.get('venue', {})
        address = venue.get('address', {})
        status_type = event.get('status', {}).get('type', {})
        broadcasts = [n for b in comp.get('broadcasts', []) for n in b.get('names', [])]

        def score(side):
            value = sides.get(side, {}).get('score')
            return int(value) if value and str(value).isdigit() else 0

        vals = {
            'name': event.get('name') or event.get('shortName'),
            'season': season,
            'season_type': season_type,
            'week': week,
            'kickoff': _parse_espn_date(event['date']) if event.get('date') else False,
            'home_team_id': teams.get(sides.get('home', {}).get('team', {}).get('id')),
            'away_team_id': teams.get(sides.get('away', {}).get('team', {}).get('id')),
            'home_score': score('home'),
            'away_score': score('away'),
            'venue': venue.get('fullName'),
            'city': address.get('city'),
            'state': address.get('state'),
            'country': address.get('country'),
            'neutral_site': bool(comp.get('neutralSite')),
            'broadcast': ", ".join(broadcasts) or False,
            'status': self._map_status(status_type),
            'status_detail': status_type.get('shortDetail'),
        }
        game = self.search([('espn_id', '=', event['id'])], limit=1)
        if game:
            game.write(vals)
        else:
            game = self.create(dict(vals, espn_id=event['id']))
        return game

    @api.model
    def _map_status(self, status_type):
        name = status_type.get('name', '')
        if 'POSTPONED' in name:
            return 'postponed'
        if 'CANCELED' in name or 'CANCELLED' in name:
            return 'canceled'
        return {'pre': 'scheduled', 'in': 'in_progress', 'post': 'final'}.get(status_type.get('state'), 'scheduled')
