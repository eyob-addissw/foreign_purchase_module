from odoo import models, fields, api, _
from odoo.exceptions import UserError
from datetime import timedelta
import logging

_logger = logging.getLogger(__name__)


class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    po_class = fields.Selection([
        ('domestic', 'Domestic'),
        ('foreign', 'Foreign')
    ], string='PO Class', default='domestic', required=True)

    lc_count = fields.Integer(string='LC Count', compute='_compute_lc_count')
    can_create_grn = fields.Boolean(string='Can Create GRN', compute='_compute_can_create_grn', store=True)
    grn_count = fields.Integer(string='GRN Count', compute='_compute_grn_count', store=True)

    def _compute_lc_count(self):
        for record in self:
            record.lc_count = self.env['foreign.lc_cad'].search_count([('purchase_order_id', '=', record.id)])

    @api.depends('po_class')
    def _compute_can_create_grn(self):
        """Check if GRN can be created for this PO's shipments"""
        for record in self:
            if record.po_class != 'foreign':
                record.can_create_grn = False
                continue

            # Get all shipments for this PO
            shipments = self.env['foreign.shipment'].search([
                ('lc_cad_id.purchase_order_id', '=', record.id)
            ])

            if not shipments:
                record.can_create_grn = False
                continue

            # Check if any shipment can create GRN
            record.can_create_grn = any(shipment.can_create_grn for shipment in shipments)

    def _compute_grn_count(self):
        """Count GRNs for all shipments of this PO"""
        for record in self:
            if record.po_class != 'foreign':
                record.grn_count = 0
                continue

            # Get all shipments for this PO
            shipments = self.env['foreign.shipment'].search([
                ('lc_cad_id.purchase_order_id', '=', record.id)
            ])

            # Count GRNs from all shipments
            grn_count = sum(1 for shipment in shipments if shipment.goods_receipt_id)
            record.grn_count = grn_count

    def _create_picking(self):
        """Override to block picking creation for foreign POs"""
        _logger.info(f"_create_picking called for PO {self.name} - PO Class: {self.po_class} - State: {self.state}")
        if self.po_class == 'foreign':
            _logger.info(f"Automatic picking creation blocked for foreign PO {self.name} - PO Class: {self.po_class}")
            # Don't create picking for foreign POs - goods receipt will be created from shipment
            return self.env['stock.picking']

        # For domestic POs, use the original logic
        _logger.info(f"Allowing picking creation for domestic PO {self.name}")
        return super(PurchaseOrder, self)._create_picking()

    def action_create_picking(self):
        """Override to block manual picking creation for foreign POs"""
        if self.po_class == 'foreign':
            _logger.info(f"Goods receipt creation blocked for foreign PO {self.name} - PO Class: {self.po_class}")
            raise UserError(
                _("Goods receipts cannot be created manually for foreign purchase orders. Please create them from the shipment when both LC and shipment are completed."))

        return super(PurchaseOrder, self).action_create_picking()

    def button_confirm(self):
        """Override to add logging for PO confirmation"""
        _logger.info(f"Confirming PO {self.name} - PO Class: {self.po_class} - State: {self.state}")
        result = super(PurchaseOrder, self).button_confirm()
        _logger.info(f"PO {self.name} confirmed - PO Class: {self.po_class} - New State: {self.state}")

        # Check if pickings were created after confirmation
        pickings = self.env['stock.picking'].search([('origin', '=', self.name)])
        if pickings:
            _logger.info(f"Found {len(pickings)} pickings for PO {self.name} after confirmation")
            for picking in pickings:
                _logger.info(f"Picking {picking.name} - State: {picking.state} - Origin: {picking.origin}")
        else:
            _logger.info(f"No pickings found for PO {self.name} after confirmation")

        return result

    def action_create_grn_from_po(self):
        """Create GRN from PO when conditions are met"""
        self.ensure_one()
        if not self.can_create_grn:
            raise UserError(_("GRN can only be created when shipment conditions are met."))

        # Get the first shipment that can create GRN
        shipments = self.env['foreign.shipment'].search([
            ('lc_cad_id.purchase_order_id', '=', self.id)
        ])

        eligible_shipment = None
        for shipment in shipments:
            if shipment.can_create_grn:
                eligible_shipment = shipment
                break

        if not eligible_shipment:
            raise UserError(_("No eligible shipment found for GRN creation."))

        # Call the shipment's GRN creation method
        return eligible_shipment.action_create_grn()

    def action_view_grn(self):
        """View all GRNs for this PO"""
        self.ensure_one()
        if self.grn_count == 0:
            raise UserError(_("No GRNs found for this purchase order."))

        # Get all GRNs for this PO's shipments
        shipments = self.env['foreign.shipment'].search([
            ('lc_cad_id.purchase_order_id', '=', self.id)
        ])

        grn_ids = [shipment.goods_receipt_id.id for shipment in shipments if shipment.goods_receipt_id]

        if not grn_ids:
            raise UserError(_("No GRNs found for this purchase order."))

        return {
            'type': 'ir.actions.act_window',
            'res_model': 'stock.picking',
            'domain': [('id', 'in', grn_ids)],
            'view_mode': 'list,form',
            'name': 'Goods Receipts',
        }

    def action_view_picking(self):
        """Override to add logging for picking viewing"""
        _logger.info(f"action_view_picking called for PO {self.name} - PO Class: {self.po_class}")
        pickings = self.env['stock.picking'].search([('origin', '=', self.name)])
        _logger.info(f"Found {len(pickings)} pickings for PO {self.name} when viewing")
        return super(PurchaseOrder, self).action_view_picking()

    def action_create_lc(self):
        self.ensure_one()
        if self.po_class != 'foreign':
            raise UserError(_("LC can only be created for foreign purchase orders."))
        if self.state != 'purchase':
            raise UserError(_("LC can only be created for confirmed purchase orders."))

        # Check if LC already exists for this PO
        existing_lc = self.env['foreign.lc_cad'].search([('purchase_order_id', '=', self.id)], limit=1)
        if existing_lc:
            raise UserError(_("An LC/CAD already exists for this purchase order."))

        # Create new LC/CAD with sequence
        lc_name = self.env['ir.sequence'].next_by_code('foreign.lc_cad')

        # If sequence fails, create a fallback with correct format
        if not lc_name:
            current_year = fields.Date.today().year
            # Find highest existing LC number for this year
            existing_lcs = self.env['foreign.lc_cad'].search([
                ('name', 'like', f'LC/{current_year}/')
            ], order='name desc', limit=1)

            if existing_lcs:
                last_number = int(existing_lcs.name.split('/')[-1])
                next_number = last_number + 1
            else:
                next_number = 1

            lc_name = f'LC/{current_year}/{next_number:04d}'

        lc_vals = {
            'name': lc_name,
            'purchase_order_id': self.id,
            'instrument_type': 'lc',  # Default to LC
            'issuance_date': fields.Date.today(),
            'expiry_date': fields.Date.today() + timedelta(days=90),  # Default 90 days from issuance
            'beneficiary': self.partner_id.name,
        }
        lc = self.env['foreign.lc_cad'].create(lc_vals)

        # Copy PO product lines to LC
        for line in self.order_line:
            product_line_vals = {
                'lc_cad_id': lc.id,
                'purchase_order_line_id': line.id,
                'product_id': line.product_id.id,
                'name': line.name,
                'product_qty': line.product_qty,
                'product_uom': line.product_uom.id,
                'price_unit': line.price_unit,
            }
            self.env['foreign.lc_cad.product_line'].create(product_line_vals)

        return {
            'type': 'ir.actions.act_window',
            'res_model': 'foreign.lc_cad',
            'res_id': lc.id,
            'view_mode': 'form',
            'name': 'LC/CAD',
        }

    def button_cancel(self):
        for po in self:
            active_lcs = self.env['foreign.lc_cad'].search([
                ('purchase_order_id', '=', po.id),
                ('state', 'not in', ['cancel', 'cancelled'])
            ])

            if active_lcs:
                raise UserError(_(
                    "You cannot cancel Purchase Order %s because it has active LC/CAD records linked to it."
                ) % po.name)

        return super().button_cancel()

    def action_view_lc(self):
        self.ensure_one()
        action = self.env.ref('foreign_purchase_module.action_foreign_lc_cad').read()[0]
        action['domain'] = [('purchase_order_id', '=', self.id)]
        return action
