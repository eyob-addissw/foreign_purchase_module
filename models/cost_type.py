from odoo import models, fields

class ForeignLCCADCostType(models.Model):
    _name = 'foreign.lc_cad.cost_type'
    _description = 'LC/CAD Cost Type'
    _order = 'name'

    name = fields.Char(string='Cost Type', required=True, translate=True)
    description = fields.Text(string='Description')
    active = fields.Boolean(string='Active', default=True)
    is_tax = fields.Boolean(string='Is Tax', default=False, help="Enable if this cost type is a tax that should not be included in inventory valuation.")
    is_adjustment = fields.Boolean(string='Is Adjustment', default=False, help="Enable if this cost type creates manual journal entries instead of vendor bills.")
    git_account_id = fields.Many2one('account.account', string='GIT Account', help="The GIT interim account to be debited for adjustment entries.")
    tax_account_id = fields.Many2one('account.account', string='Tax Account', help="The account to be debited when creating bills for this cost type.")
    adjustment_account_id = fields.Many2one('account.account', string='Adjustment Account', help="The account to be credited when creating adjustment entries for this cost type.")

    _sql_constraints = [
        ('name_unique', 'unique(name)', 'Cost type name must be unique!')
    ]
