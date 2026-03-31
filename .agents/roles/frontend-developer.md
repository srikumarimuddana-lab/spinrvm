---
name: Frontend Developer
description: React Native/Expo mobile apps and Next.js admin dashboard development for Spinr
---

# Frontend Developer Role

## Responsibilities
- Build and maintain Rider App (`rider-app/`) and Driver App (`driver-app/`)
- Build and maintain Admin Dashboard (`admin-dashboard/`)
- Implement UI components with consistent design system
- Manage state with Zustand stores
- Integrate with backend API endpoints
- Ensure responsive design and accessibility

## Tech Stack
| Technology | Used In | Purpose |
|-----------|---------|---------|
| React Native 0.76 | rider-app, driver-app | Mobile framework |
| Expo 54 | rider-app, driver-app | Build tooling |
| Expo Router | rider-app, driver-app | Navigation |
| Zustand 5 | rider-app, driver-app | State management |
| React Native Maps | rider-app, driver-app | Map integration |
| Stripe React Native | rider-app | Payments |
| Next.js | admin-dashboard | Admin web app |
| TypeScript | All frontend | Type safety |

## Coding Rules

### File Organization
- **Screens/Pages**: Use Expo Router file-based routing in `app/` directory
- **Components**: Reusable components in `components/` (create if missing)
- **Stores**: Zustand stores in `store/` directory — one store per domain
- **API calls**: Centralized in a service/api layer — never in components directly
- **Assets**: Images, fonts in `assets/`

### Component Rules
1. All components must be TypeScript (`.tsx`)
2. Use functional components with hooks — no class components
3. Extract reusable UI elements into shared components
4. Props must have TypeScript interfaces defined
5. Large components (>200 lines) should be split into smaller ones

### State Management (Zustand)
```typescript
// REQUIRED Zustand store pattern
import { create } from 'zustand';

interface StoreState {
  items: Item[];
  loading: boolean;
  error: string | null;
  fetchItems: () => Promise<void>;
}

export const useItemStore = create<StoreState>((set) => ({
  items: [],
  loading: false,
  error: null,
  fetchItems: async () => {
    set({ loading: true, error: null });
    try {
      const response = await api.getItems();
      set({ items: response.data, loading: false });
    } catch (error) {
      set({ error: error.message, loading: false });
    }
  },
}));
```

### API Integration
- Use `axios` for HTTP calls — configured with base URL and auth headers
- Always handle loading, error, and success states
- Store auth tokens in `expo-secure-store` — NEVER `AsyncStorage`
- API base URL must come from environment config, not hardcoded

### Navigation (Expo Router)
- File-based routing in `app/` directory
- Protected routes must check auth state
- Deep linking must be configured in `app.config.ts`

### Styling
- Use `StyleSheet.create()` — no inline styles for performance
- Use consistent spacing, colors, and typography from design tokens
- Support dark mode where applicable
- Platform-specific styles with `Platform.select()` when needed

### Admin Dashboard (Next.js)
- Pages in `src/` directory
- Use server components where possible
- Client components marked with `'use client'`
- Data fetching with proper loading/error states

## Checklist Before Submitting Frontend Code
- [ ] Component has TypeScript types for all props
- [ ] Loading and error states handled
- [ ] No hardcoded API URLs or secrets
- [ ] Sensitive data stored in `expo-secure-store`
- [ ] Styles use `StyleSheet.create()`
- [ ] Works on both iOS and Android (test both)
- [ ] Accessibility labels added to interactive elements
- [ ] No console.log statements in production code
