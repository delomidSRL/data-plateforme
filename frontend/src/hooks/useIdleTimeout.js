import { useEffect, useRef, useState, useCallback } from "react";

const ACTIVITY_EVENTS = ["mousemove", "mousedown", "keydown", "scroll", "touchstart", "wheel"];

/**
 * Watches for user activity and, after `idleMs` of silence, opens a warning
 * window of `warningSeconds` during which any activity cancels the logout.
 * Uses a single 1s interval against a last-activity ref rather than
 * resetting a timer on every mousemove, since mousemove fires far too often
 * for that to be cheap.
 */
export function useIdleTimeout({ idleMs, warningSeconds, onTimeout, enabled }) {
  const [warning, setWarning] = useState(false);
  const [secondsLeft, setSecondsLeft] = useState(warningSeconds);
  const lastActivity = useRef(Date.now());
  const warningStartedAt = useRef(null);

  const handleActivity = useCallback(() => {
    lastActivity.current = Date.now();
    if (warningStartedAt.current !== null) {
      warningStartedAt.current = null;
      setWarning(false);
      setSecondsLeft(warningSeconds);
    }
  }, [warningSeconds]);

  useEffect(() => {
    if (!enabled) return undefined;
    ACTIVITY_EVENTS.forEach((ev) => window.addEventListener(ev, handleActivity));
    return () => ACTIVITY_EVENTS.forEach((ev) => window.removeEventListener(ev, handleActivity));
  }, [enabled, handleActivity]);

  useEffect(() => {
    if (!enabled) return undefined;
    lastActivity.current = Date.now();
    warningStartedAt.current = null;
    setWarning(false);

    const tick = setInterval(() => {
      const now = Date.now();
      if (warningStartedAt.current === null) {
        if (now - lastActivity.current >= idleMs) {
          warningStartedAt.current = now;
          setWarning(true);
          setSecondsLeft(warningSeconds);
        }
        return;
      }
      const remaining = warningSeconds - Math.floor((now - warningStartedAt.current) / 1000);
      if (remaining <= 0) {
        clearInterval(tick);
        onTimeout();
      } else {
        setSecondsLeft(remaining);
      }
    }, 1000);

    return () => clearInterval(tick);
  }, [enabled, idleMs, warningSeconds, onTimeout]);

  const stay = useCallback(() => handleActivity(), [handleActivity]);

  return { warning, secondsLeft, stay };
}
