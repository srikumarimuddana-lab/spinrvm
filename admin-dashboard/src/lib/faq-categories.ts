// Canonical FAQ category taxonomy — the single list both FAQ admin editors
// (the dedicated /dashboard/faqs page and the condensed Support-tab editor)
// must offer, so the two screens can't drift into different category sets.
//
// Derived from what the seeded FAQ content actually uses (see
// backend/migrations/210/212/230/322_*.sql) rather than picked from scratch —
// the previous dropdown (general/payments/safety/account/rides/drivers/other)
// didn't match most of the categories the seed data actually assigned
// (onboarding/documents/troubleshooting/wallet/pricing/accessibility/
// promotions), so editing a seeded FAQ through the dedicated page silently
// couldn't select its own category.
//
// "general" is kept as the explicit catch-all/default — it's what the
// backend (`FaqCreateRequest.category` in backend/routes/admin/faqs.py) and
// both editors' empty-form state already default to. "drivers" and "other"
// were dropped: "drivers" described an audience, not a topic (that's what
// the separate `audience` field is for), and no FAQ has ever used either
// value.
export const FAQ_CATEGORIES = [
    "onboarding",
    "documents",
    "troubleshooting",
    "payments",
    "wallet",
    "pricing",
    "promotions",
    "rides",
    "account",
    "safety",
    "accessibility",
    "general",
] as const;

export type FaqCategory = (typeof FAQ_CATEGORIES)[number];
