import api from '@shared/api/client';
import type {
  TripLocationBatchAck,
  TripLocationBatchRequest,
  TripLocationTransport,
} from './tripLocationRecorder';

// Single REST transport for every foreground call site (dashboard interval,
// completion pre-flush). Background/headless code keeps its own fetch-based
// transport because it must refresh tokens outside the Axios interceptor
// chain (see backgroundLocation.ts).
export const apiLocationBatchTransport: TripLocationTransport = async (
  request: TripLocationBatchRequest,
): Promise<TripLocationBatchAck> => {
  const response = await api.post<TripLocationBatchAck>('/drivers/location-batch', request);
  return response.data;
};
