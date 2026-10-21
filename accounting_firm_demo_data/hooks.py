import json
import logging
import re
from collections import defaultdict
from datetime import date, datetime, time, timedelta

from odoo import Command, fields
from odoo.tools.misc import file_open, file_path

_logger = logging.getLogger(__name__)

MODULE = 'accounting_firm_demo_data'
MARKER = f'{MODULE}.loaded'
NOTE = 'Accounting Firm Demo Data'
PRODUCT_PREFIX = 'AFD'
MONTH_ABBR = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']


def post_init_hook(env):
    params = env['ir.config_parameter'].sudo()
    if params.get_str(MARKER):
        _logger.info("%s: demo data already loaded on %s, skipping", MODULE, params.get_str(MARKER))
        return
    with file_open(f'{MODULE}/data/demo_data.json') as f:
        data = json.load(f)
    loader = DemoLoader(env, data)
    loader.load_firm()
    params.set_str(MARKER, fields.Datetime.to_string(fields.Datetime.now()))

    # When Accounting is installed together with this module, Odoo loads the chart of accounts only
    # once the registry is ready (account's ir.module.module._register_hook). Queue the accounting
    # part behind that same step instead of running it against a company without journals.
    pending = getattr(env.registry, '_auto_install_template', None)
    if pending and not loader.company.chart_template:
        def load_chart_then_books(new_env):
            pending(new_env)
            loader.rebind(new_env)
            loader.load_books()
        env.registry._auto_install_template = load_chart_then_books
    else:
        loader.load_books()


def shift_month(day, months):
    index = day.year * 12 + day.month - 1 + months
    return date(index // 12, index % 12 + 1, 1)


def period_label(text, today):
    """Turn period placeholders into labels relative to the install date.

    {m-1} -> 'September 2026'   {mn-1} -> 'September'
    {q-1} -> 'Q3 2026'          {qn-1} -> 'Q3'      {qr-1} -> 'Jul-Sep'      {y} -> '2026'
    """
    if not text or '{' not in text:
        return text
    quarter_start = date(today.year, (today.month - 1) // 3 * 3 + 1, 1)

    def repl(match):
        kind, shift = match.group(1), int(match.group(2) or 0)
        if kind == 'y':
            return str(today.year)
        if kind in ('m', 'mn'):
            month = shift_month(today, shift)
            return month.strftime('%B %Y' if kind == 'm' else '%B')
        start = shift_month(quarter_start, 3 * shift)
        quarter = (start.month - 1) // 3 + 1
        if kind == 'q':
            return f'Q{quarter} {start.year}'
        if kind == 'qn':
            return f'Q{quarter}'
        return f'{MONTH_ABBR[start.month - 1]}-{MONTH_ABBR[start.month + 1]}'

    return re.sub(r'\{(qr|qn|q|mn|m|y)([+-]\d+)?\}', repl, text)


class DemoLoader:

    def __init__(self, env, data):
        self.data = data
        self.today = fields.Date.today()
        admin = env.ref('base.user_admin', raise_if_not_found=False)
        if not admin or not admin.active:
            admin = env['res.users'].search([('share', '=', False), ('active', '=', True),
                                             ('id', '!=', env.ref('base.user_root').id)], limit=1, order='id')
        self.user = admin
        self.company = admin.company_id
        self.ctx = {'tracking_disable': True, 'mail_create_nolog': True, 'mail_notrack': True,
                    'mail_create_nosubscribe': True, 'allowed_company_ids': [self.company.id]}
        self.env = env(context={**env.context, **self.ctx})
        self.employees = {}
        self.tags = {}
        self.products = {}
        self.clients = {}       # client name -> project
        self.new_clients = set()  # clients created by this install (existing ones are left alone)
        self.orders = {}        # (client name, ref template) -> sale.order
        self.tasks = []         # index -> project.task (or None)
        self.vendors = {}
        self.firm_invoices = []
        self.bills = []
        self.stats = defaultdict(int)

    # ------------------------------------------------------------------ helpers
    def day(self, offset):
        return self.today + timedelta(days=offset) if offset is not None else False

    def moment(self, offset, hour=9):
        return datetime.combine(self.day(offset), time(hour)) if offset is not None else False

    def section(self, name, method):
        try:
            with self.env.cr.savepoint():
                method()
        except Exception:  # noqa: BLE001 - demo data must never block the install
            _logger.exception("%s: section '%s' skipped", MODULE, name)
            self.stats[f'skipped: {name}'] += 1

    def journal(self, journal_type):
        return self.env['account.journal'].search([
            ('type', '=', journal_type), *self.env['account.journal']._check_company_domain(self.company),
        ], limit=1)

    def load_firm(self):
        self.section('staff', self.load_staff)
        self.section('tags', self.load_tags)
        self.section('products', self.load_products)
        self.section('kpi columns', self.load_kpi_columns)
        self.section('clients', self.load_clients)
        self.section('sales orders', self.load_orders)
        self.section('tasks and timesheets', self.load_tasks)
        _logger.info("%s: firm data loaded %s", MODULE, dict(self.stats))

    def load_books(self):
        self.stats.clear()
        self.section('engagement invoices', self.invoice_orders)
        self.section('client invoices', self.load_firm_invoices)
        self.section('vendor bills', self.load_bills)
        self.section('miscellaneous entries', self.load_misc)
        self.section('bank lines', self.load_bank)
        self.section('activities', self.load_activities)
        self.section('documents inbox', self.load_documents)
        _logger.info("%s: accounting data loaded %s", MODULE, dict(self.stats))

    def rebind(self, env):
        """Re-attach every record kept between the two phases to a new environment."""
        self.env = env(context={**env.context, **self.ctx})
        self.user = self.user.with_env(self.env)
        self.company = self.company.with_env(self.env)
        for mapping in (self.employees, self.tags, self.products, self.clients, self.orders, self.vendors):
            for key, record in mapping.items():
                mapping[key] = record.with_env(self.env)

    # ------------------------------------------------------------------ firm setup
    def load_staff(self):
        Employee = self.env['hr.employee']
        for row in self.data['staff']:
            emp = Employee.search([('name', '=', row['name']), ('company_id', '=', self.company.id)], limit=1)
            if not emp:
                emp = Employee.create({
                    'name': row['name'], 'job_title': row['job_title'], 'work_email': row['email'],
                    'hourly_cost': row['hourly_cost'], 'company_id': self.company.id,
                })
                self.stats['employees'] += 1
            self.employees[row['name']] = emp

    def load_tags(self):
        Tag = self.env['project.tags']
        for row in self.data['tags']:
            self.tags[row['name']] = Tag.search([('name', '=', row['name'])], limit=1) or Tag.create(row)

    def load_products(self):
        Product = self.env['product.product']
        unit = self.env.ref('uom.product_uom_unit')
        hour = self.env.ref('uom.product_uom_hour')
        for i, row in enumerate(self.data['products'], 1):
            code = f'{PRODUCT_PREFIX}-{i:02d}'
            product = Product.search([('default_code', '=', code)], limit=1)
            if not product:
                vals = {
                    'name': row['name'], 'default_code': code, 'type': 'service',
                    'sale_ok': True, 'purchase_ok': False,
                    'list_price': row['price'], 'standard_price': row['cost'],
                    'uom_id': (hour if row['hourly'] else unit).id,
                    'service_tracking': 'no',
                    'taxes_id': [Command.clear()], 'supplier_taxes_id': [Command.clear()],
                }
                if row['hourly']:
                    vals.update({'invoice_policy': 'delivery', 'service_type': 'timesheet'})
                else:
                    vals.update({'invoice_policy': 'order', 'service_type': 'manual'})
                product = Product.create(vals)
                self.stats['products'] += 1
            self.products[row['name']] = product

    def load_kpi_columns(self):
        definition = self.env['properties.base.definition'].sudo()._get_definition_for_property_field(
            'project.project', 'database_kpi_properties')
        current = list(definition.properties_definition or [])
        known = {d['name'] for d in current}
        added = [d for d in self.data['kpi_definition'] if d['name'] not in known]
        if added:
            definition.properties_definition = current + added

    def stages(self):
        refs = ['databases.stage_new', 'databases.stage_progress', 'databases.stage_done']
        stages = [self.env.ref(r, raise_if_not_found=False) for r in refs]
        if not all(stages):
            Stage = self.env['project.task.type']
            stages = [s or Stage.create({'name': n, 'sequence': i})
                      for i, (s, n) in enumerate(zip(stages, ['New', 'In Progress', 'Done']))]
        return dict(zip(['new', 'progress', 'done'], stages))

    # ------------------------------------------------------------------ clients
    def load_clients(self):
        Project = self.env['project.project']
        Partner = self.env['res.partner']
        stages = self.stages()
        self.stage = stages
        stage_ids = [s.id for s in stages.values()]
        unreachable = self.env.ref('databases.project_tag_db_unreachable', raise_if_not_found=False)
        us = self.env.ref('base.us', raise_if_not_found=False)
        new_york = self.env.ref('base.state_us_27', raise_if_not_found=False)
        for row in self.data['clients']:
            existing = Project.with_context(active_test=False).search([('database_url', '=', row['url'])], limit=1)
            if existing:
                self.clients[row['name']] = existing
                continue
            partner = Partner.create({
                'name': row['name'], 'is_company': True, 'customer_rank': 1,
                'city': row['city'], 'email': row['email'], 'website': row['website'],
                'comment': row['industry'],
                'country_id': us and us.id, 'state_id': new_york and new_york.id,
            })
            if row['contact']:
                Partner.create({**row['contact'], 'parent_id': partner.id})
            tag_ids = [self.tags[t].id for t in row['tags'] if t in self.tags]
            if row['unreachable'] and unreachable:
                tag_ids.append(unreachable.id)
            vals = {
                'name': row['name'], 'partner_id': partner.id, 'user_id': self.user.id,
                'tag_ids': [Command.set(tag_ids)], 'type_ids': [Command.set(stage_ids)],
                'privacy_visibility': 'followers', 'allow_timesheets': True, 'allow_billable': True,
                'description': f"<p>{row['industry']}. Owner at the firm: {row['owner'] or '-'}.</p>",
                'database_hosting': row['hosting'], 'database_url': row['url'],
                'company_id': self.company.id,
            }
            if row['hosting'] != 'other':
                vals.update({'database_name': row['database_name'], 'database_version': row['version']})
            project = Project.create(vals)
            if row['hosting'] != 'other':
                users = [u for u in row['db_users']] + [{'name': self.user.name, 'login': self.user.login, 'last_login': -1}]
                self.env['databases.user'].sudo().create([{
                    'project_id': project.id, 'name': u['name'], 'login': u['login'],
                    'latest_authentication': self.moment(u['last_login'], 14),
                } for u in users])
                project.sudo().write({
                    'database_last_synchro': self.moment(row['last_sync'], 6),
                    'database_nb_documents': row['inbox'],
                    # the client URLs are fictional: keep the daily sync job from retrying them
                    'database_nb_synchro_errors': 5,
                    'database_kpi_properties': row['kpis'],
                })
            self.clients[row['name']] = project
            self.new_clients.add(row['name'])
            self.stats['clients'] += 1

    # ------------------------------------------------------------------ sales
    def load_orders(self):
        Order = self.env['sale.order']
        for row in self.data['orders']:
            project = self.clients.get(row['client'])
            if not project or row['client'] not in self.created_clients():
                continue
            lines = [Command.create({'product_id': self.products[l['product']].id,
                                     'product_uom_qty': l['qty'], 'price_unit': l['price']})
                     for l in row['lines'] if l['product'] in self.products]
            if not lines:
                continue
            order = Order.create({
                'partner_id': project.partner_id.id, 'project_id': project.id,
                'client_order_ref': period_label(row['ref'], self.today),
                'note': f'<p>{NOTE}</p>', 'order_line': lines, 'company_id': self.company.id,
                'validity_date': self.day(row['validity']) if row['validity'] is not None else False,
            })
            if row['state'] == 'sale':
                order.action_confirm()
            elif row['state'] == 'sent':
                order.state = 'sent'
            order.date_order = self.moment(row['date'])
            self.orders[(row['client'], row['ref'])] = order
            self.stats[f"orders ({row['state']})"] += 1

    def created_clients(self):
        return self.new_clients

    def order_line(self, client, ref, product):
        order = self.orders.get((client, ref))
        if not order or product not in self.products:
            return self.env['sale.order.line']
        return order.order_line.filtered(lambda l: l.product_id == self.products[product])[:1]

    def load_tasks(self):
        Task = self.env['project.task']
        stages = getattr(self, 'stage', None) or self.stages()
        vals_list, keep = [], []
        for i, row in enumerate(self.data['tasks']):
            project = self.clients.get(row['client'])
            if not project or row['client'] not in self.created_clients():
                self.tasks.append(None)
                continue
            sol = self.order_line(row['client'], row['order_ref'], row['product']) if row['order_ref'] else False
            vals_list.append({
                'name': period_label(row['name'], self.today), 'project_id': project.id,
                'tag_ids': [Command.set([self.tags[t].id for t in row['tags'] if t in self.tags])],
                'stage_id': stages[row['stage']].id, 'user_ids': [Command.set(self.user.ids)],
                'allocated_hours': row['hours'] or 0, 'priority': row['priority'] or '0',
                'date_deadline': self.moment(row['deadline'], 17),
                'sale_line_id': sol.id if sol else False,
                'description': f"<p>Owner at the firm: {row['assignee'] or '-'}</p>",
            })
            keep.append(i)
            self.tasks.append(None)
        tasks = Task.create(vals_list)
        for i, task in zip(keep, tasks):
            self.tasks[i] = task
        done = Task.browse([t.id for i, t in zip(keep, tasks) if self.data['tasks'][i]['done']])
        done.write({'state': '1_done'})
        # project-level sales order item: the main recurring line of the current engagement
        for project in {t.project_id for t in tasks}:
            open_lines = tasks.filtered(lambda t: t.project_id == project and not t.is_closed).sale_line_id
            main = open_lines.filtered(lambda l: l.product_id.service_type != 'timesheet')[:1] or open_lines[:1]
            if main:
                project.sale_line_id = main
        self.stats['tasks'] += len(tasks)

        timesheets = []
        for row in self.data['timesheets']:
            task = self.tasks[row['task']] if row['task'] < len(self.tasks) else None
            employee = self.employees.get(row['employee'])
            if not task or not employee:
                continue
            timesheets.append({'name': row['name'], 'project_id': task.project_id.id, 'task_id': task.id,
                               'employee_id': employee.id, 'unit_amount': row['hours'],
                               'date': self.day(row['date'])})
        self.env['account.analytic.line'].create(timesheets)
        self.stats['timesheets'] += len(timesheets)

    # ------------------------------------------------------------------ accounting helpers
    def post(self, moves, statuses):
        """Post moves and set their review status. statuses: list aligned with moves."""
        to_post = moves.filtered(lambda m: m.state == 'draft')
        posted = self.env['account.move']
        for move, status in zip(moves, statuses):
            if status != 'draft':
                posted |= move
        (posted & to_post).action_post()
        if 'review_state' in self.env['account.move']._fields:
            for move, status in zip(moves, statuses):
                if status in ('reviewed', 'todo', 'anomaly'):
                    move.review_state = status

    def pay(self, move, offset):
        if move.state != 'posted' or move.payment_state in ('paid', 'in_payment'):
            return
        self.env['account.payment.register'].with_context(
            active_model='account.move', active_ids=move.ids,
        ).create({'payment_date': self.day(offset)})._create_payments()
        self.stats['payments'] += 1

    def invoice_orders(self):
        for row in self.data['orders']:
            order = self.orders.get((row['client'], row['ref']))
            if not order or not isinstance(row['invoice'], dict) or order.state != 'sale':
                continue
            invoice = order._create_invoices()
            invoice.write({'invoice_date': self.day(row['invoice']['date']),
                           'invoice_date_due': self.day(row['invoice']['due'])})
            invoice.action_post()
            self.stats['engagement invoices'] += 1
            if row['invoice']['paid']:
                self.pay(invoice, row['invoice']['paid_on'] or min(-1, row['invoice']['due']))

    def move_lines(self, lines):
        return [Command.create({'name': l['name'], 'quantity': l['qty'], 'price_unit': l['price'],
                                'tax_ids': [Command.clear()]}) for l in lines]

    def load_firm_invoices(self):
        journal = self.journal('sale')
        if not journal:
            return
        rows = [r for r in self.data['invoices'] if r['client'] in self.created_clients()]
        moves = self.env['account.move'].create([{
            'move_type': 'out_invoice', 'journal_id': journal.id,
            'partner_id': self.clients[r['client']].partner_id.id,
            'invoice_date': self.day(r['date']), 'invoice_date_due': self.day(r['due']),
            'narration': NOTE, 'invoice_line_ids': self.move_lines(r['lines']),
        } for r in rows])
        self.post(moves, [r['status'] for r in rows])
        for move, r in zip(moves, rows):
            if r['paid']:
                self.pay(move, min(-1, r['due']))
        self.firm_invoices = list(zip(moves, rows))
        self.stats['client invoices'] += len(moves)

    def load_bills(self):
        journal = self.journal('purchase')
        if not journal:
            return
        Partner = self.env['res.partner']
        for name in self.data['vendors']:
            self.vendors[name] = Partner.search([('name', '=', name), ('is_company', '=', True)], limit=1) \
                or Partner.create({'name': name, 'is_company': True, 'supplier_rank': 1})
        rows = self.data['bills']
        moves = self.env['account.move'].create([{
            'move_type': 'in_invoice', 'journal_id': journal.id, 'partner_id': self.vendors[r['vendor']].id,
            'invoice_date': self.day(r['date']), 'invoice_date_due': self.day(r['due']),
            'narration': NOTE, 'invoice_line_ids': self.move_lines(r['lines']),
        } for r in rows])
        self.post(moves, [r['status'] for r in rows])
        for move, r in zip(moves, rows):
            if r['paid']:
                self.pay(move, min(-1, r['due']))
        self.bills = list(zip(moves, rows))
        self.stats['vendor bills'] += len(moves)

    def load_misc(self):
        journal = self.journal('general')
        Account = self.env['account.account']
        domain = Account._check_company_domain(self.company)
        expense = Account.search([('account_type', '=', 'expense'), *domain], limit=1)
        accrued = Account.search([('account_type', '=', 'liability_current'), *domain], limit=1)
        if not (journal and expense and accrued):
            return
        rows = self.data['misc']
        moves = self.env['account.move'].create([{
            'move_type': 'entry', 'journal_id': journal.id, 'date': self.day(r['date']), 'ref': r['name'],
            'line_ids': [Command.create({'name': r['name'], 'account_id': expense.id, 'debit': r['amount']}),
                         Command.create({'name': r['name'], 'account_id': accrued.id, 'credit': r['amount']})],
        } for r in rows])
        self.post(moves, [r['status'] for r in rows])
        self.stats['misc entries'] += len(moves)

    def load_bank(self):
        journal = self.journal('bank')
        if not journal:
            return
        lines = []
        open_invoices = [m for m, _ in self.firm_invoices
                         if m.state == 'posted' and m.payment_state == 'not_paid']
        for move in open_invoices[:self.data.get('matching_deposits', 0)]:
            lines.append({'payment_ref': f'ACH DEPOSIT {move.partner_id.name.upper()} {move.name}',
                          'amount': move.amount_total, 'partner_id': move.partner_id.id,
                          'date': self.today - timedelta(days=2)})
        for row in self.data['bank']:
            vendor = self.vendors.get(row['partner']) if row['partner'] else False
            lines.append({'payment_ref': row['label'], 'amount': row['amount'], 'date': self.day(row['date']),
                          'partner_id': vendor.id if vendor else False})
        for line in lines:
            line['journal_id'] = journal.id
        self.env['account.bank.statement.line'].create(lines)
        self.stats['bank lines'] += len(lines)

    def load_activities(self):
        types = {'To-Do': self.env.ref('mail.mail_activity_data_todo', raise_if_not_found=False),
                 'Call': self.env.ref('mail.mail_activity_data_call', raise_if_not_found=False)}
        IrModel = self.env['ir.model']
        vals_list = []
        for row in self.data['activities']:
            target, kind = row['target'], row['target']['kind']
            record = False
            if kind == 'bill' and target['index'] < len(self.bills):
                record = self.bills[target['index']][0]
            elif kind == 'invoice' and target['index'] < len(self.firm_invoices):
                record = self.firm_invoices[target['index']][0]
            elif kind == 'partner':
                project = self.clients.get(target['name'])
                record = project.partner_id if project else self.vendors.get(target['name'])
            activity_type = types.get(row['type'])
            if not record or not activity_type:
                continue
            vals_list.append({'res_model_id': IrModel._get_id(record._name), 'res_id': record.id,
                              'activity_type_id': activity_type.id, 'user_id': self.user.id,
                              'summary': row['summary'], 'date_deadline': self.day(row['date'])})
        self.env['mail.activity'].create(vals_list)
        self.stats['activities'] += len(vals_list)

    def load_documents(self):
        if 'documents.document' not in self.env:
            return
        inbox = self.env.ref('documents.document_inbox_folder', raise_if_not_found=False)
        if not inbox:
            return
        Document = self.env['documents.document']
        names = ['Ledgerline_Tax_Software_INV-88412.pdf', 'National_Grid_statement.pdf',
                 'Verizon_Business_statement.pdf', 'Queen_City_Courier_statement.pdf',
                 'Lakeshore_Office_Supply_R-7731.pdf', 'Erie_Insurance_renewal_notice.pdf']
        existing = set(Document.search([('folder_id', '=', inbox.id), ('name', 'in', names)]).mapped('name'))
        for name in names:
            if name in existing:
                continue
            with open(file_path(f'{MODULE}/data/documents/{name}'), 'rb') as f:
                content = f.read()
            vals = {'name': name, 'folder_id': inbox.id, 'raw': content, 'mimetype': 'application/pdf'}
            if 'owner_id' in Document._fields:
                vals['owner_id'] = self.user.id
            Document.create(vals)
            self.stats['inbox documents'] += 1
