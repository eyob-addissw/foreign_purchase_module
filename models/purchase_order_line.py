from odoo import models


class PurchaseOrderLine(models.Model):
    _inherit = "purchase.order.line"

    def _prepare_account_move_line(self, move=False):
        """Force a dedicated GIT account for foreign PO bills.

        purchase.action_create_invoice() uses this hook to build vendor bill lines.
        We only set account_id when:
        - The PO is marked as foreign in this module (po_class == 'foreign')
        - A company-level foreign_purchase_git_account_id is configured
        - The line is an actual product line (not a section/note)
        """
        res = super()._prepare_account_move_line(move=move)
        self.ensure_one()

        if res.get("display_type") != "product":
            return res

        order = self.order_id
        if getattr(order, "po_class", False) != "foreign":
            return res

        account = order.company_id.foreign_purchase_git_account_id
        if account:
            res["account_id"] = account.id
        return res

