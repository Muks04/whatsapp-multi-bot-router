"""
WhatsApp Multi-Bot Router
==========================
Single Twilio webhook that routes messages to the correct bot Lambda
based on prefix keywords or intent detection.

Routing logic:
  - "legal:" or "कानून:" → Legal AI Bot
  - "farm:" or "खेती:" or "प्याज:" → Onion Farm Bot
  - "/switch legal" → Set default to legal bot
  - "/switch farm" → Set default to farm bot
  - "/help" → Show available bots
  - Default → Last used bot (stored in DynamoDB) or farm bot

Deploy: Single Lambda + API Gateway → Twilio webhook
"""

import json
import os
import time
import urllib.parse
import urllib.request
import base64
from datetime import datetime

import boto3

# AWS Clients
lambda_client = boto3.client("lambda", region_name="us-east-1")
dynamodb = boto3.resource("dynamodb", region_name="us-east-1")

# Configuration
ROUTER_TABLE = os.environ.get("ROUTER_TABLE", "whatsapp-router-prod")
TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "")
TWILIO_WHATSAPP_NUMBER = os.environ.get("TWILIO_WHATSAPP_NUMBER", "+14155238886")

# Bot registry
BOTS = {
    "legal": {
        "function_name": "legal-ai-bot-prod",
        "name": "Legal AI Bot",
        "hindi": "कानूनी सहायक",
        "description": "Legal research, drafting, devil's advocate",
        "keywords": ["legal", "law", "कानून", "केस", "petition", "draft", "judgment", "court",
                     "section", "act", "vakalatnama", "reply", "argument", "advocate"],
    },
    "farm": {
        "function_name": "onion-farm-bot-prod",
        "name": "Onion Farm Bot",
        "hindi": "प्याज खेती सहायक",
        "description": "Weather, crop calendar, mandi prices, pest management",
        "keywords": ["farm", "खेती", "प्याज", "onion", "मौसम", "weather", "मंडी", "mandi",
                     "price", "भाव", "कीड़ा", "pest", "सिंचाई", "irrigation", "पानी",
                     "बारिश", "rain", "फसल", "crop", "रोपाई", "harvest", "कटाई"],
    },
}

HELP_MESSAGE = (
    "🤖 *WhatsApp Multi-Bot*\n\n"
    "आपके पास 2 बॉट उपलब्ध हैं:\n\n"
    "⚖️ *Legal Bot* — कानूनी रिसर्च, ड्राफ्टिंग\n"
    "   → \"legal:\" लिखकर शुरू करें\n\n"
    "🧅 *Farm Bot* — प्याज खेती सलाह, मौसम, मंडी भाव\n"
    "   → \"farm:\" या \"खेती:\" लिखकर शुरू करें\n\n"
    "🔄 Default बदलें:\n"
    "   /switch legal — कानूनी बॉट default\n"
    "   /switch farm — खेती बॉट default\n\n"
    "ℹ️ /help — यह संदेश दिखाएं"
)


def get_user_default(phone: str) -> str:
    """Get user's default bot preference."""
    try:
        table = dynamodb.Table(ROUTER_TABLE)
        response = table.get_item(Key={"phone": phone})
        item = response.get("Item", {})
        return item.get("default_bot", "farm")
    except Exception:
        return "farm"


def set_user_default(phone: str, bot_key: str):
    """Set user's default bot preference."""
    try:
        table = dynamodb.Table(ROUTER_TABLE)
        table.put_item(Item={
            "phone": phone,
            "default_bot": bot_key,
            "updated_at": datetime.utcnow().isoformat(),
            "ttl": int(time.time()) + (365 * 86400),
        })
    except Exception:
        pass


def detect_bot_from_message(message: str) -> tuple:
    """
    Detect which bot to route to based on message content.
    Returns (bot_key, cleaned_message)
    """
    msg_lower = message.lower().strip()

    # Check explicit prefix
    for prefix in ["legal:", "कानून:", "law:"]:
        if msg_lower.startswith(prefix):
            return "legal", message[len(prefix):].strip()

    for prefix in ["farm:", "खेती:", "प्याज:", "onion:"]:
        if msg_lower.startswith(prefix):
            return "farm", message[len(prefix):].strip()

    # Check keywords
    legal_score = 0
    farm_score = 0

    for keyword in BOTS["legal"]["keywords"]:
        if keyword in msg_lower:
            legal_score += 1

    for keyword in BOTS["farm"]["keywords"]:
        if keyword in msg_lower:
            farm_score += 1

    if legal_score > farm_score and legal_score >= 1:
        return "legal", message
    elif farm_score > legal_score and farm_score >= 1:
        return "farm", message

    # No clear match — return None (will use default)
    return None, message


def forward_to_bot(bot_key: str, event_body: str, is_base64: bool = False):
    """Forward to target bot — AgentCore for legal, Lambda for farm."""
    if bot_key == "legal":
        return forward_to_agentcore(event_body, is_base64)
    else:
        return forward_to_lambda(bot_key, event_body, is_base64)


def forward_to_agentcore(event_body: str, is_base64: bool = False):
    """Invoke Legal AI Agent via AgentCore Runtime."""
    import urllib.error

    # Parse the Twilio body to extract the message
    if is_base64:
        raw = base64.b64decode(event_body).decode("utf-8")
    else:
        raw = event_body
    params = dict(urllib.parse.parse_qsl(raw))
    user_message = params.get("Body", "").strip()
    from_number = params.get("From", "").replace("whatsapp:", "")
    sender_name = params.get("ProfileName", "Counsellor")

    if not user_message:
        return False

    # Invoke AgentCore Runtime
    AGENTCORE_RUNTIME_ARN = "arn:aws:bedrock-agentcore:us-east-1:008714537357:runtime/legalAiAgentRuntime-mteSKGE2Ym"

    try:
        bedrock_agentcore = boto3.client("bedrock-agentcore", region_name="us-east-1")
        response = bedrock_agentcore.invoke_agent_runtime(
            agentRuntimeArn=AGENTCORE_RUNTIME_ARN,
            qualifier="legalAiEndpoint",
            payload=json.dumps({
                "prompt": user_message,
            }).encode(),
            contentType="application/json",
            accept="application/json",
            runtimeSessionId=f"legal-session-{from_number.replace('+', '')}-00000000",
        )

        # Process response — can be streaming (SSE) or JSON
        content_type = response.get("contentType", "")
        reply_text = ""

        if "text/event-stream" in content_type:
            # Handle streaming response
            content_parts = []
            for line in response["response"].iter_lines(chunk_size=10):
                if line:
                    decoded = line.decode("utf-8")
                    if decoded.startswith("data: "):
                        content_parts.append(decoded[6:])
            reply_text = "".join(content_parts)
        elif "application/json" in content_type:
            # Handle JSON response
            content_parts = []
            for chunk in response.get("response", []):
                content_parts.append(chunk.decode("utf-8"))
            result = json.loads("".join(content_parts))
            reply_text = result.get("response", str(result))
        else:
            # Raw response
            content_parts = []
            for chunk in response.get("response", []):
                content_parts.append(chunk.decode("utf-8"))
            reply_text = "".join(content_parts)

        if not reply_text:
            reply_text = "I couldn't process that. Please try again."

        # Truncate for WhatsApp
        if len(reply_text) > 1500:
            reply_text = reply_text[:1450] + "\n\n... [Ask for more details]"

        # Send WhatsApp reply
        send_direct_reply(f"whatsapp:{from_number}", reply_text)
        print(f"[ROUTER] AgentCore response sent to {from_number}")
        return True

    except Exception as e:
        print(f"[ROUTER] AgentCore invocation failed: {e}")
        # Fallback to Lambda
        print("[ROUTER] Falling back to Lambda...")
        return forward_to_lambda("legal", event_body, is_base64)


def forward_to_lambda(bot_key: str, event_body: str, is_base64: bool = False):
    """Forward to Lambda (farm bot or legal fallback)."""
    target_function = BOTS[bot_key]["function_name"]

    payload = {
        "body": event_body,
        "isBase64Encoded": is_base64,
    }

    try:
        lambda_client.invoke(
            FunctionName=target_function,
            InvocationType="Event",  # Async — don't wait for response
            Payload=json.dumps(payload).encode(),
        )
        print(f"[ROUTER] Forwarded to {target_function}")
    except Exception as e:
        print(f"[ROUTER] Forward failed: {e}")
        return False
    return True


def send_direct_reply(to: str, body: str):
    """Send a direct WhatsApp reply (for router-level messages like /help)."""
    import urllib.error

    if len(body) > 1500:
        body = body[:1450] + "\n\n..."

    url = f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/Messages.json"

    encoded_to = urllib.parse.quote(to, safe='')
    encoded_from = urllib.parse.quote(f"whatsapp:{TWILIO_WHATSAPP_NUMBER}", safe='')
    encoded_body = urllib.parse.quote(body, safe='')
    data = f"To={encoded_to}&From={encoded_from}&Body={encoded_body}".encode()

    credentials = base64.b64encode(
        f"{TWILIO_ACCOUNT_SID}:{TWILIO_AUTH_TOKEN}".encode()
    ).decode()

    req = urllib.request.Request(
        url, data=data,
        headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )

    try:
        urllib.request.urlopen(req)
    except Exception as e:
        print(f"[ROUTER] Direct reply failed: {e}")


def lambda_handler(event, context):
    """
    Main router Lambda handler.
    Parses Twilio webhook, determines target bot, forwards asynchronously.
    """
    try:
        # Parse body
        body = event.get("body", "")
        is_base64 = event.get("isBase64Encoded", False)

        if is_base64:
            raw_body = base64.b64decode(body).decode("utf-8")
        else:
            raw_body = body

        params = dict(urllib.parse.parse_qsl(raw_body))

        from_number = params.get("From", "").replace("whatsapp:", "")
        message_body = params.get("Body", "").strip()

        if not message_body:
            return {
                "statusCode": 200,
                "body": '<?xml version="1.0" encoding="UTF-8"?><Response></Response>',
                "headers": {"Content-Type": "application/xml"},
            }

        print(f"[ROUTER] From: {from_number} | Msg: {message_body[:80]}")

        # Handle router commands
        msg_lower = message_body.lower().strip()

        if msg_lower == "/help" or msg_lower == "help":
            send_direct_reply(f"whatsapp:{from_number}", HELP_MESSAGE)
            return {"statusCode": 200, "body": '<?xml version="1.0" encoding="UTF-8"?><Response></Response>', "headers": {"Content-Type": "application/xml"}}

        if msg_lower.startswith("/switch"):
            parts = msg_lower.split()
            if len(parts) >= 2 and parts[1] in BOTS:
                bot_key = parts[1]
                set_user_default(from_number, bot_key)
                bot_info = BOTS[bot_key]
                reply = f"✅ Default switched to *{bot_info['name']}* ({bot_info['hindi']})"
                send_direct_reply(f"whatsapp:{from_number}", reply)
            else:
                send_direct_reply(f"whatsapp:{from_number}", "Usage: /switch legal OR /switch farm")
            return {"statusCode": 200, "body": '<?xml version="1.0" encoding="UTF-8"?><Response></Response>', "headers": {"Content-Type": "application/xml"}}

        # Detect target bot
        bot_key, cleaned_message = detect_bot_from_message(message_body)

        if bot_key is None:
            # Use user's default
            bot_key = get_user_default(from_number)

        # If message was prefixed, update the Body in the forwarded payload
        if cleaned_message != message_body:
            params["Body"] = cleaned_message
            raw_body = urllib.parse.urlencode(params)

        print(f"[ROUTER] Routing to: {bot_key} ({BOTS[bot_key]['function_name']})")

        # Forward to target bot
        success = forward_to_bot(bot_key, raw_body, is_base64=False)

        if not success:
            send_direct_reply(
                f"whatsapp:{from_number}",
                f"⚠️ {BOTS[bot_key]['name']} is temporarily unavailable. Try again."
            )

        # Update last used
        set_user_default(from_number, bot_key)

        return {
            "statusCode": 200,
            "body": '<?xml version="1.0" encoding="UTF-8"?><Response></Response>',
            "headers": {"Content-Type": "application/xml"},
        }

    except Exception as e:
        print(f"[ROUTER] ERROR: {str(e)}")
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)}),
            "headers": {"Content-Type": "application/json"},
        }
