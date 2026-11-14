{
    'name': 'Repair Job Costing',
    'version': '20.0.1.1.1',
    'category': 'Supply Chain/Inventory',
    'summary': 'Job costing, stages, photos and a customer portal page for repairs sold on sale orders',
    'description': """
Extends the Repairs app for repairs that are sold on a sale order. Confirming the
order creates the repair, as in standard Odoo. This module adds:

* One analytic account per sale order. Invoiced revenue, labor from timesheets
  logged on the repair and the cost of the parts used on the repair are posted to it.
* A Gross Margin button on the sale order that shows the balance of that account
  and opens its lines.
* A Timesheets tab on the repair with the hours spent and the labor cost, based on
  each employee's hourly cost.
* Configurable repair stages with a kanban view. Ending a repair moves it to Ready
  for Pickup, and moving it to Ready for Pickup ends it.
* A Photos tab on the repair. On a phone or tablet the camera can be used.
* A portal page where customers follow their repairs and send messages. Costs,
  timesheets, internal notes and photos are not shown.
* A Repair Margin report with revenue, labor and parts per customer and per
  repaired product.
""",
    'depends': ['repair', 'hr_timesheet', 'project_stock_account'],
    'data': [
        'security/ir.access.csv',
        'data/repair_job_costing_data.xml',
        'views/repair_stage_views.xml',
        'views/repair_order_views.xml',
        'views/sale_order_views.xml',
        'views/account_analytic_line_views.xml',
        'views/repair_portal_templates.xml',
    ],
    'assets': {
        'web.assets_backend': ['repair_job_costing/static/src/**/*'],
    },
    'demo': ['demo/repair_job_costing_demo.xml'],
    'post_init_hook': '_post_init_hook',
    'images': ['static/description/banner.png', 'static/description/screenshot_repair_margin.png'],
    'author': 'Nicholas Kosinski',
    'maintainer': 'Nicholas Kosinski',
    'website': 'https://github.com/nko614/odoo_developments',
    'license': 'LGPL-3',
    'price': 0.0,
    'currency': 'USD',
}
