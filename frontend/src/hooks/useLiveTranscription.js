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
    addLocalAnnotation: (sentenceIndex, type, content) => streamManager.addLocalAnnotation(sentenceIndex, type, content),
    updateLocalAnnotation: (annotationId, updates) => streamManager.updateLocalAnnotation(annotationId, updates),
    deleteLocalAnnotation: (annotationId) => streamManager.deleteLocalAnnotation(annotationId),
    toggleLocalTodo: (annotationId) => streamManager.toggleLocalTodo(annotationId),
  };
}
