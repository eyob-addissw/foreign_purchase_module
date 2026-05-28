from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError

class ForeignShipment(models.Model):
    _name = 'foreign.shipment'
    _description = 'Foreign Shipment'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(string='Shipment Ref', required=True, tracking=True)
    lc_cad_id = fields.Many2one('foreign.lc_cad', string='LC/CAD', required=True, ondelete='cascade', tracking=True, db_index=True)
    purchase_order_id = fields.Many2one(related='lc_cad_id.purchase_order_id', string='Purchase Order', readonly=True, store=True)
    
    state = fields.Selection([
        ('draft', 'Draft'),
        ('in_transit', 'In Transit'),
        ('customs_clearance', 'Customs Clearance'),
        ('arrived', 'Arrived'),
        ('completed', 'Completed')
    ], string='Status', default='draft', tracking=True, db_index=True)
    
    # Transportation details
    vessel_flight = fields.Char(string='Vessel/Flight Number', tracking=True)
    bill_of_lading = fields.Char(string='BOL/AWB Number', tracking=True)
    
    # Schedule fields
    estimated_departure = fields.Datetime(string='Estimated Departure (ETD)', tracking=True)
    actual_departure = fields.Datetime(string='Actual Departure (ATD)', tracking=True)
    estimated_arrival = fields.Datetime(string='Estimated Arrival (ETA)', tracking=True)
    actual_arrival = fields.Datetime(string='Actual Arrival (ATA)', tracking=True)
    
    # Port information
    port_of_loading = fields.Char(string='Port of Loading', tracking=True)
    port_of_discharge = fields.Char(string='Port of Discharge', tracking=True)
    
    # Cost and goods receipt
    cost_line_ids = fields.One2many('foreign.shipment.cost', 'shipment_id', string='Shipment Costs')
    product_line_ids = fields.One2many('foreign.shipment.product_line', 'shipment_id', string='Product Lines')
    goods_receipt_id = fields.Many2one('stock.picking', string='Goods Receipt', readonly=True)
    
    # Computed fields
    currency_id = fields.Many2one(related='lc_cad_id.currency_id', store=True)
    total_costs = fields.Monetary(string='Total Costs', compute='_compute_total_costs', currency_field='currency_id')
    can_create_grn = fields.Boolean(string='Can Create GRN', compute='_compute_can_create_grn', store=True)
    grn_count = fields.Integer(string='GRN Count', compute='_compute_grn_count', store=True)
    landed_costs_computed = fields.Boolean(string='Landed Costs Computed', default=False, help="Indicates if landed costs have been calculated for this shipment")

    _sql_constraints = [
        ('name_unique', 'unique(name)', 'Shipment reference must be unique!')
    ]

    @api.depends('product_line_ids.product_qty', 'cost_line_ids.amount')
    def _compute_landed_costs(self):
        """Compute and store landed costs for all product lines - only once when shipment is completed"""
        for shipment in self:
            if shipment.state == 'completed' and shipment.lc_cad_id and not shipment.landed_costs_computed:
                shipment._calculate_landed_costs()

    def _calculate_landed_costs(self):
        """Centralized landed cost calculation for performance optimization"""
        print(f"=== DEBUG: Calculating landed costs for shipment {self.name} ===")
        if not self.lc_cad_id:
            print("No LC/CAD found, skipping calculation")
            return
        
        # Get total costs
        total_lc_cost = self._get_total_lc_cost()
        total_shipment_cost = self._get_total_shipment_cost()
        
        print(f"Total LC cost: {total_lc_cost}")
        print(f"Total shipment cost: {total_shipment_cost}")
        print(f"Product lines to process: {len(self.product_line_ids)}")
        
        # Calculate ratios and landed costs for each product line
        for line in self.product_line_ids:
            # Calculate LC ratio
            lc_ratio = self._calculate_product_lc_ratio(line)
            
            # Calculate shipment ratio  
            shipment_ratio = self._calculate_product_shipment_ratio(line)
            
            # Calculate landed unit cost
            landed_cost = (total_lc_cost * lc_ratio / 100) + (total_shipment_cost * shipment_ratio / 100)
            
            print(f"Product: {line.product_id.name}")
            print(f"  LC ratio: {lc_ratio}")
            print(f"  Shipment ratio: {shipment_ratio}")
            print(f"  Landed unit cost: {landed_cost}")
            
            # Store calculated values
            line.write({
                'lc_ratio': lc_ratio,
                'shipment_ratio': shipment_ratio,
                'landed_unit_cost': landed_cost
            })
        
        # Mark landed costs as computed
        self.write({'landed_costs_computed': True})
        print("=== DEBUG: Landed costs calculation completed ===")

    def _get_total_lc_cost(self):
        """Calculate total LC cost including PO value and all LC costs"""
        lc_cad = self.lc_cad_id
        
        # PO total value
        po_total = sum(line.product_qty * line.price_unit for line in lc_cad.product_line_ids)
        
        # LC costs
        lc_costs = sum(line.amount for line in lc_cad.cost_line_ids 
                      if line.vendor_bill_id and line.vendor_bill_id.state == 'posted' and not line.cost_type_id.is_tax)
        
        return po_total + lc_costs
    
    def _get_total_shipment_cost(self):
        """Calculate total shipment cost"""
        return sum(line.amount for line in self.cost_line_ids 
                  if line.vendor_bill_id and line.vendor_bill_id.state == 'posted' and not line.cost_type_id.is_tax)

    def _calculate_product_lc_ratio(self, product_line):
        """Calculate product's LC ratio percentage"""
        lc_cad = self.lc_cad_id
        
        # Find the corresponding product line in LC/CAD
        lc_product_line = lc_cad.product_line_ids.filtered(
            lambda l: l.product_id == product_line.product_id
        )
        if not lc_product_line:
            return 0.0
        
        lc_product_line = lc_product_line[0]
        
        # Calculate LC ratio as percentage of PO total
        total_po_value = sum(line.product_qty * line.price_unit for line in lc_cad.product_line_ids)
        if total_po_value == 0:
            return 0.0
        
        product_po_value = lc_product_line.product_qty * lc_product_line.price_unit
        lc_ratio_percent = (product_po_value / total_po_value) * 100
        
        # Convert to unit LC ratio (divide by PO quantity, not shipment quantity)
        po_quantity = lc_product_line.product_qty
        unit_lc_ratio = lc_ratio_percent / po_quantity if po_quantity > 0 else 0
        
        return unit_lc_ratio

    def _calculate_product_shipment_ratio(self, product_line):
        """Calculate product's shipment ratio percentage"""
        # Sum of (unit price × qty) for all lines in this shipment
        total_shipment_value = sum(
            line.product_qty * line.purchase_order_line_id.price_unit 
            for line in self.product_line_ids
        )
        
        if total_shipment_value == 0:
            return 0.0
        
        # This product's shipment line value
        product_shipment_value = product_line.product_qty * product_line.purchase_order_line_id.price_unit
        
        # Product's share of shipment cost as %
        shipment_ratio_percent = (product_shipment_value / total_shipment_value) * 100
        
        # Convert to unit shipment ratio (divide by shipment quantity)
        quantity = product_line.product_qty
        unit_shipment_ratio = shipment_ratio_percent / quantity if quantity > 0 else 0
        
        return unit_shipment_ratio

    
    @api.depends('cost_line_ids.amount')
    def _compute_total_costs(self):
        for record in self:
            costs = sum(line.amount for line in record.cost_line_ids)
            record.total_costs = costs

    
    @api.depends('state', 'cost_line_ids.vendor_bill_id.state', 'lc_cad_id.cost_line_ids.vendor_bill_id.state', 'goods_receipt_id')
    def _compute_can_create_grn(self):
        for record in self:
            # can_create_grn is essentially the same as can_receive_goods
            if record.state != 'completed' or record.goods_receipt_id:
                record.can_create_grn = False
                continue
                
            # Check shipment cost bills
            all_shipment_bills_posted = all(
                cost.vendor_bill_id and cost.vendor_bill_id.state == 'posted'
                for cost in record.cost_line_ids
            )
            # Check LC cost bills
            all_lc_bills_posted = all(
                cost.vendor_bill_id and cost.vendor_bill_id.state == 'posted'
                for cost in record.lc_cad_id.cost_line_ids
            )
            
            record.can_create_grn = all_shipment_bills_posted and all_lc_bills_posted

    @api.depends('goods_receipt_id')
    def _compute_grn_count(self):
        for record in self:
            if record.goods_receipt_id:
                # Check if the picking actually exists - goods_receipt_id is a record object
                try:
                    # Try to check if it exists as a record object
                    record.grn_count = 1 if record.goods_receipt_id.exists() else 0
                except AttributeError:
                    # If it's not a record object (e.g., integer ID), search for it
                    picking = self.env['stock.picking'].browse(record.goods_receipt_id)
                    record.grn_count = 1 if picking.exists() else 0
            else:
                record.grn_count = 0

    def unlink(self):
        for record in self:
            if record.state != 'draft':
                raise UserError(_("You cannot delete a shipment that is not in draft state."))
            if record.cost_line_ids or record.goods_receipt_id:
                raise UserError(_("You cannot delete a shipment with linked costs or goods receipts."))
        return super(ForeignShipment, self).unlink()

    # Status flow methods
    def action_in_transit(self):
        self.write({'state': 'in_transit'})

    def action_customs_clearance(self):
        if self.state != 'in_transit':
            raise UserError(_("Shipment must be in transit to start customs clearance."))
        self.write({'state': 'customs_clearance'})

    def action_arrived(self):
        if self.state != 'customs_clearance':
            raise UserError(_("Shipment must be in customs clearance to mark as arrived."))
        self.write({'state': 'arrived'})

    def action_completed(self):
        if self.state != 'arrived':
            raise UserError(_("Shipment must be arrived to be marked as completed."))
        
        # Check if all shipment cost bills are posted
        unposted_bills = self.cost_line_ids.filtered(lambda line: not line.vendor_bill_id or line.vendor_bill_id.state != 'posted')
        if unposted_bills:
            raise UserError(_("Shipment cannot be completed while there are unposted shipment bills. Please post all vendor bills first."))
        
        # Check if all LC cost bills are posted
        if self.lc_cad_id:
            unposted_lc_bills = self.lc_cad_id.cost_line_ids.filtered(lambda line: not line.vendor_bill_id or line.vendor_bill_id.state != 'posted')
            if unposted_lc_bills:
                raise UserError(_("Shipment cannot be completed while there are unposted LC bills. Please post all LC vendor bills first."))
        
        self.write({'state': 'completed'})

    def action_create_grn(self):
        """Create goods receipt when shipment is completed and LC is closed"""
        self.ensure_one()
        if not self.can_create_grn:
            raise UserError(_("Goods can only be received when shipment is completed and all related bills are posted."))
        
        # Calculate landed costs only if not already computed
        if not self.landed_costs_computed:
            print(f"=== DEBUG: GRN creation - triggering landed cost calculation for shipment {self.name} ===")
            self._calculate_landed_costs()
        else:
            print(f"=== DEBUG: GRN creation - landed costs already computed for shipment {self.name} ===")
        
        # Create goods receipt (stock picking) based on PO order lines
        picking_vals = {
            'partner_id': self.purchase_order_id.partner_id.id,
            'picking_type_id': self.env.ref('stock.picking_type_in').id,
            'origin': f"{self.purchase_order_id.name} - {self.name}",
            'location_id': self.env.ref('stock.stock_location_suppliers').id,
            'location_dest_id': self.env.ref('stock.stock_location_stock').id,
        }
        picking = self.env['stock.picking'].create(picking_vals)
        
        # Create stock moves from shipment product lines
        for shipment_product_line in self.product_line_ids:
            po_line = shipment_product_line.purchase_order_line_id
            
            # Use stored landed cost for foreign purchases, or PO price for domestic
            if shipment_product_line.landed_unit_cost > 0:
                cost_price = shipment_product_line.landed_unit_cost
            else:
                cost_price = po_line.price_unit  # Fallback to PO price
            
            move_vals = {
                'name': shipment_product_line.name,
                'product_id': shipment_product_line.product_id.id,
                'product_uom_qty': shipment_product_line.product_qty,
                'product_uom': shipment_product_line.product_uom.id,
                'picking_id': picking.id,
                'location_id': self.env.ref('stock.stock_location_suppliers').id,
                'location_dest_id': self.env.ref('stock.stock_location_stock').id,
                'price_unit': po_line.price_unit,           # Unit price from PO line
                'purchase_line_id': po_line.id,              # Link to PO line for cost calculation
                'inventory_cost': cost_price,             # Cost price for inventory valuation
            }
            self.env['stock.move'].create(move_vals)
        
        self.goods_receipt_id = picking.id
        
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'stock.picking',
            'res_id': picking.id,
            'view_mode': 'form',
            'name': 'Goods Receipt',
        }

    def action_create_grn(self):
        """Create GRN for shipment when all conditions are met"""
        self.ensure_one()
        if not self.can_create_grn:
            raise UserError(_("GRN can only be created when shipment is completed and all related bills are posted."))
        
        # Create goods receipt (stock picking) based on PO order lines
        picking_vals = {
            'partner_id': self.purchase_order_id.partner_id.id,
            'picking_type_id': self.env.ref('stock.picking_type_in').id,
            'origin': f"{self.purchase_order_id.name} - {self.name}",
            'location_id': self.env.ref('stock.stock_location_suppliers').id,
            'location_dest_id': self.env.ref('stock.stock_location_stock').id,
        }
        picking = self.env['stock.picking'].create(picking_vals)
        
        # Create stock moves from shipment product lines
        for shipment_product_line in self.product_line_ids:
            po_line = shipment_product_line.purchase_order_line_id
            
            # Use stored landed cost for foreign purchases, or PO price for domestic
            if shipment_product_line.landed_unit_cost > 0:
                cost_price = shipment_product_line.landed_unit_cost
            else:
                cost_price = po_line.price_unit  # Fallback to PO price
            
            move_vals = {
                'name': shipment_product_line.name,
                'product_id': shipment_product_line.product_id.id,
                'product_uom_qty': shipment_product_line.product_qty,
                'product_uom': shipment_product_line.product_uom.id,
                'picking_id': picking.id,
                'location_id': self.env.ref('stock.stock_location_suppliers').id,
                'location_dest_id': self.env.ref('stock.stock_location_stock').id,
                'price_unit': po_line.price_unit,           # Unit price from PO line
                'purchase_line_id': po_line.id,              # Link to PO line for cost calculation
                'inventory_cost': cost_price,             # Cost price for inventory valuation
            }
            self.env['stock.move'].create(move_vals)
        
        self.goods_receipt_id = picking.id
        
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'stock.picking',
            'res_id': picking.id,
            'view_mode': 'form',
            'name': 'Goods Receipt',
        }

    def action_view_grn(self):
        """View the GRN (Goods Receipt) for this shipment"""
        self.ensure_one()
        if not self.goods_receipt_id:
            raise UserError(_("No GRN found for this shipment."))
        
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'stock.picking',
            'res_id': self.goods_receipt_id.id,
            'view_mode': 'form',
            'name': 'Goods Receipt',
        }

    def action_view_costs(self):
        self.ensure_one()
        # Since this is a smart button, we'll just scroll to the costs tab
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'foreign.shipment',
            'res_id': self.id,
            'view_mode': 'form',
            'name': 'Shipment',
            'context': {'default_shipment_id': self.id},
        }

    def action_create_shipment_costs(self):
        pass

    def action_cost_build_up(self):
        """Generate Cost Build-Up report for the shipment"""
        self.ensure_one()
        
        # Validation checks
        self._validate_cost_build_up_requirements()
        
        # Redirect to the report
        return {
            'type': 'ir.actions.report',
            'report_name': 'foreign_purchase_module.report_cost_build_up',
            'report_type': 'qweb-pdf',
            'report_file': 'foreign_purchase_module.report_cost_build_up',
            'data': {'shipment_id': self.id},
            'context': {'active_id': self.id},
        }
    
    def _validate_cost_build_up_requirements(self):
        """Validate that all required bills are posted before generating cost build-up"""
        # Check LC cost lines
        lc = self.lc_cad_id
        unposted_lc_bills = lc.cost_line_ids.filtered(
            lambda line: not line.vendor_bill_id or line.vendor_bill_id.state != 'posted'
        )
        if unposted_lc_bills:
            bill_names = unposted_lc_bills.mapped(
                lambda line: f"Cost line '{line.name}' (Bill: {line.vendor_bill_id.name if line.vendor_bill_id else 'None'})"
            )
            raise UserError(_(
                "Cannot generate Cost Build-Up. The following LC cost lines do not have posted bills:\n%s"
            ) % "\n".join(bill_names))
        
        # Check shipment cost lines
        unposted_shipment_bills = self.cost_line_ids.filtered(
            lambda line: not line.vendor_bill_id or line.vendor_bill_id.state != 'posted'
        )
        if unposted_shipment_bills:
            bill_names = unposted_shipment_bills.mapped(
                lambda line: f"Cost line '{line.name}' (Bill: {line.vendor_bill_id.name if line.vendor_bill_id else 'None'})"
            )
            raise UserError(_(
                "Cannot generate Cost Build-Up. The following shipment cost lines do not have posted bills:\n%s"
            ) % "\n".join(bill_names))
        
        # Check if there are product lines
        if not self.product_line_ids:
            raise UserError(_("Cannot generate Cost Build-Up. No product lines found in this shipment."))
        
        # Check if LC has product lines
        if not lc.product_line_ids:
            raise UserError(_("Cannot generate Cost Build-Up. No product lines found in the associated LC."))

class ForeignShipmentCost(models.Model):
    _name = 'foreign.shipment.cost'
    _description = 'Shipment Cost'

    shipment_id = fields.Many2one('foreign.shipment', string='Shipment', required=True, ondelete='cascade')
    cost_type_id = fields.Many2one('foreign.lc_cad.cost_type', string='Cost Type', required=True, db_index=True)
    date = fields.Date(string='Date', default=fields.Date.today, required=True)
    name = fields.Char(string='Description', required=True)
    currency_id = fields.Many2one('res.currency', related='shipment_id.currency_id')
    amount = fields.Monetary(string='Amount', currency_field='currency_id', required=True)
    vendor_bill_id = fields.Many2one('account.move', string='Vendor Bill', readonly=True)
    bill_state = fields.Selection(related='vendor_bill_id.state', string='Bill Status')
    is_adjustment = fields.Boolean(related='cost_type_id.is_adjustment', string='Is Adjustment', store=True)

    @api.onchange('cost_type_id')
    def _onchange_cost_type_id(self):
        if self.cost_type_id:
            self.name = self.cost_type_id.name

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            # Check if shipment is completed
            if vals.get('shipment_id'):
                shipment = self.env['foreign.shipment'].browse(vals['shipment_id'])
                if shipment.state == 'completed':
                    raise UserError(_("Cannot add cost lines to a completed shipment."))
        return super(ForeignShipmentCost, self).create(vals_list)

    def create_bill(self):
        self.ensure_one()
        if self.vendor_bill_id:
            raise UserError(_("A record already exists for this cost line."))
        
        if self.cost_type_id.is_adjustment:
            return self.action_create_journal()

        # Determine the invoice line account (cost type override / company default GIT)
        invoice_line = {
            'name': self.name,
            'quantity': 1,
            'price_unit': self.amount,
        }
        if self.cost_type_id.is_tax and self.cost_type_id.tax_account_id:
            invoice_line['account_id'] = self.cost_type_id.tax_account_id.id
        elif self.cost_type_id.git_account_id:
            invoice_line['account_id'] = self.cost_type_id.git_account_id.id
        else:
            company_git = self.shipment_id.lc_cad_id.company_id.foreign_purchase_git_account_id
            if self.shipment_id.purchase_order_id.po_class == 'foreign' and company_git:
                invoice_line['account_id'] = company_git.id
            
        # Create draft vendor bill
        bill_vals = {
            'move_type': 'in_invoice',
            'partner_id': self.shipment_id.purchase_order_id.partner_id.id,
            'invoice_date': fields.Date.today(),
            'invoice_line_ids': [(0, 0, invoice_line)],
        }
        bill = self.env['account.move'].create(bill_vals)
        self.vendor_bill_id = bill
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'account.move',
            'res_id': bill.id,
            'view_mode': 'form',
        }

    def action_create_journal(self):
        """Create manual journal entry for this specific cost line"""
        self.ensure_one()
        if self.vendor_bill_id:
            raise UserError(_("A record already exists for this cost line."))
        
        if not self.cost_type_id.git_account_id or not self.cost_type_id.adjustment_account_id:
            raise UserError(_("Please configure both GIT and Adjustment accounts on the cost type."))

        journal = self.env['account.journal'].search([('type', '=', 'general')], limit=1)
        if not journal:
            raise UserError(_("Please configure a General journal."))

        move_vals = {
            'move_type': 'entry',
            'date': fields.Date.today(),
            'journal_id': journal.id,
            'ref': f"Adjustment: {self.name} (Shipment: {self.shipment_id.name})",
            'line_ids': [
                (0, 0, {
                    'name': f"{self.name} Adjustment",
                    'account_id': self.cost_type_id.git_account_id.id,
                    'debit': self.amount,
                    'credit': 0.0,
                }),
                (0, 0, {
                    'name': f"{self.name} Adjustment",
                    'account_id': self.cost_type_id.adjustment_account_id.id,
                    'debit': 0.0,
                    'credit': self.amount,
                }),
            ]
        }
        move = self.env['account.move'].create(move_vals)
        move.action_post()
        self.vendor_bill_id = move.id
        
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'account.move',
            'res_id': move.id,
            'view_mode': 'form',
            'name': 'Journal Entry',
        }

    def action_view_journal(self):
        """View journal entry for this specific cost line"""
        self.ensure_one()
        if not self.vendor_bill_id:
            raise UserError(_("No journal entry exists for this cost line."))
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Journal Entry',
            'res_model': 'account.move',
            'res_id': self.vendor_bill_id.id,
            'view_mode': 'form',
        }

    def view_bill(self):
        self.ensure_one()
        if not self.vendor_bill_id:
            raise UserError(_("No bill exists for this cost line."))
        
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'account.move',
            'res_id': self.vendor_bill_id.id,
            'view_mode': 'form',
            'name': 'Vendor Bill',
        }

    def unlink(self):
        for record in self:
            if record.vendor_bill_id and record.vendor_bill_id.state == 'posted':
                raise UserError(_("Cannot delete a cost line with a posted vendor bill."))
            
            # Prevent deletion if GRN has been created for related shipment
            if record.shipment_id.goods_receipt_id:
                raise UserError(_("Cannot delete cost line after GRN creation. The landed costs have been finalized."))
            
            # Delete draft bill if exists
            if record.vendor_bill_id and record.vendor_bill_id.state == 'draft':
                record.vendor_bill_id.unlink()
        return super(ForeignShipmentCost, self).unlink()

    def write(self, vals):
        for record in self:
            if record.vendor_bill_id and record.vendor_bill_id.state == 'posted':
                # Allow only certain fields to be modified when bill is posted
                allowed_fields = {'name'}  # Only allow description change
                if any(field not in allowed_fields for field in vals.keys()):
                    raise UserError(_("Cannot modify a cost line with a posted vendor bill. Only description can be changed."))
        return super(ForeignShipmentCost, self).write(vals)




class ForeignShipmentProductLine(models.Model):
    _name = 'foreign.shipment.product_line'
    _description = 'Shipment Product Line'
    _order = 'shipment_id, id'

    shipment_id = fields.Many2one('foreign.shipment', string='Shipment', required=True, ondelete='cascade')
    purchase_order_line_id = fields.Many2one('purchase.order.line', string='PO Line', required=True)
    product_id = fields.Many2one('product.product', string='Product', required=True)
    name = fields.Text(string='Description', required=True)
    product_qty = fields.Float(string='Quantity', digits='Product Unit of Measure', required=True)
    product_uom = fields.Many2one('uom.uom', string='Unit of Measure', required=True)
    
    # Landed cost calculation fields
    landed_unit_cost = fields.Float(
        string='Landed Unit Cost', 
        digits='Product Price',
        help="Calculated landed unit cost including all associated costs (LLC, shipment, etc.)"
    )
    lc_ratio = fields.Float(
        string='LC Ratio (%)', 
        digits=(12, 4),
        help="Product's share of total LC cost as percentage"
    )
    shipment_ratio = fields.Float(
        string='Shipment Ratio (%)', 
        digits=(12, 4),
        help="Product's share of shipment costs as percentage"
    )
