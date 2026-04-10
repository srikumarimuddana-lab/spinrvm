# E2E Testing Guide

## Admin Dashboard — Playwright

Playwright tests live in `admin-dashboard/e2e/`. They mock all API calls so
no backend is required.

```bash
cd admin-dashboard
npm run test:e2e          # run all tests
npm run test:e2e:ui       # interactive UI mode
npm run test:e2e:report   # view last report
```

Tests run automatically in CI on every push to `main`.

## Mobile Apps — Maestro

Maestro flow files live in `.maestro/`. They require a running simulator.

See `.maestro/README.md` for setup and run instructions.

### App IDs

| App | Bundle ID |
|-----|-----------|
| Rider | `com.spinr.user` |
| Driver | `com.spinr.driver` |

> **Note:** Check `rider-app/app.config.ts` and `driver-app/app.config.ts` for
> the actual bundle IDs and update the `appId` in the flow files if different.
