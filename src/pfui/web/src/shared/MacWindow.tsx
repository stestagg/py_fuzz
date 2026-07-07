import type { ReactNode } from "react";

const DECORATIVE_TONES = ["red", "yellow", "green"] as const;

export function MacLight({ tone, label, active, disabled, onClick }: {
  tone: "red" | "yellow" | "green";
  label: string;
  active: boolean;
  disabled?: boolean;
  onClick: () => void;
}) {
  return <button
    type="button"
    className={`mac-light mac-light-${tone} ${active ? "active" : ""}`}
    title={label}
    aria-label={label}
    aria-pressed={active}
    disabled={disabled}
    onClick={onClick}
  />;
}

export function MacWindow({ title, lights, decorativeLights, badge, pin, children }: {
  title: string;
  lights?: ReactNode;
  decorativeLights?: boolean;
  badge?: ReactNode;
  pin?: ReactNode;
  children?: ReactNode;
}) {
  const lightContent = decorativeLights
    ? DECORATIVE_TONES.map((tone) => <MacLight key={tone} tone={tone} label="" active={false} disabled onClick={() => undefined} />)
    : lights;
  const right = (badge || lightContent) && <>
    {badge && <div className="mac-titlebar-right">{badge}</div>}
    {lightContent && <div className="mac-lights" aria-hidden={decorativeLights || undefined}>{lightContent}</div>}
  </>;
  return <div className="mac-window">
    <div className="mac-titlebar">
      <span className="mac-title">{title}</span>
      {(pin || right) && <div className="mac-titlebar-controls">{pin}{right}</div>}
    </div>
    {children}
  </div>;
}
