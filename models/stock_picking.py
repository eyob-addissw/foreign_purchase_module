from odoo import models, api, _
import logging

_logger = logging.getLogger(__name__)

class StockPicking(models.Model):
    _inherit = 'stock.picking'

    @api.model_create_multi
    def create(self, vals_list):
        """Override to prevent *receipt* picking creation for foreign POs.

        Important: never block outgoing/internal/other pickings, otherwise stock valuation for
        deliveries can be impacted by missing pickings/moves in the normal flow.
        """
        filtered_vals_list = []
        
        for vals in vals_list:
            origin = vals.get('origin')

            # Determine operation type if possible.
            picking_type_id = (
                vals.get('picking_type_id')
                or self.env.context.get('default_picking_type_id')
            )
            picking_type = self.env['stock.picking.type'].browse(picking_type_id) if picking_type_id else self.env['stock.picking.type']

            # Only consider blocking *incoming receipts* linked to a foreign PO.
            if origin and picking_type and picking_type.code == 'incoming':
                _logger.info(
                    "Attempting to create incoming picking with origin=%s, picking_type_id=%s",
                    origin, picking_type_id,
                )

                po = self.env['purchase.order'].search([('name', '=', origin)], limit=1)
                if po and po.po_class == 'foreign':
                    _logger.info("BLOCKING incoming receipt picking for foreign PO %s (origin=%s)", po.name, origin)
                    continue

            filtered_vals_list.append(vals)
        
        if not filtered_vals_list:
            _logger.info("No pickings to create - all were blocked for foreign POs")
            return self.env['stock.picking']
        
        result = super(StockPicking, self).create(filtered_vals_list)
        
        # Log the created picking details
        for picking in result:
            _logger.info("Created picking %s (origin=%s, state=%s, type=%s)", picking.name, picking.origin, picking.state, picking.picking_type_code)
        
        return result
