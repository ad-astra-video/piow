import { useSyncExternalStore } from 'react';
import streamManager from '../lib/streamManager';

export default function useLiveTranscription() {
  const state = useSyncExternalStore(
    (callback) => streamManager.subscribe(callback),
    () => streamManager.getState(),
    () => streamManager.getState()
  );

  return {
    ...state,
    start: (accessToken) => streamManager.start(accessToken),
    stop: (opts) => streamManager.stop(opts),
  };
}
