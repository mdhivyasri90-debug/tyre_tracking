# -*- coding: utf-8 -*-
"""
Test for the sale.order approval extension.
"""
from odoo import Command
from odoo.exceptions import UserError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged('post_install', '-at_install', 'sale_approval')
class TestSaleOrderApproval(TransactionCase):

    MSG_SUBMITTED = 'Quotation sent for approval.'
    MSG_APPROVED = 'Approved by'
    MSG_REJECTED = 'Rejected by'
    ERR_SUBMIT_STATE = 'Only draft or sent quotations can be sent for approval.'
    ERR_NOT_WAITING = 'This order is not waiting for approval.'

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.SaleOrder = cls.env['sale.order']
        cls.partner = cls.env['res.partner'].create({'name': 'Approval Test Customer'})
        cls.product = cls.env['product.product'].create({
            'name': 'Approval Test Service',
            'type': 'service',
            'list_price': 100.0,
        })

    def _create_order(self, state='draft', **extra_vals):
        vals = {
            'partner_id': self.partner.id,
            'order_line': [Command.create({
                'product_id': self.product.id,
                'price_unit': 100.0,
            })],
        }
        vals.update(extra_vals)
        order = self.SaleOrder.create(vals)
        if state != 'draft':
            order.write({'state': state})
        return order

    def _messages_with(self, order, text):
        return order.message_ids.filtered(lambda m: text in (m.body or ''))

    def _tracked_field_names(self, order):
        values = order.message_ids.tracking_value_ids
        attr = 'field_id' if 'field_id' in values._fields else 'field'
        return {getattr(v, attr).name for v in values}

    # A. Field / model definition
    def test_vehicle_number_defaults_empty(self):
        """TC-01"""
        order = self._create_order()
        self.assertFalse(order.vehicle_number)

    def test_vehicle_number_set_on_create_and_write(self):
        """TC-02"""
        order = self._create_order(vehicle_number='TN30AB1234')
        self.assertEqual(order.vehicle_number, 'TN30AB1234')
        order.write({'vehicle_number': 'TN30CD5678'})
        self.assertEqual(order.vehicle_number, 'TN30CD5678')

    def test_vehicle_number_is_tracked(self):
        """TC-03"""
        order = self._create_order().with_context(
            tracking_disable=False, mail_notrack=False)
        order.write({'vehicle_number': 'TN30AB1234'})
        # Odoo 16 creates tracking values only at commit time; force it here.
        self.env.flush_all()
        self.env.cr.precommit.run()
        order.invalidate_recordset(['message_ids'])
        self.assertIn('vehicle_number', self._tracked_field_names(order))

    def test_state_selection_contains_waiting_approval(self):
        """TC-04"""
        selection = self.SaleOrder.fields_get(['state'])['state']['selection']
        keys = [key for key, _label in selection]
        labels = dict(selection)
        self.assertIn('to_approve', keys)
        self.assertEqual(labels['to_approve'], 'Waiting Approval')
        for original in ('draft', 'sent', 'sale', 'cancel'):
            self.assertIn(original, keys)
        self.assertLess(keys.index('to_approve'), keys.index('sale'))

    def test_state_ondelete_set_default(self):
        """TC-05"""
        ondelete = self.SaleOrder._fields['state'].ondelete or {}
        self.assertEqual(ondelete.get('to_approve'), 'set default')

    # B. action_confirm
    def test_confirm_draft_goes_to_waiting_approval(self):
        """TC-06"""
        order = self._create_order('draft')
        result = order.action_confirm()
        self.assertTrue(result)
        self.assertEqual(order.state, 'to_approve')
        self.assertTrue(self._messages_with(order, self.MSG_SUBMITTED))

    def test_confirm_sent_goes_to_waiting_approval(self):
        """TC-07"""
        order = self._create_order('sent')
        order.action_confirm()
        self.assertEqual(order.state, 'to_approve')
        self.assertTrue(self._messages_with(order, self.MSG_SUBMITTED))

    def test_confirm_rejected_for_invalid_states(self):
        """TC-08"""
        for state in ('to_approve', 'sale', 'cancel'):
            with self.subTest(state=state):
                order = self._create_order(state)
                with self.assertRaises(UserError) as cm:
                    order.action_confirm()
                self.assertIn(self.ERR_SUBMIT_STATE, str(cm.exception))
                order.invalidate_recordset()
                self.assertEqual(order.state, state)

    def test_confirm_multiple_orders(self):
        """TC-09"""
        orders = self._create_order('draft') | self._create_order('sent')
        orders.action_confirm()
        self.assertEqual(set(orders.mapped('state')), {'to_approve'})
        for order in orders:
            self.assertTrue(self._messages_with(order, self.MSG_SUBMITTED))

    def test_confirm_mixed_batch_raises_and_rolls_back(self):
        """TC-10"""
        draft = self._create_order('draft')
        confirmed = self._create_order('sale')
        with self.assertRaises(UserError):
            (draft | confirmed).action_confirm()
        (draft | confirmed).invalidate_recordset()
        self.assertEqual(draft.state, 'draft')
        self.assertEqual(confirmed.state, 'sale')

    def test_confirm_with_skip_approval_confirms_directly(self):
        """TC-11"""
        order = self._create_order('draft')
        order.with_context(skip_approval=True).action_confirm()
        self.assertEqual(order.state, 'sale')
        self.assertFalse(self._messages_with(order, self.MSG_SUBMITTED))

    def test_confirm_with_skip_approval_false_still_needs_approval(self):
        """TC-12"""
        order = self._create_order('draft')
        order.with_context(skip_approval=False).action_confirm()
        self.assertEqual(order.state, 'to_approve')

    def test_confirm_skip_approval_on_cancelled_order_blocked_by_core(self):
        """TC-13"""
        order = self._create_order('cancel')
        with self.assertRaises(UserError):
            order.with_context(skip_approval=True).action_confirm()
        order.invalidate_recordset()
        self.assertEqual(order.state, 'cancel')

    # C. action_approve
    def test_approve_confirms_the_order(self):
        """TC-14"""
        order = self._create_order('draft')
        order.action_confirm()
        self.assertEqual(order.state, 'to_approve')
        result = order.action_approve()
        self.assertTrue(result)
        self.assertEqual(order.state, 'sale')
        approved = self._messages_with(order, self.MSG_APPROVED)
        self.assertTrue(approved)
        self.assertIn(self.env.user.name, approved[0].body)

    def test_approve_rejected_for_invalid_states(self):
        """TC-15"""
        for state in ('draft', 'sent', 'sale', 'cancel'):
            with self.subTest(state=state):
                order = self._create_order(state)
                with self.assertRaises(UserError) as cm:
                    order.action_approve()
                self.assertIn(self.ERR_NOT_WAITING, str(cm.exception))
                order.invalidate_recordset()
                self.assertEqual(order.state, state)
                self.assertFalse(self._messages_with(order, self.MSG_APPROVED))

    def test_approve_mixed_batch_is_atomic(self):
        """TC-16"""
        waiting = self._create_order('to_approve')
        draft = self._create_order('draft')
        with self.assertRaises(UserError):
            (waiting | draft).action_approve()
        (waiting | draft).invalidate_recordset()
        self.assertEqual(waiting.state, 'to_approve')
        self.assertEqual(draft.state, 'draft')

    def test_known_issue_approve_multiple_orders_fails(self):
        """TC-17 (KNOWN ISSUE)"""
        orders = self._create_order('to_approve') | self._create_order('to_approve')
        with self.assertRaises(ValueError):
            orders.action_approve()

    def test_known_issue_approve_empty_recordset_fails(self):
        """TC-18 (KNOWN ISSUE)"""
        with self.assertRaises(ValueError):
            self.SaleOrder.browse().action_approve()

    # D. action_reject
    def test_reject_returns_order_to_draft(self):
        """TC-19"""
        order = self._create_order('to_approve')
        result = order.action_reject()
        self.assertTrue(result)
        self.assertEqual(order.state, 'draft')
        rejected = self._messages_with(order, self.MSG_REJECTED)
        self.assertTrue(rejected)
        self.assertIn(self.env.user.name, rejected[0].body)

    def test_known_issue_reject_multiple_orders_fails(self):
        """TC-20 (KNOWN ISSUE)"""
        orders = self._create_order('to_approve') | self._create_order('to_approve')
        with self.assertRaises(ValueError):
            orders.action_reject()

    def test_known_issue_reject_has_no_state_guard(self):
        """TC-21 (KNOWN ISSUE)"""
        for state in ('draft', 'sent', 'sale', 'cancel'):
            with self.subTest(state=state):
                order = self._create_order(state)
                order.action_reject()
                self.assertEqual(order.state, 'draft')

    # E. End-to-end
    def test_full_cycle_submit_reject_resubmit_approve(self):
        """TC-22"""
        order = self._create_order('draft', vehicle_number='TN30AB1234')
        order.action_confirm()
        self.assertEqual(order.state, 'to_approve')
        order.action_reject()
        self.assertEqual(order.state, 'draft')
        order.action_confirm()
        self.assertEqual(order.state, 'to_approve')
        order.action_approve()
        self.assertEqual(order.state, 'sale')
        self.assertEqual(order.vehicle_number, 'TN30AB1234')
        self.assertEqual(len(self._messages_with(order, self.MSG_SUBMITTED)), 2)
        self.assertEqual(len(self._messages_with(order, self.MSG_REJECTED)), 1)
        self.assertEqual(len(self._messages_with(order, self.MSG_APPROVED)), 1)

    def test_duplicate_resets_state_but_keeps_vehicle_number(self):
        """TC-23"""
        order = self._create_order('to_approve', vehicle_number='TN30AB1234')
        copy = order.copy()
        self.assertEqual(copy.state, 'draft')
        self.assertEqual(copy.vehicle_number, 'TN30AB1234')
