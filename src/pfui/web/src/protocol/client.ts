import type { ConnectionState, ProtocolEvent, ProtocolResponse } from "./types";

type EventHandler = (data: unknown) => void;
type StateHandler = (state: ConnectionState) => void;
type Pending = {
  resolve: (value: unknown) => void;
  reject: (reason: Error) => void;
  timeout: number;
};

export class ProtocolClient {
  private socket: WebSocket | null = null;
  private pending = new Map<string, Pending>();
  private eventHandlers = new Map<string, Set<EventHandler>>();
  private stateHandlers = new Set<StateHandler>();
  private reconnectTimer: number | null = null;
  private reconnectAttempt = 0;
  private requestCounter = 0;
  private stopped = false;

  connect(): void {
    this.stopped = false;
    this.open();
  }

  close(): void {
    this.stopped = true;
    if (this.reconnectTimer !== null) window.clearTimeout(this.reconnectTimer);
    this.socket?.close();
    this.rejectPending("Connection closed");
  }

  subscribe(event: string, handler: EventHandler): () => void {
    const handlers = this.eventHandlers.get(event) ?? new Set<EventHandler>();
    handlers.add(handler);
    this.eventHandlers.set(event, handlers);
    return () => handlers.delete(handler);
  }

  onState(handler: StateHandler): () => void {
    this.stateHandlers.add(handler);
    return () => this.stateHandlers.delete(handler);
  }

  request<T>(method: string, project?: string, params: Record<string, unknown> = {}, timeoutMs = 30_000): Promise<T> {
    if (!this.socket || this.socket.readyState !== WebSocket.OPEN) {
      return Promise.reject(new Error("PFUI is not connected"));
    }
    const id = `${Date.now()}-${this.requestCounter++}`;
    this.socket.send(JSON.stringify({ id, method, ...(project ? { project } : {}), params }));
    return new Promise<T>((resolve, reject) => {
      const timeout = window.setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`Request timed out: ${method}`));
      }, timeoutMs);
      this.pending.set(id, { resolve: resolve as (value: unknown) => void, reject, timeout });
    });
  }

  private open(): void {
    this.emitState("connecting");
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    this.socket = new WebSocket(`${protocol}//${window.location.host}/ws`);
    this.socket.addEventListener("open", () => {
      this.reconnectAttempt = 0;
      this.emitState("connected");
    });
    this.socket.addEventListener("message", (event) => this.handleMessage(event.data));
    this.socket.addEventListener("close", () => {
      this.emitState("disconnected");
      this.rejectPending("WebSocket disconnected");
      if (!this.stopped) this.scheduleReconnect();
    });
    this.socket.addEventListener("error", () => this.socket?.close());
  }

  private handleMessage(raw: string): void {
    const message = JSON.parse(raw) as ProtocolEvent | ProtocolResponse;
    if ("event" in message) {
      for (const handler of this.eventHandlers.get(message.event) ?? []) handler(message.data);
      return;
    }
    if (!message.id) return;
    const pending = this.pending.get(message.id);
    if (!pending) return;
    window.clearTimeout(pending.timeout);
    this.pending.delete(message.id);
    if (message.ok) pending.resolve(message.result);
    else pending.reject(new Error(message.error?.message ?? "Request failed"));
  }

  private scheduleReconnect(): void {
    const delay = Math.min(10_000, 500 * 2 ** this.reconnectAttempt++);
    this.reconnectTimer = window.setTimeout(() => this.open(), delay);
  }

  private rejectPending(message: string): void {
    for (const pending of this.pending.values()) {
      window.clearTimeout(pending.timeout);
      pending.reject(new Error(message));
    }
    this.pending.clear();
  }

  private emitState(state: ConnectionState): void {
    for (const handler of this.stateHandlers) handler(state);
  }
}
