from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError

class ForeignLCCAD(models.Model):
    _name = 'foreign.lc_cad'
    _description = 'Letter of Credit / Cash Against Documents'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(string='LC/CAD Number', required=True, copy=False, tracking=True)
    instrument_type = fields.Selection([
        ('lc', 'LC'),
        ('cad', 'CAD')
    ], string='Instrument Type', required=True, tracking=True)
    
    purchase_order_id = fields.Many2one(
        'purchase.order', string='Purchase Order', required=True,
        domain=[('po_class', '=', 'foreign')], tracking=True, db_index=True
    )
    
    company_id = fields.Many2one('res.company', related='purchase_order_id.company_id', store=True, readonly=True)
    
    issuance_date = fields.Date(string='Issuance Date', required=True, tracking=True)
    expiry_date = fields.Date(string='Expiry Date', required=True, tracking=True)
    issuing_bank = fields.Char(string='Issuing Bank', tracking=True)
    beneficiary = fields.Char(string='Beneficiary', required=True, tracking=True)
    
    currency_id = fields.Many2one('res.currency', related='company_id.currency_id', store=True, readonly=True)
    total_value = fields.Monetary(
        string='Total LC Value', currency_field='currency_id',
        compute='_compute_total_value', store=True, readonly=True, tracking=True
    )
    
    state = fields.Selection([
        ('draft', 'Draft'),
        ('open', 'Open'),
        ('closed', 'Closed'),
        ('archived', 'Archived')
    ], string='Status', default='draft', tracking=True, db_index=True)
    
    no_further_shipments = fields.Boolean(string='No Further Shipments', tracking=True)
    
    cost_line_ids = fields.One2many('foreign.lc_cad.cost', 'lc_cad_id', string='Costs')
    product_line_ids = fields.One2many('foreign.lc_cad.product_line', 'lc_cad_id', string='Product Lines')
    shipment_ids = fields.One2many('foreign.shipment', 'lc_cad_id', string='Shipments')
    shipment_count = fields.Integer(string='Shipment Count', compute='_compute_shipment_count')
    
    total_costs = fields.Monetary(string='Total Costs', compute='_compute_total_costs', currency_field='currency_id')
    all_bills_paid = fields.Boolean(string='All Bills Paid', compute='_compute_all_bills_paid')
    all_shipments_completed = fields.Boolean(string='All Shipments Completed', compute='_compute_all_shipments_completed')
    can_be_closed = fields.Boolean(string='Can be Closed', compute='_compute_can_be_closed')
    all_related_bills_posted = fields.Boolean(string='All Related Bills Posted', compute='_compute_all_related_bills_posted')
    has_remaining_qty = fields.Boolean(string='Has Remaining Qty', compute='_compute_has_remaining_qty')

    _sql_constraints = [
        ('name_unique', 'unique(name)', 'The LC/CAD number must be unique!')
    ]

    @api.depends('purchase_order_id.amount_total', 'purchase_order_id.currency_id', 'currency_id')
    def _compute_total_value(self):
        for record in self:
            if record.purchase_order_id and record.currency_id:
                # Convert PO amount from PO currency to ETB (company currency)
                po_currency = record.purchase_order_id.currency_id
                company_currency = record.currency_id
                po_amount = record.purchase_order_id.amount_total
                
                if po_currency != company_currency:
                    # Convert using Odoo's currency conversion
                    record.total_value = po_currency._convert(
                        po_amount,
                        company_currency,
                        record.company_id,
                        record.issuance_date or fields.Date.today()
                    )
                else:
                    record.total_value = po_amount
            else:
                record.total_value = 0.0

    @api.depends('cost_line_ids.amount')
    def _compute_total_costs(self):
        for record in self:
            # Sum of all cost_line_ids.amount regardless of bill status
            costs = sum(line.amount for line in record.cost_line_ids)
            record.total_costs = costs

    @api.depends('cost_line_ids.vendor_bill_id.payment_state')
    def _compute_all_bills_paid(self):
        for record in self:
            bills = record.cost_line_ids.mapped('vendor_bill_id')
            if not bills:
                record.all_bills_paid = True
            else:
                record.all_bills_paid = all(bill.payment_state in ['paid', 'in_payment'] for bill in bills)

    @api.depends('shipment_ids')
    def _compute_shipment_count(self):
        for record in self:
            record.shipment_count = len(record.shipment_ids)

    @api.depends('shipment_ids.state', 'shipment_ids.cost_line_ids.vendor_bill_id.state')
    def _compute_all_shipments_completed(self):
        for record in self:
            if not record.shipment_ids:
                record.all_shipments_completed = False
            else:
                record.all_shipments_completed = all(shipment.state == 'completed' for shipment in record.shipment_ids)

    @api.depends('cost_line_ids.vendor_bill_id.state', 'shipment_ids.cost_line_ids.vendor_bill_id.state')
    def _compute_all_related_bills_posted(self):
        for record in self:
            # Check LC cost bills
            lc_bills_posted = True
            for cost_line in record.cost_line_ids:
                if cost_line.vendor_bill_id and cost_line.vendor_bill_id.state != 'posted':
                    lc_bills_posted = False
                    break
            
            # Check shipment cost bills
            shipment_bills_posted = True
            for shipment in record.shipment_ids:
                for cost_line in shipment.cost_line_ids:
                    if cost_line.vendor_bill_id and cost_line.vendor_bill_id.state != 'posted':
                        shipment_bills_posted = False
                        break
                if not shipment_bills_posted:
                    break
            
            record.all_related_bills_posted = lc_bills_posted and shipment_bills_posted

    
    @api.depends('all_bills_paid', 'all_shipments_completed')
    def _compute_can_be_closed(self):
        for record in self:
            record.can_be_closed = (record.all_shipments_completed or record.no_further_shipments) and record.all_bills_paid

    @api.depends('product_line_ids.product_qty', 'shipment_ids.product_line_ids.product_qty')
    def _compute_has_remaining_qty(self):
        for record in self:
            # If there are no product lines at all, don't allow shipment creation
            if not record.product_line_ids:
                record.has_remaining_qty = False
                continue
            
            # Pre-aggregate shipped quantities using SQL for performance
            shipped_quantities = {}
            if record.shipment_ids:
                # Use SQL query to aggregate shipped quantities by PO line
                self.env.cr.execute("""
                    SELECT 
                        spl.purchase_order_line_id,
                        SUM(spl.product_qty) as shipped_qty
                    FROM foreign_shipment_product_line spl
                    JOIN foreign_shipment s ON spl.shipment_id = s.id
                    WHERE s.lc_cad_id = %s
                    GROUP BY spl.purchase_order_line_id
                """, (record.id,))
                shipped_results = self.env.cr.fetchall()
                shipped_quantities = {row[0]: row[1] for row in shipped_results}
                print(f"DEBUG: shipped_quantities from SQL: {shipped_quantities}")
            
            # Check if any product line has remaining quantity
            has_rem = False
            for line in record.product_line_ids:
                shipped_qty = shipped_quantities.get(line.purchase_order_line_id.id, 0)
                if line.product_qty > shipped_qty:
                    has_rem = True
                    break
            
            record.has_remaining_qty = has_rem

    def unlink(self):
        for record in self:
            if record.state != 'draft':
                raise UserError(_("You cannot delete an LC/CAD that is not in draft state."))
            if record.shipment_ids or record.cost_line_ids.mapped('vendor_bill_id'):
                raise UserError(_("You cannot delete an LC/CAD with linked shipments or bills."))
        return super(ForeignLCCAD, self).unlink()

    def action_open(self):
        """Open LC with comprehensive validation"""
        self.ensure_one()
        
        # Validate expiry date is not in the past
        if self.expiry_date and self.expiry_date < fields.Date.today():
            raise UserError(_("Cannot open LC with expiry date in the past. Please update the expiry date."))
        
        # Validate beneficiary is not empty
        if not self.beneficiary or not self.beneficiary.strip():
            raise UserError(_("Cannot open LC without beneficiary. Please specify the beneficiary."))
        
        # Validate purchase order is confirmed
        if not self.purchase_order_id or self.purchase_order_id.state != 'purchase':
            raise UserError(_("Cannot open LC without a confirmed purchase order. Please confirm the purchase order first."))
        
        # Check for existing open LC for same PO
        existing_open_lc = self.env['foreign.lc_cad'].search([
            ('purchase_order_id', '=', self.purchase_order_id.id),
            ('state', '=', 'open'),
            ('id', '!=', self.id)
        ], limit=1)
        if existing_open_lc:
            raise UserError(_("An open LC already exists for this purchase order: %s. Please close the existing LC first.") % existing_open_lc.name)
        
        self.write({'state': 'open'})

    def action_close(self):
        if self.state != 'open':
            raise UserError(_("Only open LC/CADs can be closed."))
        if not self.can_be_closed:
            raise UserError(_("LC/CAD cannot be closed. Check if all shipments are completed and all bills are paid."))
        
        # Check if all cost bills are posted
        unposted_bills = self.cost_line_ids.filtered(lambda line: not line.vendor_bill_id or line.vendor_bill_id.state != 'posted')
        if unposted_bills:
            raise UserError(_("LC/CAD cannot be closed while there are unposted bills. Please post all vendor bills first."))
        
        self.write({'state': 'closed'})
        
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('LC/CAD Closed'),
                'message': _('The LC/CAD has been successfully closed.'),
                'type': 'success',
            }
        }

    def action_archive(self):
        self.write({'state': 'archived'})

    
    def action_view_shipments(self):
        self.ensure_one()
        action = self.env.ref('foreign_purchase_module.action_foreign_shipment').read()[0]
        action['domain'] = [('lc_cad_id', '=', self.id)]
        return action
    
    
    def action_create_shipment(self):
        self.ensure_one()
        if self.state != 'open':
            raise UserError(_("Shipments can only be created when LC/CAD is in open state."))
        
        # Create new shipment with proper sequence
        shipment_name = self.env['ir.sequence'].next_by_code('foreign.shipment')
        
        # If sequence fails, create a fallback with correct format
        if not shipment_name:
            current_year = fields.Date.today().year
            # Find the highest existing shipment number for this year
            existing_shipments = self.env['foreign.shipment'].search([
                ('name', 'like', f'SHIP/{current_year}/')
            ], order='name desc', limit=1)
            
            if existing_shipments:
                last_number = int(existing_shipments.name.split('/')[-1])
                next_number = last_number + 1
            else:
                next_number = 1
            
            shipment_name = f'SHIP/{current_year}/{next_number:04d}'
        
        shipment_vals = {
            'name': shipment_name,
            'lc_cad_id': self.id,
        }
        shipment = self.env['foreign.shipment'].create(shipment_vals)
        
        # Copy PO product lines to shipment with remaining qty
        for line in self.product_line_ids:
            shipped = sum(
                sl.product_qty for sl in self.shipment_ids.mapped('product_line_ids')
                if sl.purchase_order_line_id.id == line.purchase_order_line_id.id
            )
            remaining_qty = line.product_qty - shipped
            if remaining_qty > 0:
                product_line_vals = {
                    'shipment_id': shipment.id,
                    'purchase_order_line_id': line.purchase_order_line_id.id,
                    'product_id': line.product_id.id,
                    'name': line.name,
                    'product_qty': remaining_qty,
                    'product_uom': line.product_uom.id,
                }
                self.env['foreign.shipment.product_line'].create(product_line_vals)
        
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'foreign.shipment',
            'res_id': shipment.id,
            'view_mode': 'form',
            'name': 'Shipment',
        }

class ForeignLCCADCost(models.Model):
    _name = 'foreign.lc_cad.cost'
    _description = 'LC/CAD Cost'

    lc_cad_id = fields.Many2one('foreign.lc_cad', string='LC/CAD', required=True, ondelete='cascade')
    cost_type_id = fields.Many2one('foreign.lc_cad.cost_type', string='Cost Type', required=True, db_index=True)
    date = fields.Date(string='Date', default=fields.Date.today, required=True)
    name = fields.Char(string='Description', required=True)
    currency_id = fields.Many2one('res.currency', related='lc_cad_id.currency_id')
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
            # Check if LC has completed shipments
            if vals.get('lc_cad_id'):
                lc_cad = self.env['foreign.lc_cad'].browse(vals['lc_cad_id'])
                completed_shipments = lc_cad.shipment_ids.filtered(lambda s: s.state == 'completed')
                if completed_shipments:
                    raise UserError(_("Cannot add cost lines to an LC that has completed shipments."))
        return super(ForeignLCCADCost, self).create(vals_list)

    def create_bill(self):
        """Create vendor bill for normal/tax cost types"""
        self.ensure_one()
        if self.vendor_bill_id:
            raise UserError(_("A record already exists for this cost line."))
        
        if self.is_adjustment:
            raise UserError(_("Cannot create bill for adjustment cost type. Use 'Create Journal' instead."))
        
        # Only validate tax account for tax cost types
        if self.cost_type_id.is_tax and not self.cost_type_id.tax_account_id:
            raise UserError(_("Please configure the Tax account on the cost type."))

        journal = self.env['account.journal'].search([('type', '=', 'general')], limit=1)
        if not journal:
            raise UserError(_("Please configure a General journal."))

        # Determine the invoice line account based on cost type / company default GIT
        invoice_line = {
            'name': self.name,
            'quantity': 1,
            'price_unit': self.amount,
        }
        
        # Set account based on cost type
        if self.cost_type_id.is_tax and self.cost_type_id.tax_account_id:
            # Tax cost type - use configured tax account
            invoice_line['account_id'] = self.cost_type_id.tax_account_id.id
        elif self.cost_type_id.git_account_id:
            # Cost-type override account
            invoice_line['account_id'] = self.cost_type_id.git_account_id.id
        else:
            # For foreign POs, default to company-level GIT account (if configured)
            company_git = self.lc_cad_id.company_id.foreign_purchase_git_account_id
            if self.lc_cad_id.purchase_order_id.po_class == 'foreign' and company_git:
                invoice_line['account_id'] = company_git.id
        # Otherwise: leave unset and let Odoo choose the default account
        # Adjustment cost types should not reach here (they use create_journal)
            
        # Create draft vendor bill
        bill_vals = {
            'move_type': 'in_invoice',
            'partner_id': self.lc_cad_id.purchase_order_id.partner_id.id, # Default to PO partner, or user can change
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
            'name': 'Vendor Bill',
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
    
    def create_journal(self):
        """Create manual journal entry for adjustment cost types"""
        self.ensure_one()
        if self.vendor_bill_id:
            raise UserError(_("A record already exists for this cost line."))
        
        if not self.is_adjustment:
            raise UserError(_("Cannot create journal for non-adjustment cost type. Use 'Create Bill' instead."))
        
        if not self.cost_type_id.tax_account_id or not self.cost_type_id.adjustment_account_id:
            raise UserError(_("Please configure both Tax/Expense and Adjustment accounts on the cost type."))
        
        # Create manual journal entry
        journal_entry_vals = {
            'date': fields.Date.today(),
            'journal_id': self.env['account.journal'].search([('type', '=', 'general')], limit=1).id,
            'ref': f"Adjustment for {self.lc_cad_id.name}",
            'line_ids': []
        }
        
        # Debit GIT interim account (normal expense)
        debit_line = (0, 0, {
            'account_id': self.cost_type_id.tax_account_id.id,
            'name': f"{self.name} Adjustment",
            'debit': self.amount,
            'credit': 0.0,
        })
        
        # Credit adjustment account (non-AP adjustment)
        credit_line = (0, 0, {
            'account_id': self.cost_type_id.adjustment_account_id.id,
            'name': f"{self.name} Adjustment",
            'debit': 0.0,
            'credit': self.amount,
        })
        
        journal_entry_vals['line_ids'].extend([debit_line, credit_line])
        
        # Create and post the journal entry
        move = self.env['account.move'].create(journal_entry_vals)
        move.action_post()
        
        # Link to cost line
        self.vendor_bill_id = move.id
        
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Success',
                'message': 'Adjustment journal entry created and posted successfully',
                'type': 'success',
            }
        }

    
    def unlink(self):
        for record in self:
            if record.vendor_bill_id and record.vendor_bill_id.state == 'posted':
                raise UserError(_("Cannot delete a cost line with a posted vendor bill."))
            
            # Prevent deletion if GRN has been created for related shipment
            if record.lc_cad_id.shipment_ids.filtered('goods_receipt_id'):
                raise UserError(_("Cannot delete cost line after GRN creation. The landed costs have been finalized."))
            
            # Delete draft bill if exists
            if record.vendor_bill_id and record.vendor_bill_id.state == 'draft':
                record.vendor_bill_id.unlink()
        return super(ForeignLCCADCost, self).unlink()
    
    def write(self, vals):
        for record in self:
            if record.vendor_bill_id and record.vendor_bill_id.state == 'posted':
                # Allow only certain fields to be modified when bill is posted
                allowed_fields = {'name'}  # Only allow description change
                if any(field not in allowed_fields for field in vals.keys()):
                    raise UserError(_("Cannot modify a cost line with a posted vendor bill. Only description can be changed."))
        return super(ForeignLCCADCost, self).write(vals)
    
    

class ForeignLCCADProductLine(models.Model):
    _name = 'foreign.lc_cad.product_line'
    _description = 'LC/CAD Product Line'
    _order = 'lc_cad_id, id'

    lc_cad_id = fields.Many2one('foreign.lc_cad', string='LC/CAD', required=True, ondelete='cascade')
    purchase_order_line_id = fields.Many2one('purchase.order.line', string='PO Line', required=True)
    product_id = fields.Many2one('product.product', string='Product', required=True)
    name = fields.Text(string='Description', required=True)
    product_qty = fields.Float(string='Quantity', digits='Product Unit of Measure', required=True)
    product_uom = fields.Many2one('uom.uom', string='Unit of Measure', required=True)
    price_unit = fields.Float(string='Unit Price', digits='Product Price')
    
