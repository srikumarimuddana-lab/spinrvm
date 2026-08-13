import { useState, useEffect, useRef, useCallback } from 'react';
import { AppState } from 'react-native';
import api from '@shared/api/client';

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

export type HeatmapStatus = 'loading' | 'ready' | 'empty' | 'stale' | 'error' | 'disabled';

type HeatmapResponse = {
  enabled: boolean;
  points?: number[][];
  cells?: V2Cell[];
  surge?: HeatmapSurge;
  forecast?: ForecastEntry[];
  total_rides?: number;
  refresh_seconds?: number;
  generated_at?: string;
};

const DEFAULT_REFRESH_SECONDS = 90;
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

      if (data.cells && data.cells.length > 0) {
        setV2Cells(data.cells);
        setIsV2(true);
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
        setSurge(data.surge || null);
        setForecast([]);
        setStatus(pts.length > 0 ? 'ready' : 'empty');
      }

      if (data.refresh_seconds) setRefreshSeconds(data.refresh_seconds);
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
      setStatus('loading');
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
  }, [rideState, isOnline, refreshSeconds, fetchHeatmap, clearTimer]);

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

  return {
    cells,
    status,
    visible: status !== 'disabled',
    surge,
    isV2,
    layer,
    setLayer,
    forecast,
  };
}
