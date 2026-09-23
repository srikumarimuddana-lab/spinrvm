interface CancellationPayment {
  refund_status?: string | null;
  refund_amount?: string | number | null;
  auth_status?: string | null;
  scheduled_notice_fee_status?: string | null;
  scheduled_notice_fee_amount?: string | number | null;
}
const amount = (value: string | number | null | undefined) => {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed >= 0 ? `$${parsed.toFixed(2)}` : null;
};

/** Only provider-confirmed outcomes may say money was refunded/collected. */
export function cancellationPaymentMessages(ride: CancellationPayment): string[] {
  const messages: string[] = [];
  if (ride.refund_status === 'succeeded') {
    const refunded = amount(ride.refund_amount);
    messages.push(refunded ? `Refund confirmed: ${refunded}` : 'Refund confirmed');
  } else if (['requested', 'pending'].includes(ride.refund_status ?? '')) {
    messages.push('Refund pending — not yet confirmed');
  } else if (['failed', 'requires_action', 'canceled'].includes(ride.refund_status ?? '')) {
    messages.push('Refund needs attention — contact support');
  } else if (ride.auth_status === 'released') {
    messages.push('Card authorization released. Your bank may take time to remove the hold.');
  }
  const feeStatus = ride.scheduled_notice_fee_status;
  if (feeStatus === 'paid') {
    const paid = amount(ride.scheduled_notice_fee_amount);
    messages.push(paid ? `Scheduled cancellation fee paid: ${paid}` : 'Scheduled cancellation fee paid');
  } else if (feeStatus === 'failed' || feeStatus === 'requires_action') {
    messages.push('Scheduled cancellation fee: payment unsuccessful');
  } else if (feeStatus === 'pending' || feeStatus === 'requested') {
    messages.push('Scheduled cancellation fee: payment pending');
  }
  return messages;
}
