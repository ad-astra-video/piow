import { useState, useEffect, useCallback } from 'react';
import { api } from '../lib/api';

export default function useAnnotations(transcriptionId) {
  const [annotations, setAnnotations] = useState([]);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    if (!transcriptionId) return;
    setLoading(true);
    try {
      const res = await api.getAnnotations(transcriptionId);
      setAnnotations(res.annotations || []);
    } catch (e) {
      console.error('Failed to load annotations:', e);
    } finally {
      setLoading(false);
    }
  }, [transcriptionId]);

  useEffect(() => { load(); }, [load]);

  const create = useCallback(async (sentenceIndex, sentenceText, sentenceTimestamp, type, content) => {
    const res = await api.createAnnotation(transcriptionId, {
      sentence_index: sentenceIndex,
      sentence_text: sentenceText,
      sentence_timestamp: sentenceTimestamp,
      type,
      content,
    });
    setAnnotations((prev) => [...prev, res]);
    return res;
  }, [transcriptionId]);

  const update = useCallback(async (annotationId, updates) => {
    const res = await api.updateAnnotation(annotationId, updates);
    setAnnotations((prev) => prev.map((a) => (a.id === annotationId ? res : a)));
    return res;
  }, []);

  const remove = useCallback(async (annotationId) => {
    await api.deleteAnnotation(annotationId);
    setAnnotations((prev) => prev.filter((a) => a.id !== annotationId));
  }, []);

  const toggleTodo = useCallback(async (annotationId) => {
    const annotation = annotations.find((a) => a.id === annotationId);
    if (!annotation) return;
    return update(annotationId, { completed: !annotation.completed });
  }, [annotations, update]);

  const bySentenceIndex = annotations.reduce((acc, a) => {
    acc[a.sentence_index] = acc[a.sentence_index] || [];
    acc[a.sentence_index].push(a);
    return acc;
  }, {});

  return { annotations, bySentenceIndex, loading, create, update, remove, toggleTodo, refresh: load };
}
