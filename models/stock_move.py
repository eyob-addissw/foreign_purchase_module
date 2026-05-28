from odoo import models, fields, api

class StockMove(models.Model):
    _inherit = 'stock.move'

    # Custom field to track inventory valuation cost for GRN operations
    # This represents the cost price that will be debited to inventory valuation
    inventory_cost = fields.Float(
        string='Cost Price', 
        copy=False,
        digits='Product Price',
        help="Cost price for inventory valuation. This value will be used for inventory accounting entries."
    )

    @api.model_create_multi
    def create(self, vals_list):
        """Override create to set inventory_cost based on purchase type"""
        for vals in vals_list:
            if vals.get('purchase_line_id'):
                # Set inventory cost based on purchase line type
                purchase_line = self.env['purchase.order.line'].browse(vals['purchase_line_id'])
                vals['inventory_cost'] = self._get_inventory_cost(purchase_line)
        
        return super().create(vals_list)
    
    def write(self, vals):
        """Override write to update inventory_cost when purchase line changes"""
        if 'purchase_line_id' in vals:
            for move in self:
                if vals['purchase_line_id']:
                    purchase_line = self.env['purchase.order.line'].browse(vals['purchase_line_id'])
                    vals['inventory_cost'] = self._get_inventory_cost(purchase_line)
                else:
                    vals['inventory_cost'] = 0.0
        
        return super().write(vals)
    
    def _get_inventory_cost(self, purchase_line):
        """
        Calculate inventory cost based on purchase type:
        - Domestic PO: Use unit price directly
        - Foreign PO: Use stored calculated unit cost from shipment
        """
        if not purchase_line:
            return 0.0
        
        purchase_order = purchase_line.order_id
        
        # Check if this is a foreign purchase (has LC/CAD)
        lc_cad = self.env['foreign.lc_cad'].search([
            ('purchase_order_id', '=', purchase_order.id)
        ], limit=1)
        
        if lc_cad:
            # Foreign purchase - get stored calculated unit cost from shipment
            return self._get_stored_foreign_purchase_unit_cost(purchase_line, lc_cad)
        else:
            # Domestic purchase - use unit price directly
            return purchase_line.price_unit
    
    def _get_stored_foreign_purchase_unit_cost(self, purchase_line, lc_cad):
        """
        Get the stored calculated unit cost for foreign purchase products
        This uses the pre-calculated landed cost from shipment product lines
        """
        # Debug: Print to understand what's happening
        print(f"=== DEBUG: Getting stored cost for PO Line {purchase_line.id}, Product {purchase_line.product_id.name} ===")
        print(f"LC/CAD ID: {lc_cad.id}, Name: {lc_cad.name}")
        
        # Find shipments for this LC/CAD
        shipments = self.env['foreign.shipment'].search([
            ('lc_cad_id', '=', lc_cad.id),
            ('state', '=', 'completed')
        ])
        print(f"Found {len(shipments)} completed shipments for LC/CAD")
        
        # Trigger landed cost calculation if needed (on-demand like report)
        for shipment in shipments:
            if shipment.product_line_ids.filtered(lambda pl: pl.purchase_order_line_id.id == purchase_line.id):
                if not shipment.product_line_ids.filtered(lambda pl: pl.purchase_order_line_id.id == purchase_line.id and pl.landed_unit_cost > 0):
                    print(f"=== DEBUG: Triggering landed cost calculation for shipment {shipment.name} ===")
                    shipment._calculate_landed_costs()
                break
        
        # Find the shipment product line for this purchase line using relationship
        shipment_product_line = None
        for shipment in shipments:
            product_lines = shipment.product_line_ids.filtered(
                lambda pl: pl.purchase_order_line_id.id == purchase_line.id
            )
            if product_lines:
                shipment_product_line = product_lines[0]
                break
        
        print(f"Shipment product line found: {shipment_product_line is not None}")
        if shipment_product_line:
            print(f"Landed unit cost: {shipment_product_line.landed_unit_cost}")
            print(f"LC ratio: {shipment_product_line.lc_ratio}")
            print(f"Shipment ratio: {shipment_product_line.shipment_ratio}")
        
        if shipment_product_line and shipment_product_line.landed_unit_cost > 0:
            # Use the stored landed unit cost
            print(f"Using landed cost: {shipment_product_line.landed_unit_cost}")
            return shipment_product_line.landed_unit_cost
        else:
            # If no stored cost found, fall back to unit price
            print(f"Falling back to unit price: {purchase_line.price_unit}")
            return purchase_line.price_unit
    
    def _get_price_unit(self):
        """
        Override to use inventory_cost for valuation instead of standard price unit.
        In Odoo 18, this method returns a dictionary of {lot: price_unit}.
        """
        self.ensure_one()
        
        # Check if this is a foreign purchase move
        if self.purchase_line_id and self.inventory_cost > 0:
            price_unit = self.inventory_cost
            if self.product_id.lot_valuated:
                return dict.fromkeys(self.lot_ids, price_unit)
            else:
                return {self.env['stock.lot']: price_unit}
        
        # Fall back to standard behavior
        return super()._get_price_unit()

    def _get_in_svl_vals(self, forced_quantity):
        """
        Override to force the Stock Valuation Layer to use our calculated inventory_cost.
        This handles all cost methods including 'standard' which usually ignores _get_price_unit.
        """
        svl_vals_list = super()._get_in_svl_vals(forced_quantity)
        
        for svl_vals in svl_vals_list:
            move = self.browse(svl_vals.get('stock_move_id'))
            if move.purchase_line_id and move.inventory_cost > 0:
                # Force the unit_cost and total value to use our calculated landed cost
                qty = svl_vals.get('quantity', 0.0)
                svl_vals['unit_cost'] = move.inventory_cost
                svl_vals['value'] = move.inventory_cost * qty
                
        return svl_vals_list

    def _is_foreign_incoming_receipt(self):
        """True only for incoming pickings tied to a foreign PO line.

        We use this to avoid changing accounting on deliveries/internal/MO flows.
        """
        self.ensure_one()
        if not self.picking_id or self.picking_id.picking_type_code != 'incoming':
            return False
        if not self.purchase_line_id:
            return False
        return getattr(self.purchase_line_id.order_id, 'po_class', False) == 'foreign'

    def _get_src_account(self, accounts_data):
        """For foreign incoming receipts, credit the company GIT account instead of the default
        stock interim received (stock input) account.

        This makes the receipt clear the same interim account used by foreign vendor bills.
        """
        self.ensure_one()
        if self._is_foreign_incoming_receipt():
            git = self.company_id.foreign_purchase_git_account_id
            if git:
                return git.id
        return super()._get_src_account(accounts_data)

