/**
 * E2E Tests for Authentication Flow
 * Tests cover OTP login, verification, and logout
 */
import { device, element, by, expect } from 'detox';

describe('Authentication Flow', () => {
  beforeAll(async () => {
    await device.launchApp({
      permissions: {
        location: 'always',
        notifications: 'YES',
      },
    });
  });

  beforeEach(async () => {
    await device.reloadReactNative();
  });

  describe('OTP Login', () => {
    it('should show phone input on launch', async () => {
      await expect(element(by.text('Enter your phone number'))).toBeVisible();
    });

    it('should validate phone number format', async () => {
      await element(by.type('XCUIElementTypeTextField')).typeText('invalid');
      await element(by.text('Send OTP')).tap();
      
      // Should show error for invalid phone
      await expect(element(by.text('Please enter a valid phone number'))).toBeVisible();
    });

    it('should send OTP for valid phone number', async () => {
      await element(by.type('XCUIElementTypeTextField')).typeText('+1234567890');
      await element(by.text('Send OTP')).tap();
      
      // Should navigate to OTP verification screen
      await expect(element(by.text('Enter verification code'))).toBeVisible();
    });

    it('should verify OTP and login successfully', async () => {
      // Enter phone number
      await element(by.type('XCUIElementTypeTextField')).typeText('+1234567890');
      await element(by.text('Send OTP')).tap();
      
      // Enter OTP (mocked to accept any 6-digit code)
      for (let i = 0; i < 6; i++) {
        await element(by.type('XCUIElementTypeTextField')).typeText('1');
      }
      await element(by.text('Verify')).tap();
      
      // Should navigate to home screen
      await expect(element(by.text('Where to?'))).toBeVisible();
    });

    it('should show error for invalid OTP', async () => {
      // Enter phone number
      await element(by.type('XCUIElementTypeTextField')).typeText('+1234567890');
      await element(by.text('Send OTP')).tap();
      
      // Enter wrong OTP
      await element(by.type('XCUIElementTypeTextField')).typeText('000000');
      await element(by.text('Verify')).tap();
      
      // Should show error
      await expect(element(by.text('Invalid verification code'))).toBeVisible();
    });

    it('should resend OTP on request', async () => {
      // Enter phone number
      await element(by.type('XCUIElementTypeTextField')).typeText('+1234567890');
      await element(by.text('Send OTP')).tap();
      
      // Wait for resend timer and tap resend
      await waitFor(element(by.text('Resend OTP')))
        .toBeVisible()
        .withTimeout(10000);
      await element(by.text('Resend OTP')).tap();
      
      // Should show confirmation
      await expect(element(by.text('OTP sent successfully'))).toBeVisible();
    });
  });

  describe('Logout', () => {
    beforeEach(async () => {
      // Login first
      await element(by.type('XCUIElementTypeTextField')).typeText('+1234567890');
      await element(by.text('Send OTP')).tap();
      
      for (let i = 0; i < 6; i++) {
        await element(by.type('XCUIElementTypeTextField')).typeText('1');
      }
      await element(by.text('Verify')).tap();
      
      await waitFor(element(by.text('Where to?')))
        .toBeVisible()
        .withTimeout(5000);
    });

    it('should logout successfully', async () => {
      // Open menu
      await element(by.id('menu-button')).tap();
      
      // Tap logout
      await element(by.text('Logout')).tap();
      
      // Confirm logout
      await element(by.text('Confirm')).tap();
      
      // Should return to login screen
      await expect(element(by.text('Enter your phone number'))).toBeVisible();
    });
  });

  describe('Session Persistence', () => {
    it('should persist session after app restart', async () => {
      // Login
      await element(by.type('XCUIElementTypeTextField')).typeText('+1234567890');
      await element(by.text('Send OTP')).tap();
      
      for (let i = 0; i < 6; i++) {
        await element(by.type('XCUIElementTypeTextField')).typeText('1');
      }
      await element(by.text('Verify')).tap();
      
      await waitFor(element(by.text('Where to?')))
        .toBeVisible()
        .withTimeout(5000);
      
      // Terminate and relaunch app
      await device.terminateApp();
      await device.launchApp();
      
      // Should still be logged in
      await expect(element(by.text('Where to?'))).toBeVisible();
    });
  });
});