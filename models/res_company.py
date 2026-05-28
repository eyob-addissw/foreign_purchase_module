from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    foreign_purchase_git_account_id = fields.Many2one(
        "account.account",
        string="Foreign Purchase GIT Account",
        help=(
            "Default interim (Goods-in-Transit) account used on vendor bills created for "
            "foreign purchase flows (PO bills, LC costs, shipment costs) unless overridden "
            "by the specific cost type."
        ),
        check_company=True,
    )

