from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    """Before 20.0.1.3.0 the setup asked for a WH/Repair location and a Repair Receipts operation
    type made by hand. Bind them to the records this version creates, so they are not duplicated."""
    env = api.Environment(cr, SUPERUSER_ID, {})
    warehouse = env.ref('stock.warehouse0')
    records = {
        'stock_location_repair': env['stock.location'].search([
            ('location_id', '=', warehouse.view_location_id.id), ('name', '=', "Repair"), ('usage', '=', 'internal'),
        ], limit=1),
        'picking_type_repair_receipt': env['stock.picking.type'].search([
            ('warehouse_id', '=', warehouse.id), ('code', '=', 'incoming'), ('sequence_code', '=', "RCV-R"),
        ], limit=1),
    }
    env['ir.model.data']._update_xmlids([
        {'xml_id': f'repair_job_costing.{name}', 'record': record, 'noupdate': True}
        for name, record in records.items() if record
    ])
