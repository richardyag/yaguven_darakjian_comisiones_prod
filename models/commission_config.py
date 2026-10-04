from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class YaguvenCommissionConfig(models.Model):
    """Commission tier settings, one per company.

    Holds the fixed rates and the upper-tier cut-off. Gabriel set 3/6/9 with the cut-off
    at 125%. They live as editable settings rather than hard-coded numbers, but Janel
    does not touch them: she only enters the monthly goal in
    ``yaguven.commission.target``.
    """

    _name = 'yaguven.commission.config'
    _description = 'Darakjian — Commission Settings'
    _inherit = ['mail.thread']
    _order = 'company_id, id'

    name = fields.Char(
        required=True,
        default=lambda self: _('Commissions — %s', self.env.company.name),
        tracking=True,
    )
    company_id = fields.Many2one(
        'res.company',
        required=True,
        index=True,
        default=lambda self: self.env.company,
        tracking=True,
    )
    active = fields.Boolean(default=True, tracking=True)

    pct_below = fields.Float(
        string='% Below Goal',
        digits=(5, 2),
        default=3.0,
        required=True,
        help='Rate applied when the month volume falls short of the goal.',
        tracking=True,
    )
    pct_target = fields.Float(
        string='% At Goal',
        digits=(5, 2),
        default=6.0,
        required=True,
        help='Rate applied when the volume reaches the goal but stays below the '
             'upper cut-off.',
        tracking=True,
    )
    pct_super = fields.Float(
        string='% Above Goal',
        digits=(5, 2),
        default=9.0,
        required=True,
        help='Rate applied when the volume reaches or passes the upper '
             'cut-off (goal x threshold).',
        tracking=True,
    )
    super_threshold_pct = fields.Float(
        string='Upper Tier Threshold (%)',
        digits=(5, 2),
        default=125.0,
        required=True,
        help='Percentage of the goal at which the upper rate starts to apply. '
             'E.g. 125 => the upper tier starts at 1.25 x goal.',
        tracking=True,
    )
    source_mode = fields.Selection(
        [
            ('invoiced', 'Invoiced sales only'),
            ('all_sales', 'All sales (invoiced + POS not yet invoiced)'),
        ],
        string='Commission Source',
        default='invoiced',
        required=True,
        tracking=True,
        help="Invoiced sales only: counts posted customer invoices/credit notes, same "
             "as before — a sale with no invoice earns no commission. All sales: also "
             "counts POS orders that were paid but never invoiced, attributed to "
             "whichever salesperson rang them up, so the choice to invoice or not "
             "never changes what a salesperson is owed. A POS order that later gets "
             "invoiced is counted once, as the invoice — never twice.",
    )

    # ------------------------------------------------------------------
    # Constraints
    # ------------------------------------------------------------------
    @api.constrains('pct_below', 'pct_target', 'pct_super')
    def _check_percentages(self):
        for rec in self:
            for value in (rec.pct_below, rec.pct_target, rec.pct_super):
                if value < 0 or value > 100:
                    raise ValidationError(_('Rates must be between 0 and 100.'))

    @api.constrains('super_threshold_pct')
    def _check_threshold(self):
        for rec in self:
            if rec.super_threshold_pct < 100:
                raise ValidationError(_(
                    'The upper tier threshold cannot be lower than 100%% '
                    '(the upper tier starts at the goal or above).'
                ))

    @api.constrains('company_id', 'active')
    def _check_single_active_per_company(self):
        for rec in self:
            if not rec.active:
                continue
            others = self.search_count([
                ('company_id', '=', rec.company_id.id),
                ('active', '=', True),
                ('id', '!=', rec.id),
            ])
            if others:
                raise ValidationError(_(
                    'Active commission settings already exist for company '
                    '"%s". Archive the existing one before creating another.'
                ) % rec.company_id.name)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @api.model
    def _get_for_company(self, company):
        """Return the company's active settings, creating them with defaults if absent."""
        company = company or self.env.company
        config = self.search([
            ('company_id', '=', company.id),
            ('active', '=', True),
        ], limit=1)
        if not config:
            config = self.sudo().create({
                'name': _('Commissions — %s', company.name),
                'company_id': company.id,
            })
        return config

    def _resolve_tier(self, volume, objective):
        """Resolve the tier and the rate from the month volume against the goal.

        A "cliff": the rate applies to the whole total, not marginally by tier.
        Returns (tier, pct), with tier in ('below', 'target', 'super').
        When the goal is not positive (not entered yet), 'below' is assumed.
        """
        self.ensure_one()
        if not objective or objective <= 0:
            return 'below', self.pct_below
        super_amount = objective * self.super_threshold_pct / 100.0
        if volume >= super_amount:
            return 'super', self.pct_super
        if volume >= objective:
            return 'target', self.pct_target
        return 'below', self.pct_below
