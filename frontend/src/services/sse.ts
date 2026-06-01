export type SSEEventCallback = (event: MessageEvent) => void;
export type SSEErrorCallback = (error: Event) => void;

export class SSEClient {
  private baseUrl: string;
  private eventSource: EventSource | null = null;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private reconnectDelay = 3000;
  private maxReconnectDelay = 30000;
  private eventCallbacks: Map<string, SSEEventCallback[]> = new Map();
  private errorCallbacks: SSEErrorCallback[] = [];
  private isManualClose = false;
  private getToken: (() => string | null) | null = null;

  constructor(url: string, getToken?: () => string | null) {
    this.baseUrl = url;
    this.getToken = getToken ?? null;
  }

  private buildUrl(): string {
    const token = this.getToken?.();
    if (!token) return this.baseUrl;
    const sep = this.baseUrl.includes('?') ? '&' : '?';
    return `${this.baseUrl}${sep}_t=${encodeURIComponent(token)}`;
  }

  connect() {
    if (this.eventSource) return;

    this.isManualClose = false;
    this.eventSource = new EventSource(this.buildUrl());

    this.eventSource.onopen = () => {
      this.reconnectDelay = 3000;
    };

    this.eventSource.onerror = (error) => {
      this.errorCallbacks.forEach((cb) => cb(error));
      this.reconnect();
    };

    this.eventCallbacks.forEach((callbacks, eventName) => {
      callbacks.forEach((cb) => {
        this.eventSource?.addEventListener(eventName, cb);
      });
    });
  }

  on(eventName: string, callback: SSEEventCallback) {
    const callbacks = this.eventCallbacks.get(eventName) || [];
    callbacks.push(callback);
    this.eventCallbacks.set(eventName, callbacks);

    if (this.eventSource) {
      this.eventSource.addEventListener(eventName, callback);
    }
  }

  off(eventName: string, callback: SSEEventCallback) {
    const callbacks = this.eventCallbacks.get(eventName) || [];
    const filtered = callbacks.filter((cb) => cb !== callback);
    this.eventCallbacks.set(eventName, filtered);

    if (this.eventSource) {
      this.eventSource.removeEventListener(eventName, callback);
    }
  }

  onError(callback: SSEErrorCallback) {
    this.errorCallbacks.push(callback);
  }

  offError(callback: SSEErrorCallback) {
    this.errorCallbacks = this.errorCallbacks.filter((cb) => cb !== callback);
  }

  private reconnect() {
    if (this.isManualClose) return;
    this.close(false);

    this.reconnectTimer = setTimeout(() => {
      this.connect();
    }, this.reconnectDelay);

    this.reconnectDelay = Math.min(
      this.reconnectDelay * 1.5,
      this.maxReconnectDelay
    );
  }

  close(manual = true) {
    if (manual) {
      this.isManualClose = true;
      if (this.reconnectTimer) {
        clearTimeout(this.reconnectTimer);
        this.reconnectTimer = null;
      }
    }

    if (this.eventSource) {
      this.eventSource.close();
      this.eventSource = null;
    }
  }
}
