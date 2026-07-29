import { useEffect, useState } from "react";

/** Value that settles `delay` ms after the input stops changing. */
export function useDebounced<T>(value: T, delay = 200): T {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const timer = window.setTimeout(() => setSettled(value), delay);
    return () => window.clearTimeout(timer);
  }, [value, delay]);
  return settled;
}
