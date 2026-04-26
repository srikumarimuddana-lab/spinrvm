/**
 * Next.js edge middleware — wires up the auth gate from proxy.ts.
 *
 * Next.js requires this file to be named `middleware.ts` and export a
 * function called `middleware` (or a default export). The auth logic lives
 * in proxy.ts so it can be tested independently.
 */
export { proxy as middleware, config } from './proxy';
