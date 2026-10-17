import { useCallback, useEffect, useReducer, useRef } from 'react';
import { parseBatch } from '../api/client';
import { FIELD_KEYS } from '../address/fields';

const STORAGE_KEY = 'jana-address-session';

const initialState = { status: 'idle', fileName: '', records: [], progress: 0, error: null };

function restore() {
  try {
    const saved = JSON.parse(window.localStorage.getItem(STORAGE_KEY));
    if (saved?.records?.length) return { ...initialState, status: 'ready', fileName: saved.fileName, records: saved.records };
  } catch {}
  return initialState;
}

function reducer(state, action) {
  switch (action.type) {
    case 'start':
      return { ...initialState, status: 'parsing', fileName: action.fileName };
    case 'progress':
      return { ...state, progress: action.progress };
    case 'done':
      return { ...state, status: 'ready', progress: 1, records: action.records };
    case 'fail':
      return { ...state, status: 'error', error: action.error };
    case 'save':
      return {
        ...state,
        records: state.records.map((r) => (r.id === action.id ? applyEdit(r, action.values) : r)),
      };
    case 'revert':
      return {
        ...state,
        records: state.records.map((r) => (r.id === action.id && r.original ? { ...r.original } : r)),
      };
    case 'reset':
      return initialState;
    default:
      return state;
  }
}

function applyEdit(record, values) {
  const original = record.original ?? { ...record };
  const next = { ...record, original, reviewed: true, confidence: 1 };
  for (const key of FIELD_KEYS) {
    const value = values[key];
    next[key] = value == null || String(value).trim() === '' ? null : String(value).trim();
  }
  next.edited = FIELD_KEYS.some((key) => next[key] !== original[key]);
  return next;
}

export function useReviewSession() {
  const [state, dispatch] = useReducer(reducer, undefined, restore);
  const abortRef = useRef(null);

  useEffect(() => {
    try {
      if (state.status === 'ready') {
        window.localStorage.setItem(STORAGE_KEY, JSON.stringify({ fileName: state.fileName, records: state.records }));
      } else if (state.status === 'idle') {
        window.localStorage.removeItem(STORAGE_KEY);
      }
    } catch {}
  }, [state.status, state.fileName, state.records]);

  useEffect(() => () => abortRef.current?.abort(), []);

  const run = useCallback(async (inputs, fileName) => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    dispatch({ type: 'start', fileName });
    try {
      const records = await parseBatch(inputs, {
        signal: controller.signal,
        onProgress: (progress) => dispatch({ type: 'progress', progress }),
      });
      if (!controller.signal.aborted) dispatch({ type: 'done', records });
    } catch (error) {
      if (error.name !== 'AbortError') dispatch({ type: 'fail', error: error.message });
    }
  }, []);

  const cancel = useCallback(() => {
    abortRef.current?.abort();
    dispatch({ type: 'reset' });
  }, []);

  return {
    ...state,
    run,
    cancel,
    reset: cancel,
    saveRecord: useCallback((id, values) => dispatch({ type: 'save', id, values }), []),
    revertRecord: useCallback((id) => dispatch({ type: 'revert', id }), []),
  };
}
