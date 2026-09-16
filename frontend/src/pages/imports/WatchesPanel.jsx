import { Fragment, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Badge } from "../../components/ui/Badge.jsx";
import { Icon } from "../../components/icons.jsx";
import { useToast } from "../../context/ToastContext.jsx";
import { useAuth } from "../../context/AuthContext.jsx";
import * as fileWatchApi from "../../api/fileWatch.js";

const STATUS_TONE = { active: "accent", paused: "neutral", error: "danger" };
const OUTCOME_TONE = { imported: "accent", skipped_duplicate: "neutral", drift_rejected: "danger", fetch_error: "danger", absent_sla: "danger" };
const NOTIFIABLE_OUTCOMES = new Set(["absent_sla", "drift_rejected", "fetch_error"]);

function eventDetail(e, t) {
  // §5.5 — journal enrichi : lignes+cast_errors sur imported, message explicite sur
  // drift_rejected/fetch_error.
  if (e.outcome === "imported") {
    const parts = [t("watches.rowsImported", { count: e.rows_imported ?? 0 })];
    if (e.cast_errors && Object.keys(e.cast_errors).length > 0) {
      parts.push(Object.entries(e.cast_errors).map(([col, count]) => t("watches.castErrors", { count, column: col })).join(", "));
    }
    return parts.join(" · ");
  }
  if (e.error) return e.error;
  return null;
}

function EventRow({ watchId, e, t, showToast, onAcked, compact }) {
  const [acking, setAcking] = useState(false);
  const detail = eventDetail(e, t);
  const ackable = NOTIFIABLE_OUTCOMES.has(e.outcome) && !e.acknowledged_at;

  const ack = async () => {
    setAcking(true);
    try {
      const updated = await fileWatchApi.ackWatchEvent(watchId, e.id);
      onAcked(updated);
    } catch (err) {
      showToast(err.message || t("watches.ackFailed"));
    } finally {
      setAcking(false);
    }
  };

  return (
    <div style={{ padding: "7px 10px", borderRadius: 6, background: "var(--bg)" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, fontSize: 12.5 }}>
        <Badge tone={OUTCOME_TONE[e.outcome] || "neutral"}>{t(`watches.outcome.${e.outcome}`, { defaultValue: e.outcome })}</Badge>
        {compact && <span style={{ fontFamily: "var(--font-d)", fontWeight: 600 }}>{compact}</span>}
        {e.file_name && <span style={{ fontFamily: "var(--font-m)" }}>{e.file_name}</span>}
        {!compact && e.file_checksum && <span style={{ fontFamily: "var(--font-m)", color: "var(--text-muted)" }}>{e.file_checksum.slice(0, 10)}…</span>}
        <span style={{ marginLeft: "auto", color: "var(--text-muted)", flexShrink: 0 }}>{new Date(e.detected_at).toLocaleString()}</span>
        {ackable && (
          <button className="btn-ghost" style={{ padding: "3px 8px", fontSize: 11.5, flexShrink: 0 }} disabled={acking} onClick={ack}>
            {acking ? "…" : t("watches.acknowledge")}
          </button>
        )}
        {e.acknowledged_at && <span style={{ fontSize: 11, color: "var(--text-muted)", flexShrink: 0 }}>{t("watches.acknowledged")}</span>}
      </div>
      {detail && (
        <div style={{ fontSize: 11.5, color: e.outcome === "imported" ? "var(--text-muted)" : "var(--danger)", marginTop: 4 }}>{detail}</div>
      )}
    </div>
  );
}

function WatchEvents({ watchId, t, showToast }) {
  const [events, setEvents] = useState(null);
  useEffect(() => { fileWatchApi.listWatchEvents(watchId).then(setEvents).catch(() => setEvents([])); }, [watchId]);

  if (events === null) return <div style={{ padding: "10px 4px", color: "var(--text-muted)", fontSize: 12.5 }}>{t("common.loading")}</div>;
  if (events.length === 0) return <div style={{ padding: "10px 4px", color: "var(--text-muted)", fontSize: 12.5 }}>{t("watches.noEvents")}</div>;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6, padding: "10px 4px" }}>
      {events.map((e) => (
        <EventRow key={e.id} watchId={watchId} e={e} t={t} showToast={showToast}
          onAcked={(updated) => setEvents((list) => list.map((x) => (x.id === updated.id ? updated : x)))} />
      ))}
    </div>
  );
}

function IncidentBanner({ watches, t, showToast, refreshKey, onAcked }) {
  const [incidents, setIncidents] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    Promise.all(watches.map((w) => fileWatchApi.listWatchEvents(w.id).then((evts) => evts.filter((e) => NOTIFIABLE_OUTCOMES.has(e.outcome) && !e.acknowledged_at).map((e) => ({ ...e, watchName: w.name })))))
      .then((groups) => { if (!cancelled) setIncidents(groups.flat().sort((a, b) => new Date(b.detected_at) - new Date(a.detected_at))); })
      .catch(() => { if (!cancelled) setIncidents([]); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [watches.length, refreshKey]);

  if (loading || incidents.length === 0) return null;

  return (
    <div className="card" style={{ padding: 16, marginBottom: 16, borderColor: "rgba(197,61,61,.3)" }}>
      <div className="field-label" style={{ marginBottom: 10 }}>{t("watches.openIncidents", { count: incidents.length })}</div>
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        {incidents.map((e) => (
          <EventRow key={`${e.watch_id}-${e.id}`} watchId={e.watch_id} e={e} t={t} showToast={showToast} compact={e.watchName}
            onAcked={(updated) => { setIncidents((list) => list.filter((x) => x.id !== updated.id)); onAcked(); }} />
        ))}
      </div>
    </div>
  );
}

export default function WatchesPanel() {
  const { t, i18n } = useTranslation();
  const showToast = useToast();
  const { isAdmin } = useAuth();
  const [watches, setWatches] = useState([]);
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState(null);
  const [incidentRefresh, setIncidentRefresh] = useState(0);

  const load = async () => {
    setLoading(true);
    try {
      setWatches(await fileWatchApi.listWatches());
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const toggle = async (w) => {
    try {
      const updated = w.status === "active" ? await fileWatchApi.pauseWatch(w.id) : await fileWatchApi.resumeWatch(w.id);
      setWatches((list) => list.map((x) => (x.id === w.id ? updated : x)));
      if (w.status === "error") showToast(t("watches.reactivatedToast"));
    } catch (err) {
      showToast(err.message || t("watches.updateFailed"));
    }
  };

  const remove = async (w) => {
    try {
      await fileWatchApi.deleteWatch(w.id);
      setWatches((list) => list.filter((x) => x.id !== w.id));
      showToast(t("watches.deletedToast"));
    } catch (err) {
      showToast(err.message || t("watches.deleteFailed"));
    }
  };

  if (loading) return <div style={{ color: "var(--text-muted)" }}>{t("common.loading")}</div>;

  return (
    <div>
      {watches.length > 0 && (
        <IncidentBanner watches={watches} t={t} showToast={showToast} refreshKey={incidentRefresh} onAcked={() => setIncidentRefresh((n) => n + 1)} />
      )}

      {watches.length === 0 ? (
        <div className="card" style={{ padding: 24, textAlign: "center", color: "var(--text-muted)" }}>{t("watches.noWatches")}</div>
      ) : (
      <div className="table-wrap">
      <table className="table">
        <thead>
          <tr>
            <th>{t("watches.colName")}</th><th>{t("watches.colLocation")}</th><th>{t("watches.colPattern")}</th>
            <th>{t("watches.colCadence")}</th><th>{t("common.status")}</th><th>{t("watches.colLastFile")}</th>
            <th>{t("watches.colSla")}</th>
            <th style={{ textAlign: "right" }}>{t("common.actions")}</th>
          </tr>
        </thead>
        <tbody>
          {watches.map((w) => (
            <Fragment key={w.id}>
              <tr>
                <td>
                  <button className="btn-ghost" style={{ padding: "2px 6px", fontFamily: "var(--font-d)", fontWeight: 600 }} onClick={() => setExpanded(expanded === w.id ? null : w.id)}>
                    {w.name}
                  </button>
                </td>
                <td style={{ fontFamily: "var(--font-m)", fontSize: 12 }}>
                  {w.transport === "minio" ? `${w.location.bucket}/${w.location.prefix || ""}` : w.location.path}
                </td>
                <td style={{ fontFamily: "var(--font-m)", fontSize: 12 }}>{w.pattern}</td>
                <td style={{ fontSize: 12.5 }}>{w.poll_interval_seconds}s</td>
                <td>
                  <Badge tone={STATUS_TONE[w.status] || "neutral"}>{t(`watches.status.${w.status}`, { defaultValue: w.status })}</Badge>
                  {w.status === "error" && w.last_error && <div style={{ fontSize: 11, color: "var(--danger)", marginTop: 4, maxWidth: 220 }}>{w.last_error}</div>}
                </td>
                <td style={{ fontFamily: "var(--font-m)", fontSize: 12 }}>{w.last_file || "—"}</td>
                <td style={{ fontSize: 11.5 }}>
                  {w.sla_state ? (
                    <>
                      <Badge tone={w.sla_state === "late" ? "danger" : "neutral"}>{t(`watches.sla.${w.sla_state}`)}</Badge>
                      {w.next_expected_arrival && (
                        <div style={{ color: "var(--text-muted)", marginTop: 4, fontFamily: "var(--font-m)" }}>
                          {t("watches.nextExpected", { date: new Date(w.next_expected_arrival).toLocaleString(i18n.language) })}
                        </div>
                      )}
                    </>
                  ) : "—"}
                </td>
                <td style={{ textAlign: "right", display: "flex", gap: 6, justifyContent: "flex-end" }}>
                  <button className="btn-ghost" style={{ padding: "6px 10px" }} onClick={() => toggle(w)}>
                    {w.status === "active" ? t("watches.pause") : w.status === "error" ? t("watches.reactivate") : t("watches.resume")}
                  </button>
                  {isAdmin && <button className="btn-icon" onClick={() => remove(w)} title={t("common.delete")}>{Icon.trash()}</button>}
                </td>
              </tr>
              {expanded === w.id && (
                <tr>
                  <td colSpan={8} style={{ background: "var(--bg)" }}>
                    <WatchEvents watchId={w.id} t={t} showToast={showToast} />
                  </td>
                </tr>
              )}
            </Fragment>
          ))}
        </tbody>
      </table>
      </div>
      )}
    </div>
  );
}
