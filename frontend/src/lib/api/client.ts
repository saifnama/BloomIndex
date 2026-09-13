import axios from 'axios';

/**
 * Extracts error details from API responses.
 *
 * Handles Blob response payloads by decoding raw text to access
 * structured JSON error bodies.
 */
export async function extractErrorDetail(
  err: unknown,
  fallback: string,
): Promise<string> {
  const data =
    typeof err === 'object' && err !== null && 'response' in err
      ? (err as { response?: { data?: unknown } }).response?.data
      : undefined;
  if (!data) return errorMessage(err, fallback);
  if (typeof data === 'object' && !(data instanceof Blob)) {
    return (data as { detail?: string }).detail || fallback;
  }
  try {
    const text = typeof data === 'string' ? data : await (data as Blob).text();
    return (JSON.parse(text) as { detail?: string }).detail || text || fallback;
  } catch {
    return fallback;
  }
}

/**
 * Resolves a human-readable message from thrown exceptions, defaulting
 * to fallback if undefined or empty.
 */
export function errorMessage(err: unknown, fallback: string): string {
  if (typeof err === 'object' && err !== null && 'message' in err) {
    const message = (err as { message?: unknown }).message;
    if (typeof message === 'string' && message) return message;
  }
  return fallback;
}

// Relies on Vite proxy in development and same-origin in production.
const API_BASE = '';

export const api = axios.create({
  baseURL: API_BASE,
  timeout: 600000,
  withCredentials: true,
});

export default api;
