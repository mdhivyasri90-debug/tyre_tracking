from odoo import api, models, fields, _
from odoo.exceptions import UserError


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    vehicle_number = fields.Char(string='Vehicle Number')

    state = fields.Selection(
        selection_add=[('to_approve', 'Waiting Approval'), ('sale',)],
        ondelete={'to_approve': 'set default'},
    )

    # ---------- Vehicle Number in chatter ----------
    @api.model_create_multi
    def create(self, vals_list):
        orders = super().create(vals_list)
        orders._log_vehicle_number({order.id: False for order in orders})
        return orders

    def write(self, vals):
        if 'vehicle_number' not in vals:
            return super().write(vals)
        old_values = {order.id: order.vehicle_number for order in self}
        res = super().write(vals)
        self._log_vehicle_number(old_values)
        return res

    def _log_vehicle_number(self, old_values):
        if self.env.context.get('tracking_disable') or self.env.context.get('mail_notrack'):
            return
        tracked = self.fields_get(['vehicle_number'])
        for order in self:
            old = old_values.get(order.id)
            if old != order.vehicle_number:
                _changes, tracking_values = order._mail_track(
                    tracked, {'vehicle_number': old})
                if tracking_values:
                    order._message_log(tracking_value_ids=tracking_values)

    # ---------- Approval ----------
    def action_confirm(self):
        if self.env.context.get('skip_approval'):
            return super().action_confirm()
        for order in self:
            if order.state not in ('draft', 'sent'):
                raise UserError(_('Only draft or sent quotations can be sent for approval.'))
            order.state = 'to_approve'
            order.message_post(body=_('Quotation sent for approval.'))
        return True

    def action_approve(self):
        for order in self:
            if order.state != 'to_approve':
                raise UserError(_('This order is not waiting for approval.'))
        self.write({'state': 'draft'})
        self.message_post(body=_('Approved by %s.', self.env.user.name))
        return self.with_context(skip_approval=True).action_confirm()

    def action_reject(self):
        self.write({'state': 'draft'})
        self.message_post(body=_('Rejected by %s.', self.env.user.name))
        return True
