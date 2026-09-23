# PR #5722 driver session atomicity

Driver OTP and Firebase sign-ins now call `begin_driver_session` for an
authenticated driver when the rollout flag is enabled. The database function
serializes generation bump, session replacement, and refresh-token revocation;
the minted access and refresh credentials use the returned generation. Refresh
rotation keeps the parent's generation, including `NULL` for legacy writers.
While the flag is dark, an unbound legacy token remains usable only if it is
newer than the account's logout-all watermark. Once enabled, unbound legacy
tokens are rejected.

Superseded-driver cleanup now honors the conditional offer and ride update
results. If acceptance wins the race, cleanup preserves the assigned ride,
driver availability, and insurance period. A successfully reverted assignment
notifies the rider and re-dispatches.

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
