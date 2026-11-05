import { afterEach, describe, expect, it, vi } from 'vitest';
import { post, request } from '../src/api';

afterEach(() => vi.restoreAllMocks());

describe('request', () => {
  it('returns decoded JSON for successful responses', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('{"ok":true}', { status: 200 })));
    await expect(request<{ ok: boolean }>('/api/test')).resolves.toEqual({ ok: true });
  });

  it('surfaces API errors instead of hiding failed responses', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('{"error":"denied"}', { status: 400 })));
    await expect(request('/api/test')).rejects.toThrow('denied');
  });

  it('rejects malformed JSON from a successful response', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('not json', { status: 200 })));
    await expect(request('/api/test')).rejects.toThrow('API response was not valid JSON');
  });

  it('sends POST bodies as JSON', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response('{"saved":true}', { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);
    await post('/api/settings', { model: 'local' });
    expect(fetchMock).toHaveBeenCalledWith('/api/settings', expect.objectContaining({
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: '{"model":"local"}',
    }));
  });
});
