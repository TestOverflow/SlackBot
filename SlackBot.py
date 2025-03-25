import time
import os
import logging
import json
import requests
import base64
import gspread
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any, Set
from google.oauth2.service_account import Credentials
from slack_bolt import App
from slack_bolt.adapter.flask import SlackRequestHandler
from flask import Flask, request
from dotenv import load_dotenv
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from functools import wraps
from threading import Thread

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("app.log", mode="a")
    ]
)
logger = logging.getLogger(__name__)

class RateLimiter:
    """Custom rate limiter implementation"""
    def __init__(self, calls_per_minute):
        self.calls_per_minute = calls_per_minute
        self.calls = []
        
    def __call__(self, func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            now = time.time()
            self.calls = [call_time for call_time in self.calls if now - call_time < 60]
            
            if len(self.calls) >= self.calls_per_minute:
                sleep_time = 60 - (now - self.calls[0])
                if sleep_time > 0:
                    time.sleep(sleep_time)
                    
            result = func(*args, **kwargs)
            self.calls.append(time.time())
            return result
        return wrapper

class Config:
    """Configuration management class"""
    def __init__(self):
        load_dotenv()
        # Slack configuration
        self.SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")
        self.SLACK_SIGNING_SECRET = os.getenv("SLACK_SIGNING_SECRET")
        
        # Guru configuration
        self.GURU_API_TOKEN = os.getenv("GURU_API_TOKEN")
        self.GURU_USER_EMAIL = os.getenv("GURU_USER_EMAIL")
        self.GURU_AGENT_ID = os.getenv("GURU_AGENT_ID")  # No default value provided
        self.GURU_ORG_ID = os.getenv("GURU_ORG_ID")
        
        # Channel IDs (Set these in your .env file)
        self.HELP_CHANNEL_ID = os.getenv("HELP_CHANNEL_ID")
        self.CS_LEADS_CHANNEL_ID = os.getenv("CS_LEADS_CHANNEL_ID")
        
        # Google Sheets configuration
        self.GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID")
        
        # Zendesk configuration
        self.ZENDESK_DOMAIN = os.getenv("ZENDESK_DOMAIN")
        self.ZENDESK_EMAIL = os.getenv("ZENDESK_EMAIL")
        self.ZENDESK_API_TOKEN = os.getenv("ZENDESK_API_TOKEN")

class GuruAPI:
    """Guru API interaction class"""
    def __init__(self, config: Config):
        self.config = config
        self.auth_header = base64.b64encode(
            f"{config.GURU_USER_EMAIL}:{config.GURU_API_TOKEN}".encode()
        ).decode()
        
    def search_cards(self, query: str) -> List[Dict[str, Any]]:
        """Search Guru cards with error handling"""
        try:
            response = requests.get(
                "https://api.getguru.com/api/v1/search/query",
                headers=self._get_headers(),
                params={
                    "searchTerms": query,
                    "organizationId": self.config.GURU_ORG_ID,
                    "typeFilter": "CARD",
                    "limit": 5
                },
                timeout=10
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Guru API error: {e}")
            return []

    def get_answer(self, question: str) -> Optional[Dict[str, Any]]:
        """Get AI-generated answer from Guru"""
        try:
            response = requests.post(
                "https://api.getguru.com/api/v1/answers",
                headers=self._get_headers(),
                json={
                    "organizationId": self.config.GURU_ORG_ID,
                    "agentId": self.config.GURU_AGENT_ID,
                    "question": question
                },
                timeout=10
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Error getting Guru answer: {e}")
            return None

    def _get_headers(self) -> Dict[str, str]:
        """Get common headers for Guru API requests"""
        return {
            "Authorization": f"Basic {self.auth_header}",
            "Content-Type": "application/json"
        }

class GoogleSheetsLogger:
    """Google Sheets logging class"""
    def __init__(self, config: Config):
        self.config = config
        # Ensure your service_account.json is NOT committed to version control
        creds = Credentials.from_service_account_file(
            "service_account.json",
            scopes=["https://www.googleapis.com/auth/spreadsheets"]
        )
        self.gc = gspread.authorize(creds)
        self.sheet = self.gc.open_by_key(config.GOOGLE_SHEET_ID).sheet1

    def log_entry(self, user_id: str, question: str, answer: str,
                  feedback: str = "Pending", manager: str = "Pending") -> None:
        """Log entries with deduplication"""
        try:
            real_name = get_slack_user_name(user_id)
            timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            
            # Check for duplicates
            existing_data = self.sheet.get_all_values()[1:]
            for row in existing_data:
                if (len(row) >= 3 and row[1] == real_name and 
                    row[2].strip().lower() == question.strip().lower()):
                    logger.info(f"Duplicate entry found for {real_name}")
                    return

            new_entry = [timestamp, real_name, question, answer, feedback, manager]
            self.sheet.append_row(new_entry)
            logger.info(f"Successfully logged entry for {real_name}")
        except Exception as e:
            logger.error(f"Error logging to Google Sheets: {e}")

    def update_feedback(self, user_id: str, question: str, feedback: str, manager: str) -> None:
        """Update feedback and manager columns"""
        try:
            user_name = get_slack_user_name(user_id)
            row_num = self.find_row_by_question(user_name, question)
            
            if row_num:
                self.sheet.update_cell(row_num, 5, feedback)
                self.sheet.update_cell(row_num, 6, manager)
                logger.info(f"Updated feedback to {feedback} and manager to {manager} for {user_name}")
            else:
                logger.warning(f"No matching row found for {user_name} and question: {question}")
        except Exception as e:
            logger.error(f"Error updating feedback: {e}")

    def find_row_by_question(self, user_name: str, question: str) -> Optional[int]:
        """Find the row number for a specific user and question"""
        try:
            data = self.sheet.get_all_values()
            for idx, row in enumerate(data[1:], start=2):
                if (len(row) >= 3 and 
                    row[1].strip() == user_name.strip() and 
                    row[2].strip() == question.strip()):
                    return idx
            return None
        except Exception as e:
            logger.error(f"Error finding row: {e}")
            return None

class ZendeskMonitor:
    """Zendesk monitoring functionality"""
    def __init__(self, config: Config, slack_client):
        self.config = config
        self.slack_client = slack_client
        self.check_interval = 60  # seconds
        self.alert_threshold = 600  # seconds (10 minutes)
        self.agent_status_times: Dict[int, datetime] = {}
        self.alerted_agents: Set[int] = set()
        # Optionally, add agent IDs to exclude by setting the EXCLUDED_AGENTS env variable (comma-separated IDs)
        excluded = os.getenv("EXCLUDED_AGENTS", "")
        self.excluded_agents = set(map(int, excluded.split(","))) if excluded else set()

    def get_zendesk_headers(self) -> Dict:
        auth_str = f"{self.config.ZENDESK_EMAIL}/token:{self.config.ZENDESK_API_TOKEN}"
        encoded_auth = base64.b64encode(auth_str.encode()).decode()
        return {
            'Authorization': f'Basic {encoded_auth}',
            'Content-Type': 'application/json'
        }

    def get_agents(self) -> List[Dict]:
        url = f"https://{self.config.ZENDESK_DOMAIN}.zendesk.com/api/v2/users?role=agent"
        try:
            response = requests.get(url, headers=self.get_zendesk_headers())
            response.raise_for_status()
            return response.json().get('users', [])
        except requests.exceptions.RequestException as e:
            logger.error(f"Error fetching agents: {e}")
            return []

    def get_agent_availability(self, agent_id: int) -> Dict:
        url = f"https://{self.config.ZENDESK_DOMAIN}.zendesk.com/api/v2/channels/voice/availabilities/{agent_id}"
        try:
            response = requests.get(url, headers=self.get_zendesk_headers())
            response.raise_for_status()
            return response.json().get('availability', {})
        except requests.exceptions.RequestException:
            return {}

    def send_slack_alert(self, agent_name: str, duration: int) -> None:
        """Send alert to a designated channel about an agent's extended status."""
        try:
            message = {
                "channel": self.config.CS_LEADS_CHANNEL_ID,
                "text": f"⚠️ Alert: Agent {agent_name} has been in Transfers Only status for 10 minutes.",
                "blocks": [
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"*Agent Status Alert*\n⚠️ Agent *{agent_name}* has been in Transfers Only status for 10 minutes."
                        }
                    },
                    {
                        "type": "actions",
                        "elements": [
                            {
                                "type": "button",
                                "text": {
                                    "type": "plain_text",
                                    "text": "✅",
                                    "emoji": True
                                },
                                "style": "primary",
                                "value": f"{agent_name}",
                                "action_id": "acknowledge_alert"
                            }
                        ]
                    }
                ]
            }
            
            self.slack_client.chat_postMessage(**message)
            logger.info(f"Successfully sent alert for agent {agent_name}")
        except Exception as e:
            logger.error(f"Error sending Slack alert: {e}")

    def format_duration(self, seconds: float) -> str:
        minutes, seconds = divmod(int(seconds), 60)
        hours, minutes = divmod(minutes, 60)
        
        if hours > 0:
            return f"{hours}h {minutes}m {seconds}s"
        elif minutes > 0:
            return f"{minutes}m {seconds}s"
        else:
            return f"{seconds}s"

    def print_status_summary(self, transfers_only_agents: List[tuple]) -> None:
        print("\n" + "="*50)
        print("AGENT STATUS SUMMARY")
        print("="*50)
        
        if transfers_only_agents:
            print("\nAgents in Transfers Only status:")
            print("-" * 40)
            for agent_name, duration in transfers_only_agents:
                formatted_duration = self.format_duration(duration)
                print(f"📱 {agent_name}: {formatted_duration}")
        else:
            print("\nNo agents currently in Transfers Only status")
        
        print("\n" + "="*50 + "\n")

    def monitor_agents(self) -> None:
        logger.info("Starting Zendesk agent status monitoring...")
    
        while True:
            current_time = datetime.now()
            agents = self.get_agents()
            transfers_only_agents = []
            
            if agents:
                for agent in agents:
                    agent_id = agent['id']
                    
                    if agent_id in self.excluded_agents:
                        continue
                        
                    agent_name = agent['name']
                    availability = self.get_agent_availability(agent_id)
                    
                    if availability:
                        agent_state = availability.get('agent_state')
                        
                        if agent_state == 'transfers_only':
                            if agent_id not in self.agent_status_times:
                                self.agent_status_times[agent_id] = current_time
                                self.alerted_agents.discard(agent_id)
                                logger.info(f"Started tracking {agent_name} in transfers_only status")
                            
                            duration = (current_time - self.agent_status_times[agent_id]).total_seconds()
                            transfers_only_agents.append((agent_name, duration))
                            
                            if duration >= self.alert_threshold and agent_id not in self.alerted_agents:
                                self.send_slack_alert(agent_name, int(duration))
                                self.alerted_agents.add(agent_id)
                                logger.info(f"Alert sent for {agent_name} - added to alerted agents")
                        else:
                            if agent_id in self.agent_status_times:
                                logger.info(f"{agent_name} no longer in transfers_only status")
                            self.agent_status_times.pop(agent_id, None)
                            self.alerted_agents.discard(agent_id)
                
                transfers_only_agents.sort(key=lambda x: x[1], reverse=True)
                self.print_status_summary(transfers_only_agents)
                logger.info(f"Monitoring cycle completed. Found {len(transfers_only_agents)} agents in transfers_only status")
            
            time.sleep(self.check_interval)

    def start_monitoring(self):
        """Start the monitoring loop in a separate thread"""
        thread = Thread(target=self.monitor_agents)
        thread.daemon = True
        thread.start()

# Initialize components
config = Config()
app = App(token=config.SLACK_BOT_TOKEN, signing_secret=config.SLACK_SIGNING_SECRET)
flask_app = Flask(__name__)
handler = SlackRequestHandler(app)

# Helper function to fetch Slack user name (with caching if desired)
def get_slack_user_name(user_id: str) -> str:
    try:
        response = app.client.users_info(user=user_id)
        return response["user"]["real_name"] if response.get("ok") else user_id
    except Exception as e:
        logger.error(f"Error fetching user name for {user_id}: {e}")
        return user_id

# Complete initialization of components
guru_api = GuruAPI(config)
sheets_logger = GoogleSheetsLogger(config)
zendesk_monitor = ZendeskMonitor(config, app.client)

# Event Handlers
@app.event("message")
def handle_message(body: Dict[str, Any], event: Dict[str, Any], say: callable, client: Any) -> None:
    """Handle incoming Slack messages and route them accordingly."""
    try:
        text = event.get("text", "")
        user_id = event.get("user")
        channel = event.get("channel")
        thread_ts = event.get("thread_ts") or event.get("ts")
        
        real_name = get_slack_user_name(user_id)

        # Handle CS leads mention - route to CS leads channel
        if "@customersupportleads" in text.replace(" ", ""):
            logger.info(f"CS leads mention from {real_name}")
            cleaned_message = text.replace("@customersupportleads", "").strip()
            
            # Get thread permalink
            try:
                permalink_response = client.chat_getPermalink(
                    channel=channel,
                    message_ts=thread_ts
                )
                thread_link = permalink_response["permalink"] if permalink_response.get("ok") else "Thread link unavailable"
            except Exception as e:
                logger.error(f"Error getting thread permalink: {e}")
                thread_link = "Thread link unavailable"

            # Send message to CS leads channel (set via env variable)
            slack_message = (
                f"🚨 *Customer Support Alert*\n"
                f"👤 *User:* {real_name}\n"
                f"💬 *Message:* {cleaned_message}\n"
                f"🔗 *< {thread_link} | Go to Thread >*"
            )

            client.chat_postMessage(
                channel=config.CS_LEADS_CHANNEL_ID,
                text=slack_message
            )
            return

        # Handle help requests
        if text.lower().startswith("@help"):
            query_text = text.replace("@help", "").strip()
            if not query_text:
                return

            # Search Guru cards
            cards = guru_api.search_cards(query_text)
            answer_text = ""

            if cards:
                answer_text = "📚 *Here are some relevant Guru cards:*\n"
                for card in cards[:5]:
                    card_title = card.get("preferredPhrase", "Untitled Card")
                    card_slug = card.get("slug", "")
                    card_url = f"https://app.getguru.com/card/{card_slug}" if card_slug else "#"
                    answer_text += f"🔹 *<{card_url}|{card_title}>*\n"
            else:
                answer_text = "🤖 Sorry, I couldn't find an answer. Please escalate if needed."

            # Send response in the original thread
            say(
                thread_ts=thread_ts,
                text=f"🤖 *Guru Answer:*\n{answer_text}"
            )

            # Add feedback buttons
            feedback_message = "Was this answer helpful?"
            say(
                thread_ts=thread_ts,
                text=feedback_message,
                blocks=[
                    {
                        "type": "section",
                        "text": {"type": "mrkdwn", "text": feedback_message}
                    },
                    {
                        "type": "actions",
                        "elements": [
                            {
                                "type": "button",
                                "text": {"type": "plain_text", "text": "👍 Yes"},
                                "style": "primary",
                                "value": json.dumps({
                                    "user": user_id,
                                    "question": query_text,
                                    "thread_ts": thread_ts,
                                    "channel": channel
                                }),
                                "action_id": "feedback_yes"
                            },
                            {
                                "type": "button",
                                "text": {"type": "plain_text", "text": "👎 No"},
                                "style": "danger",
                                "value": json.dumps({
                                    "user": user_id,
                                    "question": query_text,
                                    "thread_ts": thread_ts,
                                    "channel": channel
                                }),
                                "action_id": "feedback_no"
                            }
                        ]
                    }
                ]
            )

            # Log the interaction to Google Sheets
            sheets_logger.log_entry(user_id, query_text, answer_text)

    except Exception as e:
        logger.error(f"Error handling message: {e}")

@app.action("acknowledge_alert")
def handle_acknowledgment(ack, body, client):
    try:
        ack()
        agent_name = body["actions"][0]["value"]
        user = body["user"]["id"]
        
        # Post acknowledgment as a thread reply
        client.chat_postMessage(
            channel=body["channel"]["id"],
            thread_ts=body["message"]["ts"],
            text=f"✅ Alert acknowledged by <@{user}>"
        )
        
        # Update the original message to remove the button while retaining the alert
        client.chat_update(
            channel=body["channel"]["id"],
            ts=body["message"]["ts"],
            text=f"⚠️ Alert: Agent {agent_name} has been in Transfers Only status for 10 minutes.",
            blocks=[{
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Agent Status Alert*\n⚠️ Agent *{agent_name}* has been in Transfers Only status for 10 minutes."
                }
            }]
        )
    except Exception as e:
        logger.error(f"Error handling acknowledgment: {e}")

@app.action("feedback_yes")
def handle_feedback_yes(ack: callable, body: Dict[str, Any], client: Any) -> None:
    """Handle positive feedback."""
    try:
        ack()
        user_data = json.loads(body["actions"][0]["value"])
        user_id = user_data["user"]
        question = user_data["question"]
        channel_id = body["channel"]["id"]
        message_ts = body["message"]["ts"]

        # Update Google Sheets with positive feedback
        sheets_logger.update_feedback(user_id, question, "Yes", "N/A")

        # Update the message with a feedback acknowledgment
        feedback_text = "Was this answer helpful?\n👍 Yes - Thanks for your feedback!"
        client.chat_update(
            channel=channel_id,
            ts=message_ts,
            text=feedback_text,
            blocks=[{
                "type": "section",
                "text": {"type": "mrkdwn", "text": feedback_text}
            }]
        )

        logger.info(f"Positive feedback logged for {get_slack_user_name(user_id)}")
    except Exception as e:
        logger.error(f"Error handling positive feedback: {e}")

@app.action("feedback_no")
def handle_feedback_no(ack: callable, body: Dict[str, Any], client: Any) -> None:
    """Handle negative feedback."""
    try:
        ack()
        user_data = json.loads(body["actions"][0]["value"])
        user_id = user_data["user"]
        question = user_data["question"]
        thread_ts = user_data["thread_ts"]
        channel = body["channel"]["id"]
        message_ts = body["message"]["ts"]

        # Update Google Sheets with negative feedback
        sheets_logger.update_feedback(user_id, question, "No", "Pending")

        # Update the message to reflect that the request is escalated
        feedback_text = "Was this answer helpful?\n👎 No - Your request has been escalated."
        client.chat_update(
            channel=channel,
            ts=message_ts,
            text=feedback_text,
            blocks=[{
                "type": "section",
                "text": {"type": "mrkdwn", "text": feedback_text}
            }]
        )

        # Get thread permalink
        try:
            permalink_response = client.chat_getPermalink(
                channel=channel,
                message_ts=thread_ts
            )
            thread_link = permalink_response["permalink"] if permalink_response.get("ok") else "Thread link unavailable"
        except Exception as e:
            logger.error(f"Error getting thread permalink: {e}")
            thread_link = "Thread link unavailable"

        # Send escalation message to the help channel
        escalation_text = (
            f"🚨 *Escalation Request*\n"
            f"👤 *User:* <@{user_id}>\n"
            f"❓ *Original Question:* {question}\n"
            f"⚡ *Assistance Needed!*\n"
            f"🔗 *< {thread_link} | Go to Thread >*"
        )

        client.chat_postMessage(
            channel=config.HELP_CHANNEL_ID,
            text=escalation_text,
            blocks=[
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": escalation_text}
                },
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "✅ Accept Request"},
                            "style": "primary",
                            "value": json.dumps({
                                "user": user_id,
                                "question": question,
                                "thread_ts": thread_ts,
                                "channel": channel,
                                "thread_link": thread_link
                            }),
                            "action_id": "accept_request"
                        }
                    ]
                }
            ]
        )

        # Notify the user in the thread that their request has been escalated
        client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            text="🚨 Your request has been escalated. Someone will assist you shortly!"
        )

    except Exception as e:
        logger.error(f"Error handling negative feedback: {e}")

@app.action("accept_request")
def handle_accept_request(ack: callable, body: Dict[str, Any], client: Any) -> None:
    """Handle request acceptance by a manager."""
    try:
        ack()
        manager_id = body["user"]["id"]
        user_data = json.loads(body["actions"][0]["value"])
        user_id = user_data["user"]
        question = user_data["question"]
        thread_link = user_data.get("thread_link", "Thread link unavailable")
        original_channel = user_data.get("channel")
        thread_ts = user_data.get("thread_ts")
        message_ts = body["message"]["ts"]

        manager_name = get_slack_user_name(manager_id)
        user_name = get_slack_user_name(user_id)

        # Update Google Sheets with manager's acceptance
        sheets_logger.update_feedback(user_id, question, "No", manager_name)

        # Update the help channel message to reflect request acceptance
        update_text = (
            f"✅ *Request Accepted by {manager_name}*\n"
            f"👤 *User:* {user_name}\n"
            f"❓ *Original Question:* {question}\n"
            f"🔧 Assistance in progress!\n"
            f"🔗 *< {thread_link} | Go to Thread >*"
        )
        
        client.chat_update(
            channel=config.HELP_CHANNEL_ID,
            ts=message_ts,
            text=update_text,
            blocks=[{
                "type": "section",
                "text": {"type": "mrkdwn", "text": update_text}
            }]
        )

        # DM the user to notify them that the request has been accepted
        dm_text = (
            f"✅ *Your escalation request has been accepted by {manager_name}!*\n"
            f"🛠 They will respond in the original thread: {thread_link}"
        )
        client.chat_postMessage(
            channel=user_id,
            text=dm_text
        )

        # Post in the original thread that the request has been accepted
        if original_channel and thread_ts:
            acceptance_text = f"✅ *{manager_name} has accepted this request and will respond shortly!*"
            client.chat_postMessage(
                channel=original_channel,
                thread_ts=thread_ts,
                text=acceptance_text
            )

    except Exception as e:
        logger.error(f"Error handling request acceptance: {e}")

@flask_app.route("/slack/events", methods=["POST"])
def slack_events() -> Any:
    """Handle Slack events webhook."""
    return handler.handle(request)

def main() -> None:
    """Main application entry point."""
    try:
        # Start Zendesk monitoring in the background
        zendesk_monitor.start_monitoring()
        
        # Start Flask app
        port = int(os.getenv("APP_PORT", 3000))
        logger.info(f"Starting application on port {port}")
        flask_app.run(host="0.0.0.0", port=port)
    except Exception as e:
        logger.error(f"Application startup failed: {e}")
        raise

if __name__ == "__main__":
    main()