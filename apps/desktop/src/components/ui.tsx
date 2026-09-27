import clsx from "clsx";
import { AnimatePresence, motion } from "framer-motion";
import { Loader2, X } from "lucide-react";
import { type ButtonHTMLAttributes, type ReactNode, useEffect } from "react";
import { scoreColor, STATUS_BADGE, titleCase } from "../lib/format";

type BtnProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "ghost" | "danger" | "default";
  size?: "sm" | "lg";
  icon?: boolean;
  loading?: boolean;
};

export function Button({ variant = "default", size, icon, loading, className, children, disabled, ...rest }: BtnProps) {
  return (
    <button
      className={clsx("btn", variant !== "default" && variant, size, icon && "icon", className)}
      disabled={disabled || loading}
      {...rest}
    >
      {loading ? <Loader2 size={14} className="spin" /> : null}
      {children}
    </button>
  );
}

export function Badge({ children, color, dot }: { children: ReactNode; color?: string; dot?: boolean }) {
  return (
    <span className={clsx("badge", color)}>
      {dot && <span className="dot" />}
      {children}
    </span>
  );
}

export function StatusBadge({ status }: { status: string | null | undefined }) {
  if (!status) return null;
  return (
    <Badge color={STATUS_BADGE[status]} dot={["running", "rendering", "uploading", "processing", "downloading"].includes(status)}>
      {status === status.toUpperCase() ? status : titleCase(status)}
    </Badge>
  );
}

export function Toggle({ on, onChange, disabled }: { on: boolean; onChange: (v: boolean) => void; disabled?: boolean }) {
  return (
    <button
      type="button"
      className={clsx("toggle", on && "on")}
      onClick={() => !disabled && onChange(!on)}
      aria-pressed={on}
      style={disabled ? { opacity: 0.5, cursor: "not-allowed" } : undefined}
    />
  );
}

export function Segmented<T extends string>({ value, options, onChange }: {
  value: T;
  options: { value: T; label: string }[];
  onChange: (v: T) => void;
}) {
  return (
    <div className="seg">
      {options.map((o) => (
        <button key={o.value} className={clsx(o.value === value && "on")} onClick={() => onChange(o.value)} type="button">
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function FieldRow({ label, desc, children }: { label: string; desc?: ReactNode; children: ReactNode }) {
  return (
    <div className="field-row">
      <div className="grow">
        <div className="strong" style={{ fontWeight: 550 }}>{label}</div>
        {desc && <div className="desc">{desc}</div>}
      </div>
      <div className="ctl">{children}</div>
    </div>
  );
}

export function Progress({ value, color, thin }: { value: number; color?: "green" | "yellow" | "red"; thin?: boolean }) {
  return (
    <div className={clsx("bar", color, thin && "thin")}>
      <span style={{ width: `${Math.max(0, Math.min(100, value * 100))}%` }} />
    </div>
  );
}

export function ScoreRing({ score, size = 44, stroke = 4 }: { score: number | null | undefined; size?: number; stroke?: number }) {
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const v = Math.max(0, Math.min(100, score ?? 0));
  return (
    <div className="score-ring" style={{ width: size, height: size }}>
      <svg width={size} height={size}>
        <circle cx={size / 2} cy={size / 2} r={r} stroke="rgba(255,255,255,.08)" strokeWidth={stroke} fill="none" />
        <motion.circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          stroke={scoreColor(score)}
          strokeWidth={stroke}
          fill="none"
          strokeLinecap="round"
          strokeDasharray={c}
          initial={{ strokeDashoffset: c }}
          animate={{ strokeDashoffset: c * (1 - v / 100) }}
          transition={{ duration: 0.9, ease: [0.2, 0.8, 0.2, 1] }}
          transform={`rotate(-90 ${size / 2} ${size / 2})`}
        />
      </svg>
      <span style={{ fontSize: size * 0.3 }}>{score === null || score === undefined ? "—" : Math.round(v)}</span>
    </div>
  );
}

export function Modal({ open, onClose, title, children, footer, wide }: {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  wide?: boolean;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    if (open) window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);
  return (
    <AnimatePresence>
      {open && (
        <motion.div className="modal-backdrop" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
          onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
          <motion.div className={clsx("modal", wide && "wide")} initial={{ opacity: 0, y: 14, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }} exit={{ opacity: 0, y: 8, scale: 0.98 }}
            transition={{ type: "spring", stiffness: 380, damping: 32 }}>
            <div className="modal-header">
              <h2>{title}</h2>
              <Button variant="ghost" icon size="sm" className="right" onClick={onClose} aria-label="Close"><X size={16} /></Button>
            </div>
            <div className="modal-body">{children}</div>
            {footer && <div className="modal-footer">{footer}</div>}
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}

export function Empty({ icon, title, children, action }: { icon: ReactNode; title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="empty">
      <div className="icon">{icon}</div>
      <h3>{title}</h3>
      {children && <div className="muted" style={{ maxWidth: 440, margin: "0 auto" }}>{children}</div>}
      {action && <div style={{ marginTop: 16 }}>{action}</div>}
    </div>
  );
}

export function Skeleton({ h = 16, w = "100%", r }: { h?: number; w?: number | string; r?: number }) {
  return <div className="skeleton" style={{ height: h, width: w, borderRadius: r }} />;
}

export function Stat({ label, value, sub, icon, accent }: { label: string; value: ReactNode; sub?: ReactNode; icon?: ReactNode; accent?: boolean }) {
  return (
    <div className="card stat">
      <div className="stat-label">{icon}{label}</div>
      <div className={clsx("stat-value", accent && "gradient-text")}>{value}</div>
      {sub && <div className="stat-sub">{sub}</div>}
    </div>
  );
}

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="page-header">
      <div>
        <h1>{title}</h1>
        {subtitle && <p>{subtitle}</p>}
      </div>
      {actions && <div className="actions">{actions}</div>}
    </div>
  );
}

const EASE_OUT: [number, number, number, number] = [0.2, 0.8, 0.2, 1];

export const fadeUp = {
  initial: { opacity: 0, y: 8 },
  animate: { opacity: 1, y: 0 },
  transition: { duration: 0.28, ease: EASE_OUT },
};

export function stagger(i: number) {
  return { ...fadeUp, transition: { ...fadeUp.transition, delay: Math.min(i * 0.03, 0.3) } };
}
