# Option A — Kotlin 2.0.21 strategy (driver-app)

**Status:** Cold storage. NOT active.

Same as `rider-app/build-options/option-a-kotlin-2.0.21/README.md` but **without Stripe**
(driver-app doesn't include @stripe/stripe-react-native).

## How to switch FROM Option C TO Option A

1. **Replace patches:**
   ```bash
   cp build-options/option-a-kotlin-2.0.21/@react-native+gradle-plugin+0.85.2.patch \
      patches/@react-native+gradle-plugin+0.85.2.patch
   ```
2. **Edit `app.config.ts`:**
   - Change `kotlinVersion: '2.2.21'` → `'2.0.21'`
   - Remove `'./plugins/withKspVersion'` from plugins array
3. **Run `yarn install`** to apply the patch.
4. **Local verify** with `npx expo prebuild --clean --platform android`.

See `rider-app/build-options/option-a-kotlin-2.0.21/README.md` for full context.
