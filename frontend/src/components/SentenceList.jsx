import React from 'react';
import Sentence from './Sentence';
import useAnnotations from '../hooks/useAnnotations';

export default function SentenceList({
  transcriptionId,
  sentences,
  readOnly = false,
}) {
  const {
    bySentenceIndex,
    loading,
    create,
    update,
    remove,
    toggleTodo,
  } = useAnnotations(transcriptionId);

  if (!sentences || sentences.length === 0) {
    return null;
  }

  return (
    <div className="sentence-list">
      {sentences.map((sentence, index) => (
        <Sentence
          key={`${transcriptionId}-${index}`}
          index={index}
          text={sentence.text}
          timestamp={sentence.timestamp}
          transcriptionId={transcriptionId}
          annotations={bySentenceIndex[index] || []}
          readOnly={readOnly}
          onCreateAnnotation={create}
          onUpdateAnnotation={update}
          onDeleteAnnotation={remove}
          onToggleTodo={toggleTodo}
        />
      ))}
    </div>
  );
}
