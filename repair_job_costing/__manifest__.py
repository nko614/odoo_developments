{
    'name': 'Repair Job Costing',
    'version': '20.0.1.0.0',
    'category': 'Supply Chain/Inventory',
    'summary': 'Repair stages, photos, timesheets, gross margin per sale order and a customer portal page',
    'description': """
Adds job costing and customer follow-up to the Repairs app:

* A sale order that creates a repair gets its own analytic account. Order revenue,
  labor from timesheets logged on the repair and the cost of parts used on the
  repair are posted to it. A Gross Margin button on the sale order shows the result.
* Configurable repair stages with a kanban view. Ending a repair moves it to
  Ready for Pickup, and moving it to Ready for Pickup ends it.
* A Timesheets tab and a Photos tab on the repair form. Photos can be taken with
  a phone or tablet camera.
* A portal page where customers follow their repairs and send messages. Costs,
  timesheets, internal notes and photos are not shown.
""",
    'depends': ['repair', 'hr_timesheet', 'project_stock_account'],
    'data': [
        'security/ir.access.csv',
        'data/repair_job_costing_data.xml',
        'views/repair_stage_views.xml',
        'views/repair_order_views.xml',
        'views/sale_order_views.xml',
        'views/repair_portal_templates.xml',
    ],
    'assets': {
        'web.assets_backend': ['repair_job_costing/static/src/**/*'],
    },
    'demo': ['demo/repair_job_costing_demo.xml'],
    'post_init_hook': '_post_init_hook',
    'images': ['static/description/banner.png'],
    'author': 'Nicholas Kosinski',
    'maintainer': 'Nicholas Kosinski',
    'website': 'https://github.com/nko614/odoo_developments',
    'license': 'LGPL-3',
    'price': 0.0,
    'currency': 'USD',
}
