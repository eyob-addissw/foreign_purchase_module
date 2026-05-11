from odoo import models, fields, api, _
from odoo.exceptions import UserError
import json

class CostBuildUpReport(models.AbstractModel):
    _name = 'report.foreign_purchase_module.report_cost_build_up'
    _description = 'Cost Build Up Report Generator'

    @api.model
    def _get_report_values(self, docids, data=None):
        """
        Generate cost build-up calculations for the shipment
        """
        if not data:
            data = {}
        
        shipment_id = data.get('shipment_id') or (docids and docids[0])
        
        if not shipment_id:
            raise UserError(_("Shipment ID is required for Cost Build-Up report"))
        
        shipment = self.env['foreign.shipment'].browse(shipment_id)
        
        if not shipment.exists():
            raise UserError(_("Shipment not found"))
        
        # Get all calculations
        report_data = self._calculate_cost_build_up(shipment)
        
        # Debug with print statements
        print(f"=== Cost Build Up Report Debug ===")
        print(f"Shipment ID: {shipment_id}")
        print(f"Shipment exists: {shipment.exists()}")
        print(f"Shipment name: {shipment.name}")
        print(f"Company: {shipment.env.company}")
        print(f"Company logo exists: {bool(shipment.env.company.logo)}")
        print(f"Company name: {shipment.env.company.name}")
        print(f"Report data keys: {list(report_data.keys())}")
        
        # Unpack everything into the context for easier access in the template
        res = {
            'doc_ids': [shipment_id],
            'doc_model': 'foreign.shipment',
            'docs': shipment,
        }
        res.update(report_data)
        
        print(f"Final context keys: {list(res.keys())}")
        print(f"=== End Debug ===")
        
        return res
    
    def _calculate_cost_build_up(self, shipment):
        """
        Perform all cost build-up calculations
        """
        lc = shipment.lc_cad_id
        
        # Step 1: Proforma Invoice Summary
        proforma_data = self._get_proforma_summary(lc)
        
        # Step 2: LLC Ratio Computation
        lc_ratio_data = self._calculate_lc_ratios(proforma_data)
        
        # Step 3: Final Landed Cost Calculation (for current shipment only)
        shipments_data = [self._calculate_landed_costs(shipment, lc_ratio_data)]
        
        # Step 4: Grand Summary
        grand_summary = self._calculate_grand_summary(lc, shipment)
        
        return {
            'shipment': shipment,
            'lc': lc,
            'po_currency_name': lc.purchase_order_id.currency_id.name,
            'proforma_summary': proforma_data,
            'lc_ratios': lc_ratio_data,
            'shipments_data': shipments_data,
            'grand_summary': grand_summary,
        }
    
    def _get_proforma_summary(self, lc):
        """
        Step 1: Get proforma invoice summary from PO lines
        """
        lines = []
        total_po_value = 0.0
        
        for line in lc.product_line_ids:
            subtotal = line.product_qty * line.price_unit
            total_po_value += subtotal
            
            lines.append({
                'product': line.product_id,
                'product_name': line.product_id.name,
                'quantity': line.product_qty,
                'unit_price': line.price_unit,
                'subtotal': subtotal,
            })
        
        return {
            'lines': lines,
            'total_po_value': total_po_value,
        }
    
    def _calculate_lc_ratios(self, proforma_data):
        """
        Step 2: Calculate LLC ratios for each product
        """
        lines = []
        total_po_value = proforma_data['total_po_value']
        
        for line_data in proforma_data['lines']:
            subtotal = line_data['subtotal']
            quantity = line_data['quantity']
            
            # LC Ratio as percentage of PO total
            lc_ratio_percent = (subtotal / total_po_value) * 100 if total_po_value > 0 else 0
            
            # Unit LC Ratio (LC Ratio divided by quantity)
            unit_lc_ratio = lc_ratio_percent / quantity if quantity > 0 else 0
            
            lines.append({
                'product': line_data['product'],
                'product_name': line_data['product_name'],
                'po_subtotal': subtotal,
                'lc_ratio_percent': lc_ratio_percent,
                'unit_lc_ratio': unit_lc_ratio,
                'quantity': quantity,
            })
        
        return {
            'lines': lines,
            'total_po_value': total_po_value,
        }
    
    def _calculate_landed_costs(self, shipment, lc_ratio_data):
        """
        Step 3: Use stored landed costs from shipment product lines for performance
        """
        # Ensure landed costs are calculated and stored
        if shipment.state == 'completed':
            shipment._calculate_landed_costs()
        
        # Use stored values from shipment product lines
        landed_cost_lines = []
        
        for product_line in shipment.product_line_ids:
            quantity = product_line.product_qty
            
            # Use stored calculated values
            unit_cost = product_line.landed_unit_cost or 0.0
            total_line_cost = unit_cost * quantity
            
            # Calculate shipment line value for display (using PO price)
            unit_price = product_line.purchase_order_line_id.price_unit
            product_shipment_value = quantity * unit_price
            
            # Calculate shipment percentage for display
            total_shipment_value = sum(
                line.product_qty * line.purchase_order_line_id.price_unit 
                for line in shipment.product_line_ids
            )
            shipment_percent = (product_shipment_value / total_shipment_value * 100) if total_shipment_value > 0 else 0
            
            # Calculate the actual shipment line cost allocated (for display)
            total_shipment_cost = shipment._get_total_shipment_cost()
            shipment_line_cost = total_shipment_cost * (shipment_percent / 100)
            
            landed_cost_lines.append({
                'product': product_line.product_id,
                'product_name': product_line.product_id.name,
                'quantity': quantity,
                'unit_price': unit_price,
                'shipment_line_cost': shipment_line_cost,
                'shipment_percent': shipment_percent,
                'unit_shipment_ratio': product_line.shipment_ratio,
                'unit_lc_ratio': product_line.lc_ratio,
                'unit_cost': unit_cost,
                'total_line_cost': total_line_cost,
                'formula': f"Stored Cost: {unit_cost:,.2f} (LC Ratio: {product_line.lc_ratio:.2f}%, Shipment Ratio: {product_line.shipment_ratio:.2f}%)",
            })
        
        # Get total costs from shipment
        total_lc_cost = shipment._get_total_lc_cost()
        total_shipment_cost = shipment._get_total_shipment_cost()
        
        return {
            'shipment': shipment,
            'lines': landed_cost_lines,
            'total_lc_cost': total_lc_cost,
            'total_shipment_cost': total_shipment_cost,
            'shipment_total': sum(line['total_line_cost'] for line in landed_cost_lines),
        }
    
    def _calculate_shipment_ratios(self, shipment, lc_ratio_data):
        """
        Calculate shipment-level ratios for cost distribution
        """
        # Create a mapping of product to LC ratio data
        lc_ratio_map = {line['product'].id: line for line in lc_ratio_data['lines']}
        
        # Calculate shipment line costs (quantity × unit LC ratio)
        shipment_lines = []
        total_shipment_cost = 0.0
        
        for line in shipment.product_line_ids:
            product_id = line.product_id.id
            quantity = line.product_qty
            
            # Get LC ratio for this product
            lc_ratio_data = lc_ratio_map.get(product_id, {})
            unit_lc_ratio = lc_ratio_data.get('unit_lc_ratio', 0)
            
            # Shipment line cost = quantity × unit LC ratio
            shipment_line_cost = quantity * unit_lc_ratio
            total_shipment_cost += shipment_line_cost
            
            shipment_lines.append({
                'product': line.product_id,
                'product_name': line.product_id.name,
                'quantity': quantity,
                'unit_lc_ratio': unit_lc_ratio,
                'shipment_line_cost': shipment_line_cost,
            })
        
        # Calculate shipment percentages and unit shipment ratios
        for line in shipment_lines:
            shipment_percent = (line['shipment_line_cost'] / total_shipment_cost * 100) if total_shipment_cost > 0 else 0
            unit_shipment_ratio = shipment_percent / line['quantity'] if line['quantity'] > 0 else 0
            
            line.update({
                'shipment_percent': shipment_percent,
                'unit_shipment_ratio': unit_shipment_ratio,
            })
        
        return {
            'lines': shipment_lines,
            'total_shipment_cost': total_shipment_cost,
        }
    
    def _get_total_lc_cost(self, lc):
        """
        Calculate total LC cost including PO value and all LC costs
        """
        # PO total value
        po_total = sum(line.product_qty * line.price_unit for line in lc.product_line_ids)
        
        # LC costs (vendor bills and adjustments)
        lc_costs = sum(line.amount for line in lc.cost_line_ids if line.vendor_bill_id and line.vendor_bill_id.state == 'posted' and not line.cost_type_id.is_tax)
        
        return po_total + lc_costs
    
    def _get_total_shipment_cost(self, shipment):
        """
        Calculate total shipment cost
        """
        return sum(line.amount for line in shipment.cost_line_ids if line.vendor_bill_id and line.vendor_bill_id.state == 'posted' and not line.cost_type_id.is_tax)
    
    def _calculate_grand_summary(self, lc, shipment):
        """
        Calculate grand summary of all costs for the current shipment
        """
        total_lc_cost = self._get_total_lc_cost(lc)
        total_shipment_cost = self._get_total_shipment_cost(shipment)
        
        return {
            'lc_cost': total_lc_cost,
            'current_shipment_cost': total_shipment_cost,
            'all_shipments_cost': total_shipment_cost,
            'total_cost': total_lc_cost + total_shipment_cost,
        }
