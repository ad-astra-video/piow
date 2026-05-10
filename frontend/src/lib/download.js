export function splitSentences(text) {
  if (!text) return [];
  const matches = text.match(/[^.!?]+[.!?]+/g);
  if (matches) return matches.map((s) => s.trim()).filter(Boolean);
  return [text];
}

function stripTimestamps(text) {
  if (!text) return '';
  return text.replace(/\[\d{2}:\d{2}:\d{2}(?:\.\d+)?\]\s*/g, '');
}

function pad2(n) {
  return n.toString().padStart(2, '0');
}

function pad3(n) {
  return n.toString().padStart(3, '0');
}

function formatSrtTime(totalSeconds) {
  const hh = Math.floor(totalSeconds / 3600);
  const mm = Math.floor((totalSeconds % 3600) / 60);
  const ss = Math.floor(totalSeconds % 60);
  const ms = Math.floor((totalSeconds % 1) * 1000);
  return `${pad2(hh)}:${pad2(mm)}:${pad2(ss)},${pad3(ms)}`;
}

function formatVttTime(totalSeconds) {
  const hh = Math.floor(totalSeconds / 3600);
  const mm = Math.floor((totalSeconds % 3600) / 60);
  const ss = Math.floor(totalSeconds % 60);
  const ms = Math.floor((totalSeconds % 1) * 1000);
  return `${pad2(hh)}:${pad2(mm)}:${pad2(ss)}.${pad3(ms)}`;
}

function parseTimestampToSeconds(ts) {
  const clean = ts.replace(/^\[|\]$/g, '');
  const [hh, mm, ss] = clean.split(':');
  return (parseInt(hh, 10) || 0) * 3600 + (parseInt(mm, 10) || 0) * 60 + (parseFloat(ss) || 0);
}

function parseTimestampedSentences(text) {
  if (!text) return [];
  const regex = /\[\d{2}:\d{2}:\d{2}(?:\.\d+)?\]/g;
  const matches = [];
  let match;
  while ((match = regex.exec(text)) !== null) {
    matches.push({ index: match.index, ts: match[0] });
  }
  if (matches.length === 0) return [];

  const sentences = [];
  for (let i = 0; i < matches.length; i++) {
    const startIdx = matches[i].index + matches[i].ts.length;
    const endIdx = i + 1 < matches.length ? matches[i + 1].index : text.length;
    const sentenceText = text.slice(startIdx, endIdx).trim();
    if (sentenceText) {
      sentences.push({
        text: sentenceText,
        start: parseTimestampToSeconds(matches[i].ts),
        end: null,
      });
    }
  }

  for (let i = 0; i < sentences.length - 1; i++) {
    sentences[i].end = sentences[i + 1].start;
  }

  return sentences;
}

function buildTimedSentences(text) {
  const textSentences = parseTimestampedSentences(text);
  if (textSentences.length > 0) {
    return textSentences;
  }
  return splitSentences(stripTimestamps(text)).map((s) => ({ text: s, start: null, end: null }));
}

function buildText(text) {
  return stripTimestamps(text) || '';
}

function buildSrt(text, durationSeconds) {
  const timedSentences = buildTimedSentences(text);
  if (timedSentences.length === 0) return '';

  const segDuration = durationSeconds > 0 ? durationSeconds / timedSentences.length : 5;
  let out = '';

  for (let i = 0; i < timedSentences.length; i++) {
    const sentence = timedSentences[i];
    const next = timedSentences[i + 1];

    let start, end;
    if (sentence.start !== null) {
      start = sentence.start;
      end = next ? next.start : (sentence.end ?? (durationSeconds > 0 ? durationSeconds : start + segDuration));
    } else {
      start = i * segDuration;
      end = (i + 1) * segDuration;
    }

    out += `${i + 1}\n`;
    out += `${formatSrtTime(start)} --> ${formatSrtTime(end)}\n`;
    out += `${sentence.text}\n\n`;
  }

  return out.trim();
}

function buildWebVtt(text, durationSeconds) {
  const timedSentences = buildTimedSentences(text);
  if (timedSentences.length === 0) return 'WEBVTT\n\n';

  const segDuration = durationSeconds > 0 ? durationSeconds / timedSentences.length : 5;
  let out = 'WEBVTT\n\n';

  for (let i = 0; i < timedSentences.length; i++) {
    const sentence = timedSentences[i];
    const next = timedSentences[i + 1];

    let start, end;
    if (sentence.start !== null) {
      start = sentence.start;
      end = next ? next.start : (sentence.end ?? (durationSeconds > 0 ? durationSeconds : start + segDuration));
    } else {
      start = i * segDuration;
      end = (i + 1) * segDuration;
    }

    out += `${formatVttTime(start)} --> ${formatVttTime(end)}\n`;
    out += `${sentence.text}\n\n`;
  }

  return out.trim();
}

function formatDurationMs(ms) {
  const totalSeconds = Math.floor(ms / 1000);
  const hh = Math.floor(totalSeconds / 3600);
  const mm = Math.floor((totalSeconds % 3600) / 60);
  const ss = totalSeconds % 60;
  return `${pad2(hh)}:${pad2(mm)}:${pad2(ss)}`;
}

function formatDate() {
  const now = new Date();
  return now.toLocaleDateString('en-US', {
    year: 'numeric',
    month: 'long',
    day: 'numeric',
  });
}

/* ============================================================
   Markdown Export
   ============================================================ */

function buildMd(text, annotationsByIndex = {}, durationSeconds = 0) {
  const sentences = splitSentences(text);
  if (sentences.length === 0) return '';

  let out = '# Transcription\n\n';
  out += `*Exported on ${formatDate()}*\n\n`;

  if (durationSeconds > 0) {
    out += `**Duration:** ${formatDurationMs(durationSeconds * 1000)}\n\n`;
  }

  const annotatedCount = Object.keys(annotationsByIndex).length;
  if (annotatedCount > 0) {
    const noteCount = Object.values(annotationsByIndex).flat().filter((a) => a.type === 'note').length;
    const todoCount = Object.values(annotationsByIndex).flat().filter((a) => a.type === 'todo').length;
    out += `**Annotations:** ${noteCount} note${noteCount !== 1 ? 's' : ''}, ${todoCount} todo${todoCount !== 1 ? 's' : ''}\n\n`;
  }

  out += '---\n\n';

  sentences.forEach((sentence, i) => {
    const annotations = annotationsByIndex[i] || [];
    const hasAnnotations = annotations.length > 0;

    if (hasAnnotations) {
      out += `## Sentence ${i + 1}\n\n`;
    }

    out += `> ${sentence}\n\n`;

    if (hasAnnotations) {
      const notes = annotations.filter((a) => a.type === 'note');
      const todos = annotations.filter((a) => a.type === 'todo');

      if (notes.length > 0) {
        out += '**Notes**\n\n';
        notes.forEach((a) => {
          out += `- ${a.content}\n`;
        });
        out += '\n';
      }

      if (todos.length > 0) {
        out += '**Todos**\n\n';
        todos.forEach((a) => {
          const checkbox = a.completed ? '[x]' : '[ ]';
          out += `- ${checkbox} ${a.content}\n`;
        });
        out += '\n';
      }

      out += '---\n\n';
    }
  });

  return out.trim();
}

function buildAnnotationsMd(annotationsByIndex = {}, sentences = [], withSentences = true) {
  const indices = Object.keys(annotationsByIndex).map(Number).sort((a, b) => a - b);
  if (indices.length === 0) return '';

  let out = '# Notes & Todos\n\n';
  out += `*Exported on ${formatDate()}*\n\n`;

  const allAnnotations = indices.flatMap((i) => (annotationsByIndex[i] || []).map((a) => ({ ...a, sentenceIndex: i })));
  const noteCount = allAnnotations.filter((a) => a.type === 'note').length;
  const todoCount = allAnnotations.filter((a) => a.type === 'todo').length;
  out += `**Summary:** ${noteCount} note${noteCount !== 1 ? 's' : ''}, ${todoCount} todo${todoCount !== 1 ? 's' : ''}\n\n`;
  out += '---\n\n';

  if (withSentences) {
    // Group by sentence, show sentence as quote
    indices.forEach((i) => {
      const annotations = annotationsByIndex[i] || [];
      if (annotations.length === 0) return;

      out += `## Sentence ${i + 1}\n\n`;

      if (sentences[i]) {
        out += `> ${sentences[i]}\n\n`;
      }

      const notes = annotations.filter((a) => a.type === 'note');
      const todos = annotations.filter((a) => a.type === 'todo');

      if (notes.length > 0) {
        out += '**Notes**\n\n';
        notes.forEach((a) => {
          out += `- ${a.content}\n`;
        });
        out += '\n';
      }

      if (todos.length > 0) {
        out += '**Todos**\n\n';
        todos.forEach((a) => {
          const checkbox = a.completed ? '[x]' : '[ ]';
          out += `- ${checkbox} ${a.content}\n`;
        });
        out += '\n';
      }

      out += '---\n\n';
    });
  } else {
    // Flat list without sentence context
    const notes = allAnnotations.filter((a) => a.type === 'note');
    const todos = allAnnotations.filter((a) => a.type === 'todo');

    if (notes.length > 0) {
      out += '## Notes\n\n';
      notes.forEach((a) => {
        out += `- ${a.content}\n`;
        if (sentences[a.sentenceIndex]) {
          out += `  *Sentence ${a.sentenceIndex + 1}*\n`;
        }
      });
      out += '\n';
    }

    if (todos.length > 0) {
      out += '## Todos\n\n';
      todos.forEach((a) => {
        const checkbox = a.completed ? '[x]' : '[ ]';
        out += `- ${checkbox} ${a.content}\n`;
        if (sentences[a.sentenceIndex]) {
          out += `  *Sentence ${a.sentenceIndex + 1}*\n`;
        }
      });
      out += '\n';
    }
  }

  return out.trim();
}

function triggerDownload(content, filename, mimeType) {
  const blob = new Blob([content], { type: mimeType });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

export function downloadTranscription(item, format, annotationsByIndex = {}) {
  const baseName = `transcription_${item.id?.slice(0, 8) || 'export'}`;
  const text = item.text || '';
  const duration = item.duration || 0;
  const sentences = splitSentences(text);

  switch (format) {
    case 'txt': {
      const content = buildText(text);
      triggerDownload(content, `${baseName}.txt`, 'text/plain');
      break;
    }
    case 'srt': {
      const content = buildSrt(text, duration);
      triggerDownload(content, `${baseName}.srt`, 'text/plain');
      break;
    }
    case 'vtt': {
      const content = buildWebVtt(text, duration);
      triggerDownload(content, `${baseName}.vtt`, 'text/vtt');
      break;
    }
    case 'md': {
      const content = buildMd(text, annotationsByIndex, duration);
      triggerDownload(content, `${baseName}.md`, 'text/markdown');
      break;
    }
    case 'notes-md': {
      const content = buildAnnotationsMd(annotationsByIndex, sentences, true);
      triggerDownload(content, `${baseName}_notes.md`, 'text/markdown');
      break;
    }
    case 'annotations': {
      const content = buildAnnotationsMd(annotationsByIndex, sentences, false);
      triggerDownload(content, `${baseName}_annotations.md`, 'text/markdown');
      break;
    }
    default:
      break;
  }
}
