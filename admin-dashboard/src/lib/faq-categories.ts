// Canonical FAQ category taxonomy for the single FAQ admin editor
// (admin-dashboard/src/app/dashboard/faqs/page.tsx — reused as-is inside
// the Support & Issues "FAQs" tab, not a second implementation; see that
// file's header comment for the merge history).
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
// the editor's empty-form state already default to. "drivers" and "other"
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
