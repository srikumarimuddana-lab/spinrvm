import { create } from 'zustand';
import api from '../api/client';
import { useAuthStore } from './authStore';

interface Location {
  address: string;
  lat: number;
  lng: number;
}

interface VehicleType {
  id: string;
  name: string;
  description: string;
  icon: string;
  capacity: number;
}

interface RideEstimate {
  vehicle_type: VehicleType;
  distance_km: number;
  duration_minutes: number;
  base_fare: number;
  distance_fare: number;
  time_fare: number;
  booking_fee: number;
  total_fare: number;
}

interface Driver {
  id: string;
  name: string;
  phone: string;
  photo_url: string;
  vehicle_make: string;
  vehicle_model: string;
  vehicle_color: string;
  license_plate: string;
  rating: number;
  total_rides: number;
  lat: number;
  lng: number;
}

interface Ride {
  id: string;
  rider_id: string;
  driver_id?: string;
  vehicle_type_id: string;
  pickup_address: string;
  pickup_lat: number;
  pickup_lng: number;
  dropoff_address: string;
  dropoff_lat: number;
  dropoff_lng: number;
  distance_km: number;
  duration_minutes: number;
  base_fare: number;
  total_fare: number;
  payment_method: string;
  status: string;
  pickup_otp: string;
  created_at: string;
}

interface SavedAddress {
  id: string;
  user_id: string;
  name: string;
  address: string;
  lat: number;
  lng: number;
  icon: string;
}

interface RideState {
  pickup: Location | null;
  dropoff: Location | null;
  estimates: RideEstimate[];
  selectedVehicle: VehicleType | null;
  currentRide: Ride | null;
  currentDriver: Driver | null;
  savedAddresses: SavedAddress[];
  isLoading: boolean;
  error: string | null;

  // Actions
  setPickup: (location: Location | null) => void;
  setDropoff: (location: Location | null) => void;
  fetchEstimates: () => Promise<void>;
  selectVehicle: (vehicle: VehicleType) => void;
  createRide: (paymentMethod: string) => Promise<Ride>;
  fetchRide: (rideId: string) => Promise<void>;
  cancelRide: () => Promise<void>;
  simulateDriverArrival: () => Promise<void>;
  fetchSavedAddresses: () => Promise<void>;
  addSavedAddress: (address: Omit<SavedAddress, 'id' | 'user_id'>) => Promise<void>;
  deleteSavedAddress: (id: string) => Promise<void>;
  startRide: () => Promise<void>;
  completeRide: () => Promise<Ride | undefined>;
  clearRide: () => void;
  clearError: () => void;
  rateRide: (rideId: string, rating: number, comment?: string, tipAmount?: number) => Promise<void>;
}

export const useRideStore = create<RideState>((set, get) => ({
  pickup: null,
  dropoff: null,
  estimates: [],
  selectedVehicle: null,
  currentRide: null,
  currentDriver: null,
  savedAddresses: [],
  isLoading: false,
  error: null,

  setPickup: (location) => set({ pickup: location }),
  setDropoff: (location) => set({ dropoff: location }),

  fetchEstimates: async () => {
    const { pickup, dropoff } = get();
    if (!pickup || !dropoff) return;

    try {
      set({ isLoading: true, error: null });
      const response = await api.post('/rides/estimate', {
        pickup_lat: pickup.lat,
        pickup_lng: pickup.lng,
        dropoff_lat: dropoff.lat,
        dropoff_lng: dropoff.lng,
      });
      set({ estimates: response.data, isLoading: false });
    } catch (error: any) {
      set({ isLoading: false, error: error.message });
    }
  },

  selectVehicle: (vehicle) => set({ selectedVehicle: vehicle }),

  createRide: async (paymentMethod) => {
    const { pickup, dropoff, selectedVehicle } = get();
    if (!pickup || !dropoff || !selectedVehicle) {
      throw new Error('Missing ride details');
    }

    try {
      set({ isLoading: true, error: null });
      const response = await api.post('/rides', {
        vehicle_type_id: selectedVehicle.id,
        pickup_address: pickup.address,
        pickup_lat: pickup.lat,
        pickup_lng: pickup.lng,
        dropoff_address: dropoff.address,
        dropoff_lat: dropoff.lat,
        dropoff_lng: dropoff.lng,
        payment_method: paymentMethod,
      });
      set({ currentRide: response.data, isLoading: false });
      return response.data;
    } catch (error: any) {
      set({ isLoading: false, error: error.message });
      throw error;
    }
  },

  fetchRide: async (rideId) => {
    try {
      set({ isLoading: true });
      const response = await api.get(`/rides/${rideId}`);
      set({
        currentRide: response.data.ride,
        currentDriver: response.data.driver,
        isLoading: false,
      });
    } catch (error: any) {
      set({ isLoading: false, error: error.message });
    }
  },

  cancelRide: async () => {
    const { currentRide } = get();
    if (!currentRide) return;

    try {
      set({ isLoading: true });
      await api.post(`/rides/${currentRide.id}/cancel`);
      set({ currentRide: null, currentDriver: null, isLoading: false });
    } catch (error: any) {
      set({ isLoading: false, error: error.message });
    }
  },

  simulateDriverArrival: async () => {
    const { currentRide } = get();
    if (!currentRide) return;

    try {
      const response = await api.post(`/rides/${currentRide.id}/simulate-arrival`);
      set({
        currentRide: { ...currentRide, status: 'driver_arrived', pickup_otp: response.data.pickup_otp },
      });
    } catch (error: any) {
      set({ error: error.message });
    }
  },

  startRide: async () => {
    const { currentRide } = get();
    if (!currentRide) return;

    try {
      await api.post(`/rides/${currentRide.id}/start`);
      set({
        currentRide: { ...currentRide, status: 'in_progress' },
      });
    } catch (error: any) {
      set({ error: error.message });
    }
  },

  completeRide: async () => {
    const { currentRide } = get();
    if (!currentRide) return;

    try {
      const response = await api.post(`/rides/${currentRide.id}/complete`);
      set({
        currentRide: response.data,
      });
      return response.data;
    } catch (error: any) {
      set({ error: error.message });
    }
  },

  rateRide: async (rideId: string, rating: number, comment?: string, tipAmount?: number) => {
    try {
      await api.post(`/rides/${rideId}/rate`, {
        rating,
        comment,
        tip_amount: tipAmount || 0,
      });
    } catch (error: any) {
      set({ error: error.message });
      throw error;
    }
  },

  fetchSavedAddresses: async () => {
    try {
      const response = await api.get('/addresses');
      set({ savedAddresses: response.data });
    } catch (error: any) {
      console.log('Error fetching addresses:', error.message);
    }
  },

  addSavedAddress: async (address) => {
    try {
      const response = await api.post('/addresses', address);
      set({ savedAddresses: [...get().savedAddresses, response.data] });
    } catch (error: any) {
      set({ error: error.message });
    }
  },

  deleteSavedAddress: async (id) => {
    try {
      await api.delete(`/addresses/${id}`);
      set({ savedAddresses: get().savedAddresses.filter((a) => a.id !== id) });
    } catch (error: any) {
      set({ error: error.message });
    }
  },

  clearRide: () => set({
    pickup: null,
    dropoff: null,
    estimates: [],
    selectedVehicle: null,
    currentRide: null,
    currentDriver: null,
  }),

  clearError: () => set({ error: null }),
}));
