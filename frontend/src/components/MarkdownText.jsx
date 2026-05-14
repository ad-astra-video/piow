import React from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

export default function MarkdownText({ content, className = '' }) {
  return (
    <ReactMarkdown
      className={`markdown-text ${className}`.trim()}
      remarkPlugins={[remarkGfm]}
    >
      {typeof content === 'string' ? content : ''}
    </ReactMarkdown>
  );
}
