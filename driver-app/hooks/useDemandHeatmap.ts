import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { AppState } from 'react-native';
import api from '@shared/api/client';

import { publishDemandHeatmap, registerDemandHeatmapPublisher } from './demandHeatmapShared';

export type HeatmapCell = {
  lat: number;
  lng: number;
  weight: number;
};

export type V2Cell = {
  lat: number;
  lng: number;
  live: number;
  baseline: number;
  scheduled: number;
};

export type HeatmapSurge = {
  multiplier: number;
  active: boolean;
};

export type HeatmapLayer = 'blend' | 'live' | 'baseline' | 'scheduled';

export type ForecastEntry = {
  hour: number;
  day_name: string;
  demand: number;
  is_peak: boolean;
};

export type Hotspot = {
  lat: number;
  lng: number;
  intensity: 'high' | 'medium';
};

export type HeatmapStatus =
  // 'idle' = not polling (offline, or on a ride). Distinct from 'loading',
  // which previously stuck forever for offline drivers: nothing ever
  // resolved it because no request was in flight, so every driver who
  // opened the app before going online saw a permanent loading shimmer.
  'idle' | 'loading' | 'ready' | 'empty' | 'stale' | 'error' | 'disabled';

type HeatmapResponse = {
  enabled: boolean;
  points?: number[][];
  cells?: V2Cell[];
  surge?: HeatmapSurge;
  forecast?: ForecastEntry[];
  total_rides?: number;
  refresh_seconds?: number;
  generated_at?: string;
  /** Grid size the server bucketed with; absent on older backends. */
  cell_lat_deg?: number;
  cell_lng_deg?: number;
};

const DEFAULT_REFRESH_SECONDS = 90;
// Bounds mirror the backend clamp in routes/drivers/profile.py.
const MIN_REFRESH_SECONDS = 30;
const MAX_REFRESH_SECONDS = 600;
const STALE_THRESHOLD_MS = 5 * 60 * 1000;
const MAX_CONSECUTIVE_ERRORS = 3;

function v2CellsToWeighted(v2: V2Cell[], layer: HeatmapLayer): HeatmapCell[] {
  return v2
    .map((c) => {
      let w: number;
      if (layer === 'live') w = c.live;
      else if (layer === 'baseline') w = c.baseline;
      else if (layer === 'scheduled') w = c.scheduled;
      else w = Math.max(c.live, c.baseline, c.scheduled);
      return { lat: c.lat, lng: c.lng, weight: w };
    })
    .filter((c) => c.weight > 0);
}

export function useDemandHeatmap(rideState: string, isOnline: boolean) {
  const [cells, setCells] = useState<HeatmapCell[]>([]);
  const [v2Cells, setV2Cells] = useState<V2Cell[]>([]);
  const [surge, setSurge] = useState<HeatmapSurge | null>(null);
  const [layer, setLayer] = useState<HeatmapLayer>('blend');
  const [isV2, setIsV2] = useState(false);
  const [forecast, setForecast] = useState<ForecastEntry[]>([]);
  const [status, setStatus] = useState<HeatmapStatus>('loading');
  const [refreshSeconds, setRefreshSeconds] = useState(DEFAULT_REFRESH_SECONDS);
  // Grid size the SERVER bucketed with. Null until a v2 payload arrives (and
  // on older backends that don't send it), in which case renderers keep their
  // own defaults.
  const [cellLatDeg, setCellLatDeg] = useState<number | null>(null);
  const [cellLngDeg, setCellLngDeg] = useState<number | null>(null);
  const lastFetchRef = useRef<number>(0);
  const errorCountRef = useRef(0);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const clearTimer = useCallback(() => {
    if (timerRef.current) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  const fetchHeatmap = useCallback(async () => {
    try {
      const res = await api.get<HeatmapResponse>('/drivers/demand-heatmap');
      const data = res.data;
      if (!data.enabled) {
        setCells([]);
        setV2Cells([]);
        setSurge(null);
        setForecast([]);
        setIsV2(false);
        setStatus('disabled');
        return;
      }

      // Array.isArray, not length: an empty-but-present cells array is a quiet
      // night under v2, not a v1 response. Testing length made the layer
      // selector and forecast strip vanish whenever demand dropped to zero.
      if (Array.isArray(data.cells)) {
        setV2Cells(data.cells);
        setIsV2(true);
        // Only accept a sane positive number: a null/garbage value must fall
        // back to the renderer's constants, never produce NaN corners (which
        // crash the native Polygon on Android).
        const cLat = data.cell_lat_deg;
        const cLng = data.cell_lng_deg;
        setCellLatDeg(typeof cLat === 'number' && Number.isFinite(cLat) && cLat > 0 ? cLat : null);
        setCellLngDeg(typeof cLng === 'number' && Number.isFinite(cLng) && cLng > 0 ? cLng : null);
        setSurge(data.surge || null);
        setForecast(data.forecast || []);
        const weighted = v2CellsToWeighted(data.cells, layer);
        setCells(weighted);
        setStatus(weighted.length > 0 ? 'ready' : 'empty');
      } else {
        const pts = (data.points || []).map((p: number[]) => ({
          lat: p[0],
          lng: p[1],
          weight: p[2] || 1,
        }));
        setCells(pts);
        setV2Cells([]);
        setIsV2(false);
        setCellLatDeg(null);
        setCellLngDeg(null);
        setSurge(data.surge || null);
        setForecast([]);
        setStatus(pts.length > 0 ? 'ready' : 'empty');
      }

      // Clamp: this value multiplies across every online driver. The backend
      // clamps too, but a stale build or a proxy must not be able to
      // turn the fleet into 1-second pollers.
      if (typeof data.refresh_seconds === 'number' && Number.isFinite(data.refresh_seconds)) {
        setRefreshSeconds(Math.min(MAX_REFRESH_SECONDS, Math.max(MIN_REFRESH_SECONDS, data.refresh_seconds)));
      }
      lastFetchRef.current = Date.now();
      errorCountRef.current = 0;
    } catch {
      errorCountRef.current += 1;
      if (errorCountRef.current >= MAX_CONSECUTIVE_ERRORS) {
        setStatus('error');
      }
    }
  }, [layer]);

  // Re-derive cells from v2 data when layer changes (no re-fetch needed)
  useEffect(() => {
    if (!isV2 || v2Cells.length === 0) return;
    const weighted = v2CellsToWeighted(v2Cells, layer);
    setCells(weighted);
    setStatus(weighted.length > 0 ? 'ready' : 'empty');
  }, [layer, isV2, v2Cells]);

  useEffect(() => {
    const shouldPoll = rideState === 'idle' && isOnline;
    if (!shouldPoll) {
      clearTimer();
      setCells([]);
      setV2Cells([]);
      setForecast([]);
      setSurge(null);
      setIsV2(false);
      setStatus('idle');
      return;
    }

    // Once the server says the feature is off for this area, stop polling
    // entirely — re-asking every 90s for the rest of the shift is pure waste
    // on the driver's battery and data.
    if (status === 'disabled') {
      clearTimer();
      return;
    }

    fetchHeatmap();

    const scheduleNext = () => {
      clearTimer();
      const jitter = 1 + (Math.random() * 0.2 - 0.1);
      const delay = refreshSeconds * 1000 * jitter;
      timerRef.current = setTimeout(() => {
        if (AppState.currentState === 'active') {
          fetchHeatmap().then(scheduleNext);
        } else {
          scheduleNext();
        }
      }, delay);
    };
    scheduleNext();

    return clearTimer;
  }, [rideState, isOnline, refreshSeconds, fetchHeatmap, clearTimer, status]);

  useEffect(() => {
    if (status !== 'ready' && status !== 'empty') return;
    const check = setInterval(() => {
      if (lastFetchRef.current && Date.now() - lastFetchRef.current > STALE_THRESHOLD_MS) {
        setStatus('stale');
      }
    }, 30_000);
    return () => clearInterval(check);
  }, [status]);

  useEffect(() => {
    const sub = AppState.addEventListener('change', (state) => {
      if (state === 'active' && rideState === 'idle' && isOnline) {
        fetchHeatmap();
      }
    });
    return () => sub.remove();
  }, [rideState, isOnline, fetchHeatmap]);

  const hotspots = useMemo<Hotspot[]>(() => {
    if (!isV2 || cells.length === 0) return [];
    const sorted = [...cells].sort((a, b) => b.weight - a.weight);
    return sorted.slice(0, 3).map((c, i) => ({
      lat: c.lat,
      lng: c.lng,
      intensity: i === 0 ? 'high' : 'medium',
    }));
  }, [isV2, cells]);

  // Publish to the shared snapshot so read-only surfaces (the Android Auto
  // car map) mirror this hook instead of running a second poller with a
  // different online signal. Registration is refcounted; when the last
  // publisher unmounts the shared snapshot resets to idle, so the car shows
  // nothing rather than a frozen map that looks live.
  useEffect(() => registerDemandHeatmapPublisher(), []);

  useEffect(() => {
    publishDemandHeatmap({ cells, status, surge, isV2, cellLatDeg, cellLngDeg });
  }, [cells, status, surge, isV2, cellLatDeg, cellLngDeg]);

  return {
    cells,
    status,
    // Hidden when the feature is off for this area, when we aren't polling
    // (offline / on a ride), and while errored — an error pill that persists
    // for the rest of a shift in a rural dead zone is not the "degrade
    // silently" behaviour this feature promised.
    visible: status !== 'disabled' && status !== 'idle' && status !== 'error',
    surge,
    isV2,
    layer,
    setLayer,
    forecast,
    hotspots,
    // Exposed so the effective poll interval (post-clamp) is observable —
    // it drives fleet-wide request volume and must be assertable in tests.
    refreshSeconds,
    // Grid size the server used, so the renderer draws the cells it was sent
    // rather than the size that happened to be the default when it was written.
    cellLatDeg,
    cellLngDeg,
  };
}
