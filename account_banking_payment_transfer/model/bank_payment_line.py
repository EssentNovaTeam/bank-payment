# -*- coding: utf-8 -*-
# © 2009 EduSense BV (<http://www.edusense.nl>)
# © 2011-2013 Therp BV (<http://therp.nl>)
# © 2015 Akretion (www.akretion.com)
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html).

from openerp import models, fields, api, _
from openerp.exceptions import Warning as UserError


class BankPaymentLine(models.Model):
    _inherit = 'bank.payment.line'

    transit_move_line_id = fields.Many2one(
        'account.move.line', string='Transfer move line', readonly=True,
        help="Move line through which the payment/debit order "
        "pays the invoice")
    transfer_move_line_id = fields.Many2one(
        'account.move.line', compute='_get_transfer_move_line',
        string='Transfer move line counterpart',
        help="Counterpart move line on the transfer account")

    @api.multi
    def move_line_transfer_account_hashcode(self):
        """
        This method is inherited in the module
        account_banking_sepa_direct_debit
        """
        self.ensure_one()
        if self.order_id.mode.transfer_move_option == 'date':
            hashcode = self.date
        else:
            hashcode = unicode(self.id)
        return hashcode

    @api.multi
    def _get_transfer_move_line(self):
        for bank_line in self:
            if bank_line.transit_move_line_id:
                order_type = bank_line.order_id.payment_order_type
                trf_lines = bank_line.transit_move_line_id.move_id.line_id
                for move_line in trf_lines:
                    if order_type == 'debit' and move_line.debit > 0:
                        bank_line.transfer_move_line_id = move_line
                    elif order_type == 'payment' and move_line.credit > 0:
                        bank_line.transfer_move_line_id = move_line

    @api.multi
    def debit_reconcile(self):
        """
        Reconcile a debit order's payment line with the the move line
        that it is based on. Called from payment_order.action_sent().
        As the amount is derived directly from the counterpart move line,
        we do not expect a write off. Take partial reconciliations into
        account though.

        This method was modified for Nova to drastically improve the
        performance of the reconciliation by disabling ORM triggers, audit
        logging and additional access and sanity checks. This is of course
        dangerous and needs to be validated and/or reconsidered (TODO).

        For one, this probably means that supplier invoices are no longer going
        into paid status if it is paid in a SEPA order (TODO).

        It might be an option to collect the move lines and fire an ORM
        'modified' trigger on move lines' reconciliation fields for a chunk
        of move lines in bulk. This is likely still much more performant
        than firing them for each individually (TODO). For this purpose, return
        a list of the reconciled move line ids.

        :param payment_line_id: the single id of the canceled payment line
        """
        self.ensure_one()
        transit_move_line = self.transit_move_line_id

        assert not transit_move_line.reconcile_id,\
            'Transit move should not be reconciled'
        assert not transit_move_line.reconcile_partial_id,\
            'Transit move should not be partially reconciled'
        lines_to_rec = transit_move_line
        for payment_line in self.payment_line_ids:

            if not payment_line.move_line_id:
                raise UserError(_(
                    "Can not reconcile: no move line for "
                    "payment line %s of partner '%s'.") % (
                        payment_line.name,
                        payment_line.partner_id.name))
            if payment_line.move_line_id.reconcile_id:
                raise UserError(_(
                    "Move line '%s' of partner '%s' has already "
                    "been reconciled") % (
                        payment_line.move_line_id.name,
                        payment_line.partner_id.name))
            if (
                    payment_line.move_line_id.account_id !=
                    transit_move_line.account_id):
                raise UserError(_(
                    "For partner '%s', the account of the account "
                    "move line to pay (%s) is different from the "
                    "account of of the transit move line (%s).") % (
                        payment_line.move_line_id.partner_id.name,
                        payment_line.move_line_id.account_id.code,
                        transit_move_line.account_id.code))

            lines_to_rec += payment_line.move_line_id

        if not any(line.reconcile_partial_id for line in lines_to_rec) and (
                self.env.user.company_id.currency_id.is_zero(
                    sum(l.debit - l.credit for l in lines_to_rec))):
            # This is the simple case of a new, full reconciliation
            self.env.cr.execute(
                """ INSERT INTO account_move_reconcile
                (create_date, create_uid, name, type) VALUES(
                NOW(), %(uid)s, %(name)s, 'auto') RETURNING id""", {
                    'name': self.name, 'uid': self.env.user.id,
                })
            rec_id = self.env.cr.fetchone()[0]
            self.env.cr.execute(
                """UPDATE account_move_line SET reconcile_id = %s
                WHERE id in %s""", (rec_id, tuple(lines_to_rec.ids)))

        else:  # Fallback on slow ORM reconciliation
            lines_to_rec.with_context(
                reconcile_name=self.name, no_balance_trigger=True
            ).reconcile_partial(type='auto')

        return lines_to_rec.ids
