// Driver Dashboard Types

export interface IncomingRide {
    ride_id: string;
    pickup_address: string;
    dropoff_address: string;
    pickup_lat: number;
    pickup_lng: number;
    dropoff_lat: number;
    dropoff_lng: number;
    fare: number;
    distance_km?: number;
    duration_minutes?: number;
    rider_name?: string;
    rider_rating?: number;
}

export interface ActiveRide {
    ride: any;
    rider: any;
    vehicle_type: any;
    incentives?: { name: string; bonus_amount: number; incentive_type?: string }[];
    total_bonus?: number;
    quest_hint?: { title: string; progress: string; reward: number } | null;
}

export interface DriverData {
    id?: string;
    is_online?: boolean;
    is_verified?: boolean;
    acceptance_rate?: string;
    total_rides?: string;
    vehicle_make?: string;
    vehicle_model?: string;
    license_plate?: string;
    photo_url?: string;
}

export interface Rider {
    id: string;
    name: string;
    rating?: number;
    photo_url?: string;
    phone?: string;
}

export interface Earnings {
    period?: string;
    total_earnings?: number;
    total_tips?: number;
    total_rides?: number;
    total_distance_km?: number;
    total_duration_minutes?: number;
    average_per_ride?: number;
}

export type RideState =
    | 'idle'
    | 'ride_offered'
    | 'navigating_to_pickup'
    | 'arrived_at_pickup'
    | 'trip_in_progress'
    | 'trip_completed';
