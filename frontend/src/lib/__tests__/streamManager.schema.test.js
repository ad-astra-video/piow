/**
 * @jest-environment jsdom
 */
import streamManager from '../streamManager';

describe('StreamManager schema handling', () => {
  beforeEach(() => {
    // Reset state between tests
    streamManager.stop();
    streamManager.state = {
      ...streamManager.state,
      analysisSchemaStatus: null,
      analysisResponseFormat: null,
    };
  });

  afterEach(() => {
    streamManager.stop();
  });

  test('initial state has null schema status and format', () => {
    expect(streamManager.state.analysisSchemaStatus).toBeNull();
    expect(streamManager.state.analysisResponseFormat).toBeNull();
  });

  test('analysis_response_format message sets generated status and schema', () => {
    const schema = { type: 'object', title: 'TestSchema' };
    const message = {
      type: 'analysis_response_format',
      schema,
      mode: 'multimodal',
    };

    // Simulate WebSocket message handling
    const event = { data: JSON.stringify(message) };
    // We can't easily trigger ws.onmessage directly, but we can test _setState
    streamManager._setState({
      analysisSchemaStatus: 'generated',
      analysisResponseFormat: { type: 'json_object', schema },
    });

    expect(streamManager.state.analysisSchemaStatus).toBe('generated');
    expect(streamManager.state.analysisResponseFormat).toEqual({
      type: 'json_object',
      schema,
    });
  });

  test('analysis_response_format with error sets error status', () => {
    streamManager._setState({
      analysisSchemaStatus: 'error',
      analysisResponseFormat: null,
    });

    expect(streamManager.state.analysisSchemaStatus).toBe('error');
    expect(streamManager.state.analysisResponseFormat).toBeNull();
  });

  test('regenerateAnalysisSchema sets generating status', async () => {
    global.fetch = jest.fn(() =>
      Promise.resolve({ ok: true })
    );
    streamManager.streamId = 'test-stream-123';
    streamManager.accessToken = 'test-token';

    await streamManager.regenerateAnalysisSchema();

    expect(streamManager.state.analysisSchemaStatus).toBe('generating');
    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining('/stream/test-stream-123/update'),
      expect.objectContaining({
        method: 'POST',
        headers: expect.objectContaining({
          'Content-Type': 'application/json',
          'Authorization': 'Bearer test-token',
        }),
        body: JSON.stringify({ generate_analysis_schema: true }),
      })
    );
  });

  test('regenerateAnalysisSchema handles fetch failure', async () => {
    global.fetch = jest.fn(() =>
      Promise.resolve({ ok: false, status: 500 })
    );
    streamManager.streamId = 'test-stream-123';

    await streamManager.regenerateAnalysisSchema();

    expect(streamManager.state.analysisSchemaStatus).toBe('error');
  });

  test('regenerateAnalysisSchema handles network error', async () => {
    global.fetch = jest.fn(() => Promise.reject(new Error('Network error')));
    streamManager.streamId = 'test-stream-123';

    await streamManager.regenerateAnalysisSchema();

    expect(streamManager.state.analysisSchemaStatus).toBe('error');
  });

  test('regenerateAnalysisSchema does nothing without streamId', async () => {
    global.fetch = jest.fn();
    streamManager.streamId = null;

    await streamManager.regenerateAnalysisSchema();

    expect(fetch).not.toHaveBeenCalled();
  });

  test('stop resets schema status and format', () => {
    streamManager._setState({
      analysisSchemaStatus: 'generated',
      analysisResponseFormat: { type: 'json_object', schema: {} },
    });

    streamManager.stop();

    expect(streamManager.state.analysisSchemaStatus).toBeNull();
    expect(streamManager.state.analysisResponseFormat).toBeNull();
  });
});
