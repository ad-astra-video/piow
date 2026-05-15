import { MCPServer } from '@model-context-protocol/sdk';
import express from 'express';
import axios from 'axios';
import dotenv from 'dotenv';
import { v4 as uuidv4 } from 'uuid';

dotenv.config();

const app = express();
const PORT = process.env.PORT || 3000;

// Backend API configuration
const BACKEND_URL = process.env.BACKEND_URL || 'http://localhost:3000';
const API_KEY = process.env.LIVE_TRANSCRIPTION_API_KEY;

// Initialize MCP server
const server = new MCPServer({
  name: 'live-transcription',
  version: '1.0.0'
});

// Middleware
app.use(express.json());

// Helper function to make authenticated requests to backend
const backendRequest = async (endpoint, method = 'GET', data = null) => {
  try {
    const config = {
      method,
      url: `${BACKEND_URL}${endpoint}`,
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${API_KEY}`
      },
      ...(data && { data })
    };
    
    const response = await axios(config);
    return response.data;
  } catch (error) {
    throw new Error(`Backend request failed: ${error.message}`);
  }
};

// MCP Tool: transcribe
server.addTool('transcribe', {
  description: 'Transcribe audio/video to text using Granite 4.0 (CPU) or Voxtral (GPU)',
  parameters: {
    type: 'object',
    properties: {
      audio_url: {
        type: 'string',
        description: 'URL to the audio/video file to transcribe'
      },
      language: {
        type: 'string',
        description: 'Language code for transcription (e.g., "en", "es", "fr")',
        default: 'en'
      },
      format: {
        type: 'string',
        description: 'Output format (txt, json, srt, vtt)',
        enum: ['txt', 'json', 'srt', 'vtt'],
        default: 'json'
      },
      streaming: {
        type: 'boolean',
        description: 'Whether to use real-time streaming (GPU) or batch processing (CPU)',
        default: false
      }
    },
    required: ['audio_url']
  },
  handler: async (params) => {
    try {
      const endpoint = '/api/v1/stream/process';
      
      const result = await backendRequest(endpoint, 'POST', {
        audio_url: params.audio_url,
        language: params.language,
        format: params.format
      });
      
      return {
        content: [
          {
            type: 'text',
            text: JSON.stringify(result, null, 2)
          }
        ]
      };
    } catch (error) {
      return {
        content: [
          {
            type: 'text',
            text: `Error: ${error.message}`
          }
        ],
        isError: true
      };
    }
  }
});

// MCP Tool: transcribe_stream (real-time)
server.addTool('transcribe_stream', {
  description: 'Start real-time streaming transcription',
  parameters: {
    type: 'object',
    properties: {
      audio_stream_id: {
        type: 'string',
        description: 'Identifier for the audio stream'
      },
      language: {
        type: 'string',
        description: 'Language code for transcription',
        default: 'en'
      }
    },
    required: ['audio_stream_id']
  },
  handler: async (params) => {
    try {
      // For streaming, we return connection information
      // In a full implementation, this would establish a WebSocket connection
      const result = await backendRequest('/api/v1/stream/process', 'POST', {
        audio_stream_id: params.audio_stream_id,
        language: params.language,
        streaming: true
      });
      
      return {
        content: [
          {
            type: 'text',
            text: JSON.stringify({
              status: 'streaming_ready',
              message: 'Use WebSocket connection for real-time streaming',
              ws_url: `${process.env.WS_URL || 'ws://localhost:6000'}/v1/realtime`,
              stream_id: params.audio_stream_id,
              language: params.language
            }, null, 2)
          }
        ]
      };
    } catch (error) {
      return {
        content: [
          {
            type: 'text',
            text: `Error: ${error.message}`
          }
        ],
        isError: true
      };
    }
  }
});

// MCP Tool: get_stream
server.addTool('get_stream', {
  description: 'Get stream by ID',
  parameters: {
    type: 'object',
    properties: {
      stream_id: {
        type: 'string',
        description: 'ID of the stream to retrieve'
      }
    },
    required: ['stream_id']
  },
  handler: async (params) => {
    try {
      const result = await backendRequest(
        `/api/v1/streams/${params.stream_id}`
      );
      
      return {
        content: [
          {
            type: 'text',
            text: JSON.stringify(result, null, 2)
          }
        ]
      };
    } catch (error) {
      return {
        content: [
          {
            type: 'text',
            text: `Error: ${error.message}`
          }
        ],
        isError: true
      };
    }
  }
});

// MCP Tool: list_streams
server.addTool('list_streams', {
  description: 'List user streams',
  parameters: {
    type: 'object',
    properties: {
      limit: {
        type: 'integer',
        description: 'Maximum number of streams to return',
        default: 10
      },
      offset: {
        type: 'integer',
        description: 'Offset for pagination',
        default: 0
      },
      language: {
        type: 'string',
        description: 'Filter by language code'
      }
    }
  },
  handler: async (params) => {
    try {
      const queryParams = new URLSearchParams();
      if (params.limit) queryParams.append('limit', params.limit.toString());
      if (params.offset) queryParams.append('offset', params.offset.toString());
      if (params.language) queryParams.append('language', params.language);
      
      const endpoint = `/api/v1/streams?${queryParams.toString()}`;
      const result = await backendRequest(endpoint);
      
      return {
        content: [
          {
            type: 'text',
            text: JSON.stringify(result, null, 2)
          }
        ]
      };
    } catch (error) {
      return {
        content: [
          {
            type: 'text',
            text: `Error: ${error.message}`
          }
        ],
        isError: true
      };
    }
  }
});

// Health check endpoint
app.get('/health', (req, res) => {
  res.json({ status: 'healthy', service: 'mcp-server' });
});

// Start the MCP server
const startServer = async () => {
  try {
    // Start MCP server (stdio transport for local usage)
    await server.start();
    console.log('MCP Server started on stdio');
    
    // Start HTTP server for health checks and potential HTTP transport
    app.listen(PORT, () => {
      console.log(`HTTP server running on port ${PORT}`);
    });
    
    process.on('SIGINT', async () => {
      console.log('Shutting down MCP server...');
      await server.stop();
      process.exit(0);
    });
  } catch (error) {
    console.error('Failed to start MCP server:', error);
    process.exit(1);
  }
};

startServer();

export default server;