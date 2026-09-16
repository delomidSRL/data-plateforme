export function Badge({ tone = "neutral", children }) {
  return <span className={`badge badge-${tone}`}>{children}</span>;
}

export function StatusDot({ color }) {
  return <span className="badge-dot" style={{ background: color }} />;
}
