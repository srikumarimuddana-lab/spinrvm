/**
 * Sensor fusion for GPS spoofing detection.
 *
 * Cross-validates accelerometer data against GPS speed claims.
 * If GPS says "moving 60 km/h" but the accelerometer shows no movement,
 * the location is likely spoofed.
 *
 * This catches sophisticated spoofing that passes the basic mocked flag check
 * (rooted devices with Magisk that strip the mock flag).
 */

import { Accelerometer, AccelerometerMeasurement } from 'expo-sensors';
import { Platform } from 'react-native';

const ACCEL_BUFFER_SIZE = 20; // ~2 seconds of readings at 100ms interval
const MOVEMENT_THRESHOLD = 0.4; // m/s² variance — widened from 0.15 to tolerate dashboard mounts, cup holders, smooth roads
const SPEED_REQUIRING_MOVEMENT = 5; // m/s (~18 km/h) — below this, could be stationary in traffic

let _accelBuffer: AccelerometerMeasurement[] = [];
let _subscription: ReturnType<typeof Accelerometer.addListener> | null = null;
let _isRunning = false;

export function startSensorMonitoring(): void {
  if (_isRunning) return;
  _isRunning = true;

  Accelerometer.setUpdateInterval(100);
  _subscription = Accelerometer.addListener((data) => {
    _accelBuffer.push(data);
    if (_accelBuffer.length > ACCEL_BUFFER_SIZE) {
      _accelBuffer.shift();
    }
  });
}

export function stopSensorMonitoring(): void {
  _isRunning = false;
  _subscription?.remove();
  _subscription = null;
  _accelBuffer = [];
}

/**
 * Returns true if the accelerometer data is consistent with the claimed GPS speed.
 * Returns true (trusted) if:
 *  - sensors unavailable (don't block on hardware limitations)
 *  - speed is low enough that being stationary is plausible
 *  - accelerometer shows movement consistent with driving
 */
export function isMovementConsistentWithSpeed(gpsSpeedMps: number | null): boolean {
  if (!_isRunning || _accelBuffer.length < 10) {
    return true; // Not enough data — don't block
  }

  const speed = gpsSpeedMps ?? 0;
  if (speed < SPEED_REQUIRING_MOVEMENT) {
    return true; // Low speed — could be stopped in traffic
  }

  // Calculate acceleration variance (magnitude minus gravity)
  // A moving vehicle has constant micro-vibrations from road, engine, turns
  const magnitudes = _accelBuffer.map((d) => {
    const mag = Math.sqrt(d.x ** 2 + d.y ** 2 + d.z ** 2);
    // Subtract gravity (1G ≈ 9.81 m/s², but expo-sensors normalizes to 1.0)
    return Math.abs(mag - 1.0);
  });

  const mean = magnitudes.reduce((a, b) => a + b, 0) / magnitudes.length;
  const variance = magnitudes.reduce((a, b) => a + (b - mean) ** 2, 0) / magnitudes.length;

  // A vehicle at 18+ km/h ALWAYS produces detectable vibration.
  // A phone sitting on a desk with a GPS spoofer has near-zero variance.
  if (variance < MOVEMENT_THRESHOLD && Platform.OS === 'android') {
    return false; // GPS says moving fast, but phone is perfectly still
  }

  return true;
}

export function getSensorConfidence(): { variance: number; sampleCount: number } {
  if (_accelBuffer.length < 5) {
    return { variance: -1, sampleCount: _accelBuffer.length };
  }
  const magnitudes = _accelBuffer.map((d) => Math.abs(Math.sqrt(d.x ** 2 + d.y ** 2 + d.z ** 2) - 1.0));
  const mean = magnitudes.reduce((a, b) => a + b, 0) / magnitudes.length;
  const variance = magnitudes.reduce((a, b) => a + (b - mean) ** 2, 0) / magnitudes.length;
  return { variance, sampleCount: _accelBuffer.length };
}

/**
 * Returns a diagnostic object instead of a simple boolean.
 * Callers should tag the location payload as `sensor_suspect` when
 * `consistent` is false, rather than dropping the update entirely.
 */
export function checkMovementConsistency(gpsSpeedMps: number | null): { consistent: boolean; variance: number } {
  if (!_isRunning || _accelBuffer.length < 10) {
    return { consistent: true, variance: -1 }; // Not enough data — assume OK
  }

  const speed = gpsSpeedMps ?? 0;
  if (speed < SPEED_REQUIRING_MOVEMENT) {
    return { consistent: true, variance: -1 }; // Low speed — stationary is plausible
  }

  const magnitudes = _accelBuffer.map((d) => {
    const mag = Math.sqrt(d.x ** 2 + d.y ** 2 + d.z ** 2);
    return Math.abs(mag - 1.0);
  });

  const mean = magnitudes.reduce((a, b) => a + b, 0) / magnitudes.length;
  const variance = magnitudes.reduce((a, b) => a + (b - mean) ** 2, 0) / magnitudes.length;

  const consistent = !(variance < MOVEMENT_THRESHOLD && Platform.OS === 'android');
  return { consistent, variance };
}
