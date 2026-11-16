import ast

from odoo.exceptions import AccessError, UserError
from odoo.fields import Command
from odoo.tests import Form, HttpCase, new_test_user, tagged

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

    def _confirm_order(self, partner):
        order = self.env['sale.order'].create({
            'partner_id': partner.id,
            'order_line': [Command.create({'product_id': self.repair_product.id})],
        })
        order.action_confirm()
        return order

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

    def test_repair_lines_carry_customer_and_repaired_product(self):
        bike = self.env['product.product'].create({'name': "City Bike"})
        self.repair.product_id = bike
        self.env['stock.quant']._update_available_quantity(self.part, self.order.warehouse_id.lot_stock_id, 5)
        technician = self.env['hr.employee'].sudo().create({'name': "Technician", 'hourly_cost': 40.0})
        self.repair.write({'move_ids': [Command.create({
            'product_id': self.part.id, 'product_uom_qty': 1.0, 'repair_line_type': 'add'})]})
        self.env['account.analytic.line'].sudo().create({
            'repair_id': self.repair.id, 'employee_id': technician.id, 'unit_amount': 1.0, 'name': "Work"})
        self.repair.move_ids.write({'quantity': 1.0, 'picked': True})
        self.repair.action_repair_done()
        self.order._create_invoices().action_post()

        lines = self._account_lines()
        self.assertEqual(len(lines), 4)  # repair and part invoiced, part cost, labor
        self.assertEqual(lines.partner_id, self.customer)
        self.assertEqual(lines.repair_product_id, bike)
        self.assertFalse(self._account_lines(product_id=self.part.id).filtered('repair_id').project_id)

    def test_part_lines_are_not_timesheets(self):
        self.env.user.group_ids |= (self.env.ref('hr_timesheet.group_hr_timesheet_user')
                                    | self.env.ref('sales_team.group_sale_salesman_all_leads'))
        self.env['stock.quant']._update_available_quantity(self.part, self.order.warehouse_id.lot_stock_id, 5)
        technician = self.env['hr.employee'].sudo().create({
            'name': "Technician", 'hourly_cost': 40.0, 'user_id': self.env.user.id})
        self.repair.write({
            'move_ids': [Command.create({'product_id': self.part.id, 'product_uom_qty': 1.0, 'repair_line_type': 'add'})],
            'timesheet_ids': [Command.create({'name': "Work", 'unit_amount': 2.0, 'employee_id': technician.id})],
        })
        self.repair.move_ids.write({'quantity': 1.0, 'picked': True})
        self.repair.action_repair_end()

        part_line = self._account_lines(product_id=self.part.id).filtered('repair_id')
        self.assertEqual(part_line.amount, -20.0)
        self.assertFalse(part_line.employee_id)
        self.assertEqual(self.repair.timesheet_ids.mapped('name'), ["Work"])
        self.assertEqual(self.repair.timesheet_hours, 2.0)
        self.assertEqual(self.repair.timesheet_cost, 80.0)

    def test_repair_margin_grouped_by_customer(self):
        other_customer = self.env['res.partner'].create({'name': "Hillside Cycles"})
        orders = self.order | self._confirm_order(self.customer) | self._confirm_order(other_customer)
        for order in orders:
            order._create_invoices().action_post()
        domain = ast.literal_eval(self.env.ref('repair_job_costing.account_analytic_line_action_repair_margin').domain)
        totals = dict(self.env['account.analytic.line']._read_group(domain, ['partner_id'], ['amount:sum']))
        self.assertEqual(totals[self.customer], 300.0)
        self.assertEqual(totals[other_customer], 150.0)

    def test_items_received_repaired_and_returned(self):
        # Repair Receipts and WH/Repair belong to the main warehouse.
        main_company = self.env.ref('base.main_company')
        self.env.user.company_ids |= main_company
        self.env.user.group_ids |= (self.env.ref('hr_timesheet.group_hr_timesheet_user')
                                    | self.env.ref('sales_team.group_sale_salesman_all_leads'))
        self.env = self.env(context=dict(self.env.context, allowed_company_ids=main_company.ids))
        self.part.with_company(main_company).standard_price = 20.0
        salesman = new_test_user(self.env, login='salesman', groups='sales_team.group_sale_salesman')
        warehouse_user = new_test_user(
            self.env, login='warehouse_user', groups='stock.group_stock_user,stock.group_production_lot')
        city, cargo = self.env['product.product'].create([
            {'name': name, 'is_storable': True, 'tracking': 'serial'} for name in ("City Bike", "Cargo Bike")])
        order = self.env['sale.order'].with_user(salesman).create({
            'partner_id': self.customer.id,
            'order_line': [Command.create({'product_id': self.repair_product.id, 'repair_product_id': item.id})
                           for item in (city, cargo)],
        })
        order.action_confirm()
        order = order.with_user(self.env.user)

        # Confirmation creates the receipt and the return delivery, not the repairs.
        self.assertFalse(order.repair_order_ids)
        receipt = order.picking_ids.filtered(lambda p: p.picking_type_code == 'incoming')
        delivery = order.picking_ids - receipt
        repair_location = self.env.ref('repair_job_costing.stock_location_repair')
        self.assertEqual(receipt.picking_type_id, self.env.ref('repair_job_costing.picking_type_repair_receipt'))
        self.assertEqual(receipt.state, 'assigned')
        self.assertEqual(receipt.location_dest_id, repair_location)
        self.assertEqual(delivery.picking_type_id, order.warehouse_id.out_type_id)
        self.assertEqual(delivery.location_id, repair_location)
        self.assertEqual(delivery.state, 'waiting')
        for picking in receipt | delivery:
            self.assertEqual((picking.owner_id, picking.partner_id, picking.origin), (self.customer, self.customer, order.name))
            self.assertEqual(picking.move_ids.product_id, city | cargo)

        # Receiving the items creates their repairs; the delivery still can't be reserved.
        receipt = receipt.with_user(warehouse_user)
        for move in receipt.move_ids:
            move.move_line_ids.write({'lot_name': f"SN-{move.product_id.name}", 'quantity': 1})
        receipt.button_validate()
        delivery.action_assign()
        self.assertEqual(delivery.state, 'waiting')
        self.assertFalse(delivery.move_line_ids)
        city_repair, cargo_repair = repairs = order.repair_order_ids.sorted('id')
        self.assertEqual(repairs.sale_order_line_id, order.order_line)
        self.assertEqual(repairs.mapped('product_id'), city | cargo)
        self.assertEqual(city_repair.lot_id.name, "SN-City Bike")
        self.assertEqual(repairs.picking_id, receipt)
        self.assertEqual(repairs.mapped('state'), ['confirmed', 'confirmed'])
        self.assertEqual(repairs.analytic_account_id, order.analytic_account_id)
        # Create Repair on the receipt opens these repairs instead of making a new one.
        self.assertEqual(receipt.action_repair_return()['domain'], [('id', 'in', repairs.ids)])

        # Labor and parts of the repair go to the order's analytic account.
        self.env['stock.quant']._update_available_quantity(self.part, order.warehouse_id.lot_stock_id, 5)
        technician = self.env['hr.employee'].sudo().create({
            'name': "Technician", 'hourly_cost': 40.0, 'user_id': self.env.user.id})
        city_repair.write({
            'move_ids': [Command.create({'product_id': self.part.id, 'product_uom_qty': 1.0, 'repair_line_type': 'add'})],
            'timesheet_ids': [Command.create({'name': "Work", 'unit_amount': 2.0, 'employee_id': technician.id})],
        })
        city_repair.with_user(warehouse_user).action_repair_end()
        lines = self.env['account.analytic.line'].sudo().search([('account_id', '=', order.analytic_account_id.id)])
        self.assertEqual(sorted(lines.mapped('amount')), [-80.0, -20.0])

        # Ending the repair makes its item ready to ship, with its serial number and owner.
        self.assertEqual(delivery.state, 'assigned')
        self.assertEqual(delivery.move_line_ids.lot_id, city_repair.lot_id)
        self.assertEqual(delivery.move_line_ids.owner_id, self.customer)
        # The items are still the customer's: owned stock, no value.
        quants = self.env['stock.quant'].search([('product_id', 'in', (city | cargo).ids), ('quantity', '>', 0),
                                                 ('location_id.usage', '=', 'internal')])
        self.assertEqual(quants.location_id, repair_location)
        self.assertEqual(quants.owner_id, self.customer)
        self.assertEqual(sum(quants.mapped('value')), 0.0)

        # Ship the finished item now; the other one waits on the backorder until its repair is done.
        delivery = delivery.with_user(warehouse_user)
        Form.from_action(self.env, delivery.button_validate()).save().process()
        self.assertEqual(delivery.state, 'done')
        backorder = delivery.backorder_ids
        self.assertEqual((backorder.state, backorder.move_ids.product_id), ('waiting', cargo))
        cargo_repair.action_repair_end()
        self.assertEqual(backorder.state, 'assigned')
        backorder.button_validate()
        self.assertFalse(self.env['stock.quant'].search([('product_id', 'in', (city | cargo).ids), ('quantity', '>', 0),
                                                         ('location_id.usage', '=', 'internal')]))

    def test_line_without_item_keeps_standard_repair(self):
        self.assertEqual(self.repair.sale_order_line_id, self.order.order_line)
        self.assertFalse(self.order.picking_ids)


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
