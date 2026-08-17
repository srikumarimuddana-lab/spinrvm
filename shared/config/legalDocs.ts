// Shared catalogue of legal/policy pages served through the backend's
// per-audience GET /legal-documents?audience=&type= endpoint
// (backend/routes/legal_documents.py). Both rider-app and driver-app's
// legal.tsx and policies.tsx screens read from this single list instead of
// each hardcoding their own — keep this in sync with:
//   - backend/routes/legal_documents.py / routes/admin/legal_documents.py
//     (ALLOWED_TYPES)
//   - admin-dashboard/src/lib/api/content-area.ts (LegalDocType)
//   - docs/legal/legal-text-publication-checklist.md
//
// Every one of these documents is a draft pending Saskatchewan/Canada
// counsel review as of this writing — publishing content for any of them
// is an admin-dashboard action for a human to take once review clears, not
// something this file or the app code decides.

export type LegalAudience = 'rider' | 'driver';

export type LegalDocType =
    | 'tos'
    | 'privacy'
    | 'community-guidelines'
    | 'non-discrimination'
    | 'accessibility'
    | 'cancellation-fees'
    | 'promotions-referral'
    | 'insurance-periods'
    | 'deactivation-appeals'
    | 'background-check-consent';

export const LEGAL_DOC_TITLES: Record<LegalDocType, string> = {
    tos: 'Terms of Service',
    privacy: 'Privacy Policy',
    'community-guidelines': 'Community Guidelines',
    'non-discrimination': 'Non-Discrimination Policy',
    accessibility: 'Accessibility Statement',
    'cancellation-fees': 'Cancellation & No-Show Fees',
    'promotions-referral': 'Promotions & Referral Terms',
    'insurance-periods': 'Insurance Coverage Periods',
    'deactivation-appeals': 'Driver Deactivation & Appeals Policy',
    'background-check-consent': 'Background-Check (CRC/VSC) Consent',
};

// Applies to both riders and drivers.
export const SHARED_LEGAL_DOC_TYPES: LegalDocType[] = [
    'tos',
    'privacy',
    'community-guidelines',
    'non-discrimination',
    'accessibility',
    'cancellation-fees',
    'promotions-referral',
    'insurance-periods',
];

// Drivers are the only ones who can be deactivated, so this policy is
// driver-only — see docs/legal/driver-deactivation-appeals-policy.md.
export const DRIVER_ONLY_LEGAL_DOC_TYPES: LegalDocType[] = ['deactivation-appeals'];

// 'background-check-consent' is deliberately absent from both lists below —
// unlike every other entry, it isn't a plain read-only policy page. It's
// shown through its own dedicated consent screen (driver-app's
// crc-consent.tsx, reached from onboarding, not from the general policies
// hub) because agreeing to it is a required action with its own consent
// record (backend/services/driver_crc_consent.py), not just reading text.
export function legalDocTypesForAudience(audience: LegalAudience): LegalDocType[] {
    return audience === 'driver'
        ? [...SHARED_LEGAL_DOC_TYPES, ...DRIVER_ONLY_LEGAL_DOC_TYPES]
        : SHARED_LEGAL_DOC_TYPES;
}

export function legalDocTitle(docType: string): string {
    return LEGAL_DOC_TITLES[docType as LegalDocType] || 'Legal Document';
}

export function legalDocFallbackText(docType: string): string {
    return `No ${legalDocTitle(docType)} has been added yet.`;
}

export function isValidLegalDocType(value: string): value is LegalDocType {
    return value in LEGAL_DOC_TITLES;
}
