from datetime import datetime
from unittest.mock import patch

from odoo.tests import TransactionCase, tagged

from ..models import nfl_team


def _event(event_id, state='pre', name='STATUS_SCHEDULED', home_score='0', away_score='0'):
    return {
        'id': event_id,
        'name': 'Away Team at Home Team',
        'date': '2026-10-16T00:15Z',
        'status': {'type': {'state': state, 'name': name, 'shortDetail': '10/15 - 8:15 PM EDT'}},
        'competitions': [{
            'neutralSite': False,
            'venue': {'fullName': 'Test Stadium', 'address': {'city': 'Orchard Park', 'state': 'NY', 'country': 'USA'}},
            'broadcasts': [{'names': ['Prime Video']}],
            'competitors': [
                {'homeAway': 'home', 'score': home_score, 'team': {'id': '901'}},
                {'homeAway': 'away', 'score': away_score, 'team': {'id': '902'}},
            ],
        }],
    }


@tagged('post_install', '-at_install')
class TestFootballSync(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Team = cls.env['nfl.team']
        cls.home = Team.create({'name': 'Home Team', 'abbreviation': 'HOM', 'espn_id': '901'})
        cls.away = Team.create({'name': 'Away Team', 'abbreviation': 'AWY', 'espn_id': '902'})
        cls.teams = {'901': cls.home.id, '902': cls.away.id}

    def test_parse_date_is_naive_utc(self):
        self.assertEqual(nfl_team._parse_espn_date('2026-10-16T00:15Z'), datetime(2026, 10, 16, 0, 15))

    def test_status_mapping(self):
        Game = self.env['nfl.game']
        self.assertEqual(Game._map_status({'state': 'pre'}), 'scheduled')
        self.assertEqual(Game._map_status({'state': 'in'}), 'in_progress')
        self.assertEqual(Game._map_status({'state': 'post'}), 'final')
        self.assertEqual(Game._map_status({'state': 'post', 'name': 'STATUS_POSTPONED'}), 'postponed')
        self.assertEqual(Game._map_status({'state': 'post', 'name': 'STATUS_CANCELED'}), 'canceled')

    def test_upsert_game_creates_then_updates(self):
        Game = self.env['nfl.game']
        game = Game._upsert_game(_event('t1'), 2026, '2', 7, self.teams)
        self.assertEqual(game.home_team_id, self.home)
        self.assertEqual(game.away_team_id, self.away)
        self.assertEqual(game.location, 'Orchard Park, NY')
        self.assertEqual(game.broadcast, 'Prime Video')
        self.assertEqual(game.status, 'scheduled')
        self.assertFalse(game.score)

        again = Game._upsert_game(_event('t1', 'post', 'STATUS_FINAL', '27', '24'), 2026, '2', 7, self.teams)
        self.assertEqual(again, game)
        self.assertEqual(Game.search_count([('espn_id', '=', 't1')]), 1)
        self.assertEqual(game.status, 'final')
        self.assertEqual(game.score, 'AWY 24 - 27 HOM')

    def test_next_game(self):
        Game = self.env['nfl.game']
        future = Game._upsert_game(dict(_event('t2'), date='2099-01-01T18:00Z'), 2098, '2', 1, self.teams)
        self.assertEqual(self.home.next_game_id, future)
        self.assertIn(future, self.away.game_ids)

    def test_espn_error_becomes_user_error(self):
        from odoo.exceptions import UserError
        import requests
        with patch.object(nfl_team.requests, 'get', side_effect=requests.ConnectionError('down')):
            with self.assertRaises(UserError):
                nfl_team._espn_get('https://example.invalid')
