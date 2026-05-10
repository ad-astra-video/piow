import React, { useState, useRef, useEffect } from 'react';
import { StickyNote, CheckSquare, Square, X, Trash2, Plus } from 'lucide-react';

export default function Sentence({
  index,
  text,
  timestamp,
  transcriptionId,
  annotations = [],
  readOnly = false,
  onCreateAnnotation,
  onUpdateAnnotation,
  onDeleteAnnotation,
  onToggleTodo,
}) {
  const [hovered, setHovered] = useState(false);
  const [editorOpen, setEditorOpen] = useState(null); // 'note' | 'todo' | null
  const [editorContent, setEditorContent] = useState('');
  const [expanded, setExpanded] = useState(false);
  const [editingId, setEditingId] = useState(null);
  const textareaRef = useRef(null);

  const hasNotes = annotations.some((a) => a.type === 'note');
  const hasTodos = annotations.some((a) => a.type === 'todo');
  const incompleteTodos = annotations.filter((a) => a.type === 'todo' && !a.completed);

  useEffect(() => {
    if (editorOpen && textareaRef.current) {
      textareaRef.current.focus();
    }
  }, [editorOpen]);

  const openEditor = (type, initialContent = '', annotationId = null) => {
    setEditorOpen(type);
    setEditorContent(initialContent);
    setEditingId(annotationId);
  };

  const closeEditor = () => {
    setEditorOpen(null);
    setEditorContent('');
    setEditingId(null);
  };

  const handleSave = async () => {
    const content = editorContent.trim();
    if (!content) return;

    if (editingId) {
      await onUpdateAnnotation(editingId, { content });
    } else {
      await onCreateAnnotation(index, text, timestamp, editorOpen, content);
    }
    closeEditor();
  };

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
      e.preventDefault();
      handleSave();
    }
    if (e.key === 'Escape') {
      closeEditor();
    }
  };

  return (
    <div
      className="sentence-wrapper"
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
    >
      <div className="sentence-row">
        {timestamp ? (
          <time className="entry-timestamp-col">[{timestamp}]</time>
        ) : (
          <span className="entry-timestamp-col placeholder" />
        )}
        <p className="entry-text">{text}</p>
        <div className="sentence-actions">
          {!readOnly && (hovered || hasNotes || hasTodos) && (
            <>
              {hasNotes && (
                <button
                  className="sentence-indicator note-indicator"
                  onClick={() => setExpanded((v) => !v)}
                  title="Toggle notes"
                >
                  <StickyNote size={12} />
                </button>
              )}
              {hasTodos && (
                <button
                  className={`sentence-indicator todo-indicator ${incompleteTodos.length === 0 ? 'all-done' : ''}`}
                  onClick={() => setExpanded((v) => !v)}
                  title="Toggle todos"
                >
                  <CheckSquare size={12} />
                  {incompleteTodos.length > 0 && (
                    <span className="todo-badge">{incompleteTodos.length}</span>
                  )}
                </button>
              )}
              {!readOnly && hovered && (
                <>
                  <button
                    className="sentence-action-btn"
                    onClick={() => openEditor('note')}
                    title="Add note"
                  >
                    <Plus size={12} /> <StickyNote size={12} />
                  </button>
                  <button
                    className="sentence-action-btn"
                    onClick={() => openEditor('todo')}
                    title="Add todo"
                  >
                    <Plus size={12} /> <CheckSquare size={12} />
                  </button>
                </>
              )}
            </>
          )}
        </div>
      </div>

      {/* Expanded annotations list */}
      {(expanded || annotations.length > 0) && (
        <div className="sentence-annotations">
          {annotations.map((a) => (
            <div key={a.id} className={`annotation-item annotation-${a.type}`}>
              {a.type === 'note' && (
                <>
                  <StickyNote size={12} className="annotation-icon" />
                  <span className="annotation-content">{a.content}</span>
                </>
              )}
              {a.type === 'todo' && (
                <>
                  <button
                    className="annotation-check"
                    onClick={() => onToggleTodo(a.id)}
                    title={a.completed ? 'Mark incomplete' : 'Mark complete'}
                  >
                    {a.completed ? <CheckSquare size={12} /> : <Square size={12} />}
                  </button>
                  <span className={`annotation-content ${a.completed ? 'completed' : ''}`}>
                    {a.content}
                  </span>
                </>
              )}
              {!readOnly && (
                <div className="annotation-actions">
                  <button
                    className="annotation-action-btn"
                    onClick={() => openEditor(a.type, a.content, a.id)}
                    title="Edit"
                  >
                    <span className="edit-icon">Edit</span>
                  </button>
                  <button
                    className="annotation-action-btn danger"
                    onClick={() => onDeleteAnnotation(a.id)}
                    title="Delete"
                  >
                    <Trash2 size={12} />
                  </button>
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Inline editor */}
      {editorOpen && (
        <div className="sentence-editor panel-glass">
          <div className="sentence-editor-header">
            {editorOpen === 'note' ? (
              <><StickyNote size={14} /> Add Note</>
            ) : (
              <><CheckSquare size={14} /> Add Todo</>
            )}
            <button className="icon-btn-sm" onClick={closeEditor}>
              <X size={14} />
            </button>
          </div>
          <textarea
            ref={textareaRef}
            className="sentence-editor-textarea"
            rows={2}
            placeholder={editorOpen === 'note' ? 'Type your note...' : 'Type your todo...'}
            value={editorContent}
            onChange={(e) => setEditorContent(e.target.value)}
            onKeyDown={handleKeyDown}
          />
          <div className="sentence-editor-footer">
            <span className="editor-hint">Ctrl+Enter to save, Esc to cancel</span>
            <div className="editor-actions">
              <button className="secondary-button" onClick={closeEditor}>
                Cancel
              </button>
              <button className="primary-button" onClick={handleSave} disabled={!editorContent.trim()}>
                Save
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
