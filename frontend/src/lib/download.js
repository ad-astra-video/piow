function splitSentences(text) {
  if (!text) return [];
  const matches = text.match(/[^.!?]+[.!?]+/g);
  if (matches) return matches.map((s) => s.trim()).filter(Boolean);
  return [text];
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

function buildText(text) {
  return text || '';
}

function buildSrt(text, durationSeconds) {
  const sentences = splitSentences(text);
  if (sentences.length === 0) return '';
  const segDuration = durationSeconds > 0 ? durationSeconds / sentences.length : 5;
  let out = '';
  sentences.forEach((sentence, i) => {
    const start = i * segDuration;
    const end = (i + 1) * segDuration;
    out += `${i + 1}\n`;
    out += `${formatSrtTime(start)} --> ${formatSrtTime(end)}\n`;
    out += `${sentence}\n\n`;
  });
  return out.trim();
}

function buildWebVtt(text, durationSeconds) {
  const sentences = splitSentences(text);
  if (sentences.length === 0) return 'WEBVTT\n\n';
  const segDuration = durationSeconds > 0 ? durationSeconds / sentences.length : 5;
  let out = 'WEBVTT\n\n';
  sentences.forEach((sentence, i) => {
    const start = i * segDuration;
    const end = (i + 1) * segDuration;
    out += `${formatVttTime(start)} --> ${formatVttTime(end)}\n`;
    out += `${sentence}\n\n`;
  });
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
