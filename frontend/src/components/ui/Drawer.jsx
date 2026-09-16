import { Icon } from "../icons.jsx";

export function Drawer({ title, description, onClose, children }) {
  return (
    <div className="drawer-overlay" onClick={onClose}>
      <div className="drawer" onClick={(e) => e.stopPropagation()}>
        <div className="drawer-head">
          <div>
            <div className="drawer-title">{title}</div>
            {description && <div className="drawer-desc">{description}</div>}
          </div>
          <button className="modal-close" onClick={onClose} aria-label="Fermer">{Icon.x()}</button>
        </div>
        {children}
      </div>
    </div>
  );
}
