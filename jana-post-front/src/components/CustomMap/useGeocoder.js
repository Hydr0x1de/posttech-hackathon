import { useCallback, useEffect, useRef, useState } from 'react';

export const MIN_QUERY_LENGTH = 3;

export function useGeocoder(provider, { debounceMs = 300 } = {}) {
  const [query, setQuery] = useState('');
  const [result, setResult] = useState({ query: '', suggestions: [], error: null });
  const abortRef = useRef(null);

  const trimmed = query.trim();
  const active = Boolean(provider) && trimmed.length >= MIN_QUERY_LENGTH;

  useEffect(() => {
    if (!active) return undefined;

    const timer = setTimeout(async () => {
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;

      try {
        const suggestions = await provider.search(trimmed, { signal: controller.signal });
        setResult({ query: trimmed, suggestions, error: null });
      } catch (error) {
        if (error.name !== 'AbortError') {
          setResult({ query: trimmed, suggestions: [], error });
        }
      }
    }, debounceMs);

    return () => clearTimeout(timer);
  }, [active, trimmed, provider, debounceMs]);

  useEffect(() => () => abortRef.current?.abort(), []);

  const clear = useCallback(() => {
    abortRef.current?.abort();
    setQuery('');
    setResult({ query: '', suggestions: [], error: null });
  }, []);

  const fresh = active && result.query === trimmed;

  return {
    query,
    setQuery,
    active,
    loading: active && !fresh,
    suggestions: fresh ? result.suggestions : [],
    error: fresh ? result.error : null,
    clear,
  };
}
