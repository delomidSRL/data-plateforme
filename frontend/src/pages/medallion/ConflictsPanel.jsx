import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Editor, DiffEditor } from "@monaco-editor/react";
import "../../lib/monaco.js";
import * as medallionApi from "../../api/medallion.js";
import { Badge } from "../../components/ui/Badge.jsx";
import { Button } from "../../components/ui/Button.jsx";
import { useToast } from "../../context/ToastContext.jsx";

function languageFor(path) {
  if (path.endsWith(".sql")) return "sql";
  if (path.endsWith(".yml") || path.endsWith(".yaml")) return "yaml";
  if (path.endsWith(".md")) return "markdown";
  return "plaintext";
}

const EDITOR_OPTS = { minimap: { enabled: false }, fontFamily: "JetBrains Mono, monospace", fontSize: 12.5, scrollBeyondLastLine: false };

/** Module 19 étape 3 §5.7 — one card per active proposal/conflict. A clean "proposed" merge
 * shows base -> merged as a diff, Accepter/Refuser (discard). An "open" conflict shows the
 * two sides for context (diff) above an editable pane pre-loaded with the marked-up merge —
 * the human edits out the <<<<<<</=======/>>>>>>> markers, then Résoudre submits the result. */
function ConflictCard({ project, conflict, onSettled }) {
  const { t } = useTranslation();
  const showToast = useToast();
  const [busy, setBusy] = useState(false);
  const [draft, setDraft] = useState(conflict.merged_content);
  const lang = languageFor(conflict.path);

  const act = async (fn) => {
    setBusy(true);
    try {
      const out = await fn();
      showToast(t("medallion.conflicts.settled"));
      onSettled(out);
    } catch (err) {
      showToast(err.message || t("medallion.conflicts.actionFailed"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="card" style={{ padding: 16, marginBottom: 14 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 10 }}>
        <span style={{ fontFamily: "var(--font-m)", fontSize: 13, fontWeight: 600 }}>{conflict.path}</span>
        <Badge tone={conflict.status === "open" ? "danger" : "accent"}>
          {conflict.status === "open" ? t("medallion.conflicts.statusOpen") : t("medallion.conflicts.statusProposed")}
        </Badge>
        <span style={{ fontSize: 11, color: "var(--text-muted)" }}>{t("medallion.conflicts.triggeredBy", { generator: conflict.generator, trigger: conflict.trigger })}</span>
      </div>

      {conflict.status === "proposed" ? (
        <>
          <div style={{ fontSize: 11.5, color: "var(--text-muted)", marginBottom: 6 }}>{t("medallion.conflicts.proposedHint")}</div>
          <div style={{ height: 280, border: "1px solid var(--border)", borderRadius: 8, overflow: "hidden" }}>
            <DiffEditor
              height="100%" language={lang} original={conflict.base_content} modified={conflict.merged_content}
              theme="vs" options={{ ...EDITOR_OPTS, readOnly: true, renderSideBySide: true }}
            />
          </div>
          <div className="modal-actions" style={{ marginTop: 12 }}>
            <Button type="button" variant="ghost" disabled={busy} onClick={() => act(() => medallionApi.discardConflict(project.id, conflict.id))}>
              {t("medallion.conflicts.discard")}
            </Button>
            <Button type="button" disabled={busy} onClick={() => act(() => medallionApi.acceptConflict(project.id, conflict.id))}>
              {busy ? t("medallion.conflicts.working") : t("medallion.conflicts.accept")}
            </Button>
          </div>
        </>
      ) : (
        <>
          <div style={{ fontSize: 11.5, color: "var(--text-muted)", marginBottom: 6 }}>{t("medallion.conflicts.openHint")}</div>
          <div style={{ height: 200, border: "1px solid var(--border)", borderRadius: 8, overflow: "hidden", marginBottom: 10 }}>
            <DiffEditor
              height="100%" language={lang} original={conflict.ours_content} modified={conflict.theirs_content}
              theme="vs" options={{ ...EDITOR_OPTS, readOnly: true, renderSideBySide: true }}
            />
          </div>
          <div style={{ fontSize: 11.5, color: "var(--text-muted)", marginBottom: 6 }}>{t("medallion.conflicts.resolveHint")}</div>
          <div style={{ height: 260, border: "1px solid var(--border)", borderRadius: 8, overflow: "hidden" }}>
            <Editor height="100%" language={lang} value={draft} onChange={(v) => setDraft(v ?? "")} theme="vs" options={EDITOR_OPTS} />
          </div>
          <div className="modal-actions" style={{ marginTop: 12 }}>
            <Button type="button" variant="ghost" disabled={busy} onClick={() => act(() => medallionApi.discardConflict(project.id, conflict.id))}>
              {t("medallion.conflicts.discard")}
            </Button>
            <Button type="button" disabled={busy || draft.includes("<<<<<<<")} onClick={() => act(() => medallionApi.resolveConflict(project.id, conflict.id, draft))}>
              {busy ? t("medallion.conflicts.working") : t("medallion.conflicts.resolve")}
            </Button>
          </div>
          {draft.includes("<<<<<<<") && <div style={{ fontSize: 11, color: "var(--danger)", marginTop: 6 }}>{t("medallion.conflicts.markersRemain")}</div>}
        </>
      )}
    </div>
  );
}

export default function ConflictsPanel({ project, conflicts, onSettled }) {
  const { t } = useTranslation();
  if (conflicts.length === 0) {
    return <div className="card" style={{ padding: 20, color: "var(--text-muted)", fontSize: 13, textAlign: "center" }}>{t("medallion.conflicts.none")}</div>;
  }
  return (
    <div>
      {conflicts.map((c) => (
        <ConflictCard key={c.id} project={project} conflict={c} onSettled={onSettled} />
      ))}
    </div>
  );
}
