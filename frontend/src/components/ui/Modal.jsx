import { Icon } from "../icons.jsx";

export function Modal({ title, description, onClose, children, maxWidth, large = false }) {
  return (
    <div className="overlay" onClick={onClose}>
      <div className={large ? "modal modal-lg" : "modal"} style={maxWidth ? { maxWidth } : undefined} onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <div>
            <div className="modal-title">{title}</div>
            {description && <div className="modal-desc">{description}</div>}
          </div>
          <button className="modal-close" onClick={onClose} aria-label="Fermer">{Icon.x()}</button>
        </div>
        {children}
      </div>
    </div>
  );
}
