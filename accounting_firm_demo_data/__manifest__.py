{
    'name': 'Accounting Firm Demo Data',
    'version': '20.0.1.0.0',
    'summary': 'A realistic accounting firm, ready to demo: 28 client databases with KPIs, '
               'engagements, tasks, timesheets, invoices, bills and bank lines',
    'description': """
Loads a complete, believable accounting-firm dataset on install so the Databases app,
Project profitability, Sales and Accounting have something real to show:

* 28 client databases (Odoo Online, Odoo.sh, On Premise, Outside of Odoo) with users,
  inbox counts, journal KPIs and tax return statuses
* 6 staff members (employees with hourly costs, no extra user licenses)
* 9 service products, last-quarter engagements (invoiced, mostly paid), current-quarter
  engagements and upsell quotations
* 238 tasks and about 800 timesheet entries, linked to sales order lines so
  Estimated Costs and Real Costs work
* The firm's own books: client invoices, vendor bills, unreconciled bank lines,
  miscellaneous entries and follow-up activities
* Sample bills in the Documents inbox when Documents is installed

All dates are relative to the install date. Only standard Odoo modules are used.
""",
    'author': 'Nicholas Kosinski',
    'maintainer': 'Nicholas Kosinski',
    'website': 'https://github.com/nko614/odoo_developments',
    'category': 'Accounting/Accounting',
    'depends': ['databases', 'sale_timesheet', 'sale_project_margin'],
    'data': [],
    'post_init_hook': 'post_init_hook',
    'images': ['static/description/banner.png'],
    'application': False,
    'installable': True,
    'license': 'LGPL-3',
    'price': 0.0,
    'currency': 'USD',
}
