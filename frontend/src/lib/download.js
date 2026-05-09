function splitSentences(text) {
  if (!text) return [];
  const matches = text.match(/[^.!?]+[.!?]+/g);
  if (matches) return matches.map((s) => s.trim()).filter(Boolean);
  return [text];
}

function stripTimestamps(text) {
  if (!text) return '';
  // Remove [HH:MM:SS] or [HH:MM:SS.sss] patterns that may be embedded
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

  // Use next sentence's timestamp as this sentence's end time
  for (let i = 0; i < sentences.length - 1; i++) {
    sentences[i].end = sentences[i + 1].start;
  }

  return sentences;
}

function buildTimedSentences(text) {
  // Primary: parse timestamps embedded in the text itself
  const textSentences = parseTimestampedSentences(text);
  if (textSentences.length > 0) {
    return textSentences;
  }

  // Fallback: plain text without timestamps
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
      // End at the start of the next sentence, or total duration if this is the last
      end = next ? next.start : (sentence.end ?? (durationSeconds > 0 ? durationSeconds : start + segDuration));
    } else {
      // Fallback: evenly distribute total duration
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

export function downloadTranscription(item, format) {
  const baseName = `transcription_${item.id?.slice(0, 8) || 'export'}`;
  const text = item.text || '';
  const duration = item.duration || 0;

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
    default:
      break;
  }
}
