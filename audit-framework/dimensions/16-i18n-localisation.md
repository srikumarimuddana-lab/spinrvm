# Dimension 16 — Internationalisation & Localisation (i18n/l10n)

**Question:** Does the app support French? Are all strings translatable? Will dates, currencies, and phone numbers display correctly in every locale?

French support is legally required under Canada's Official Languages Act for federally regulated businesses. Saskatchewan has a Francophone community. This is not optional.

---

## Checklist

### Translation Coverage
- [ ] Every user-visible string goes through the i18n system (e.g. `t('key')` or `i18n.t('key')`)
- [ ] No hardcoded English strings in JSX or component files
- [ ] Translation files exist for: `en-CA` (English Canada) and `fr-CA` (French Canada)
- [ ] All translation keys present in both locale files — no missing keys in French
- [ ] Fallback locale defined (en-CA) if a key is missing in the target locale
- [ ] Legal / Terms / Privacy Policy text available in French

### String Formatting
- [ ] Dates formatted using `Intl.DateTimeFormat` or `date-fns` with locale — not hardcoded `MM/DD/YYYY`
- [ ] Times use 12h for en-CA, 24h for fr-CA
- [ ] Currency formatted using `Intl.NumberFormat('en-CA', {style: 'currency', currency: 'CAD'})`
- [ ] Distance: kilometres (not miles) — Canadian standard
- [ ] Phone number formatted for Canadian display: `(306) 555-1234` not `+13065551234`

### Layout & Typography for French
- [ ] French strings are ~20–30% longer than English — layout doesn't clip French text
- [ ] Buttons with fixed width accommodate longer French labels
- [ ] No truncation of important French labels (`numberOfLines` set carefully)

### Dynamic Content
- [ ] Backend API responses: consider language-specific content (error messages, notifications)
- [ ] FCM push notification bodies localisable — not hardcoded English in backend
- [ ] FAQ / Knowledge base content available in French

### Locale Detection
- [ ] App detects device locale on first launch
- [ ] Language preference saved and respected across sessions
- [ ] Language switchable in app Settings without reinstalling

### App Store / Play Store
- [ ] App Store listing in English and French (`language: 'en-CA'` in `eas.json` submit config — add `fr-CA`)
- [ ] App name, description, keywords in both languages
- [ ] Screenshots in both languages (or language-neutral)

---

## Severity Guide

| Finding | Severity |
|---|---|
| No French support at all — Official Languages Act violation | HIGH |
| Hardcoded English strings — not translatable | HIGH |
| Date format MM/DD/YYYY hardcoded — wrong for Canada | MEDIUM |
| French strings clipped by fixed-width buttons | MEDIUM |
| Missing French translation keys — app crashes on French locale | HIGH |
| Currency not formatted with CAD symbol | MEDIUM |
| App Store listing English only | MEDIUM |
| Language not switchable in settings | LOW |
| Distances shown in miles | LOW |
