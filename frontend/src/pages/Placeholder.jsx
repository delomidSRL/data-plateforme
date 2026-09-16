export default function Placeholder({ title, desc, icon }) {
  return (
    <>
      <div className="page-head">
        <div>
          <h1 className="page-title">{title}</h1>
          <p className="page-desc">{desc}</p>
        </div>
      </div>
      <div className="placeholder">
        <div className="placeholder-ring">{icon}</div>
        <div className="placeholder-title">Bientôt disponible</div>
        <div className="placeholder-desc">Ce module fait partie des prochaines étapes de la construction (voir la roadmap).</div>
      </div>
    </>
  );
}
