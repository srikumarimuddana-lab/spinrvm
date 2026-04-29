export interface UserProfile {
  id: string;
  phone: string;
  first_name?: string;
  last_name?: string;
  email?: string;
  gender?: string;
  profile_image?: string;
  profile_image_status?: string;
  role: string;
  corporate_account_id?: string;
  created_at: string;
  profile_complete: boolean;
  is_driver: boolean;
  driver_onboarding_status?: string;
  driver_onboarding_detail?: string;
  driver_onboarding_next_screen?: string;
}

export interface Driver {
  id: string;
  user_id?: string;
  name: string;
  phone: string;
  photo_url: string;
  vehicle_type_id: string;
  vehicle_make: string;
  vehicle_model: string;
  vehicle_color: string;
  license_plate: string;
  city?: string;
  license_number?: string;
  license_expiry_date?: string;
  work_eligibility_expiry_date?: string;
  vehicle_year?: number;
  vehicle_vin?: string;
  vehicle_inspection_expiry_date?: string;
  insurance_expiry_date?: string;
  background_check_expiry_date?: string;
  documents: unknown;
  is_verified: boolean;
  rejection_reason?: string;
  submitted_at?: string;
  rating: number;
  total_rides: number;
  /** Coordinate — not money. */
  lat: number;
  /** Coordinate — not money. */
  lng: number;
  is_online: boolean;
  is_available: boolean;
  created_at: string;
}

export interface AuthResponse {
  token: string;
  refresh_token: string;
  user: UserProfile;
  is_new_user: boolean;
  expires_in: number;
  access_expires_at?: string;
  refresh_expires_at?: string;
  csrf_token?: string;
}
