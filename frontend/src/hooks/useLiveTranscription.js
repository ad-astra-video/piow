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
    start: (accessToken, sourceConfig, translationConfig) => streamManager.start(accessToken, sourceConfig, translationConfig),
    stop: (opts) => streamManager.stop(opts),
    addLocalAnnotation: (sentenceIndex, sentenceTimestamp, type, content) => streamManager.addLocalAnnotation(sentenceIndex, sentenceTimestamp, type, content),
    updateLocalAnnotation: (annotationId, updates) => streamManager.updateLocalAnnotation(annotationId, updates),
    deleteLocalAnnotation: (annotationId) => streamManager.deleteLocalAnnotation(annotationId),
    toggleLocalTodo: (annotationId) => streamManager.toggleLocalTodo(annotationId),
  };
}
