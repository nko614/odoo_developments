from datetime import date

from odoo.tests import TransactionCase, tagged

from ..hooks import period_label


@tagged('post_install', '-at_install')
class TestPeriodLabel(TransactionCase):

    def test_labels_follow_install_date(self):
        today = date(2026, 10, 20)
        self.assertEqual(period_label('{m-1} month-end close', today), 'September 2026 month-end close')
        self.assertEqual(period_label('{mn+0} advisory call', today), 'October advisory call')
        self.assertEqual(period_label('{q-1} services ({qr-1})', today), 'Q3 2026 services (Jul-Sep)')
        self.assertEqual(period_label('Catch-up: {qn-2} items', today), 'Catch-up: Q2 items')
        self.assertEqual(period_label('{y} year-end planning', today), '2026 year-end planning')

    def test_labels_cross_year_boundary(self):
        today = date(2027, 2, 3)
        self.assertEqual(period_label('{m-4} month-end close', today), 'October 2026 month-end close')
        self.assertEqual(period_label('{q-1} services ({qr-1})', today), 'Q4 2026 services (Oct-Dec)')
        self.assertEqual(period_label('plain text', today), 'plain text')
