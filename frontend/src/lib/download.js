export function splitSentences(text) {
  if (!text) return [];
  const matches = text.match(/[^.!?]+[.!?]+/g);
  if (matches) return matches.map((s) => s.trim()).filter(Boolean);
  return [text];
}

function normalizeSentenceText(text) {
  if (!text) return '';
  return text.replace(/\s+/g, ' ').trim();
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

function normalizeTimestamp(ts) {
  if (!ts || typeof ts !== 'string') return '';
  return ts.replace(/^\[|\]$/g, '').trim();
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
    const sentenceText = normalizeSentenceText(text.slice(startIdx, endIdx));
    if (sentenceText) {
      sentences.push({
        text: sentenceText,
        timestamp: normalizeTimestamp(matches[i].ts),
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
  return splitSentences(stripTimestamps(text)).map((s) => ({
    text: normalizeSentenceText(s),
    timestamp: '',
    start: null,
    end: null,
  }));
}

export function parseTranscriptSentences(text) {
  return buildTimedSentences(text).map((sentence) => ({
    text: sentence.text,
    timestamp: sentence.timestamp || '',
  }));
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

function countAnnotations(annotationsByIndex = {}) {
  const allAnnotations = Object.values(annotationsByIndex).flat();
  return {
    noteCount: allAnnotations.filter((a) => a.type === 'note').length,
    todoCount: allAnnotations.filter((a) => a.type === 'todo').length,
  };
}

function getSentenceTimestamp(annotation, sentence) {
  return normalizeTimestamp(annotation?.sentence_timestamp) || normalizeTimestamp(sentence?.timestamp);
}

function formatAnnotationSuffix(annotation, sentence) {
  const timestamp = getSentenceTimestamp(annotation, sentence);
  return timestamp ? ` _(${timestamp})_` : '';
}

function formatSentenceHeading(index, sentence) {
  const timestamp = normalizeTimestamp(sentence?.timestamp);
  return timestamp ? `### Sentence ${index + 1} · ${timestamp}\n\n` : `### Sentence ${index + 1}\n\n`;
}

function formatSentenceBody(sentence) {
  return `${sentence?.text || ''}\n\n`;
}

function buildTranscriptOverview(durationSeconds, annotationsByIndex = {}) {
  let out = `- Exported: ${formatDate()}\n`;
  if (durationSeconds > 0) {
    out += `- Duration: ${formatDurationMs(durationSeconds * 1000)}\n`;
  }

  const { noteCount, todoCount } = countAnnotations(annotationsByIndex);
  if (noteCount > 0 || todoCount > 0) {
    out += `- Notes: ${noteCount}\n`;
    out += `- Todos: ${todoCount}\n`;
  }

  return `${out}\n`;
}

function buildAnnotationSection(title, annotations, sentence, isTodo = false) {
  if (annotations.length === 0) return '';

  let out = `#### ${title}\n\n`;
  annotations.forEach((annotation) => {
    if (isTodo) {
      const checkbox = annotation.completed ? '[x]' : '[ ]';
      out += `- ${checkbox} ${annotation.content}${formatAnnotationSuffix(annotation, sentence)}\n`;
      return;
    }

    out += `- ${annotation.content}${formatAnnotationSuffix(annotation, sentence)}\n`;
  });

  return `${out}\n`;
}

/* ============================================================
   Markdown Export
   ============================================================ */

function buildMd(text, annotationsByIndex = {}, durationSeconds = 0) {
  const sentences = parseTranscriptSentences(text);
  if (sentences.length === 0) return '';

  let out = '# Transcript Export\n\n';
  out += buildTranscriptOverview(durationSeconds, annotationsByIndex);
  out += '## Transcript\n\n';

  sentences.forEach((sentence, i) => {
    const annotations = annotationsByIndex[i] || [];

    out += formatSentenceHeading(i, sentence);
    out += formatSentenceBody(sentence);

    if (annotations.length === 0) {
      out += '\n';
      return;
    }

    const notes = annotations.filter((a) => a.type === 'note');
    const todos = annotations.filter((a) => a.type === 'todo');

    out += buildAnnotationSection('Notes', notes, sentence);
    out += buildAnnotationSection('Todos', todos, sentence, true);
  });

  return out.trim();
}

function buildAnnotationsMd(annotationsByIndex = {}, sentences = [], withSentences = true) {
  const indices = Object.keys(annotationsByIndex).map(Number).sort((a, b) => a - b);
  if (indices.length === 0) return '';

  let out = '# Transcript Notes\n\n';
  out += buildTranscriptOverview(0, annotationsByIndex);

  const allAnnotations = indices.flatMap((i) => (annotationsByIndex[i] || []).map((a) => ({ ...a, sentenceIndex: i })));
  const notes = allAnnotations.filter((a) => a.type === 'note');
  const todos = allAnnotations.filter((a) => a.type === 'todo');

  if (withSentences) {
    out += '## Transcript\n\n';
    indices.forEach((i) => {
      const annotations = annotationsByIndex[i] || [];
      if (annotations.length === 0) return;

      const sentence = sentences[i] || { text: '', timestamp: '' };
      out += formatSentenceHeading(i, sentence);
      out += formatSentenceBody(sentence);

      out += buildAnnotationSection('Notes', annotations.filter((a) => a.type === 'note'), sentence);
      out += buildAnnotationSection('Todos', annotations.filter((a) => a.type === 'todo'), sentence, true);
    });
  } else {
    if (notes.length > 0) {
      out += '## Notes\n\n';
      notes.forEach((a) => {
        const sentence = sentences[a.sentenceIndex] || { text: a.sentence_text || '', timestamp: '' };
        out += `- ${a.content}${formatAnnotationSuffix(a, sentence)}\n`;
        if (sentence.text) {
          out += `  ${sentence.text}\n`;
        }
      });
      out += '\n';
    }

    if (todos.length > 0) {
      out += '## Todos\n\n';
      todos.forEach((a) => {
        const sentence = sentences[a.sentenceIndex] || { text: a.sentence_text || '', timestamp: '' };
        const checkbox = a.completed ? '[x]' : '[ ]';
        out += `- ${checkbox} ${a.content}${formatAnnotationSuffix(a, sentence)}\n`;
        if (sentence.text) {
          out += `  ${sentence.text}\n`;
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
  const sentences = parseTranscriptSentences(text);

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
