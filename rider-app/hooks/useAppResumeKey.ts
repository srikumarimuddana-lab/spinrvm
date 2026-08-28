import { useEffect, useRef, useState } from 'react';
import { AppState } from 'react-native';

export function useAppResumeKey(): number {
  const [key, setKey] = useState(0);
  const appStateRef = useRef(AppState.currentState);

  useEffect(() => {
    const sub = AppState.addEventListener('change', (nextState) => {
      if (appStateRef.current.match(/inactive|background/) && nextState === 'active') {
        setKey(k => k + 1);
      }
      appStateRef.current = nextState;
    });
    return () => sub.remove();
  }, []);

  return key;
}
