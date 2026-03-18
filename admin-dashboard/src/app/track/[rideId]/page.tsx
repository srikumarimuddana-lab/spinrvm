'use client';

import { useEffect, useState } from 'react';
import { useParams } from 'next/navigation';

interface RideInfo {
  status: string;
  driver_name?: string;
  vehicle_info?: string;
  pickup_address: string;
  dropoff_address: string;
  driver_lat?: number;
  driver_lng?: number;
}

export default function TrackRide() {
  const params = useParams();
  const rideId = params.rideId as string;
  const [ride, setRide] = useState<RideInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    // Polling function to grab ride status
    const fetchRideStatus = async () => {
      try {
        // Assume API endpoint is available on the same host or from env var
        const apiUrl = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000/api';
        const res = await fetch(`${apiUrl}/rides/${rideId}`);
        if (!res.ok) {
          throw new Error('Ride not found');
        }
        const data = await res.json();
        
        // Handle nested response format if necessary
        const rideData = data.ride || data;
        const driverData = data.driver || null;
        
        setRide({
          status: rideData.status,
          pickup_address: rideData.pickup_address,
          dropoff_address: rideData.dropoff_address,
          driver_name: driverData?.name || 'Driver assigned',
          vehicle_info: driverData ? `${driverData.vehicle_color} ${driverData.vehicle_make} ${driverData.vehicle_model}` : '',
          driver_lat: driverData?.lat,
          driver_lng: driverData?.lng
        });
      } catch (err: any) {
        setError(err.message);
      } finally {
        setLoading(false);
      }
    };

    fetchRideStatus();
    // Poll every 5 seconds for live tracking
    const interval = setInterval(fetchRideStatus, 5000);
    return () => clearInterval(interval);
  }, [rideId]);

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-screen bg-gray-100">
        <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-600"></div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex items-center justify-center min-h-screen bg-gray-100 p-4">
        <div className="bg-white p-8 rounded-xl shadow-lg max-w-md w-full text-center">
          <div className="text-red-500 text-5xl mb-4">⚠️</div>
          <h1 className="text-2xl font-bold text-gray-800 mb-2">Tracking Unavailable</h1>
          <p className="text-gray-600">{error}</p>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gray-100 flex flex-col">
      {/* Header */}
      <header className="bg-white shadow-sm p-4 text-center">
        <h1 className="text-xl font-bold text-gray-800 tracking-tight">Spinr Live Tracking</h1>
        <p className="text-sm text-gray-500">Ride #{rideId.substring(0, 8)}</p>
      </header>

      {/* Main Content */}
      <main className="flex-1 max-w-lg w-full mx-auto p-4 flex flex-col gap-4">
        
        {/* Map Placeholder for Live Tracking */}
        <div className="bg-gray-300 rounded-xl h-64 w-full relative overflow-hidden shadow-inner flex items-center justify-center">
          <div className="absolute inset-0 bg-blue-50 opacity-20 puzzle-pattern"></div>
          {ride?.driver_lat && ride?.driver_lng ? (
            <div className="text-center z-10 p-4 bg-white/80 rounded-lg backdrop-blur-sm shadow-sm">
              <span className="text-3xl block mb-2">🚗</span>
              <p className="text-sm font-semibold text-gray-700">Driver Location</p>
              <p className="text-xs text-gray-500">{ride.driver_lat.toFixed(4)}, {ride.driver_lng.toFixed(4)}</p>
            </div>
          ) : (
            <p className="text-gray-500 font-medium">Map loading...</p>
          )}
        </div>

        {/* Status Card */}
        <div className="bg-white rounded-xl shadow-sm p-6">
          <div className="flex items-center justify-between mb-6">
            <h2 className="text-lg font-bold text-gray-800">Trip Status</h2>
            <span className="px-3 py-1 bg-blue-100 text-blue-800 text-xs font-bold rounded-full uppercase tracking-wider">
              {ride?.status.replace('_', ' ')}
            </span>
          </div>

          <div className="space-y-4 relative before:absolute before:inset-y-0 before:left-2.5 before:w-px before:bg-gray-200 ml-1">
            <div className="flex gap-4 relative">
              <div className="w-5 h-5 rounded-full bg-blue-600 border-4 border-white shadow-sm z-10 shrink-0 mt-0.5"></div>
              <div>
                <p className="text-xs font-bold text-gray-400 tracking-wider">PICKUP</p>
                <p className="text-sm font-medium text-gray-800 mt-0.5">{ride?.pickup_address}</p>
              </div>
            </div>
            <div className="flex gap-4 relative">
              <div className="w-5 h-5 rounded-full bg-green-500 border-4 border-white shadow-sm z-10 shrink-0 mt-0.5"></div>
              <div>
                <p className="text-xs font-bold text-gray-400 tracking-wider">DROP-OFF</p>
                <p className="text-sm font-medium text-gray-800 mt-0.5">{ride?.dropoff_address}</p>
              </div>
            </div>
          </div>
        </div>

        {/* Driver Card */}
        {ride?.driver_name && (
          <div className="bg-white rounded-xl shadow-sm p-6 flex items-center gap-4">
            <div className="w-14 h-14 bg-gray-200 rounded-full flex items-center justify-center shrink-0">
              <span className="text-2xl">👤</span>
            </div>
            <div>
              <p className="text-xs font-bold text-gray-400 tracking-wider mb-1">Driver Information</p>
              <p className="font-bold text-gray-800">{ride.driver_name}</p>
              {ride.vehicle_info && (
                <p className="text-sm text-gray-600 mt-0.5">{ride.vehicle_info}</p>
              )}
            </div>
          </div>
        )}
      </main>
    </div>
  );
}
