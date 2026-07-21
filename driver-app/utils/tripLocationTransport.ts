import axios from 'axios';
import api from '@shared/api/client';
import type {
  TripLocationBatchAck,
  TripLocationBatchRequest,
  TripLocationTransport,
} from './tripLocationRecorder';

const TERMINAL_STATUS_CODES = new Set([404, 409, 410, 422]);

function drainAck(request: TripLocationBatchRequest): TripLocationBatchAck {
  const lastSequence = request.points[request.points.length - 1]?.sequence_number ?? 0;
  return {
    recording_session_id: request.recording_session_id,
    acked_through: lastSequence,
  };
}

// Single REST transport for every foreground call site (dashboard interval,
// completion pre-flush). Background/headless code keeps its own fetch-based
// transport because it must refresh tokens outside the Axios interceptor
// chain (see backgroundLocation.ts).
export const apiLocationBatchTransport: TripLocationTransport = async (
  request: TripLocationBatchRequest,
): Promise<TripLocationBatchAck> => {
  try {
    const response = await api.post<TripLocationBatchAck>('/drivers/location-batch', request);
    return response.data;
  } catch (error: unknown) {
    if (axios.isAxiosError(error) && TERMINAL_STATUS_CODES.has(error.response?.status ?? 0)) {
      return drainAck(request);
    }
    throw error;
  }
};
