# Challenges & Issues — Audit and remediate corporate KYB document Storage bucket RLS/access scoping

## Stage 3-4: Design & Architecture

**The migration-340 reference pattern isn't a safe direct copy for this bucket, and that's easy to miss.**

The chosen approach (Stage 1-2) explicitly says the new migration should mirror
`backend/migrations/340_safety_incident_photos.sql`. That's the right pattern for the
*provisioning* half (idempotent `INSERT ... ON CONFLICT (id) DO UPDATE`, "no RLS grant to
authenticated/anon" comment, `NOTIFY pgrst`). But migration 340's bucket (`safety-evidence`)
was net-new — nothing existed in it before the migration ran, so its rollback comment safely
says `DROP TABLE` / `DELETE FROM storage.objects/buckets`.

`kyb-documents` is not net-new. Per `backend/docs/STORAGE_BUCKETS.md`, it has existed since
before this repo tracked storage buckets in migrations at all, created by hand and written to
by the running application ever since — meaning it may already hold real corporate KYB
verification documents. A rollback comment that copies 340's wording verbatim would read as a
safe revert path but would actually destroy live company documents if ever run. This wasn't
called out in the Stage 1-2 approach description, and would have been easy to carry straight
into Stage 5 unnoticed if a mirror-the-pattern instruction is followed literally instead of
read for what it actually implies about *this* bucket's data.

**Resolution for this run:** not fixed here — that's Stage 5's job, this stage's job is to
catch it before the file is written. Flagged explicitly in `progress-report.md`'s Stage 3-4
section and in `decisions.md` so Stage 5 writes a rollback comment that reflects this bucket's
actual state (config-only revert, explicit warning against `DROP`/object deletion) rather than
reusing 340's text.

**Why this belongs in this file rather than just being silently handled:** GUARDRAILS.md asks
each stage to write down what it's uncertain about rather than resolve it quietly. This isn't
a blocked check or a failed grep — the blast-radius check itself came back clean (see
`progress-report.md`) — but it's exactly the kind of "looks like the same shape as a past
pattern, isn't" mismatch that's worth a human's eyes at Stage 8 Change Review, given this run
already requires that pause per acceptance criterion 5.

No other rough edges this stage — the file/function plan matched the Stage 1-2 approach
directly, the migration number was unambiguous (353 is the current max, no gaps/collisions in
the 350s), and the blast-radius grep came back small and fully enumerable on the first pass.
