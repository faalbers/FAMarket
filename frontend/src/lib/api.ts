/** Typed fetch helpers. Every call goes to the FastAPI server at /api. */

export class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function unwrap<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = (await res.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      /* non-JSON error body — keep the status text */
    }
    throw new ApiError(detail, res.status);
  }
  return (await res.json()) as T;
}

export async function get<T>(path: string, params?: Record<string, string | number | undefined>) {
  const qs = params
    ? "?" +
      new URLSearchParams(
        Object.entries(params)
          .filter(([, v]) => v !== undefined && v !== "")
          .map(([k, v]) => [k, String(v)]),
      )
    : "";
  return unwrap<T>(await fetch(`/api${path}${qs}`));
}

export async function post<T>(path: string, body?: unknown) {
  return unwrap<T>(
    await fetch(`/api${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    }),
  );
}

export async function put<T>(path: string, body?: unknown) {
  return unwrap<T>(
    await fetch(`/api${path}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    }),
  );
}

// --- shared response shapes -------------------------------------------------

/** One `config/param_hints.py` entry — the canonical description of a metric. */
export type ParamHint = {
  name: string;
  category: string;
  unit?: string;
  what_it_is?: string;
  how_to_use?: string | string[];
  vs_peers?: string;
};

export type HintRegistry = Record<string, ParamHint>;

export type AnalysisMeta = {
  available: boolean;
  analyzed_at?: string | null;
  prices_as_of?: string | null;
  n_symbols?: number | null;
};
