import { useEffect, useRef, useState } from "react";

// Debounced server search with a stale-response guard: only the latest
// query's results are ever applied, and superseded requests are aborted.
export function useSearch(fetcher, query, { minLength = 2, delay = 300 } = {}) {
  const [results, setResults] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const seq = useRef(0);
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  useEffect(() => {
    const term = (query || "").trim();
    const id = ++seq.current;
    if (term.length < minLength) {
      setResults([]);
      setLoading(false);
      setError("");
      return undefined;
    }
    const controller = new AbortController();
    setLoading(true);
    const timer = setTimeout(async () => {
      try {
        const data = await fetcherRef.current(term, controller.signal);
        if (id === seq.current) {
          setResults(Array.isArray(data) ? data : []);
          setError("");
        }
      } catch (err) {
        if (err?.name === "AbortError") return;
        if (id === seq.current) {
          setResults([]);
          setError(err?.message || "Search failed");
        }
      } finally {
        if (id === seq.current) setLoading(false);
      }
    }, delay);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [query, minLength, delay]);

  return { results, loading, error };
}
