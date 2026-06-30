import { ProtocolClient } from "./client";

class FakeWebSocket extends EventTarget {
  static OPEN = 1;
  static instances: FakeWebSocket[] = [];
  readyState = 0;
  sent: string[] = [];

  constructor(public url: string) {
    super();
    FakeWebSocket.instances.push(this);
  }

  open() {
    this.readyState = FakeWebSocket.OPEN;
    this.dispatchEvent(new Event("open"));
  }

  send(value: string) {
    this.sent.push(value);
  }

  close() {
    this.readyState = 3;
    this.dispatchEvent(new Event("close"));
  }

  receive(value: unknown) {
    this.dispatchEvent(new MessageEvent("message", { data: JSON.stringify(value) }));
  }
}

describe("ProtocolClient", () => {
  beforeEach(() => {
    FakeWebSocket.instances = [];
    vi.stubGlobal("WebSocket", FakeWebSocket);
  });

  afterEach(() => vi.unstubAllGlobals());

  it("correlates responses and emits events", async () => {
    const client = new ProtocolClient();
    const ready = vi.fn();
    client.subscribe("session.ready", ready);
    client.connect();
    const socket = FakeWebSocket.instances[0];
    socket.open();
    socket.receive({ event: "session.ready", data: { projects: [] } });
    expect(ready).toHaveBeenCalledWith({ projects: [] });

    const pending = client.request<{ projects: string[] }>("projects.list");
    const request = JSON.parse(socket.sent[0]);
    socket.receive({ id: request.id, ok: true, result: { projects: ["alpha"] } });
    await expect(pending).resolves.toEqual({ projects: ["alpha"] });
    client.close();
  });

  it("rejects pending requests on disconnect", async () => {
    const client = new ProtocolClient();
    client.connect();
    const socket = FakeWebSocket.instances[0];
    socket.open();
    const pending = client.request("project.get", "alpha");
    socket.close();
    await expect(pending).rejects.toThrow("disconnected");
    client.close();
  });
});
