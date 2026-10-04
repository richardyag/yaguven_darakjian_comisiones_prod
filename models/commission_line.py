from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class YaguvenCommissionLine(models.Model):
    """One commission line per sale, for the salesperson, within the month.

    One line per document issued by the salesperson inside the ``target`` period. A
    line's source is EITHER a posted invoice/credit note (``move_id``) OR a POS order
    that was never invoiced (``pos_order_id``) — never both, never neither. Which
    sources a target actually looks for is decided by ``config_id.source_mode``: a POS
    order only ever gets its own line while it stays uninvoiced — the moment it is
    invoiced, that line is dropped and replaced by the invoice-sourced one on the next
    recompute, so nothing is ever counted twice.

    The ``volume`` and ``cost_total`` snapshots are frozen when the line is created by
    the recompute engine in ``yaguven.commission.target``, while ``pct_applied`` and the
    collection state are refreshed on every recompute — and the commission amounts
    follow from those.
    """

    _name = 'yaguven.commission.line'
    _description = 'Darakjian — Commission Line'
    _order = 'target_id, invoice_date, id'

    target_id = fields.Many2one(
        'yaguven.commission.target',
        required=True,
        index=True,
        ondelete='cascade',
    )
    salesperson_id = fields.Many2one(
        related='target_id.salesperson_id',
        store=True,
        index=True,
    )
    company_id = fields.Many2one(
        related='target_id.company_id',
        store=True,
        index=True,
    )
    currency_id = fields.Many2one(
        related='company_id.currency_id',
        store=True,
    )

    # --- Source document (native datasource, read only): exactly one of the two ---
    move_id = fields.Many2one(
        'account.move',
        string='Invoice',
        index=True,
        ondelete='cascade',
    )
    pos_order_id = fields.Many2one(
        'pos.order',
        string='POS Order',
        index=True,
        ondelete='cascade',
        help='Set only while the order has not been invoiced. Once it is, this line '
             'is replaced by an invoice-sourced one on the next recompute.',
    )
    source_type = fields.Selection(
        [('invoice', 'Invoice'), ('pos_order', 'POS Order (not invoiced)')],
        compute='_compute_source',
        store=True,
    )
    move_name = fields.Char(string='Document', compute='_compute_source', store=True)
    invoice_date = fields.Date(string='Date', compute='_compute_source', store=True)
    move_type = fields.Selection(related='move_id.move_type')

    # --- Snapshots frozen as of the invoice date ---
    volume = fields.Monetary(
        currency_field='currency_id',
        help='Attributed net billed, signed: credit notes subtract.',
    )
    cost_total = fields.Monetary(
        currency_field='currency_id',
        help='Total cost of the product lines, frozen as of the invoice date.',
    )
    margin = fields.Monetary(
        currency_field='currency_id',
        compute='_compute_margin',
        store=True,
        help='Margin = net billed − cost. The commission base.',
    )

    # --- Tier and commission ---
    pct_applied = fields.Float(
        string='Rate Applied',
        digits=(5, 2),
        help='Rate of the tier the month volume reached (cliff, not marginal).',
    )
    commission_amount = fields.Monetary(
        string='Commission Earned',
        currency_field='currency_id',
        compute='_compute_commission_amount',
        store=True,
    )

    # --- Cobro (criterio percibido) ---
    is_collected = fields.Boolean(
        string='Collected',
        help='The source invoice has been collected (payment_state paid/in_payment), '
             'or the source is a POS order (paid in full at the register).',
    )
    commission_payable = fields.Monetary(
        string='Commission Payable',
        currency_field='currency_id',
        compute='_compute_commission_payable',
        store=True,
        help='Commission that can already be paid out: earned, but only once the invoice is collected.',
    )

    _sql_constraints = [
        (
            'target_move_uniq',
            'unique(target_id, move_id)',
            'A commission line already exists for this invoice in this period.',
        ),
        (
            'target_pos_order_uniq',
            'unique(target_id, pos_order_id)',
            'A commission line already exists for this POS order in this period.',
        ),
    ]

    @api.constrains('move_id', 'pos_order_id')
    def _check_single_source(self):
        for rec in self:
            if bool(rec.move_id) == bool(rec.pos_order_id):
                raise ValidationError(_(
                    'A commission line must have exactly one source: an invoice or a POS order.'
                ))

    @api.depends('move_id.name', 'move_id.invoice_date', 'pos_order_id.name', 'pos_order_id.date_order')
    def _compute_source(self):
        for rec in self:
            if rec.move_id:
                rec.source_type = 'invoice'
                rec.move_name = rec.move_id.name
                rec.invoice_date = rec.move_id.invoice_date
            else:
                rec.source_type = 'pos_order'
                rec.move_name = rec.pos_order_id.name
                rec.invoice_date = rec.pos_order_id.date_order.date() if rec.pos_order_id.date_order else False

    @api.depends('volume', 'cost_total')
    def _compute_margin(self):
        for rec in self:
            rec.margin = rec.volume - rec.cost_total

    @api.depends('margin', 'pct_applied')
    def _compute_commission_amount(self):
        for rec in self:
            rec.commission_amount = rec.margin * rec.pct_applied / 100.0

    @api.depends('commission_amount', 'is_collected')
    def _compute_commission_payable(self):
        for rec in self:
            rec.commission_payable = rec.commission_amount if rec.is_collected else 0.0
