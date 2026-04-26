# Spinr Regulatory Matrix — Canada (Saskatchewan-first)

Canonical reference for every module audit. Findings **must** tag the applicable
regulation IDs in their `regulations:` field (see each plan's Output Schema).

## How to use
- Tag every finding with one or more short IDs from the ID column below.
- If a dimension names a regulation here and you produce NO finding or PASS
  for it, you haven't audited it — go back.
- New regulations: **append rows here**, do not scatter them across plans.

---

## Federal

| ID | Act / Standard | Authority | Trigger | Key audit check | Expected artifact |
|---|---|---|---|---|---|
| PIPEDA | Personal Information Protection and Electronic Documents Act | OPC | Any personal info processed | DSAR endpoint · ≤30 d response · breach notification ASAF · cross-border disclosure | Privacy policy · PIA · DSAR log |
| CPPA | Consumer Privacy Protection Act (Bill C-27, in force) | OPC | Automated decision-making, algorithmic pricing | Right-to-explanation for surge · algorithmic transparency | Algorithmic Impact Assessment |
| CASL | Canadian Anti-Spam Legislation | CRTC | SMS / email / push promos | Express vs implied consent ledger · unsubscribe ≤10 biz days · sender ID in every message | Consent ledger · template audit |
| OLA | Official Languages Act | OCOL | Federally-regulated commerce | Full EN+FR parity on strings · SMS · email · receipts · push · error codes | i18n catalogue with 100% coverage |
| ACA | Accessible Canada Act | ASC | Federally-regulated sector | Published accessibility plan · feedback mechanism · 3-year progress reports | Accessibility plan PDF · feedback URL |
| AML | PCMLTFA (FINTRAC) | FINTRAC | Wallet, corporate accounts, top-ups | $10k aggregate threshold reporting · KYC on corporate · suspicious-txn pipeline | MSB registration · STR pipeline |
| CRA | Income Tax Act + Excise Tax Act | CRA | Driver payouts, fare collection | T4A ≥$500/yr/driver · GST/HST reg ≥$30k/yr · BN-9 validation on corporate | T4A XMLs · tax filings |
| COMP | Competition Act | Competition Bureau | Dynamic pricing, surge | Upfront price disclosure · surge cap during declared emergencies | Pricing algorithm spec |
| E911 | CRTC E911 Phase II | CRTC + provincial PSAPs | SOS / in-app 911 | Automatic location forwarding when 911 dialled from app · fallback to OS dialler with pre-filled SMS location | SOS flow spec |
| CRTC | Telecommunications Act (SMS) | CRTC | SMS delivery | Carrier registration · verified sender / short code · STOP / HELP keyword handling | SMS vendor contract |

## Provincial — Saskatchewan (expand per-province on entry)

| ID | Act / Regulation | Authority | Trigger | Key audit check |
|---|---|---|---|---|
| SK-TNC | Passenger Transportation (Vehicles for Hire) Act + regs | SK Govt / Municipalities | Operating ride-share in SK | Driver permit # · vehicle permit # · operator permit · accessibility quota if any |
| SGI | SGI rideshare commercial endorsement | Saskatchewan Government Insurance | Driver activates ride | Period 1/2/3 gap coverage · proof on dispatch · expiry blocks dispatch (not warning) |
| SK-PST | Provincial Sales Tax Act | SK Finance | Taxable ride portion | 6% PST line-itemised on receipt |
| SK-CPPA | Consumer Protection and Business Practices Act | SK Govt | Consumer transactions | Refund policy · cancellation fee disclosure pre-booking · clear pricing |
| SK-HRC | Saskatchewan Human Rights Code | SK HRC | Service refusal | Service-animal + WAV enforcement · driver training record |

## Municipal

| ID | Bylaw | Authority | Key audit check |
|---|---|---|---|
| REG-BL | Regina Taxi & TNC Bylaw | City of Regina | City permit · airport concession · accessibility fleet % |
| SKN-BL | Saskatoon Vehicle-for-Hire Bylaw | City of Saskatoon | Same |

## Industry standards

| ID | Standard | Trigger | Key audit check |
|---|---|---|---|
| PCI-DSS | PCI-DSS v4.0 | Card processing | No raw PAN at rest · Stripe Elements/tokenisation · HMAC webhook signatures · quarterly ASV scans if in scope |
| SOC2 | SOC 2 Type II (aspirational, enterprise customers) | Corporate sales | Change mgmt · access reviews · IR · vendor mgmt |
| WCAG | WCAG 2.1 AA | All UI | Contrast · keyboard nav · screen-reader labels · reflow · Dynamic Type |

## Safety / screening

| ID | Item | Key audit check |
|---|---|---|
| SAFE-CRC | Criminal Record Check (incl. Vulnerable Sector) | Refresh cadence enforced · expired = dispatch block |
| SAFE-DRV | Driver's licence + abstract | Expiry blocks dispatch |
| SAFE-VEH | Vehicle inspection | Annual · expiry blocks dispatch |
| SAFE-AGE | Age gating | Driver ≥21 (SK policy) · rider ≥16 unaccompanied (policy) |
| SAFE-OCAP | Indigenous data sovereignty (OCAP®) | If Indigenous user data: ownership · control · access · possession |

---

## Data classification (referenced by Dimension 12)

| Class | Examples | Retention | Storage | Access |
|---|---|---|---|---|
| PUBLIC | Service areas, base fares | Indefinite | Any | All |
| INTERNAL | Aggregate metrics, non-PII logs | 2 y | Canadian region | Employees |
| PII | Name, phone, email, address, ride history | Active + 2 y post-closure | Canadian region + encrypted at rest | Need-to-know + audit log row |
| PCI | PAN, CVV, track data | **Never at rest** | Stripe only (tokenised) | Never direct |
| SENSITIVE | Criminal-check result, SIN, bank account, driver abstract | Active + per-reg retention | Canadian region + encrypted + HSM | Dual control + audit log + alert |

---

## Per-module applicability (quick index)

| Regulation ID | Backend | Admin | Rider | Driver |
|---|:-:|:-:|:-:|:-:|
| PIPEDA | Y | Y | Y | Y |
| CPPA | Y | Y | Y | — |
| CASL | Y | Y | Y | Y |
| OLA | Y | Y | Y | Y |
| ACA | — | Y | Y | Y |
| AML | Y | Y | — | — |
| CRA | Y | Y | — | Y |
| COMP | Y | — | Y | — |
| E911 | Y | — | Y | Y |
| CRTC | Y | — | — | — |
| SK-TNC | Y | Y | — | Y |
| SGI | Y | Y | — | Y |
| SK-PST | Y | Y | Y | Y |
| SK-CPPA | Y | — | Y | — |
| SK-HRC | Y | Y | Y | Y |
| PCI-DSS | Y | Y | Y | Y |
| SOC2 | Y | Y | — | — |
| WCAG | — | Y | Y | Y |
| SAFE-* | Y | Y | — | Y |

Legend: Y = in scope for that module's audit · — = not primary scope
