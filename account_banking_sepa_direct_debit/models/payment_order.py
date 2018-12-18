# coding: utf-8
# © 2016 Opener B.V. <https://opener.amsterdam>
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html).
from openerp import api, models


class PaymentOrder(models.Model):
    _inherit = 'payment.order'

    @api.multi
    def action_rejected(self):
        res = super(PaymentOrder, self).action_rejected()
        self.env['account.banking.mandate'].browse(
            self.with_context(prefetch_fields=False).mapped(
                'line_ids.mandate_id').ids).amendment_reset()
        return res

    @api.multi
    def action_sent(self):
        """ Lazy compatibility with account_banking_payment_transfer """
        res = super(PaymentOrder, self).action_sent()
        self.env['account.banking.mandate'].browse(
            self.with_context(prefetch_fields=False).mapped(
                'line_ids.mandate_id').ids).amendment_sent()
        return res

    @api.multi
    def action_done(self):
        res = super(PaymentOrder, self).action_done()
        if not hasattr(self, 'action_sent'):
            # no account_banking_payment_transfer
            self.env['account.banking.mandate'].browse(
                self.with_context(prefetch_fields=False).mapped(
                    'line_ids.mandate_id').ids).amendment_sent()
        return res
