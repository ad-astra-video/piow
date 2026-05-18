import React, { useEffect, useMemo, useState } from 'react';
import { Trash2, Mic, Upload, Link as LinkIcon, Globe, Clock, Search, Filter, X, Download } from 'lucide-react';
import { api } from '../lib/api';
import { downloadTranscription } from '../lib/download';
import SentenceList from '../components/SentenceList';
import { splitSentences, parseTranscriptSentences } from '../lib/download';

function parseAnalysisDisplay(rawText) {
  if (typeof rawText !== 'string') {
    return { mode: 'text', text: '' };
  }

  const trimmed = rawText.trim();
  if (!trimmed) {
    return { mode: 'text', text: '' };
  }

  try {
    const parsed = JSON.parse(trimmed);
    if (parsed && typeof parsed === 'object' && Array.isArray(parsed.items)) {
      const rows = parsed.items
        .map((row) => {
          if (!row || typeof row !== 'object') return null;
          const itemText = typeof row.item === 'string' ? row.item.trim() : '';
          if (!itemText) return null;
          const category = typeof row.category === 'string' ? row.category.trim() : '';
          const priority = typeof row.priority === 'string' ? row.priority.trim() : '';
          return {
            category,
            item: itemText,
            priority,
          };
        })
        .filter(Boolean);

      if (rows.length > 0) {
        return { mode: 'items', items: rows };
      }
    }

    return { mode: 'json', text: JSON.stringify(parsed, null, 2) };
  } catch (_err) {
    return { mode: 'text', text: trimmed };
  }
}

export default function HistoryPage() {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState('transcription');
  const [search, setSearch] = useState('');
  const [cardViewById, setCardViewById] = useState({});
  const [cardAnalysisPreviewById, setCardAnalysisPreviewById] = useState({});
  const [cardAnalysisLoadingById, setCardAnalysisLoadingById] = useState({});
  const [cardAnalysisErrorById, setCardAnalysisErrorById] = useState({});
  const [modalItem, setModalItem] = useState(null);
  const [modalStreamTab, setModalStreamTab] = useState('transcription');
  const [modalSentences, setModalSentences] = useState(null); // null = not loaded yet
  const [modalTranslationsByLanguage, setModalTranslationsByLanguage] = useState({});
  const [activeModalLanguage, setActiveModalLanguage] = useState(null);
  const [showModalTranscript, setShowModalTranscript] = useState(true);
  const [showModalTranslation, setShowModalTranslation] = useState(false);
  const [modalAnalysisEntries, setModalAnalysisEntries] = useState([]);
  const [modalAnalysisLoading, setModalAnalysisLoading] = useState(false);
  const [modalAnalysisError, setModalAnalysisError] = useState('');

  const load = async () => {
    setLoading(true);
    try {
      const params = { limit: 100 };
      params.type = 'transcription';
      const res = await api.getHistory(params);
      setItems((res.items || []).filter((item) => item._type === 'transcription'));
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, [filter]);

  const handleDownload = async (item, format) => {
    let annotationsByIndex = {};
    if (item._type === 'transcription' && item.id && (format === 'md' || format === 'notes-md' || format === 'annotations')) {
      try {
        const streamId = item.stream_session_id || item.stream_id || item.id;
        const res = await api.getAnnotations(streamId);
        annotationsByIndex = (res.annotations || []).reduce((acc, a) => {
          acc[a.sentence_index] = acc[a.sentence_index] || [];
          acc[a.sentence_index].push(a);
          return acc;
        }, {});
      } catch (e) {
        console.error('Failed to load annotations for download:', e);
      }
    }
    downloadTranscription(item, format, annotationsByIndex);
  };

  const handleDelete = async (item) => {
    if (!confirm('Delete this item?')) return;
    try {
      const streamId = item.stream_session_id || item.stream_id || item.id;
      await api.deleteStream(streamId);
      load();
    } catch (e) {
      alert('Failed to delete: ' + e.message);
    }
  };

  const openModal = async (item) => {
    setModalItem(item);
    setModalStreamTab('transcription');
    setModalSentences(null);
    setModalTranslationsByLanguage({});
    setActiveModalLanguage(null);
    setShowModalTranscript(true);
    setShowModalTranslation(false);
    setModalAnalysisEntries([]);
    setModalAnalysisError('');
    setModalAnalysisLoading(true);

    const streamId = item.stream_session_id || item.stream_id || item.id;

    const [sentencesResult, analysisResult] = await Promise.allSettled([
      api.getSentences(streamId),
      api.getStreamAnalysis(streamId),
    ]);

    if (sentencesResult.status === 'fulfilled') {
      const res = sentencesResult.value || {};
      setModalSentences(res.sentences || null);
      setModalTranslationsByLanguage(res.translations_by_language || {});
      const translatedLanguages = res.translated_languages || [];
      if (translatedLanguages.length > 0) {
        setActiveModalLanguage(translatedLanguages[0]);
        setShowModalTranslation(true);
      }
    } else {
      setModalSentences(null); // fall back to parsing item.text
    }

    if (analysisResult.status === 'fulfilled') {
      const analysisRows = Array.isArray(analysisResult.value?.analysis)
        ? analysisResult.value.analysis
        : [];
      setModalAnalysisEntries(
        analysisRows.filter((entry) => typeof entry?.summary_text === 'string' && entry.summary_text.trim())
      );
      setModalAnalysisError('');
    } else {
      setModalAnalysisEntries([]);
      setModalAnalysisError('Could not load analysis summaries for this stream.');
    }
    setModalAnalysisLoading(false);
  };

  const closeModal = () => {
    setModalItem(null);
    setModalStreamTab('transcription');
    setModalSentences(null);
    setModalTranslationsByLanguage({});
    setActiveModalLanguage(null);
    setShowModalTranscript(true);
    setShowModalTranslation(false);
    setModalAnalysisEntries([]);
    setModalAnalysisError('');
    setModalAnalysisLoading(false);
  };

  const filtered = useMemo(() => items.filter((item) => {
    if (filter === 'analysis' && !item.has_analysis) return false;
    if (!search) return true;
    const q = search.toLowerCase();
    const text = (item.text || item.original_text || '').toLowerCase();
    return text.includes(q);
  }), [items, filter, search]);

  const formatDate = (d) => new Date(d).toLocaleString();
  const formatDuration = (s) => s ? `${Math.floor(s / 60)}m ${s % 60}s` : '';

  const getCardId = (item) => String(item.stream_session_id || item.stream_id || item.id);

  const getCardView = (item) => {
    const cardId = getCardId(item);
    if (cardViewById[cardId]) return cardViewById[cardId];
    if (filter === 'analysis' && item.has_analysis) return 'analysis';
    return 'transcription';
  };

  const setCardView = (item, view) => {
    if (view === 'analysis' && !item.has_analysis) return;

    const cardId = getCardId(item);
    setCardViewById((current) => ({
      ...current,
      [cardId]: view,
    }));

    if (view === 'analysis') {
      loadAnalysisPreviewsForItems([item], { force: true });
    }
  };

  const loadAnalysisPreviewsForItems = async (itemsToLoad, options = {}) => {
    const force = options.force === true;
    const requests = [];

    for (const item of itemsToLoad || []) {
      if (!item?.has_analysis) continue;
      const cardId = getCardId(item);
      const streamId = item.stream_session_id || item.stream_id || item.id;
      if (!streamId) continue;

      const hasExisting = Boolean(item.analysis_summary_text || cardAnalysisPreviewById[cardId]);
      if (!force && hasExisting) continue;
      if (cardAnalysisLoadingById[cardId]) continue;

      requests.push({ cardId, streamId: String(streamId) });
    }

    if (requests.length === 0) return;

    const cardIds = requests.map((r) => r.cardId);
    const streamIds = [...new Set(requests.map((r) => r.streamId))];

    setCardAnalysisLoadingById((current) => {
      const next = { ...current };
      cardIds.forEach((id) => { next[id] = true; });
      return next;
    });
    setCardAnalysisErrorById((current) => {
      const next = { ...current };
      cardIds.forEach((id) => { next[id] = ''; });
      return next;
    });

    try {
      const res = await api.getHistoryAnalysisPreviews(streamIds);
      const previews = res?.previews || {};
      setCardAnalysisPreviewById((current) => {
        const next = { ...current };
        requests.forEach(({ cardId, streamId }) => {
          next[cardId] = previews?.[streamId]?.summary_text || '';
        });
        return next;
      });
    } catch (_err) {
      setCardAnalysisErrorById((current) => {
        const next = { ...current };
        cardIds.forEach((id) => { next[id] = 'Could not load analysis preview.'; });
        return next;
      });
    } finally {
      setCardAnalysisLoadingById((current) => {
        const next = { ...current };
        cardIds.forEach((id) => { next[id] = false; });
        return next;
      });
    }
  };

  useEffect(() => {
    loadAnalysisPreviewsForItems(filtered);
  }, [filtered]);

  const getModalSentencesForLanguage = () => {
    const baseSentences = modalSentences !== null
      ? modalSentences
      : parseTranscriptSentences(modalItem?.text || '').map((s, sentenceIndex) => ({
        sentence_index: sentenceIndex,
        text: s.text,
        timestamp: s.timestamp,
      }));

    const languageRows = activeModalLanguage
      ? modalTranslationsByLanguage[activeModalLanguage] || []
      : [];
    const byIndex = (languageRows || []).reduce((acc, row) => {
      acc[row.sentence_index] = row.translated_text;
      return acc;
    }, {});

    return baseSentences.map((s, fallbackIndex) => {
      const sentenceIndex = s.sentence_index ?? fallbackIndex;
      const translatedText = byIndex[sentenceIndex];

      if (showModalTranscript && showModalTranslation) {
        return {
          text: s.text,
          timestamp: s.timestamp,
          translatedText,
        };
      }

      if (showModalTranscript) {
        return {
          text: s.text,
          timestamp: s.timestamp,
        };
      }

      return {
        text: translatedText,
        timestamp: s.timestamp,
      };
    }).filter((sentence) => sentence.text);
  };

  const toggleModalTranscript = () => {
    if (showModalTranscript && !showModalTranslation) {
      return;
    }

    setShowModalTranscript((current) => !current);
  };

  const toggleModalTranslationLanguage = (language) => {
    if (activeModalLanguage !== language) {
      setActiveModalLanguage(language);
      setShowModalTranslation(true);
      return;
    }

    if (showModalTranslation) {
      if (!showModalTranscript) {
        return;
      }

      setShowModalTranslation(false);
      return;
    }

    setShowModalTranslation(true);
  };

  const hasActiveTranslationRows = activeModalLanguage
    ? (modalTranslationsByLanguage[activeModalLanguage] || []).length > 0
    : false;

  const isShowingTranslationOnly = showModalTranslation && !showModalTranscript;

  const getTranslationButtonActive = (language) => {
    return showModalTranslation && activeModalLanguage === language;
  };

  const formatAnalysisMode = (mode) => {
    if (mode === 'audio_only') return 'audio';
    if (mode === 'video_only') return 'video';
    if (mode === 'multimodal') return 'multimodal';
    return '';
  };

  const latestModalAnalysis = modalAnalysisEntries.length > 0 ? modalAnalysisEntries[0] : null;
  const olderModalAnalyses = modalAnalysisEntries.slice(1);

  const getCardAnalysisPreviewText = (item) => {
    const cardId = getCardId(item);
    return item.analysis_summary_text || cardAnalysisPreviewById[cardId] || '';
  };

  const getFullTranscriptionText = () => {
    if (modalSentences !== null) {
      return (modalSentences || []).map((row) => row.text).filter(Boolean).join('\n');
    }
    return (modalItem?.text || '').trim();
  };

  const getFullAnalysisText = () => {
    if (modalAnalysisEntries.length > 0) {
      return modalAnalysisEntries
        .map((entry) => (typeof entry?.summary_text === 'string' ? entry.summary_text.trim() : ''))
        .filter(Boolean)
        .join('\n\n');
    }
    return (modalItem?.analysis_summary_text || getCardAnalysisPreviewText(modalItem || {}) || '').trim();
  };

  const renderAnalysisContent = (rawText) => {
    const parsed = parseAnalysisDisplay(rawText);

    if (parsed.mode === 'items') {
      return (
        <ul className="history-analysis-items-list">
          {parsed.items.map((row, idx) => (
            <li key={`${row.item}-${idx}`} className="history-analysis-items-row">
              {row.category ? <span className="history-analysis-item-category">{row.category}</span> : null}
              <span className="history-analysis-item-text">{row.item}</span>
              {row.priority ? <span className="history-analysis-item-priority">{row.priority}</span> : null}
            </li>
          ))}
        </ul>
      );
    }

    if (parsed.mode === 'json') {
      return <pre className="history-analysis-json">{parsed.text}</pre>;
    }

    return <p className="history-analysis-preview-text">{parsed.text}</p>;
  };



  const sourceIcon = (type, src) => {
    if (type === 'translation') return <Globe size={14} />;
    if (src === 'stream' || src === 'whip') return <Mic size={14} />;
    if (src === 'url') return <LinkIcon size={14} />;
    return <Upload size={14} />;
  };

  return (
    <div className="history-page">
      <h1 className="page-title">History</h1>

      <div className="history-controls panel-glass">
        <div className="search-wrap">
          <Search size={16} />
          <input
            type="text"
            placeholder="Search text..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <div className="filter-wrap">
          <Filter size={16} />
          <select value={filter} onChange={(e) => setFilter(e.target.value)}>
            <option value="transcription">Transcription</option>
            <option value="analysis">Analysis</option>
          </select>
        </div>
      </div>

      {loading ? (
        <div className="loading-state">Loading...</div>
      ) : filtered.length === 0 ? (
        <div className="empty-state panel-glass">
          <p>No items found.</p>
        </div>
      ) : (
        <div className="history-list">
          {filtered.map((item) => (
            <div key={`${item._type}-${item.id}`} className="history-item panel-glass">
              <div className="history-main">
                <div className="history-header">
                  <span className={`badge ${item._type}`}>
                    {sourceIcon(item._type, item.source_type)} {item.source_type || item._type}
                  </span>
                  <span className="history-date">{formatDate(item.created_at)}</span>
                </div>
                <div className="history-card-view-switch" role="tablist" aria-label="History card view">
                  <button
                    type="button"
                    className={`history-card-view-btn ${getCardView(item) === 'transcription' ? 'active' : ''}`}
                    onClick={() => setCardView(item, 'transcription')}
                    aria-pressed={getCardView(item) === 'transcription'}
                  >
                    Transcription
                  </button>
                  <button
                    type="button"
                    className={`history-card-view-btn ${getCardView(item) === 'analysis' ? 'active' : ''}`}
                    onClick={() => setCardView(item, 'analysis')}
                    aria-pressed={getCardView(item) === 'analysis'}
                    disabled={!item.has_analysis}
                    title={!item.has_analysis ? 'Analysis not available for this item' : undefined}
                  >
                    Analysis{item.analysis_mode ? ` • ${formatAnalysisMode(item.analysis_mode)}` : ''}
                  </button>
                </div>
                <div className="history-sentences preview" onClick={() => openModal(item)}>
                  {getCardView(item) === 'analysis' && item.has_analysis ? (
                    getCardAnalysisPreviewText(item) ? (
                      <div className="history-analysis-preview">
                        <p className="history-analysis-preview-label">Latest analysis</p>
                        {renderAnalysisContent(getCardAnalysisPreviewText(item))}
                      </div>
                    ) : cardAnalysisLoadingById[getCardId(item)] ? (
                      <div className="history-analysis-preview">
                        <p className="history-analysis-preview-label">Latest analysis</p>
                        <p className="history-analysis-preview-text">Loading analysis preview…</p>
                      </div>
                    ) : cardAnalysisErrorById[getCardId(item)] ? (
                      <div className="history-analysis-preview">
                        <p className="history-analysis-preview-label">Latest analysis</p>
                        <p className="history-analysis-preview-text">{cardAnalysisErrorById[getCardId(item)]}</p>
                      </div>
                    ) : (
                      <div className="history-analysis-preview">
                        <p className="history-analysis-preview-label">Latest analysis</p>
                        <p className="history-analysis-preview-text">Analysis is available. Open the item for full history.</p>
                      </div>
                    )
                  ) : (
                    <>
                      {splitSentences(item.text).slice(0, 5).map((sentence, i) => (
                        <p key={i} className="history-sentence">{sentence}</p>
                      ))}
                      {splitSentences(item.text).length > 5 && (
                        <p className="history-more">+{splitSentences(item.text).length - 5} more…</p>
                      )}
                    </>
                  )}
                </div>
                <div className="history-footer">
                  <span className="lang-tag">
                    {item.language || item.source_language}
                    {item.target_language ? ` → ${item.target_language}` : ''}
                  </span>
                  {(item.translated_languages || []).map((lang) => (
                    <span key={lang} className="lang-tag secondary">{lang}</span>
                  ))}
                  {item.duration ? <span className="duration-tag"><Clock size={12} /> {formatDuration(item.duration)}</span> : null}
                  {item.word_count ? <span>{item.word_count} words</span> : null}
                  {item.token_count ? <span>{item.token_count} tokens</span> : null}
                </div>
              </div>
              <div className="history-actions">
                <>
                  <button className="icon-btn" onClick={() => handleDownload(item, 'txt')} title="Download TXT">
                    <Download size={16} />
                  </button>
                  <button className="icon-btn" onClick={() => handleDownload(item, 'srt')} title="Download SRT">
                    SRT
                  </button>
                  <button className="icon-btn" onClick={() => handleDownload(item, 'vtt')} title="Download VTT">
                    VTT
                  </button>
                  <button className="icon-btn" onClick={() => handleDownload(item, 'md')} title="Download Markdown">
                    MD
                  </button>
                  <button className="icon-btn" onClick={() => handleDownload(item, 'notes-md')} title="Download Notes & Todos with Sentences">
                    Notes+
                  </button>
                  <button className="icon-btn" onClick={() => handleDownload(item, 'annotations')} title="Download Notes & Todos Only">
                    Notes
                  </button>
                </>
                <button className="icon-btn danger" onClick={() => handleDelete(item)} title="Delete">
                  <Trash2 size={16} />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {modalItem && (
        <div className="history-modal-overlay" onClick={closeModal}>
          <div className="history-modal panel-glass" onClick={(e) => e.stopPropagation()}>
            <div className="history-modal-header">
              <div className="history-modal-title">
                <span className="badge transcription">
                  {sourceIcon(modalItem._type, modalItem.source_type)} stream
                </span>
                <span className="history-date">{formatDate(modalItem.created_at)}</span>
              </div>
              <button className="icon-btn" onClick={closeModal} title="Close">
                <X size={16} />
              </button>
            </div>
            <div className="history-modal-body">
              <div className="history-stream-tabs" role="tablist" aria-label="Stream modal view">
                <button
                  type="button"
                  className={`history-stream-tab ${modalStreamTab === 'transcription' ? 'active' : ''}`}
                  aria-pressed={modalStreamTab === 'transcription'}
                  onClick={() => setModalStreamTab('transcription')}
                >
                  Transcription
                </button>
                <button
                  type="button"
                  className={`history-stream-tab ${modalStreamTab === 'analysis' ? 'active' : ''}`}
                  aria-pressed={modalStreamTab === 'analysis'}
                  onClick={() => setModalStreamTab('analysis')}
                  disabled={!modalItem.has_analysis}
                  title={!modalItem.has_analysis ? 'Analysis not available for this stream' : undefined}
                >
                  Analysis{modalItem.analysis_mode ? ` • ${formatAnalysisMode(modalItem.analysis_mode)}` : ''}
                </button>
              </div>

              {modalStreamTab === 'transcription' ? (
                <>
                  <div className="history-language-tabs" aria-label="Transcript and translations display options">
                    <button
                      className={`history-language-tab ${showModalTranscript ? 'active' : ''}`}
                      onClick={toggleModalTranscript}
                      type="button"
                      aria-pressed={showModalTranscript}
                    >
                      Transcript
                    </button>
                    {Object.keys(modalTranslationsByLanguage).map((lang) => (
                      <button
                        key={lang}
                        className={`history-language-tab ${getTranslationButtonActive(lang) ? 'active' : ''}`}
                        onClick={() => toggleModalTranslationLanguage(lang)}
                        type="button"
                        aria-pressed={getTranslationButtonActive(lang)}
                      >
                        {lang.toUpperCase()}
                      </button>
                    ))}
                  </div>
                  <SentenceList
                    transcriptionId={modalItem.stream_session_id || modalItem.stream_id || modalItem.id}
                    sentences={getModalSentencesForLanguage()}
                    readOnly={!showModalTranscript}
                  />
                  {isShowingTranslationOnly && !hasActiveTranslationRows && (
                      <div className="history-translation-modal">
                        <p className="history-modal-section-label">No sentence translations for this language yet.</p>
                      </div>
                  )}
                  <div className="history-full-text-block">
                    <p className="history-modal-section-label">Full transcription</p>
                    <pre className="history-analysis-json">{getFullTranscriptionText() || 'No transcription text available.'}</pre>
                  </div>
                </>
              ) : (
                <div className="history-analysis-card panel-glass">
                  <div className="history-analysis-header">
                    <p className="history-modal-section-label">Live Analysis</p>
                    {(latestModalAnalysis?.analysis_mode || modalItem.analysis_mode) ? (
                      <span className="lang-tag secondary">{formatAnalysisMode(latestModalAnalysis?.analysis_mode || modalItem.analysis_mode)}</span>
                    ) : null}
                  </div>

                  {modalAnalysisLoading && <p className="history-analysis-status">Loading analysis…</p>}
                  {!modalAnalysisLoading && modalAnalysisError && <p className="history-analysis-error">{modalAnalysisError}</p>}

                  {!modalAnalysisLoading && (latestModalAnalysis || getFullAnalysisText()) && (
                    <div className="history-full-text-block">
                      <p className="history-modal-section-label">Full analysis</p>
                      <div className="history-analysis-text">
                        {renderAnalysisContent(getFullAnalysisText() || 'No analysis text available.')}
                      </div>
                    </div>
                  )}

                  {!modalAnalysisLoading && olderModalAnalyses.length > 0 && (
                    <details className="history-analysis-older">
                      <summary>Previous summaries ({olderModalAnalyses.length})</summary>
                      <div className="history-analysis-older-list">
                        {olderModalAnalyses.map((entry) => (
                          <div key={entry.id} className="history-analysis-older-item">
                            <div className="history-analysis-text">{renderAnalysisContent(entry.summary_text)}</div>
                            <p className="history-analysis-meta">{formatDate(entry.created_at)}</p>
                          </div>
                        ))}
                      </div>
                    </details>
                  )}
                </div>
              )}
            </div>
            <div className="history-modal-footer">
              <span className="lang-tag">
                {modalItem.language || modalItem.source_language}
                {modalItem.target_language ? ` → ${modalItem.target_language}` : ''}
              </span>
              {Object.keys(modalTranslationsByLanguage).map((lang) => (
                <span key={lang} className="lang-tag secondary">{lang}</span>
              ))}
              {modalItem.duration ? <span className="duration-tag"><Clock size={12} /> {formatDuration(modalItem.duration)}</span> : null}
              {modalItem.word_count ? <span>{modalItem.word_count} words</span> : null}
              {modalItem.token_count ? <span>{modalItem.token_count} tokens</span> : null}
              <div className="history-modal-downloads">
                <button className="icon-btn" onClick={() => handleDownload(modalItem, 'txt')} title="Download TXT">
                  <Download size={14} />
                </button>
                <button className="icon-btn" onClick={() => handleDownload(modalItem, 'srt')} title="Download SRT">
                  SRT
                </button>
                <button className="icon-btn" onClick={() => handleDownload(modalItem, 'vtt')} title="Download VTT">
                  VTT
                </button>
                <button className="icon-btn" onClick={() => handleDownload(modalItem, 'md')} title="Download Markdown">
                  MD
                </button>
                <button className="icon-btn" onClick={() => handleDownload(modalItem, 'notes-md')} title="Download Notes & Todos with Sentences">
                  Notes+
                </button>
                <button className="icon-btn" onClick={() => handleDownload(modalItem, 'annotations')} title="Download Notes & Todos Only">
                  Notes
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
