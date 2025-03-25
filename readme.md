# MudflapBot - Zendesk Monitor & Help Desk Bot

A Slack bot that combines Zendesk Talk monitoring with help desk functionality. It monitors agents' "Transfers Only" status, handles help requests, and provides automated support through Guru integration.

## Table of Contents
- [Features](#features)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Configuration](#configuration)
  - [Slack App Setup](#slack-app-setup)
  - [Zendesk Setup](#zendesk-setup)
  - [Guru Setup](#guru-setup)
  - [Google Sheets Setup](#google-sheets-setup)
- [Environment Setup](#environment-setup)
- [Running the Bot](#running-the-bot)
- [Usage](#usage)
- [Workflow Diagrams](#workflow-diagrams)
- [Channel Structure](#channel-structure)
- [Troubleshooting](#troubleshooting)
- [Security Notes](#security-notes)
- [Support](#support)

## Features

- Monitors Zendesk Talk agents' "Transfers Only" status
- Sends alerts after 10 minutes in "Transfers Only" status
- Routes help requests using @help command
- Integrates with Guru knowledge base
- Logs all interactions to Google Sheets
- Handles feedback and escalation workflows

## Prerequisites

- Python 3.7+
- A Slack workspace with admin access
- Zendesk with Talk enabled
- Guru account with API access
- Google Cloud account
- ngrok (for local development)

## Installation

1. Clone the repository:
```bash
git clone [your-repository]
cd [repository-name]
```

2. Create virtual environment:
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. Install required packages:
```bash
pip install slack-bolt flask python-dotenv requests gspread google-auth
```

## Configuration

### Slack App Setup

1. Create New Slack App:
   - Go to https://api.slack.com/apps
   - Click "Create New App"
   - Choose "From scratch"
   - Name it "SlackBot" and select your workspace

2. Bot Token Scopes (OAuth & Permissions):
   Add these scopes:
   ```
   app_mentions:read
   channels:history
   channels:join
   channels:read
   chat:write
   groups:history
   groups:read
   im:history
   im:read
   im:write
   reactions:read
   reactions:write
   users:read
   users:read.email
   ```

3. Event Subscriptions:
   - Enable Events
   - Add these events:
     ```
     app_mention
     message.channels
     message.groups
     message.im
     ```
   - Subscribe to bot events:
     ```
     app_mentions
     message.channels
     message.im
     ```

4. Install app to workspace
   - Note down Bot User OAuth Token (starts with `xoxb-`)
   - Note down Signing Secret

### Zendesk Setup

1. Go to Admin → Channels → API
2. Create new API token
3. Note your:
   - Subdomain (your-company.zendesk.com)
Wc   - Email address
   - API token

### Guru Setup

1. Go to Admin → API Settings
2. Create new API token
3. Note your:
   - User email
   - API token
   - Organization ID (found in URL)

### Google Sheets Setup

1. Google Cloud Console:
   - Create new project
   - Enable Google Sheets API
   - Create Service Account
   - Download JSON key
   - Rename to `service_account.json`

2. Google Sheet:
   - Create new sheet
   - Share with service account email
   - Note Sheet ID from URL

## Environment Setup

1. Create `.env` file:
```env
SLACK_BOT_TOKEN=xoxb-your-bot-token
SLACK_SIGNING_SECRET=your-signing-secret
GURU_API_TOKEN=your-guru-token
GURU_USER_EMAIL=your-guru-email
GURU_ORG_ID=your-org-id
ZENDESK_DOMAIN=your-subdomain
ZENDESK_EMAIL=your-zendesk-email
ZENDESK_API_TOKEN=your-zendesk-token
APP_PORT=3000
```

2. Update Channel IDs in code:
```python
self.HELP_CHANNEL_ID = "your-help-channel-id"
self.CS_LEADS_CHANNEL_ID = "your-cs-leads-channel-id"
```

## Running the Bot

1. Start ngrok (for development):
```bash
ngrok http 3000
```

2. Update Slack Event Subscription URL:
   - Use your ngrok URL + /slack/events
   - Example: https://your-ngrok-url.ngrok.io/slack/events

3. Run the bot:
```bash
source venv/bin/activate
python SlackBot.py
```

## Usage

### Zendesk Monitoring
- Automatically monitors agents in "Transfers Only" status
- Sends alerts after 10 minutes
- Click ✅ to acknowledge alerts
- Acknowledgments appear in thread

### Help Desk Features
- `@help [question]`: Search Guru knowledge base
- `@customersupportleads [message]`: Alert CS leads
- Users can provide feedback on answers
- Negative feedback triggers escalation workflow

## Workflow Diagrams

### 1. Zendesk Monitoring Flow
```mermaid
sequenceDiagram
    participant ZM as Zendesk Monitor
    participant ZA as Zendesk API
    participant SL as Slack
    participant AG as Agent

    loop Every 60 seconds
        ZM->>ZA: Get all agents
        ZA-->>ZM: Return agents list
        loop Each Agent
            ZM->>ZA: Check agent status
            ZA-->>ZM: Return availability
            alt is in Transfers Only
                ZM->>ZM: Track duration
                alt Duration ≥ 10 minutes
                    ZM->>SL: Send alert to CS Leads channel
                    SL-->>AG: Show alert with ✅ button
                    AG->>SL: Click ✅ button
                    SL-->>ZM: Acknowledgment
                    ZM->>SL: Post acknowledgment in thread
                end
            end
        end
    end
```

### 2. Help Request Flow
```mermaid
sequenceDiagram
    participant U as User
    participant SL as Slack
    participant B as Bot
    participant GU as Guru API
    participant GS as Google Sheets

    U->>SL: Send "@help question"
    SL->>B: Message event
    B->>GU: Search cards
    GU-->>B: Return matching cards
    B->>SL: Post answer with feedback buttons
    B->>GS: Log interaction
    
    alt Positive Feedback
        U->>SL: Click 👍
        SL->>B: Button click
        B->>GS: Update feedback as "Yes"
        B->>SL: Update message
    else Negative Feedback
        U->>SL: Click 👎
        SL->>B: Button click
        B->>GS: Update feedback as "No"
        B->>SL: Post escalation to Help channel
        B->>SL: Notify user
    end
```

### 3. Escalation Process Flow
```mermaid
sequenceDiagram
    participant U as User
    participant SL as Slack
    participant B as Bot
    participant GS as Google Sheets
    participant M as Manager

    U->>SL: Give negative feedback
    SL->>B: Feedback event
    B->>SL: Post escalation to Help channel
    B->>SL: Update original thread
    B->>GS: Log escalation
    
    M->>SL: Click "Accept Request"
    SL->>B: Accept event
    B->>GS: Update with manager name
    B->>SL: Update Help channel message
    B->>SL: DM user with acceptance
    B->>SL: Update original thread
```

### 4. CS Leads Alert Flow
```mermaid
sequenceDiagram
    participant U as User
    participant SL as Slack
    participant B as Bot
    participant CS as CS Leads Channel

    U->>SL: Mention "@customersupportleads"
    SL->>B: Message event
    B->>SL: Get thread permalink
    B->>CS: Forward message with context
    B->>SL: Continue original thread
```

## Channel Structure

- CS Leads Channel (C060L9NABT5):
  - Receives Zendesk monitoring alerts
  - Receives @customersupportleads mentions

- Help Channel (C060LCFHSGJ):
  - Receives escalated help requests
  - Handles request acceptance workflow

## Troubleshooting

### Slack Issues
1. Check Event Subscription URL status
2. Verify bot token and signing secret
3. Ensure bot is invited to channels
4. Check required scopes

### Zendesk Issues
1. Verify API token permissions
2. Check Talk is enabled
3. Confirm agent IDs format

### Guru Issues
1. Verify API token permissions
2. Check organization ID format
3. Confirm search API access

### Google Sheets Issues
1. Check service account file presence
2. Verify sheet permissions
3. Confirm sheet ID format

## Security Notes

- Keep `.env` file secure and never commit to version control
- Implement regular token rotation
- Use HTTPS in production
- Monitor access logs
- Restrict API tokens to necessary permissions only
- Regularly audit access logs and permissions

## Support

For additional support:
1. Check the logs in `app.log`
2. Review Slack App configuration
3. Verify API credentials
4. Contact [your-support-email]

## Excluded Agents

To exclude agents from monitoring, update their IDs in ZendeskMonitor class:
```python
self.excluded_agents = {agent_id_1, agent_id_2}
```

## Logs

Check `app.log` for detailed logging information. The log includes:
- Monitoring cycles
- Alert triggers
- User interactions
- API errors
- System status
