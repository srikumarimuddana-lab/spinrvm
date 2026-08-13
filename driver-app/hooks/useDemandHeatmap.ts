import { useState, useEffect, useRef, useCallback } from 'react';
import { AppState } from 'react-native';
import api from '@shared/api/client';

export type HeatmapCell = {
  lat: number;
  lng: number;
  weight: number;
};

export type HeatmapStatus = 'loading' | 'ready' | 'empty' | 'stale' | 'error' | 'disabled';

type HeatmapResponse = {
  enabled: boolean;
  points?: number[][];
  total_rides?: number;
  refresh_seconds?: number;
  generated_at?: string;
};

const DEFAULT_REFRESH_SECONDS = 90;
const STALE_THRESHOLD_MS = 5 * 60 * 1000;
const MAX_CONSECUTIVE_ERRORS = 3;

export function useDemandHeatmap(rideState: string, isOnline: boolean) {
  const [cells, setCells] = useState<HeatmapCell[]>([]);
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
        setStatus('disabled');
        return;
      }
      const pts = (data.points || []).map((p: number[]) => ({
        lat: p[0],
        lng: p[1],
        weight: p[2] || 1,
      }));
      setCells(pts);
      setStatus(pts.length > 0 ? 'ready' : 'empty');
      if (data.refresh_seconds) setRefreshSeconds(data.refresh_seconds);
      lastFetchRef.current = Date.now();
      errorCountRef.current = 0;
    } catch {
      errorCountRef.current += 1;
      if (errorCountRef.current >= MAX_CONSECUTIVE_ERRORS) {
        setStatus('error');
      }
    }
  }, []);

  useEffect(() => {
    const shouldPoll = rideState === 'idle' && isOnline;
    if (!shouldPoll) {
      clearTimer();
      setCells([]);
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

  return { cells, status, visible: status !== 'disabled' };
}
