import { cancellationPaymentMessages as messages } from '../cancellationPaymentMessages';

it.each(['requested', 'pending', 'failed', 'requires_action', 'canceled'])('never labels %s refunds as confirmed', refund_status => {
  const text = messages({ refund_status, refund_amount: '20.00' }).join(' ');
  expect(text).not.toContain('Refund confirmed');
  expect(text).not.toContain('$20.00');
});
it('separately displays a succeeded refund and the actual notice fee', () => {
  expect(messages({ refund_status: 'succeeded', refund_amount: '20.00',
    scheduled_notice_fee_status: 'paid', scheduled_notice_fee_amount: '3.00' })).toEqual([
    'Refund confirmed: $20.00', 'Scheduled cancellation fee paid: $3.00',
  ]);
});
it('never claims a failed scheduled fee was paid', () => {
  expect(messages({ scheduled_notice_fee_status: 'failed', scheduled_notice_fee_amount: '0' }))
    .toEqual(['Scheduled cancellation fee: payment unsuccessful']);
});
it('distinguishes a released authorization from a refund', () => {
  expect(messages({ auth_status: 'released' })[0]).toContain('authorization released');
});
