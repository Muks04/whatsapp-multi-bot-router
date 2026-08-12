# WhatsApp Multi-Bot Router

A single Twilio webhook Lambda that intelligently routes incoming WhatsApp messages to the correct backend bot based on prefix keywords, intent detection, or user preferences — enabling multiple AI services on one WhatsApp number.

## Live System

- **Lambda:** `whatsapp-router-prod` (us-east-1)
- **API Gateway:** https://6mjfaec7l3.execute-api.us-east-1.amazonaws.com/
- **Twilio Webhook:** Points to the API Gateway above
- **Twilio Sandbox:** +14155238886 (join code: "made-member")

## How It Works

One WhatsApp number → multiple AI bots. The router inspects each incoming message and forwards it to the correct backend:

```
User sends WhatsApp message
         │
         ▼
┌─────────────────────────────────┐
│   WhatsApp Router (Lambda)      │
│                                 │
│   1. Check for prefix/command   │
│   2. Detect intent from keywords│
│   3. Fall back to user default  │
└────────┬────────────────┬───────┘
         │                │
         ▼                ▼
┌─────────────┐  ┌────────────────┐
│ Legal AI Bot│  │ Onion Farm Bot │
│ (AgentCore) │  │ (Lambda)       │
└─────────────┘  └────────────────┘
```

## Routing Logic

| Trigger | Routes to | Example |
|---------|-----------|---------|
| `legal:` prefix | Legal AI Bot | `legal: anticipatory bail test` |
| `farm:` or `खेती:` prefix | Onion Farm Bot | `farm: mandi prices` |
| Legal keywords detected | Legal AI Bot | `What does Section 138 say?` |
| Farm keywords detected | Onion Farm Bot | `मंडी भाव क्या है?` |
| `/switch legal` | Sets default to Legal | (command) |
| `/switch farm` | Sets default to Farm | (command) |
| `/help` | Shows bot menu | (command) |
| No match | User's last-used bot | (defaults to farm) |

## Supported Commands

| Command | Action |
|---------|--------|
| `/help` | Show available bots and usage instructions |
| `/switch legal` | Set Legal AI Bot as your default |
| `/switch farm` | Set Onion Farm Bot as your default |
| `legal: <query>` | Route directly to Legal Bot |
| `farm: <query>` | Route directly to Farm Bot |

## Bot Registry

| Bot Key | Lambda/Runtime | Features |
|---------|---------------|----------|
| `legal` | `legal-ai-bot-prod` → AgentCore | Case law search, draft checking, devil's advocate |
| `farm` | `onion-farm-bot-prod` | ML price forecast, weather, crop calendar, pest management |

## Architecture

```
Twilio (WhatsApp)
       │
       ▼
API Gateway (us-east-1)
       │
       ▼
Lambda: whatsapp-router-prod
       │
       ├── DynamoDB (user preferences, last-used bot)
       │
       ├── [legal] → AgentCore invoke_agent_runtime
       │              (legalAiAgentRuntime-mteSKGE2Ym)
       │
       └── [farm] → Lambda invoke (onion-farm-bot-prod)
```

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Runtime | AWS Lambda (us-east-1) |
| API | API Gateway (HTTP API) |
| Messaging | Twilio WhatsApp API |
| State | DynamoDB (user preferences with TTL) |
| Routing | Keyword matching + AgentCore / Lambda forwarding |
| Language | Supports Hindi, Hinglish, English keyword detection |

## Key Design Decisions

- **Stateless routing** — no session maintained in the router itself, just forwards
- **User default stored in DynamoDB** — remembers last bot used per phone number (365-day TTL)
- **Prefix override** — user can always force a specific bot regardless of default
- **Hindi/English bilingual** — keyword detection works in both languages
- **AgentCore integration** — Legal bot routes to AgentCore runtime (managed container), Farm bot routes to direct Lambda invoke

## Project Structure

```
whatsapp-router/
├── router.py       # Single Lambda handler (routing logic + Twilio integration)
├── .gitignore
└── README.md
```

## Deployment

```bash
# Zip and deploy
zip router.zip router.py
aws lambda update-function-code \
  --function-name whatsapp-router-prod \
  --zip-file fileb://router.zip \
  --region us-east-1
```

## Adding a New Bot

1. Add entry to `BOTS` dict in `router.py`:
```python
"new_bot": {
    "function_name": "new-bot-lambda-name",
    "name": "New Bot",
    "description": "What it does",
    "keywords": ["keyword1", "keyword2"],
}
```
2. Add routing case in `forward_to_bot()`
3. Redeploy Lambda

## Scaling

This router is designed to be the single entry point for any number of WhatsApp bots. Current bots:
- Legal AI (Indian case law)
- Onion Farm (agriculture advisory)

Planned additions:
- FinBot (German insurance, currently on separate Twilio number)
- Insurance Claims (multi-agent, currently routed via FinBot)

## Author

**Saurabh Mukherjee** — AWS Solutions Architect Professional | GenAI Professional | ML Engineer Associate
