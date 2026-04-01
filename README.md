# Live Transcription & Translation Platform

A real-time transcription and translation platform with Chrome Extension, Web App, and AI Agent API support.

**Primary Offering:** Real-time Audio/Video Transcription (`/transcribe`)  
**Secondary Offering:** Translation (`/translate`)

## Features

- 🎤 **Real-time Transcription**: Live audio/video to text with sub-second latency
- 🌐 **Translation**: Translate transcriptions to 20+ languages
- 🔐 **Web3 Authentication**: Sign-In with Ethereum (SIWE) + Google OAuth
- 💰 **Crypto Payments**: Accept USDC, BTC, ETH via Stripe Crypto
- 🔄 **Real-time Sync**: Supabase Realtime for instant state synchronization
- 📦 **Monorepo**: Turborepo-powered TypeScript monorepo
- 🤖 **AI Agent API**: MCP, A2A, and OAuth 2.0 support for programmatic access

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    CLIENT LAYER                                  │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────────┐  │
│  │ Chrome Ext  │  │  Web App    │  │   AI Agents (SDK)       │  │
│  │ - Captions  │  │ - Dashboard │  │   - MCP Server          │  │
│  │ - Recording │  │ - Library   │  │   - LangChain           │  │
│  └─────────────┘  └─────────────┘  │   - AutoGPT             │  │
│                                     └─────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│                   SHARED LIBRARIES                               │
│  @lib/ui │ @lib/supabase │ @lib/web3 │ @lib/types │ @lib/mcp   │
└─────────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│                    SUPABASE PLATFORM                             │
│  Auth (SIWE/OAuth) │ Database │ Realtime │ Edge Functions       │
│  - Users           │ Tables:  │ - Usage  │ - transcribe         │
│  - Agents          │ - users  │ - Subs   │ - translate          │
│  - API Keys        │ - transcriptions                          │
└─────────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│                   TRANSLATION SERVICES                           │
│  VLLM Voxtral Realtime API │ WHIP WebRTC Ingest                │
└─────────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│                       STRIPE                                     │
│  Cards │ Crypto (USDC/BTC/ETH) │ Subscriptions │ Invoices       │
└─────────────────────────────────────────────────────────────────┘
```

## Quick Start

### Prerequisites

- Node.js 18+
- pnpm 8+
- Supabase CLI
- MetaMask or compatible Web3 wallet (for Ethereum sign-in)

### Installation

```bash
# Clone repository
git clone https://github.com/your-org/live-transcription-app.git
cd live-transcription-app

# Install dependencies
pnpm install

# Copy environment variables
cp .env.example .env
# Edit .env with your Supabase and Stripe credentials

# Start Supabase locally (optional)
supabase start

# Run database migrations
pnpm supabase:migrate

# Start development servers
pnpm dev
```

### Development

```bash
# Run all apps in development mode
pnpm dev

# Run specific app
pnpm dev:web      # Web app on port 5173
pnpm dev:extension # Chrome extension

# Type checking
pnpm typecheck

# Linting
pnpm lint
```

## Project Structure

```
live-transcription-app/
├── apps/
│   ├── chrome-extension/    # Chrome Extension (Manifest V3)
│   │                       # - Live captions for video/audio
│   │                       # - Meeting transcription
│   │                       # - Audio recording
│   ├── web-app/             # React Web Application
│   │                       # - Transcription library
│   │                       # - Export options
│   │                       # - Subscription management
│   └── api/                 # API routes (optional)
├── packages/
│   ├── ui/                  # Shared UI components
│   ├── supabase/            # Supabase client & utilities
│   ├── web3/                # Web3/SIWE authentication
│   ├── types/               # Shared TypeScript types
│   ├── mcp-server/          # MCP server for AI agents
│   ├── agent-sdk/           # Agent SDK for API access
│   └── config/              # Shared configs (eslint, tsconfig)
├── supabase/
│   ├── migrations/          # Database migrations
│   ├── functions/           # Supabase Edge Functions
│   │                       # - transcribe
│   │                       # - transcribe-stream
│   │                       # - translate
│   │                       # - verify-siwe
│   │                       # - process-payment
│   └── config.toml          # Supabase configuration
├── backend/                 # Backend services
└── infra/                   # Infrastructure (Terraform)
```

## API Endpoints

### Primary: Transcription

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/v1/transcribe` | Transcribe audio/video |
| POST | `/api/v1/transcribe/stream` | Real-time streaming transcription |
| GET | `/api/v1/transcriptions` | List user transcriptions |
| GET | `/api/v1/transcriptions/:id` | Get transcription by ID |
| DELETE | `/api/v1/transcriptions/:id` | Delete transcription |

### Secondary: Translation

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/v1/translate` | Translate text |
| POST | `/api/v1/translate/transcription` | Translate transcription |
| GET | `/api/v1/languages` | Get supported languages |

### Agent API

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/v1/agents/register` | Register new agent |
| GET | `/api/v1/agents/me/usage` | Get agent usage |
| POST | `/api/v1/agents/me/keys` | Create API key |

## Authentication

### Ethereum Sign-In (SIWE)

1. Connect your Web3 wallet (MetaMask, etc.)
2. Sign the SIWE message to authenticate
3. Supabase Edge Function verifies the signature
4. Session is created and synced across platforms

### Google OAuth

1. Click "Sign in with Google"
2. Complete OAuth flow via Supabase
3. Session is created automatically

### API Key Authentication (Agents)

Agents authenticate using API key + HMAC signature:

```
X-API-Key: ltk_xxx
X-Signature: hmac-sha256(signature)
X-Timestamp: 1234567890
```

## Payments

### Supported Payment Methods

- **Credit/Debit Cards**: Visa, Mastercard, American Express
- **Cryptocurrency**: 
  - USDC (Ethereum, Solana, Polygon)
  - Bitcoin (BTC)
  - Ethereum (ETH)

### Subscription Plans

| Plan | Monthly | Yearly | Transcription | Translation |
|------|---------|--------|---------------|-------------|
| Free | $0 | $0 | 60 min/month | 1,000 chars/month |
| Starter | $19 | $190 | 10 hours/month | 50,000 chars/month |
| Pro | $49 | $490 | 50 hours/month | Unlimited |
| Enterprise | $199 | $1,990 | Unlimited | Unlimited |

## Chrome Extension

### Installation (Development)

1. Build the extension:
   ```bash
   pnpm --filter chrome-extension build
   ```

2. Load in Chrome:
   - Open `chrome://extensions/`
   - Enable "Developer mode"
   - Click "Load unpacked"
   - Select `apps/chrome-extension/dist`

### Features

- **Live Captions**: Real-time captions for any video/audio
- **Meeting Transcription**: Capture Zoom, Google Meet, Teams calls
- **YouTube Transcription**: Auto-generate transcripts
- **Audio Recording**: Record microphone and transcribe

## AI Agent Integration

### MCP (Model Context Protocol)

Add to your MCP client configuration:

```json
{
  "mcpServers": {
    "live-transcription": {
      "command": "npx",
      "args": ["-y", "@live-transcription/mcp-server@latest"],
      "env": {
        "LIVE_TRANSCRIPTION_API_KEY": "ltk_xxx"
      }
    }
  }
}
```

### Available MCP Tools

- `transcribe` - Transcribe audio/video to text
- `transcribe_stream` - Real-time streaming transcription
- `get_transcription` - Get transcription by ID
- `list_transcriptions` - List user's transcriptions
- `translate` - Translate text
- `translate_transcription` - Translate existing transcription

### Agent SDK

```typescript
import { AgentClient } from '@lib/agent-sdk';

const client = new AgentClient({
  apiKey: 'ltk_xxx',
  apiSecret: 'lts_xxx',
});

// Transcribe audio
const transcription = await client.transcribe({
  audio_url: 'https://example.com/audio.mp3',
  language: 'en',
  format: 'json',
});

console.log(transcription.text);
```

## Documentation

- [Technical Specification V3](docs/TECHNICAL_SPECIFICATION.md) - Main spec with /transcribe as primary offering
- [Agent Integration Spec](docs/AGENT_INTEGRATION_SPEC.md) - MCP, A2A, OAuth 2.0 for agents
- [Implementation Guide](docs/IMPLEMENTATION_GUIDE.md) - Configuration templates and setup

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Run tests and linting
5. Submit a pull request

## License

MIT License - see [LICENSE](LICENSE) for details

## Support

For issues and questions:
- GitHub Issues: https://github.com/your-org/live-transcription-app/issues
- Email: support@livetranscription.app
