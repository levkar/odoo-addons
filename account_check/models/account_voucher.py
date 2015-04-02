# -*- coding: utf-8 -*-
from openerp import models, fields, _, api
import openerp.addons.decimal_precision as dp
import logging
from openerp.exceptions import Warning
_logger = logging.getLogger(__name__)


class account_voucher(models.Model):

    _inherit = 'account.voucher'

    received_third_check_ids = fields.One2many(
        'account.check', 'voucher_id', 'Third Checks',
        domain=[('type', '=', 'third')],
        context={'default_type': 'third', 'from_voucher': True},
        required=False, readonly=True, copy=False,
        states={'draft': [('readonly', False)]}
        )
    issued_check_ids = fields.One2many(
        'account.check', 'voucher_id', 'Issued Checks',
        domain=[('type', '=', 'issue')],
        context={'default_type': 'issue', 'from_voucher': True}, copy=False,
        required=False, readonly=True, states={'draft': [('readonly', False)]}
        )
    delivered_third_check_ids = fields.One2many(
        'account.check', 'third_handed_voucher_id',
        'Third Checks', domain=[('type', '=', 'third')], copy=False,
        context={'from_voucher': True}, required=False, readonly=True,
        states={'draft': [('readonly', False)]}
        )
    validate_only_checks = fields.Boolean(
        related='journal_id.validate_only_checks',
        string='Validate only Checks', readonly=True,
        )
    check_type = fields.Selection(
        related='journal_id.check_type',
        string='Check Type', readonly=True,
        )
    dummy_journal_id = fields.Many2one(
        related='journal_id', readonly=True,
        string='Dummy Journa',
        help='Field used for new api onchange methods over journal',
        )
    amount_readonly = fields.Float(
        related='amount', string='Total',
        digits_compute=dp.get_precision('Account'), readonly=True,
        )

    @api.onchange('dummy_journal_id')
    def change_dummy_journal_id(self):
        """Unlink checks on journal change"""
        self.delivered_third_check_ids = False
        self.issued_check_ids = False
        self.received_third_check_ids = False

    @api.multi
    def action_cancel_draft(self):
        res = super(account_voucher, self).action_cancel_draft()
        checks = self.env['account.check'].search(
            [('voucher_id', 'in', self.ids)])
        checks.action_cancel_draft()
        return res

    @api.model
    def first_move_line_get(
            self, voucher_id, move_id, company_currency,
            current_currency):
        vals = super(account_voucher, self).first_move_line_get(
            voucher_id, move_id, company_currency, current_currency)
        voucher = self.browse(voucher_id)
        if company_currency != current_currency and voucher.amount:
            debit = vals.get('debit')
            credit = vals.get('credit')
            total = debit - credit
            exchange_rate = total / voucher.amount
            checks = []
            if voucher.check_type == 'third':
                checks = voucher.received_third_check_ids
            elif voucher.check_type == 'issue':
                checks = voucher.issued_check_ids
            for check in checks:
                company_currency_amount = abs(check.amount * exchange_rate)
                check.company_currency_amount = company_currency_amount
        return vals

    @api.multi
    def cancel_voucher(self):
        for voucher in self:
            for check in voucher.received_third_check_ids:
                if check.state not in ['draft', 'holding']:
                    raise Warning(_(
                        'You can not cancel a voucher thas has received third checks in states other than "draft or "holding". First try to change check state.'))
            for check in voucher.issued_check_ids:
                if check.state not in ['draft', 'handed']:
                    raise Warning(_(
                        'You can not cancel a voucher thas has issue checks in states other than "draft or "handed". First try to change check state.'))
            for check in voucher.delivered_third_check_ids:
                if check.state not in ['handed']:
                    raise Warning(_(
                        'You can not cancel a voucher thas has delivered checks in states other than "handed". First try to change check state.'))
        res = super(account_voucher, self).cancel_voucher()
        checks = self.env['account.check'].search([
            '|',
            ('voucher_id', 'in', self.ids),
            ('third_handed_voucher_id', 'in', self.ids)])
        for check in checks:
            check.signal_workflow('cancel')
        return res

    def proforma_voucher(self, cr, uid, ids, context=None):
        res = super(account_voucher, self).proforma_voucher(
            cr, uid, ids, context=None)
        for voucher in self.browse(cr, uid, ids, context=context):
            if voucher.type == 'payment':
                for check in voucher.issued_check_ids:
                    check.signal_workflow('draft_router')
                for check in voucher.delivered_third_check_ids:
                    check.signal_workflow('holding_handed')
            elif voucher.type == 'receipt':
                for check in voucher.received_third_check_ids:
                    check.signal_workflow('draft_router')
        return res

    @api.one
    @api.onchange('amount_readonly')
    def onchange_amount_readonly(self):
        self.amount = self.amount_readonly

    @api.one
    @api.onchange('received_third_check_ids', 'issued_check_ids')
    def onchange_customer_checks(self):
        self.amount_readonly = sum(
            x.amount for x in self.received_third_check_ids)

    @api.one
    @api.onchange('delivered_third_check_ids', 'issued_check_ids')
    def onchange_supplier_checks(self):
        amount = sum(x.amount for x in self.delivered_third_check_ids)
        amount += sum(x.amount for x in self.issued_check_ids)
        self.amount_readonly = amount

    @api.model
    def prepare_move_line(self, voucher_id, amount, move_id, name, company_currency, current_currency, date_due):
        voucher = self.env['account.voucher'].browse(voucher_id)
        exchange_rate = voucher.paid_amount_in_company_currency / voucher.amount
        debit = credit = 0.0
        if voucher.type in ('purchase', 'payment'):
            credit = amount * exchange_rate
        elif voucher.type in ('sale', 'receipt'):
            debit = amount * exchange_rate
        if debit < 0: credit = -debit; debit = 0.0
        if credit < 0: debit = -credit; credit = 0.0
        sign = debit - credit < 0 and -1 or 1
        move_line = {
                'name': name,
                'debit': debit,
                'credit': credit,
                'account_id': voucher.account_id.id,
                'move_id': move_id,
                'journal_id': voucher.journal_id.id,
                'period_id': voucher.period_id.id,
                'partner_id': voucher.partner_id.id,
                'currency_id': company_currency <> current_currency and  current_currency or False,
                'amount_currency': (sign * abs(amount) # amount < 0 for refunds
                    if company_currency != current_currency else 0.0),
                'date': voucher.date,
                'date_maturity': date_due or False,
            }
        return move_line


    def action_move_line_create(self, cr, uid, ids, context=None):
        '''
        Confirm the vouchers given in ids and create the journal entries for each of them
        '''
        if context is None:
            context = {}
        move_pool = self.pool.get('account.move')
        move_line_pool = self.pool.get('account.move.line')
        for voucher in self.browse(cr, uid, ids, context=context):
            local_context = dict(context, force_company=voucher.journal_id.company_id.id)
            if voucher.move_id:
                continue
            company_currency = self._get_company_currency(cr, uid, voucher.id, context)
            current_currency = self._get_current_currency(cr, uid, voucher.id, context)
            # we select the context to use accordingly if it's a multicurrency case or not
            context = self._sel_context(cr, uid, voucher.id, context)
            # But for the operations made by _convert_amount, we always need to give the date in the context
            ctx = context.copy()
            ctx.update({'date': voucher.date})
            # Create the account move record.
            move_id = move_pool.create(cr, uid, self.account_move_get(cr, uid, voucher.id, context=context), context=context)
            # Get the name of the account_move just created
            name = move_pool.browse(cr, uid, move_id, context=context).name

            # Additional move lines for check
            if voucher.check_type:
                if voucher.check_type == 'third':
                    if voucher.type == 'payment':
                        checks = voucher.delivered_third_check_ids
                    else:
                        checks = voucher.received_third_check_ids
                elif voucher.check_type == 'issue':
                    checks = voucher.issued_check_ids
                # Calculate total
                line_total = 0.0
                for check in checks:
                    bank_name = ''
                    if check.bank_id:
                        bank_name = '/' + check.bank_id.name
                    move_line_id =  move_line_pool.create(cr, uid, self.prepare_move_line(cr,uid,voucher.id, check.amount,  move_id, check.name + bank_name, company_currency, current_currency, check.payment_date, local_context), local_context)
                    move_line_brw = move_line_pool.browse(cr, uid, move_line_id, context=context)
                    line_total += move_line_brw.debit - move_line_brw.credit
            else:
                # Create the first line of the voucher
                move_line_id = move_line_pool.create(cr, uid, self.first_move_line_get(cr,uid,voucher.id, move_id, company_currency, current_currency, local_context), local_context)
                move_line_brw = move_line_pool.browse(cr, uid, move_line_id, context=context)
                line_total = move_line_brw.debit - move_line_brw.credit
            rec_list_ids = []
            if voucher.type == 'sale':
                line_total = line_total - self._convert_amount(cr, uid, voucher.tax_amount, voucher.id, context=ctx)
            elif voucher.type == 'purchase':
                line_total = line_total + self._convert_amount(cr, uid, voucher.tax_amount, voucher.id, context=ctx)
            # Create one move line per voucher line where amount is not 0.0
            line_total, rec_list_ids = self.voucher_move_line_create(cr, uid, voucher.id, line_total, move_id, company_currency, current_currency, context)

            # Create the writeoff line if needed
            ml_writeoff = self.writeoff_move_line_get(cr, uid, voucher.id, line_total, move_id, name, company_currency, current_currency, local_context)
            if ml_writeoff:
                move_line_pool.create(cr, uid, ml_writeoff, local_context)

            # We post the voucher.
            self.write(cr, uid, [voucher.id], {
                'move_id': move_id,
                'state': 'posted',
                'number': name,
            })
            if voucher.journal_id.entry_posted:
                move_pool.post(cr, uid, [move_id], context={})
            # We automatically reconcile the account move lines.
            reconcile = False
            for rec_ids in rec_list_ids:
                if len(rec_ids) >= 2:
                    reconcile = move_line_pool.reconcile_partial(cr, uid, rec_ids, writeoff_acc_id=voucher.writeoff_acc_id.id, writeoff_period_id=voucher.period_id.id, writeoff_journal_id=voucher.journal_id.id)
        return True





