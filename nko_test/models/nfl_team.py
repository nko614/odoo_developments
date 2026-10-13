import logging
from datetime import datetime, timezone

import requests

from odoo import api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

ESPN_SITE = 'https://site.api.espn.com/apis/site/v2/sports/football/nfl'
ESPN_STANDINGS = 'https://site.api.espn.com/apis/v2/sports/football/nfl/standings'
REGULAR_SEASON_WEEKS = 18
POSTSEASON_WEEKS = 5


def _espn_get(url, **params):
    try:
        response = requests.get(url, params=params, timeout=20)
        response.raise_for_status()
        return response.json()
    except (requests.RequestException, ValueError) as e:
        raise UserError(f"Could not reach ESPN ({url}): {e}") from e


def _parse_espn_date(value):
    """ESPN sends UTC like '2026-10-16T00:15Z'; Odoo stores naive UTC."""
    dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


class NflTeam(models.Model):
    _name = 'nfl.team'
    _description = 'NFL Team'
    _order = 'conference, division, win_pct desc, name'

    name = fields.Char(required=True)
    abbreviation = fields.Char()
    location = fields.Char()
    espn_id = fields.Char(string='ESPN ID', required=True, index=True)
    logo_url = fields.Char(string='Logo')
    color = fields.Char()
    conference = fields.Selection([('AFC', 'AFC'), ('NFC', 'NFC')])
    division = fields.Char()

    wins = fields.Integer()
    losses = fields.Integer()
    ties = fields.Integer()
    record = fields.Char(compute='_compute_record', store=True)
    win_pct = fields.Float(string='Win %', digits=(4, 3))
    points_for = fields.Integer(string='PF')
    points_against = fields.Integer(string='PA')
    point_diff = fields.Integer(string='Diff', compute='_compute_record', store=True)
    streak = fields.Char()
    playoff_seed = fields.Integer()

    home_game_ids = fields.One2many('nfl.game', 'home_team_id')
    away_game_ids = fields.One2many('nfl.game', 'away_team_id')
    game_ids = fields.Many2many('nfl.game', string='Games', compute='_compute_games')
    next_game_id = fields.Many2one('nfl.game', string='Next Game', compute='_compute_games')
    next_kickoff = fields.Datetime(compute='_compute_games')

    _espn_id_unique = models.Constraint('unique(espn_id)', "This ESPN team already exists.")

    @api.depends('wins', 'losses', 'ties', 'points_for', 'points_against')
    def _compute_record(self):
        for team in self:
            team.record = f"{team.wins}-{team.losses}" + (f"-{team.ties}" if team.ties else "")
            team.point_diff = team.points_for - team.points_against

    def _compute_games(self):
        now = fields.Datetime.now()
        for team in self:
            games = (team.home_game_ids | team.away_game_ids).sorted('kickoff')
            upcoming = games.filtered(lambda g: g.status in ('scheduled', 'in_progress') and g.kickoff and g.kickoff >= now)
            team.game_ids = games
            team.next_game_id = upcoming[:1]
            team.next_kickoff = upcoming[:1].kickoff

    def action_view_games(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': f"{self.name} Schedule",
            'res_model': 'nfl.game',
            'view_mode': 'list,calendar,form',
            'domain': ['|', ('home_team_id', '=', self.id), ('away_team_id', '=', self.id)],
        }

    # ------------------------------------------------------------------
    # ESPN sync
    # ------------------------------------------------------------------

    def action_sync_all(self):
        summary = self.env['nfl.team']._sync_all()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': "NFL sync complete",
                'message': summary,
                'type': 'success',
                'next': {'type': 'ir.actions.client', 'tag': 'soft_reload'},
            },
        }

    @api.model
    def _cron_sync(self):
        self._sync_all()

    @api.model
    def _sync_all(self):
        season = _espn_get(f'{ESPN_SITE}/scoreboard')['season']['year']
        teams = self._sync_teams()
        self._sync_standings(season)
        games = self.env['nfl.game']._sync_schedule(season)
        summary = f"{len(teams)} teams, {len(games)} games for the {season} season."
        _logger.info("NFL sync: %s", summary)
        return summary

    @api.model
    def _sync_teams(self):
        data = _espn_get(f'{ESPN_SITE}/teams')
        teams = self.browse()
        for entry in data['sports'][0]['leagues'][0]['teams']:
            t = entry['team']
            logo = next((l['href'] for l in t.get('logos', []) if 'default' in l.get('rel', [])), False)
            teams |= self._upsert_team(t['id'], {
                'name': t['displayName'],
                'abbreviation': t.get('abbreviation'),
                'location': t.get('location'),
                'logo_url': logo,
                'color': t.get('color') and f"#{t['color']}",
            })
        return teams

    @api.model
    def _sync_standings(self, season):
        data = _espn_get(ESPN_STANDINGS, season=season, level=3)
        for conference in data.get('children', []):
            conf_code = conference.get('abbreviation')
            for division in conference.get('children', []):
                for entry in division['standings']['entries']:
                    stats = {s['name']: s for s in entry.get('stats', [])}

                    def num(key):
                        return int(stats.get(key, {}).get('value') or 0)

                    self._upsert_team(entry['team']['id'], {
                        'name': entry['team']['displayName'],
                        'conference': conf_code if conf_code in ('AFC', 'NFC') else False,
                        'division': division['name'],
                        'wins': num('wins'),
                        'losses': num('losses'),
                        'ties': num('ties'),
                        'win_pct': stats.get('winPercent', {}).get('value') or 0.0,
                        'points_for': num('pointsFor'),
                        'points_against': num('pointsAgainst'),
                        'streak': stats.get('streak', {}).get('displayValue'),
                        'playoff_seed': num('playoffSeed'),
                    })

    @api.model
    def _upsert_team(self, espn_id, vals):
        team = self.search([('espn_id', '=', str(espn_id))], limit=1)
        if team:
            team.write(vals)
        else:
            team = self.create(dict(vals, espn_id=str(espn_id)))
        return team
