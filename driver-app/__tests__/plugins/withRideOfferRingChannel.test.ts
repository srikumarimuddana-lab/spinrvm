/**
 * plugins/withRideOfferRingChannel.js — the generated Kotlin that runs in
 * Application.onCreate. Source-level checks: the Kotlin itself only compiles
 * in an EAS build, which this suite cannot run.
 */
// eslint-disable-next-line @typescript-eslint/no-require-imports
const plugin = require('../../plugins/withRideOfferRingChannel');

const KOTLIN = plugin.buildKotlinSource('com.spinr.driver');

describe('withRideOfferRingChannel Kotlin source', () => {
  it('creates the alarm channel on the alarm stream with the ride_offer sound', () => {
    expect(plugin.ALARM_CHANNEL_ID).toBe('ride-offers-alarm-v1');
    expect(KOTLIN).toContain('const val ALARM_CHANNEL_ID = "ride-offers-alarm-v1"');
    expect(KOTLIN).toContain('.setUsage(AudioAttributes.USAGE_ALARM)');
    expect(KOTLIN).toContain('getIdentifier("ride_offer", "raw", context.packageName)');
    expect(KOTLIN).toContain('NotificationManager.IMPORTANCE_HIGH');
    expect(KOTLIN).toContain('manager.createNotificationChannel(channel)');
  });

  it('never recreates an existing alarm channel (keeps the driver\'s own settings)', () => {
    expect(KOTLIN).toMatch(/getNotificationChannel\(ALARM_CHANNEL_ID\) != null\) return/);
  });

  it('never uses the ringtone usage that hid the minimised offer', () => {
    expect(KOTLIN).not.toContain('USAGE_NOTIFICATION_RINGTONE');
  });

  it('still deletes ride-offers-v4, independently of the alarm channel', () => {
    expect(KOTLIN).toContain('const val CHANNEL_ID = "ride-offers-v4"');
    expect(KOTLIN).toContain('manager.deleteNotificationChannel(CHANNEL_ID)');
    // Separate functions, each with its own try/catch, so a failure in one
    // cannot skip the other.
    expect(KOTLIN).toContain('removeRingChannel(manager)');
    expect(KOTLIN).toContain('createAlarmChannel(context, manager)');
    expect(KOTLIN.match(/catch \(e: Exception\)/g)).toHaveLength(2);
  });
});

describe('injectEnsureCall', () => {
  const MAIN_APPLICATION = [
    'class MainApplication : Application() {',
    '  override fun onCreate() {',
    '    super.onCreate()',
    '    loadReactNative(this)',
    '  }',
    '}',
  ].join('\n');

  it('calls ensure() right after super.onCreate(), once', () => {
    const once = plugin.injectEnsureCall(MAIN_APPLICATION, 'kt');
    expect(once).toContain('    super.onCreate()\n    RideOfferRingChannel.ensure(this)\n');
    expect(plugin.injectEnsureCall(once, 'kt')).toBe(once);
  });

  it('fails the build rather than ship without the call', () => {
    expect(() => plugin.injectEnsureCall(MAIN_APPLICATION, 'java')).toThrow();
    expect(() => plugin.injectEnsureCall('class MainApplication {}', 'kt')).toThrow();
  });
});
