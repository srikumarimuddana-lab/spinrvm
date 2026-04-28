/**
 * Formats a monetary amount as a Canadian dollar string.
 * Uses Intl.NumberFormat with fr-CA locale so amounts like 1000 render as "1 000,00 $"
 * when shown to French-speaking Saskatchewan riders/drivers.
 *
 * Pass locale explicitly when the caller has access to the user's preferred locale.
 * Falls back to en-CA, which renders "CA$1,000.00".
 */
export function formatCurrency(amount: number, locale: string = 'en-CA'): string {
  return new Intl.NumberFormat(locale, {
    style: 'currency',
    currency: 'CAD',
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(amount);
}

/**
 * Lightweight helper that always returns a plain "$X.XX" string regardless of locale.
 * Use this when the context is a single-currency app and locale is not yet wired.
 */
export function formatFare(amount: number): string {
  return `$${amount.toFixed(2)}`;
}
