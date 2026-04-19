# Dimension 14 — Performance & Scalability

**Question:** Is the app fast to start, smooth to use, and efficient at scale? Will it break under load?

---

## Checklist

### JS Bundle & Startup (React Native)
- [ ] Hermes JS engine explicitly enabled (50–60% startup improvement on Android)
  - Expo SDK 54: `newArchEnabled: true` enables new arch; Hermes is default on both platforms
  - Verify: no `"jsEngine": "jsc"` override in `app.config.ts` or `eas.json`
- [ ] New Architecture (JSI/Fabric) enabled — `newArchEnabled: true`
- [ ] Production build has minification and tree shaking (EAS build defaults)
- [ ] Large dependencies lazy-loaded where possible (e.g. charts loaded only when earnings screen opens)
- [ ] Metro cache configured — shared across web/android builds reduces rebuild time
- [ ] No unnecessary `console.log` in production — remove before store submission

### List Performance (React Native)
- [ ] FlatList `keyExtractor` uses stable ID — never `Math.random()` (causes full remount on every render)
- [ ] FlatList has `initialNumToRender` (suggest: 10)
- [ ] FlatList has `maxToRenderPerBatch` (suggest: 5)
- [ ] FlatList has `windowSize` (suggest: 5)
- [ ] Fixed-height rows: `getItemLayout` defined (avoids measuring every item)
- [ ] `removeClippedSubviews={true}` on Android for long lists
- [ ] Dynamic-height rows: use `FlashList` (from Shopify) instead of FlatList

### Image Loading
- [ ] Use `expo-image` (not React Native `Image`) — built-in memory + disk cache, blurhash placeholder, progressive loading
- [ ] All images have explicit `width` and `height` — prevents layout reflow
- [ ] Avatar images loaded once and cached — not re-downloaded on every render
- [ ] Car marker images on map: cache static assets, don't re-fetch on every location update

### Component Re-Render Prevention
- [ ] Pure components wrapped in `React.memo`
- [ ] Callback functions wrapped in `useCallback` in hook-heavy screens
- [ ] Derived values wrapped in `useMemo` — not recalculated on every render
- [ ] Zustand store selectors use fine-grained selection — not selecting entire store object

### Backend Query Performance
- [ ] No unbounded queries — all `get_rows()` calls have a `limit` parameter
- [ ] `limit` values are exposed as API pagination parameters — not hardcoded
- [ ] Heavy queries (e.g. driver rating from 1000 rides) use DB aggregation — not Python loop
- [ ] Database indexes exist for common query patterns:
  - `rides(driver_id, created_at DESC)` — driver history
  - `rides(status, created_at)` — dispatch queue
  - `drivers(status, rating)` — driver search
  - `refresh_tokens(token_hash)` — auth (UNIQUE index)
- [ ] N+1 query patterns avoided — no loop calling DB per item

### Memory Management
- [ ] Location buffer bounded (500 entries — rotate, not grow)
- [ ] All `useEffect` subscriptions return cleanup functions
- [ ] WebSocket connection closed on unmount
- [ ] `setInterval` and `setTimeout` cleared on unmount
- [ ] Error log ring buffer bounded (50 entries)

### Backend Scalability
- [ ] Stateless API — no in-memory state between requests
- [ ] Redis used for shared state (rate limits, locks) — not per-worker dicts
- [ ] WebSocket state managed via Redis pub/sub — horizontally scalable
- [ ] Long-running tasks (document expiry, payment retry) run in background workers — not blocking request threads
- [ ] Pagination on all list endpoints that can return >100 items

---

## Severity Guide

| Finding | Severity |
|---|---|
| `Math.random()` as FlatList key — full list remount on every state change | CRITICAL |
| Unbounded DB query loads 1000+ rows per request | HIGH |
| Hermes not enabled — 50% slower startup on Android | HIGH |
| No pagination — list endpoint returns all records | HIGH |
| N+1 query pattern — 1 request per list item | HIGH |
| Missing DB index on high-traffic query | MEDIUM |
| FlatList missing `initialNumToRender` — slow initial render | MEDIUM |
| `expo-image` not used — no caching for remote images | MEDIUM |
| Component missing `React.memo` — unnecessary re-renders | MEDIUM |
| `console.log` in production build | LOW |
| Missing `getItemLayout` for fixed-height rows | LOW |
