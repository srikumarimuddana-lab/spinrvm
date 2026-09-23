# PR #5722 driver session atomicity

Driver OTP, Firebase, company-email, and reactivation sign-ins now call
`begin_driver_session` for an authenticated driver when the rollout flag is
enabled. OTP uses the stored driver identity; client-supplied `client_app` is
attribution only and cannot disable generation revocation. When user role flags
do not identify a driver, auth confirms the linked active `drivers` row. The
database function
serializes generation bump, session replacement, and refresh-token revocation;
the minted access and refresh credentials use the returned generation. Refresh
rotation keeps the parent's generation, including `NULL` for legacy writers.
While the flag is dark, an unbound legacy token remains usable unless it
predates an account's logout-all watermark. Once enabled, unbound legacy
tokens are rejected.

Superseded-driver cleanup now honors the conditional offer and ride update
results. If acceptance wins the race, cleanup preserves the assigned ride,
driver availability, and insurance period. A successfully reverted assignment
notifies the rider and re-dispatches. Company-email and reactivation logins now
also tombstone the prior session, kick its sockets, and clean up driver presence.

The alternative of treating every `NULL` generation as zero was rejected: an
old API replica could issue a credential after a newer generation had been
established and accidentally inherit generation zero. Rejecting every legacy
row was also rejected because it would break refresh for users during a dark
rolling deployment. A new migration was unnecessary because the unmerged
452 migration already defines the atomic RPC and the feature flag.

Blast radius is limited to driver sign-in/refresh behavior, superseded-driver
presence cleanup, and the rider notification for a reverted assigned offer.
Rider sign-in does not invoke the driver-session RPC. The generation feature
remains opt-in and requires migration 452 before deployment.

Validation covered the auth session, refresh compatibility, and offer-cleanup
regressions plus the existing auth endpoint suite. Direct-pool migration tests
and production rolling-deployment behavior were not run; no live database was
modified. There is no visual surface in this change.

The refresh-generation contract matrix explicitly covers a legacy unbound row
at generation zero, flag-on rejection at a newer generation, flag-off
acceptance without a watermark, and a bound row matching the current generation.
