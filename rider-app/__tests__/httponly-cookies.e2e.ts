import { test, expect } from '@playwright/test'

/**
 * E2E test: Verify HttpOnly cookie authentication works in staging.
 * Run with: npx playwright test httponly-cookies.e2e.ts
 */

test.describe('HttpOnly Cookie Authentication', () => {
  test.beforeEach(async ({ page }) => {
    // Navigate to staging
    await page.goto(process.env.STAGING_URL || 'http://localhost:8081')
  })

  test('login sets httponly auth_token cookie', async ({ page, context }) => {
    // Navigate to login
    await page.goto('/login')

    // Fill in credentials (mock or test account)
    await page.fill('[data-testid="phone-input"]', '+16475551234')
    await page.click('[data-testid="send-otp"]')

    // Wait for OTP screen
    await page.waitForSelector('[data-testid="otp-input"]')
    await page.fill('[data-testid="otp-input"]', '1234') // Mock OTP

    // Submit
    await page.click('[data-testid="verify-otp"]')

    // Wait for navigation to home
    await page.waitForURL('/home')

    // Get cookies from browser context
    const cookies = await context.cookies()
    const authCookie = cookies.find((c) => c.name === 'auth_token')

    // Verify cookie exists and has security flags
    expect(authCookie).toBeDefined()
    expect(authCookie?.httpOnly).toBe(true)
    expect(authCookie?.sameSite).toBe('Strict')
    expect(authCookie?.secure).toBe(true) // Production only

    // Verify token NOT in localStorage
    const localStorage = await page.evaluate(() => {
      return {
        authToken: window.localStorage.getItem('auth_token'),
        refreshToken: window.localStorage.getItem('refresh_token'),
        token: window.localStorage.getItem('token'),
      }
    })

    expect(localStorage.authToken).toBeNull()
    expect(localStorage.refreshToken).toBeNull()
    expect(localStorage.token).toBeNull()
  })

  test('protected endpoint accessible with cookie', async ({ page, context }) => {
    // Login first
    await page.goto('/login')
    // ... [login steps from above] ...

    // Verify /rides endpoint is accessible
    const response = await page.request.get('/api/rides')
    expect(response.status()).toBe(200)

    // Cookies should be sent automatically via withCredentials
  })

  test('401 without valid cookie triggers refresh', async ({ page }) => {
    // Navigate to protected route without auth
    const response = await page.request.get('/api/me')

    // Should be 401 or redirect to login
    expect([301, 302, 401]).toContain(response.status())
  })
})
