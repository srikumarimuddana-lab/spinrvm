import axios from 'axios';
import api from '@shared/api/client';
import {
  drainTerminalAck,
  TERMINAL_STATUS_CODES,
  type TripLocationBatchAck,
  type TripLocationBatchRequest,
  type TripLocationTransport,
} from './tripLocationRecorder';

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
    // axios's default export IS the right thing to call .isAxiosError() on
    // here; the rule's suggested `import { isAxiosError } from 'axios'`
    // is an equally-valid alternative but not what the rest of the app's
    // axios call sites use — keep the default import consistent.
    // eslint-disable-next-line import/no-named-as-default-member
    if (axios.isAxiosError(error) && TERMINAL_STATUS_CODES.has(error.response?.status ?? 0)) {
      return drainTerminalAck(request);
    }
    throw error;
  }
};
