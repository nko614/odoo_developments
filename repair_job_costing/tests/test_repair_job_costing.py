from odoo.exceptions import AccessError, UserError
from odoo.fields import Command
from odoo.tests import HttpCase, new_test_user, tagged

from odoo.addons.account.tests.common import AccountTestInvoicingCommon


@tagged('post_install', '-at_install')
class TestRepairJobCosting(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.group_ids |= (
            cls.env.ref('sales_team.group_sale_salesman') | cls.env.ref('stock.group_stock_user')
            | cls.env.ref('analytic.group_analytic_accounting'))
        cls.customer = cls.env['res.partner'].create({'name': "Lakeside Bikes", 'is_company': True})
        cls.repair_product = cls.env['product.product'].create({
            'name': "Bike Repair",
            'type': 'service',
            'service_tracking': 'repair',
            'invoice_policy': 'order',
            'list_price': 150.0,
        })
        cls.part = cls.env['product.product'].create({
            'name': "Brake Pad Set",
            'is_storable': True,
            'standard_price': 20.0,
            'list_price': 35.0,
        })
        cls.order = cls.env['sale.order'].create({
            'partner_id': cls.customer.id,
            'order_line': [Command.create({'product_id': cls.repair_product.id})],
        })
        cls.order.action_confirm()
        cls.repair = cls.order.repair_order_ids
        cls.account = cls.order.analytic_account_id

    def _account_lines(self, **domain):
        return self.env['account.analytic.line'].sudo().search(
            [('account_id', '=', self.account.id)] + [(k, '=', v) for k, v in domain.items()])

    def test_confirm_creates_repair_and_analytic_account(self):
        self.assertEqual(len(self.repair), 1)
        self.assertTrue(self.account)
        self.assertEqual(self.repair.analytic_account_id, self.account)
        self.assertEqual(self.account.partner_id, self.customer)
        self.assertEqual(self.order.order_line.analytic_distribution, {str(self.account.id): 100})
        self.assertEqual(self.repair.stage_id, self.env.ref('repair_job_costing.repair_stage_new'))

    def test_stage_change_is_tracked(self):
        stage = self.env.ref('repair_job_costing.repair_stage_in_progress')
        self.repair.stage_id = stage
        self.env.cr.precommit.run()
        # v20 writes tracked changes into the message body.
        self.assertIn("Received → <b>In Repair</b> <i>(Stage)</i>", self.repair.message_ids[0].body)

    def test_timesheet_cost_on_repair_and_account(self):
        technician = new_test_user(
            self.env, login='technician',
            groups='stock.group_stock_user,hr_timesheet.group_hr_timesheet_user')
        self.env['hr.employee'].sudo().create({
            'name': "Technician", 'user_id': technician.id, 'hourly_cost': 40.0,
            'company_id': self.env.company.id,
        })
        repair = self.repair.with_user(technician)
        repair.write({'timesheet_ids': [Command.create({'name': "Replace brakes", 'unit_amount': 2.0})]})

        self.assertEqual(repair.timesheet_hours, 2.0)
        self.assertEqual(repair.timesheet_cost, 80.0)
        self.assertEqual(repair.timesheet_ids.account_id, self.account)
        self.assertEqual(repair.timesheet_ids.project_id, self.env.ref('repair_job_costing.project_repairs'))
        self.assertEqual(sum(self._account_lines(repair_id=self.repair.id).mapped('amount')), -80.0)

    def test_parts_cost_on_account(self):
        stock_location = self.order.warehouse_id.lot_stock_id
        self.env['stock.quant']._update_available_quantity(self.part, stock_location, 5)
        self.repair.write({'move_ids': [Command.create({
            'product_id': self.part.id,
            'product_uom_qty': 1.0,
            'repair_line_type': 'add',
        })]})
        # The part is also sold on the order, so its revenue goes to the same account.
        part_line = self.order.order_line.filtered(lambda line: line.product_id == self.part)
        self.assertEqual(part_line.analytic_distribution, {str(self.account.id): 100})

        self.repair.move_ids.write({'quantity': 1.0, 'picked': True})
        self.repair.action_repair_done()

        self.assertEqual(self.repair.state, 'done')
        self.assertEqual(sum(self._account_lines(product_id=self.part.id).mapped('amount')), -20.0)

    def test_invoice_revenue_on_account(self):
        invoice = self.order._create_invoices()
        invoice.action_post()
        self.assertEqual(sum(self._account_lines(move_line_id=invoice.invoice_line_ids.id).mapped('amount')), 150.0)

    def test_photo_readable_by_repair_user(self):
        photo = self.env['ir.attachment'].create({
            'name': "intake.png", 'raw': b"photo", 'mimetype': 'image/png',
            'res_model': 'repair.order', 'res_id': self.repair.id,
        })
        self.repair.photo_ids = photo
        repair_user = new_test_user(self.env, login='repair_user', groups='stock.group_stock_user')
        self.assertEqual(bytes(self.repair.with_user(repair_user).photo_ids.raw), b"photo")

    def test_gross_margin_is_account_balance(self):
        self.order._create_invoices().action_post()
        self.env['account.analytic.line'].create({'name': "Labor", 'account_id': self.account.id, 'amount': -30.0})
        self.assertEqual(self.order.analytic_balance, 120.0)
        self.assertEqual(self.order.analytic_balance, self.account.balance)
        action = self.order.action_view_analytic_lines()
        self.assertEqual(self.env['account.analytic.line'].search(action['domain']), self._account_lines())

    def test_end_repair_sets_ready_stage(self):
        self.repair.action_repair_end()
        self.assertEqual(self.repair.state, 'done')
        self.assertEqual(self.repair.stage_id, self.env.ref('repair_job_costing.repair_stage_ready_for_pickup'))

    def test_ready_stage_ends_repair(self):
        self.env['stock.quant']._update_available_quantity(self.part, self.order.warehouse_id.lot_stock_id, 5)
        self.repair.write({'move_ids': [Command.create({
            'product_id': self.part.id, 'product_uom_qty': 1.0, 'repair_line_type': 'add'})]})
        self.repair.stage_id = self.env.ref('repair_job_costing.repair_stage_ready_for_pickup')
        self.assertEqual(self.repair.state, 'done')
        self.assertEqual(self.repair.move_ids.state, 'done')

    def test_ready_stage_needs_parts(self):
        self.repair.write({'move_ids': [Command.create({
            'product_id': self.part.id, 'product_uom_qty': 1.0, 'repair_line_type': 'add'})]})
        with self.assertRaises(UserError):
            self.repair.stage_id = self.env.ref('repair_job_costing.repair_stage_ready_for_pickup')


@tagged('post_install', '-at_install')
class TestRepairPortal(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        customer = cls.env['res.partner'].create({'name': "Lakeside Bikes", 'is_company': True})
        contact = cls.env['res.partner'].create({'name': "Ann Lakeside", 'parent_id': customer.id})
        other_customer = cls.env['res.partner'].create({'name': "Hillside Cycles", 'is_company': True})
        cls.portal_user = cls.env['res.users'].create({
            'name': "Ann Lakeside",
            'login': 'ann_portal',
            'password': 'ann_portal',
            'partner_id': contact.id,
            'group_ids': [Command.set([cls.env.ref('base.group_portal').id])],
        })
        cls.repair = cls.env['repair.order'].create({'partner_id': customer.id})
        cls.other_repair = cls.env['repair.order'].create({'partner_id': other_customer.id})

    def test_portal_user_sees_own_repairs_only(self):
        Repair = self.env['repair.order'].with_user(self.portal_user)
        self.assertEqual(Repair.search([]), self.repair)
        with self.assertRaises(AccessError):
            self.other_repair.with_user(self.portal_user).check_access('read')
        with self.assertRaises(AccessError):
            self.repair.with_user(self.portal_user).write({'internal_notes': "x"})
        with self.assertRaises(AccessError):
            self.repair.with_user(self.portal_user).read(['timesheet_cost'])
        with self.assertRaises(AccessError):
            self.env['account.analytic.line'].with_user(self.portal_user).search([])

    def test_portal_pages(self):
        self.authenticate('ann_portal', 'ann_portal')
        response = self.url_open('/my/repairs')
        self.assertEqual(response.status_code, 200)
        self.assertIn(self.repair.name, response.text)
        self.assertNotIn(self.other_repair.name, response.text)

        response = self.url_open(self.repair.access_url)
        self.assertEqual(response.status_code, 200)
        self.assertIn(self.repair.stage_id.name, response.text)
        self.assertNotIn("Labor Cost", response.text)

        # Without the share token from the chatter's share link, another customer's
        # repair redirects to /my.
        response = self.url_open(self.other_repair.access_url)
        self.assertNotIn(self.other_repair.name, response.text)
