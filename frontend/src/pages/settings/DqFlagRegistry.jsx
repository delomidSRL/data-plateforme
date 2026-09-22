import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import * as dqFlagRegistryApi from "../../api/dqFlagRegistry.js";
import { useToast } from "../../context/ToastContext.jsx";
import { Icon } from "../../components/icons.jsx";
import { Badge } from "../../components/ui/Badge.jsx";
import DqFlagRegistryDrawer from "./DqFlagRegistryDrawer.jsx";

export default function DqFlagRegistry() {
  const { t } = useTranslation();
  const showToast = useToast();
  const [entries, setEntries] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [panel, setPanel] = useState(null); // { entry } | { new: true } | null

  const load = async () => {
    setLoading(true);
    try {
      setEntries(await dqFlagRegistryApi.listEntries());
      setError("");
    } catch (err) {
      setError(err.message || t("settings.dqFlagRegistry.loadFailed"));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const onSaved = (e) => {
    setEntries((list) => {
      const exists = list.some((x) => x.id === e.id);
      return exists ? list.map((x) => (x.id === e.id ? e : x)) : [...list, e].sort((a, b) => a.flag_name.localeCompare(b.flag_name));
    });
    setPanel(null);
    showToast(t("settings.dqFlagRegistry.entrySaved"));
  };

  const onDeleted = (id) => {
    setEntries((list) => list.filter((x) => x.id !== id));
    setPanel(null);
    showToast(t("settings.dqFlagRegistry.entryDeleted"));
  };

  return (
    <>
      <div className="page-head">
        <div>
          <div className="badge badge-accent" style={{ marginBottom: 8 }}>{t("settings.badge")}</div>
          <h1 className="page-title">{t("settings.dqFlagRegistry.title")}</h1>
          <p className="page-desc">{t("settings.dqFlagRegistry.subtitle")}</p>
        </div>
        <button className="add-btn" onClick={() => setPanel({ new: true })}>{Icon.plus()}{t("settings.dqFlagRegistry.newEntry")}</button>
      </div>

      {error && <div className="error-banner">{Icon.warn()}<span>{error}</span></div>}

      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr>
              <th>{t("settings.dqFlagRegistry.colName")}</th>
              <th>{t("settings.dqFlagRegistry.colCategory")}</th>
              <th>{t("settings.dqFlagRegistry.colSourceRule")}</th>
            </tr>
          </thead>
          <tbody>
            {loading && <tr><td colSpan={3} style={{ textAlign: "center", color: "var(--text-muted)" }}>{t("common.loading")}</td></tr>}
            {!loading && entries.length === 0 && (
              <tr><td colSpan={3} style={{ textAlign: "center", color: "var(--text-muted)" }}>{t("settings.dqFlagRegistry.noEntries")}</td></tr>
            )}
            {entries.map((e) => (
              <tr key={e.id} style={{ cursor: "pointer" }} onClick={() => setPanel({ entry: e })}>
                <td className="uc-name" style={{ fontFamily: "var(--font-m)", fontSize: 12.5 }}>{e.flag_name}</td>
                <td>
                  <Badge tone={e.category === "elimination" ? "danger" : "accent"}>
                    {t(e.category === "elimination" ? "settings.dqFlagRegistry.categoryElimination" : "settings.dqFlagRegistry.categoryInformative")}
                  </Badge>
                </td>
                <td style={{ fontSize: 12.5, color: "var(--text-muted)" }}>{e.source_rule || "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {panel && (
        <DqFlagRegistryDrawer
          entry={panel.entry}
          onClose={() => setPanel(null)}
          onSaved={onSaved}
          onDeleted={onDeleted}
        />
      )}
    </>
  );
}
