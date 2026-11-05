import type { ApiErrorBody } from './types';

export async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(url, options);
  const raw = await response.text();
  let data: unknown;
  if (raw) {
    try {
      data = JSON.parse(raw) as unknown;
    } catch {
      if (response.ok) throw new Error('API response was not valid JSON');
      data = { error: raw } satisfies ApiErrorBody;
    }
  } else if (response.status === 204) {
    return undefined as T;
  } else if (response.ok) {
    throw new Error('API response was empty');
  } else {
    data = { error: response.statusText } satisfies ApiErrorBody;
  }
  if (!response.ok) {
    const body = data as ApiErrorBody;
    throw new Error(body.error || response.statusText || `HTTP ${response.status}`);
  }
  return data as T;
}

export function post<T>(url: string, body: unknown): Promise<T> {
  return request<T>(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}
