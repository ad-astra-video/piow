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

function buildWordTimeline(segments) {
  if (!Array.isArray(segments) || segments.length === 0) return [];
  const words = [];
  for (const seg of segments) {
    if (seg && Array.isArray(seg.words)) {
      for (const w of seg.words) {
        if (w && typeof w.word === 'string' && typeof w.start === 'number' && typeof w.end === 'number') {
          words.push({ word: w.word, start: w.start, end: w.end });
        }
      }
    }
  }
  return words;
}

function buildTimedSentences(text, segments) {
  const cleanText = stripTimestamps(text);
  const words = buildWordTimeline(segments);

  if (words.length === 0) {
    // Fallback: no word-level timing available
    return splitSentences(cleanText).map((s) => ({ text: s, start: null, end: null }));
  }

  const sentences = [];
  let currentWords = [];

  for (const w of words) {
    currentWords.push(w);
    const cleanWord = w.word.trim();
    if (/[.!?]+$/.test(cleanWord)) {
      if (currentWords.length > 0) {
        sentences.push({
          text: currentWords.map((cw) => cw.word).join(' '),
          start: currentWords[0].start,
          end: currentWords[currentWords.length - 1].end,
        });
        currentWords = [];
      }
    }
  }

  // Remaining words without a sentence terminator
  if (currentWords.length > 0) {
    sentences.push({
      text: currentWords.map((cw) => cw.word).join(' '),
      start: currentWords[0].start,
      end: currentWords[currentWords.length - 1].end,
    });
  }

  return sentences;
}

function buildText(text) {
  return stripTimestamps(text) || '';
}

function buildSrt(text, durationSeconds, segments) {
  const timedSentences = buildTimedSentences(text, segments);
  if (timedSentences.length === 0) return '';

  const segDuration = durationSeconds > 0 ? durationSeconds / timedSentences.length : 5;
  let out = '';

  for (let i = 0; i < timedSentences.length; i++) {
    const sentence = timedSentences[i];
    const next = timedSentences[i + 1];

    let start, end;
    if (sentence.start !== null) {
      start = sentence.start;
      // End at the start of the next sentence, or this sentence's last word end if last
      end = next ? next.start : sentence.end;
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

function buildWebVtt(text, durationSeconds, segments) {
  const timedSentences = buildTimedSentences(text, segments);
  if (timedSentences.length === 0) return 'WEBVTT\n\n';

  const segDuration = durationSeconds > 0 ? durationSeconds / timedSentences.length : 5;
  let out = 'WEBVTT\n\n';

  for (let i = 0; i < timedSentences.length; i++) {
    const sentence = timedSentences[i];
    const next = timedSentences[i + 1];

    let start, end;
    if (sentence.start !== null) {
      start = sentence.start;
      end = next ? next.start : sentence.end;
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
  const segments = item.segments || [];

  switch (format) {
    case 'txt': {
      const content = buildText(text);
      triggerDownload(content, `${baseName}.txt`, 'text/plain');
      break;
    }
    case 'srt': {
      const content = buildSrt(text, duration, segments);
      triggerDownload(content, `${baseName}.srt`, 'text/plain');
      break;
    }
    case 'vtt': {
      const content = buildWebVtt(text, duration, segments);
      triggerDownload(content, `${baseName}.vtt`, 'text/vtt');
      break;
    }
    default:
      break;
  }
}
