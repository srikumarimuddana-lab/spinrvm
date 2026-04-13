'use client';

import { useEffect, useState } from 'react';
import { useParams } from 'next/navigation';

interface RideInfo {
  status: string;
  message?: string;
  pickup_address: string;
  dropoff_address: string;
  driver?: {
    name: string;
    lat?: number;
    lng?: number;
    vehicle_make?: string;
    vehicle_model?: string;
    vehicle_color?: string;
  };
}

const STATUS_CONFIG: Record<string, { label: string; color: string; bg: string; icon: string }> = {
  searching: { label: 'Finding Driver', color: '#F59E0B', bg: '#FEF3C7', icon: '🔍' },
  driver_assigned: { label: 'Driver Assigned', color: '#3B82F6', bg: '#DBEAFE', icon: '🚗' },
  driver_accepted: { label: 'Driver Accepted', color: '#3B82F6', bg: '#DBEAFE', icon: '✅' },
  driver_arrived: { label: 'Driver Arrived', color: '#10B981', bg: '#ECFDF5', icon: '📍' },
  in_progress: { label: 'Trip In Progress', color: '#8B5CF6', bg: '#EDE9FE', icon: '🚀' },
  completed: { label: 'Trip Completed', color: '#6B7280', bg: '#F3F4F6', icon: '🏁' },
  cancelled: { label: 'Trip Cancelled', color: '#EF4444', bg: '#FEE2E2', icon: '❌' },
};

export default function TrackRide() {
  const params = useParams();
  const shareToken = params.rideId as string;
  const [ride, setRide] = useState<RideInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);

  useEffect(() => {
    const fetchRideStatus = async () => {
      try {
        const apiUrl = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';
        // Try the public share token endpoint first
        const res = await fetch(`${apiUrl}/api/v1/rides/track/${shareToken}`);
        if (!res.ok) {
          // Fallback: try as ride ID with v1 prefix
          const fallback = await fetch(`${apiUrl}/api/v1/rides/${shareToken}`);
          if (!fallback.ok) throw new Error('Ride not found or link expired');
          const data = await fallback.json();
          const rideData = data.ride || data;
          const driverData = data.driver || null;
          setRide({
            status: rideData.status,
            pickup_address: rideData.pickup_address,
            dropoff_address: rideData.dropoff_address,
            driver: driverData ? {
              name: driverData.name || 'Driver',
              lat: driverData.lat,
              lng: driverData.lng,
              vehicle_make: driverData.vehicle_make,
              vehicle_model: driverData.vehicle_model,
              vehicle_color: driverData.vehicle_color,
            } : undefined,
          });
        } else {
          const data = await res.json();
          setRide({
            status: data.status,
            message: data.message,
            pickup_address: data.pickup_address,
            dropoff_address: data.dropoff_address,
            driver: data.driver || undefined,
          });
        }
        setLastUpdated(new Date());
        setError('');
      } catch (err: any) {
        setError(err.message);
      } finally {
        setLoading(false);
      }
    };

    fetchRideStatus();
    const interval = setInterval(fetchRideStatus, 5000);
    return () => clearInterval(interval);
  }, [shareToken]);

  const statusConfig = STATUS_CONFIG[ride?.status || ''] || STATUS_CONFIG.searching;
  const isActive = ride?.status && !['completed', 'cancelled'].includes(ride.status);

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-screen bg-gradient-to-b from-gray-50 to-gray-100">
        <div className="text-center">
          <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-600 mx-auto mb-4" />
          <p className="text-gray-500 font-medium">Loading ride details...</p>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex items-center justify-center min-h-screen bg-gradient-to-b from-gray-50 to-gray-100 p-4">
        <div className="bg-white p-8 rounded-2xl shadow-lg max-w-md w-full text-center">
          <div className="text-5xl mb-4">⚠️</div>
          <h1 className="text-2xl font-bold text-gray-800 mb-2">Tracking Unavailable</h1>
          <p className="text-gray-600">{error}</p>
          <p className="text-sm text-gray-400 mt-4">This link may have expired or the ride has ended.</p>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gradient-to-b from-gray-50 to-gray-100 flex flex-col">
      {/* Header */}
      <header className="bg-white shadow-sm px-4 py-3">
        <div className="max-w-lg mx-auto flex items-center justify-between">
          <div>
            <h1 className="text-lg font-bold text-gray-800 flex items-center gap-2">
              <span className="text-xl">📍</span> Spinr Live Tracking
            </h1>
          </div>
          {isActive && (
            <div className="flex items-center gap-1.5">
              <span className="relative flex h-2.5 w-2.5">
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-green-400 opacity-75" />
                <span className="relative inline-flex rounded-full h-2.5 w-2.5 bg-green-500" />
              </span>
              <span className="text-xs font-semibold text-green-600">LIVE</span>
            </div>
          )}
        </div>
      </header>

      <main className="flex-1 max-w-lg w-full mx-auto p-4 flex flex-col gap-4">
        {/* Status Banner */}
        <div
          className="rounded-2xl p-5 shadow-sm"
          style={{ backgroundColor: statusConfig.bg }}
        >
          <div className="flex items-center gap-3">
            <span className="text-3xl">{statusConfig.icon}</span>
            <div>
              <p className="text-xs font-bold tracking-wider uppercase" style={{ color: statusConfig.color }}>
                Current Status
              </p>
              <p className="text-lg font-bold text-gray-800 mt-0.5">{statusConfig.label}</p>
              {ride?.message && (
                <p className="text-sm text-gray-600 mt-1">{ride.message}</p>
              )}
            </div>
          </div>
        </div>

        {/* Driver Location Map Placeholder */}
        {ride?.driver?.lat && ride?.driver?.lng && isActive && (
          <div className="bg-white rounded-2xl shadow-sm overflow-hidden">
            <div className="bg-blue-50 h-48 flex items-center justify-center relative">
              <div className="text-center z-10 p-4 bg-white/90 rounded-xl shadow-sm">
                <span className="text-4xl block mb-2">🚗</span>
                <p className="text-sm font-bold text-gray-800">Driver is on the way</p>
                <p className="text-xs text-gray-500 mt-1 font-mono">
                  {ride.driver.lat.toFixed(5)}, {ride.driver.lng.toFixed(5)}
                </p>
              </div>
            </div>
          </div>
        )}

        {/* Route Info */}
        <div className="bg-white rounded-2xl shadow-sm p-5">
          <h2 className="text-xs font-bold text-gray-400 tracking-wider mb-4">TRIP ROUTE</h2>
          <div className="space-y-4 relative before:absolute before:top-2 before:bottom-2 before:left-[9px] before:w-0.5 before:bg-gray-200">
            <div className="flex gap-3 relative">
              <div className="w-5 h-5 rounded-full bg-green-500 border-[3px] border-white shadow-sm z-10 shrink-0 mt-0.5" />
              <div>
                <p className="text-[10px] font-bold text-gray-400 tracking-wider">PICKUP</p>
                <p className="text-sm font-medium text-gray-800 mt-0.5">{ride?.pickup_address}</p>
              </div>
            </div>
            <div className="flex gap-3 relative">
              <div className="w-5 h-5 rounded-full bg-blue-500 border-[3px] border-white shadow-sm z-10 shrink-0 mt-0.5" />
              <div>
                <p className="text-[10px] font-bold text-gray-400 tracking-wider">DROP-OFF</p>
                <p className="text-sm font-medium text-gray-800 mt-0.5">{ride?.dropoff_address}</p>
              </div>
            </div>
          </div>
        </div>

        {/* Driver Info */}
        {ride?.driver && (
          <div className="bg-white rounded-2xl shadow-sm p-5 flex items-center gap-4">
            <div className="w-14 h-14 bg-blue-100 rounded-full flex items-center justify-center shrink-0">
              <span className="text-2xl">👤</span>
            </div>
            <div>
              <p className="text-[10px] font-bold text-gray-400 tracking-wider">YOUR DRIVER</p>
              <p className="font-bold text-gray-800 mt-0.5">{ride.driver.name}</p>
              {ride.driver.vehicle_make && (
                <p className="text-sm text-gray-500 mt-0.5">
                  {ride.driver.vehicle_color} {ride.driver.vehicle_make} {ride.driver.vehicle_model}
                </p>
              )}
            </div>
          </div>
        )}

        {/* Last Updated */}
        {lastUpdated && isActive && (
          <p className="text-center text-xs text-gray-400">
            Last updated: {lastUpdated.toLocaleTimeString()} · Auto-refreshing every 5s
          </p>
        )}
      </main>

      {/* Footer */}
      <footer className="bg-white border-t py-3 px-4 text-center">
        <p className="text-xs text-gray-400">
          Powered by <span className="font-bold text-gray-600">Spinr</span> · Shared ride tracking
        </p>
      </footer>
    </div>
  );
}
