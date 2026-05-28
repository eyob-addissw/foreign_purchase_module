from odoo import models, api, _
import logging

_logger = logging.getLogger(__name__)

class StockPicking(models.Model):
    _inherit = 'stock.picking'

    @api.model_create_multi
    def create(self, vals_list):
        """Override to prevent picking creation for foreign POs"""
        filtered_vals_list = []
        
        for vals in vals_list:
            if vals.get('origin'):
                _logger.info(f"Attempting to create picking with origin: {vals.get('origin')} - Picking Type: {vals.get('picking_type_id')}")
                
                # Check if this picking is related to a foreign PO
                po = self.env['purchase.order'].search([('name', '=', vals.get('origin'))], limit=1)
                if po and po.po_class == 'foreign':
                    _logger.info(f"BLOCKING picking creation for foreign PO {po.name} - Origin: {vals.get('origin')}")
                    # Skip this picking - don't add to filtered list
                    continue
                else:
                    _logger.info(f"Allowing picking creation for origin: {vals.get('origin')}")
                    filtered_vals_list.append(vals)
            else:
                # No origin, allow creation
                filtered_vals_list.append(vals)
        
        if not filtered_vals_list:
            _logger.info("No pickings to create - all were blocked for foreign POs")
            return self.env['stock.picking']
        
        result = super(StockPicking, self).create(filtered_vals_list)
        
        # Log the created picking details
        for picking in result:
            _logger.info(f"Created picking {picking.name} - Origin: {picking.origin} - State: {picking.state}")
        
        return result
