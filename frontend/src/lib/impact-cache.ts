/** Bounded, shared requests: aborting a viewer never cancels another consumer. */
export class ImpactCache<T> {
  private entries = new Map<string, Promise<T>>();
  private limit: number;
  constructor(limit: number) { this.limit = limit; }

  get(key: string, loader: () => Promise<T>): Promise<T> {
    const existing = this.entries.get(key);
    if (existing) {
      this.entries.delete(key); this.entries.set(key, existing);
      return existing;
    }
    const pending = Promise.resolve().then(loader).catch(error => {
      if (this.entries.get(key) === pending) this.entries.delete(key);
      throw error;
    });
    this.entries.set(key, pending);
    while (this.entries.size > this.limit) this.entries.delete(this.entries.keys().next().value!);
    return pending;
  }
}

export function awaitImpact<T>(pending: Promise<T>, signal?: AbortSignal): Promise<T> {
  if (!signal) return pending;
  if (signal.aborted) return Promise.reject(new DOMException("Selection changed", "AbortError"));
  return new Promise((resolve, reject) => {
    const aborted = () => reject(new DOMException("Selection changed", "AbortError"));
    signal.addEventListener("abort", aborted, { once: true });
    pending.then(resolve, reject).finally(() => signal.removeEventListener("abort", aborted));
  });
}
