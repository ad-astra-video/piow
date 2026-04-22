import axios, { AxiosInstance } from 'axios';
import { v4 as uuidv4 } from 'uuid';
import crypto from 'crypto';

// Types for our SDK
export interface AgentClientOptions {
  apiKey: string;
  apiSecret: string;
  baseUrl?: string;
}

export interface TranscriptionResult {
  text: string;
  segments: Array<{
    start: number;
    end: number;
    text: string;
  }>;
  language: string;
  duration: number;
  processing_time?: number;
  model: string;
  hardware: string;
}

export interface TranslationResult {
  original_text: string;
  translated_text: string;
  source_language: string;
  target_language: string;
  processing_time?: number;
  model: string;
  hardware: string;
}

export interface TranscriptionListResponse {
  transcriptions: Array<{
    id: string;
    text: string;
    language: string;
    duration: number;
    created_at: string;
  }>;
  total: number;
  limit: number;
  offset: number;
}

export interface LanguageResponse {
  languages: Array<{
    code: string;
    name: string;
    native_name: string;
  }>;
}

export interface AgentUsageResponse {
  agent_id: string;
  total_requests: number;
  total_transcription_seconds: number;
  total_translation_characters: number;
  current_period_usage: {
    transcription_seconds: number;
    translation_characters: number;
  };
}

export interface AgentKeysResponse {
  keys: Array<{
    id: string;
    name: string;
    key_prefix: string;
    created_at: string;
    expires_at: string | null;
    last_used_at: string | null;
  }>;
}

/**
 * AgentClient - Main client for interacting with the Live Transcription & Translation Platform
 */
export class AgentClient {
  private axiosInstance: AxiosInstance;
  private apiKey: string;
  private apiSecret: string;
  private baseUrl: string;

  constructor(options: AgentClientOptions) {
    this.apiKey = options.apiKey;
    this.apiSecret = options.apiSecret;
    this.baseUrl = options.baseUrl || 'http://localhost:3000';

    this.axiosInstance = axios.create({
      baseURL: this.baseUrl,
      timeout: 10000,
      headers: {
        'Content-Type': 'application/json',
        'User-Agent': '@live-transcription/agent-sdk/0.1.0'
      }
    });

    // Add request interceptor to sign requests
    this.axiosInstance.interceptors.request.use((config) => {
      const timestamp = Math.floor(Date.now() / 1000);
      const nonce = uuidv4().replace(/-/g, '');
      const signature = this.signRequest(config.method?.toUpperCase() || 'GET', config.url || '', timestamp, nonce, config.data);

      config.headers = {
        ...config.headers,
        'X-API-Key': this.apiKey,
        'X-Timestamp': timestamp.toString(),
        'X-Nonce': nonce,
        'X-Signature': signature
      };

      return config;
    });
  }

  /**
   * Sign a request using HMAC-SHA256
   */
  private signRequest(method: string, url: string, timestamp: number, nonce: string, body: any): string {
    // Create the string to sign: method + url + timestamp + nonce + body
    const bodyString = body ? JSON.stringify(body) : '';
    const stringToSign = `${method}${url}${timestamp}${nonce}${bodyString}`;

    // Create HMAC-SHA256 signature
    const hmac = crypto.createHmac('sha256', this.apiSecret);
    hmac.update(stringToSign);
    return hmac.digest('hex');
  }

  /**
   * Transcribe audio/video file
   */
  async transcribe(params: {
    audio_url: string;
    language?: string;
    format?: 'txt' | 'json' | 'srt' | 'vtt';
    streaming?: boolean;
  }): Promise<TranscriptionResult> {
    const response = await this.axiosInstance.post('/api/v1/transcribe', params);
    return response.data;
  }

  /**
   * Get a list of supported languages
   */
  async getLanguages(): Promise<LanguageResponse> {
    const response = await this.axiosInstance.get('/api/v1/languages');
    return response.data;
  }

  /**
   * Translate text
   */
  async translate(params: {
    text: string;
    source_lang: string;
    target_lang: string;
  }): Promise<TranslationResult> {
    const response = await this.axiosInstance.post('/api/v1/translate', params);
    return response.data;
  }

  /**
   * Get transcription by ID
   */
  async getTranscription(id: string): Promise<TranscriptionResult> {
    const response = await this.axiosInstance.get(`/api/v1/transcriptions/${id}`);
    return response.data;
  }

  /**
   * List transcriptions for the agent
   */
  async listTranscriptions(params: {
    limit?: number;
    offset?: number;
    language?: string;
  } = {}): Promise<TranscriptionListResponse> {
    const response = await this.axiosInstance.get('/api/v1/transcriptions', { params });
    return response.data;
  }

  /**
   * Delete a transcription
   */
  async deleteTranscription(id: string): Promise<void> {
    await this.axiosInstance.delete(`/api/v1/transcriptions/${id}`);
  }

  /**
   * Get agent usage statistics
   */
  async getUsage(): Promise<AgentUsageResponse> {
    const response = await this.axiosInstance.get('/api/v1/agents/me/usage');
    return response.data;
  }

  /**
   * List agent API keys
   */
  async listKeys(): Promise<AgentKeysResponse> {
    const response = await this.axiosInstance.get('/api/v1/agents/me/keys');
    return response.data;
  }

  /**
   * Create a new agent API key
   */
  async createKey(params: {
    name: string;
    expires_in_days?: number;
  }): Promise<{ key: string }> {
    const response = await this.axiosInstance.post('/api/v1/agents/me/keys', params);
    return response.data;
  }

  /**
   * Revoke an agent API key
   */
  async revokeKey(keyId: string): Promise<void> {
    await this.axiosInstance.delete(`/api/v1/agents/me/keys/${keyId}`);
  }
}

// Export the client as the default export
export default AgentClient;