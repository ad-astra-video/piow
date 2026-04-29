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
    start: (accessToken, sourceConfig) => streamManager.start(accessToken, sourceConfig),
    stop: (opts) => streamManager.stop(opts),
  };
}
