import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import * as dbtMacrosApi from "../../api/dbtMacros.js";
import { useToast } from "../../context/ToastContext.jsx";
import { Icon } from "../../components/icons.jsx";
import DbtMacroDrawer from "./DbtMacroDrawer.jsx";
import { BUILTIN_MACROS } from "../medallion/builtinMacros.js";

// The admin writes the whole { % macro name(...) % } block by hand now (no separate
// structured parameters form) — the signature shown here is just extracted from it for
// display, assuming no nested parentheses in the argument list (true of every real macro).
const MACRO_SIGNATURE_RE = /\{%-?\s*macro\s+([a-zA-Z_][a-zA-Z0-9_]*\([^)]*\))/;
function signature(m) {
  return MACRO_SIGNATURE_RE.exec(m.definition || "")?.[1] || `${m.name}(...)`;
}

export default function DbtMacros() {
  const { t } = useTranslation();
  const showToast = useToast();
  const [macros, setMacros] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [panel, setPanel] = useState(null); // { macro } | { new: true } | null

  const load = async () => {
    setLoading(true);
    try {
      setMacros(await dbtMacrosApi.listMacros());
      setError("");
    } catch (err) {
      setError(err.message || t("settings.dbtMacros.loadFailed"));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const onSaved = (m) => {
    setMacros((list) => {
      const exists = list.some((x) => x.id === m.id);
      return exists ? list.map((x) => (x.id === m.id ? m : x)) : [...list, m].sort((a, b) => a.name.localeCompare(b.name));
    });
    setPanel(null);
    showToast(t("settings.dbtMacros.macroSaved"));
  };

  const onDeleted = (id) => {
    setMacros((list) => list.filter((x) => x.id !== id));
    setPanel(null);
    showToast(t("settings.dbtMacros.macroDeleted"));
  };

  return (
    <>
      <div className="page-head">
        <div>
          <div className="badge badge-accent" style={{ marginBottom: 8 }}>{t("settings.badge")}</div>
          <h1 className="page-title">{t("settings.dbtMacros.title")}</h1>
          <p className="page-desc">{t("settings.dbtMacros.subtitle")}</p>
        </div>
        <button className="add-btn" onClick={() => setPanel({ new: true })}>{Icon.plus()}{t("settings.dbtMacros.newMacro")}</button>
      </div>

      {error && <div className="error-banner">{Icon.warn()}<span>{error}</span></div>}

      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr><th>{t("settings.dbtMacros.colName")}</th><th>{t("settings.dbtMacros.colDescription")}</th></tr>
          </thead>
          <tbody>
            {loading && <tr><td colSpan={2} style={{ textAlign: "center", color: "var(--text-muted)" }}>{t("common.loading")}</td></tr>}
            {!loading && macros.length === 0 && (
              <tr><td colSpan={2} style={{ textAlign: "center", color: "var(--text-muted)" }}>{t("settings.dbtMacros.noMacros")}</td></tr>
            )}
            {macros.map((m) => (
              <tr key={m.id} style={{ cursor: "pointer" }} onClick={() => setPanel({ macro: m })}>
                <td className="uc-name" style={{ fontFamily: "var(--font-m)", fontSize: 12.5 }}>{signature(m)}</td>
                <td style={{ fontSize: 12.5, color: "var(--text-muted)" }}>{m.description || "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h2 style={{ fontSize: 13.5, fontWeight: 600, margin: "22px 0 10px" }}>{t("settings.dbtMacros.builtinTitle")}</h2>
      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr><th>{t("settings.dbtMacros.colName")}</th><th>{t("settings.dbtMacros.colDescription")}</th></tr>
          </thead>
          <tbody>
            {BUILTIN_MACROS.map((m) => (
              <tr key={m.name}>
                <td style={{ fontFamily: "var(--font-m)", fontSize: 12.5 }}>{m.name}({m.params.join(", ")})</td>
                <td style={{ fontSize: 12.5, color: "var(--text-muted)" }}>{t(`medallion.macros.builtin_${m.descriptionKey}`)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {panel && (
        <DbtMacroDrawer
          macro={panel.macro}
          onClose={() => setPanel(null)}
          onSaved={onSaved}
          onDeleted={onDeleted}
        />
      )}
    </>
  );
}
