import { OverlayToaster, Position } from "@blueprintjs/core";

// Persistent, top-center toasts for ambient status (connection, running tasks).
export const statusToaster = OverlayToaster.create({ position: Position.TOP });
