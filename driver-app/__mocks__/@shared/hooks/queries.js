/**
 * Jest stub for '@shared/hooks/queries'.
 *
 * driver-app's moduleNameMapper rewrites every '@shared/*' import into this
 * __mocks__ tree; without this file, any test that (transitively) imports
 * the driver home screen fails module resolution. Hooks resolve to inert
 * empty-state queries; tests that care about their data mock them
 * explicitly. Tests for the real implementations import
 * '../../shared/hooks/queries/...' by relative path instead (see
 * __tests__/shared/demandHeatmap.test.ts).
 */

const emptyQuery = () => ({
  data: undefined,
  isLoading: false,
  isError: false,
  error: null,
  refetch: jest.fn(),
});

module.exports = {
  useDriverMe: jest.fn(emptyQuery),
  useUpdateDriverMe: jest.fn(() => ({ mutate: jest.fn(), mutateAsync: jest.fn() })),
  useDriverConfig: jest.fn(emptyQuery),
  useDriverEarnings: jest.fn(emptyQuery),
  useActiveRide: jest.fn(emptyQuery),
  useDemandHeatmap: jest.fn(emptyQuery),
  normalizeDemandHeatmap: jest.fn((raw) => ({
    enabled: !!(raw && raw.enabled),
    points: [],
    refreshSeconds: 300,
    colorTheme: 'inferno',
  })),
  HEATMAP_DEFAULT_REFRESH_SECONDS: 300,
  HEATMAP_MIN_REFRESH_SECONDS: 30,
  HEATMAP_THEMES: ['inferno', 'ocean', 'viridis'],
  HEATMAP_DEFAULT_THEME: 'inferno',
};
