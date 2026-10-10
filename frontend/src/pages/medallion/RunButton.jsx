import { Icon } from "../../components/icons.jsx";

// Bouton RUN par nœud — partagé par LineageCanvas (React Flow) et ThreeColumnView (cartes
// CSS), les deux rendent indépendamment les mêmes nœuds (voir leurs propres fichiers).
// orange+spinner pendant l'exécution, vert au succès, rouge avec message d'erreur sinon —
// `status` vient du polling temps réel posé dans ProjectDetail.jsx (GET .../node-status).
export const RUN_STATUS_COLOR = { running: "var(--ember)", success: "#2f9e6e", failed: "var(--danger)" };

export function RunButton({ onRun, status, title, style }) {
  const busy = status === "running";
  return (
    <button
      type="button"
      title={title}
      disabled={busy}
      onClick={(e) => { e.stopPropagation(); onRun(); }}
      className={"run-btn" + (status ? ` run-btn-${status}` : "")}
      style={style}
    >
      {busy ? <span className="run-spinner" /> : Icon.play({ width: 10, height: 10 })}
    </button>
  );
}

export function RunErrorBanner({ error }) {
  if (!error) return null;
  return (
    <div
      title={error}
      style={{
        marginTop: 6, fontSize: 10.5, color: "var(--danger)", lineHeight: 1.3,
        overflow: "hidden", textOverflow: "ellipsis", display: "-webkit-box",
        WebkitLineClamp: 2, WebkitBoxOrient: "vertical",
      }}
    >
      {error}
    </div>
  );
}
