import { useEffect, useState } from "react";

export function useIsMobile() {
  // Lazy initializer instead of a setState call in the effect body below —
  // avoids the extra render-then-correct flash and satisfies
  // react-hooks/set-state-in-effect. Guarded for SSR (no `window` at module
  // eval time); the effect's mql listener keeps this in sync afterward.
  const [isMobile, setIsMobile] = useState(() =>
    typeof window !== "undefined" ? window.innerWidth < 768 : false,
  );

  useEffect(() => {
    const mql = window.matchMedia("(max-width: 768px)");
    const onChange = () => {
      setIsMobile(window.innerWidth < 768);
    };
    mql.addEventListener("change", onChange);
    return () => mql.removeEventListener("change", onChange);
  }, []);

  return isMobile;
}
