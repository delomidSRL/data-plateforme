import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import * as medallionApi from "../../api/medallion.js";
import { Button } from "../../components/ui/Button.jsx";
import { Icon } from "../../components/icons.jsx";
import MacroPanel from "./MacroPanel.jsx";
import { BUILTIN_MACROS } from "./builtinMacros.js";

function signature(m) {
  return `${m.name}(${(m.parameters || []).map((p) => (p.default ? `${p.name}=${p.default}` : p.name)).join(", ")})`;
}

export default function MacrosTab({ project, readOnly = false }) {
  const { t } = useTranslation();
  const [macros, setMacros] = useState([]);
  const [loading, setLoading] = useState(true);
  const [panelMacro, setPanelMacro] = useState(undefined); // undefined = closed, null = create, object = edit

  const load = () => {
    setLoading(true);
    medallionApi.listMacros(project.id).then(setMacros).finally(() => setLoading(false));
  };

  useEffect(() => { load(); }, [project.id]);

  return (
    <>
      <div className="card" style={{ padding: 14, marginBottom: 14 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 10 }}>
          <div>
            <div style={{ fontWeight: 600, fontSize: 13.5 }}>{t("medallion.macros.tabTitle")}</div>
            <div style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 4 }}>{t("medallion.macros.tabDesc")}</div>
          </div>
          {!readOnly && <Button className="inline" onClick={() => setPanelMacro(null)}>{Icon.plus()} {t("medallion.macros.add")}</Button>}
        </div>
      </div>

      <div style={{ marginBottom: 18 }}>
        <div className="field-label" style={{ marginBottom: 8 }}>{t("medallion.macros.customSection")}</div>
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>{t("medallion.macros.colSignature")}</th>
                <th>{t("medallion.macros.colDescription")}</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {loading && <tr><td colSpan={3} style={{ textAlign: "center", color: "var(--text-muted)" }}>{t("common.loading")}</td></tr>}
              {!loading && macros.length === 0 && (
                <tr><td colSpan={3} style={{ textAlign: "center", color: "var(--text-muted)" }}>{t("medallion.macros.noCustom")}</td></tr>
              )}
              {macros.map((m) => (
                <tr key={m.id}>
                  <td style={{ fontFamily: "var(--font-m)", fontSize: 12 }}>{signature(m)}</td>
                  <td style={{ fontSize: 12.5, color: "var(--text-muted)" }}>{m.description || "—"}</td>
                  <td style={{ textAlign: "right" }}>
                    {!readOnly && (
                      <button type="button" className="btn-icon" onClick={() => setPanelMacro(m)} title={t("common.edit")}>{Icon.edit()}</button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div>
        <div className="field-label" style={{ marginBottom: 8 }}>{t("medallion.macros.builtinSection")}</div>
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>{t("medallion.macros.colSignature")}</th>
                <th>{t("medallion.macros.colDescription")}</th>
              </tr>
            </thead>
            <tbody>
              {BUILTIN_MACROS.map((m) => (
                <tr key={m.name}>
                  <td style={{ fontFamily: "var(--font-m)", fontSize: 12 }}>{m.name}({m.params.join(", ")})</td>
                  <td style={{ fontSize: 12.5, color: "var(--text-muted)" }}>{t(`medallion.macros.builtin_${m.descriptionKey}`)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {panelMacro !== undefined && (
        <MacroPanel
          project={project} macro={panelMacro}
          onClose={() => setPanelMacro(undefined)}
          onSaved={() => { setPanelMacro(undefined); load(); }}
          onDeleted={() => { setPanelMacro(undefined); load(); }}
        />
      )}
    </>
  );
}
