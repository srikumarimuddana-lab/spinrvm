import { walletTopUpSchema, isWalletTopUpAmountValid } from '../walletTopUpSchema';

describe('walletTopUpSchema / isWalletTopUpAmountValid', () => {
  // Accept cases — matches the pre-existing inline check:
  // effectiveAmount >= 1 && effectiveAmount <= 500
  it.each([1, 1.5, 10, 25, 50, 100, 250, 499.99, 500])(
    'accepts %s (within $1–$500 inclusive)',
    (amount) => {
      expect(isWalletTopUpAmountValid(amount)).toBe(true);
      expect(walletTopUpSchema.safeParse(amount).success).toBe(true);
    },
  );

  // Reject cases
  it.each([0, 0.99, -1, -100, 500.01, 501, 1000, NaN])(
    'rejects %s (outside $1–$500 inclusive, or NaN)',
    (amount) => {
      expect(isWalletTopUpAmountValid(amount)).toBe(false);
      expect(walletTopUpSchema.safeParse(amount).success).toBe(false);
    },
  );
});
