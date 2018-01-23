# -*- coding: utf-8 -*-
# © 2009 EduSense BV (<http://www.edusense.nl>)
# © 2011-2013 Therp BV (<http://therp.nl>)
# © 2016 Serv. Tecnol. Avanzados - Pedro M. Baeza
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html).

from collections import OrderedDict
from datetime import datetime
import logging
from openerp import models, fields, api, exceptions, workflow, _
try:
    # This is to avoid the drop of the column total each time you update
    # the module account_payment, because the store attribute is set later
    # and Odoo doesn't defer this removal
    from openerp.addons.account_payment.account_payment import payment_order
    payment_order._columns['total'].nodrop = True
except ImportError:
    pass

_logger = logging.getLogger(__name__)


def chunks(l, n):
    """Yield successive n-sized chunks from l."""
    for i in range(0, len(l), n):
        yield l[i:i + n]


def get_values_clause_bulk(lst):
    """Helper function for bulk inserts"""
    fst_dict = lst and lst[0] or {}

    query = ' (' + ','.join(
        '"%s"' % key for key in
        OrderedDict(fst_dict).keys()) + ') VALUES '

    values_query = '(' + ','.join(
        '%s' for i in range(len(fst_dict))) + ')'

    values = []
    count = 0
    for dct in lst:
        count += 1
        query += values_query
        values += OrderedDict(dct).values()

        if count != len(lst):
            query += ', '

    return (query, values)


class PaymentOrder(models.Model):
    _inherit = 'payment.order'

    payment_order_type = fields.Selection(
        [('payment', 'Payment'), ('debit', 'Direct debit')],
        'Payment order type', required=True, default='payment',
        readonly=True, states={'draft': [('readonly', False)]})
    mode_type = fields.Many2one('payment.mode.type', related='mode.type',
                                string='Payment Type')
    bank_line_ids = fields.One2many(
        'bank.payment.line', 'order_id', string="Bank Payment Lines",
        readonly=True)
    total = fields.Float(compute='_compute_total', store=True)
    bank_line_count = fields.Integer(
        compute='_bank_line_count', string='Number of Bank Lines')

    @api.depends('line_ids', 'line_ids.amount')
    @api.one
    def _compute_total(self):
        self.total = sum(self.mapped('line_ids.amount') or [0.0])

    @api.multi
    @api.depends('bank_line_ids')
    def _bank_line_count(self):
        for order in self:
            order.bank_line_count = len(order.bank_line_ids)

    @api.multi
    def unlink(self):
        for order in self:
            if order.state not in ('draft', 'cancel'):
                raise exceptions.Warning(
                    _("You cannot remove any order that is not in 'draft' or "
                      "'cancel' state."))
        return super(PaymentOrder, self).unlink()

    @api.multi
    def launch_wizard(self):
        """Search for a wizard to launch according to the type.
        If type is manual. just confirm the order.
        Previously (pre-v6) in account_payment/wizard/wizard_pay.py
        """
        context = self.env.context.copy()
        order = self[0]
        # check if a wizard is defined for the first order
        if order.mode.type and order.mode.type.ir_model_id:
            context['active_ids'] = self.ids
            wizard_model = order.mode.type.ir_model_id.model
            wizard_obj = self.env[wizard_model]
            return {
                'name': wizard_obj._description or _('Payment Order Export'),
                'view_type': 'form',
                'view_mode': 'form',
                'res_model': wizard_model,
                'domain': [],
                'context': context,
                'type': 'ir.actions.act_window',
                'target': 'new',
                'nodestroy': True,
            }
        else:
            # should all be manual orders without type or wizard model
            for order in self[1:]:
                if order.mode.type and order.mode.type.ir_model_id:
                    raise exceptions.Warning(
                        _('Error'),
                        _('You can only combine payment orders of the same '
                          'type'))
            # process manual payments
            for order_id in self.ids:
                workflow.trg_validate(self.env.uid, 'payment.order',
                                      order_id, 'done', self.env.cr)
            return {}

    @api.multi
    def action_done(self):
        self.write({
            'date_done': fields.Date.context_today(self),
            'state': 'done',
            })
        return True

    @api.multi
    def action_cancel(self):
        for order in self:
            order.write({'state': 'cancel'})
            order.bank_line_ids.unlink()
        return True

    @api.model
    def _prepare_bank_payment_line(self, paylines):
        return {
            'order_id': paylines[0].order_id.id,
            'payment_line_ids': [(6, 0, paylines.ids)],
            'communication': '-'.join(
                [line.communication for line in paylines]),
            }

    @api.multi
    def action_open(self):
        """
        Called when you click on the 'Confirm' button
        Set the 'date' on payment line depending on the 'date_prefered'
        setting of the payment.order
        Re-generate the bank payment lines
        """
        create_time = datetime.now()

        res = super(PaymentOrder, self).action_open()
        today = fields.Date.context_today(self)
        for order in self:
            date_update_dict = {}
            if order.date_prefered == 'due':
                for payline in order.line_ids:
                    requested_date = payline.ml_maturity_date or today
                    if requested_date not in date_update_dict:
                        date_update_dict[requested_date] = []
                    date_update_dict[requested_date].append(payline.id)
            else:
                if order.date_prefered == 'fixed':
                    requested_date = order.date_scheduled or today
                else:
                    requested_date = today

                date_update_dict[requested_date] = order.line_ids.ids

            for date, payment_line_ids in date_update_dict.items():
                self.env.cr.execute(
                    'UPDATE payment_line SET date = %s WHERE id IN %s',
                    (date, tuple(payment_line_ids)))

            # Delete existing bank payment lines
            order.bank_line_ids.unlink()
            # Create the bank payment lines from the payment lines
            group_paylines = {}  # key = hashcode
            for payline in order.line_ids:
                # Group options
                if order.mode.group_lines:
                    hashcode = payline.payment_line_hashcode()
                else:
                    # Use line ID as hascode, which actually means no grouping
                    hashcode = payline.id
                if hashcode in group_paylines:
                    group_paylines[hashcode]['paylines'] += payline
                    group_paylines[hashcode]['total'] +=\
                        payline.amount_currency
                else:
                    group_paylines[hashcode] = {
                        'paylines': payline,
                        'total': payline.amount_currency,
                    }
            # Create bank payment lines
            all_values = []
            to_update_payment_line_ids = []
            for paydict in group_paylines.values():
                # Block if a bank payment line is <= 0
                if paydict['total'] <= 0:
                    raise exceptions.Warning(_(
                        "The amount for Partner '%s' is negative "
                        "or null (%.2f) !")
                        % (paydict['paylines'][0].partner_id.name,
                           paydict['total']))
                new_values = self._prepare_bank_payment_line(paydict['paylines'])
                new_values = self.env['bank.payment.line'].\
                    _add_missing_default_values(new_values)

                new_values.update({
                    'create_uid': self.env.uid,
                    'write_uid': self.env.uid,
                    'create_date': create_time,
                    'write_date': create_time,
                    'amount_currency':
                        sum([payline.amount_currency
                             for payline in paydict['paylines']])
                })

                if new_values.get('name', '/') == '/':
                    new_values['name'] = self.env['ir.sequence'].next_by_code(
                        'bank.payment.line')

                to_update_payment_line_ids.append(new_values['payment_line_ids'][0][2])
                del new_values['payment_line_ids']

                all_values.append(new_values)

            created_count = 0
            created_ids = []
            for next_values in chunks(all_values, 1000):
                created_count += len(next_values)
                clause, values = get_values_clause_bulk(next_values)
                self.env.cr.execute("INSERT INTO bank_payment_line %s RETURNING id"
                                    % (clause), values)
                _logger.debug("Created %s of %s bank payment lines" %
                              (created_count, len(all_values)))

                created_ids += [id[0] for id in self.env.cr.fetchall()]

            assert len(to_update_payment_line_ids) == len(created_ids), \
                'Something went wrong while creating bank payment lines, ' \
                'this did not result in the expected amount.'

            bank_payment_info = dict(zip(created_ids, to_update_payment_line_ids))

            update_str = ""
            values = []
            for bank_payment_line_id, payment_line_ids in \
                    bank_payment_info.items():
                update_str += 'UPDATE payment_line SET bank_line_id = %s ' \
                              'WHERE id IN %s; '
                values += [bank_payment_line_id, tuple(payment_line_ids)]

            self.env.cr.execute(update_str, values)

        return res
