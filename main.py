import os
import time
import json
import datetime
import requests
from dotenv import load_dotenv
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

load_dotenv()

SLACK_TOKEN = os.getenv("SLACK_TOKEN")
CANVAS_ID = os.getenv("CANVAS_ID")

hcai_helpers_channel = "C0BEF63483A"
hcai_help_channel = "C0BDLT68ENN"
hackclub_ai_channel = "C099S1LLFFU"

ENDPOINT = "https://ai.hackclub.com/up"
STATE_FILE = "bot_json"
HISTORY_FILE = "history.json"

client = WebClient(token=SLACK_TOKEN)

def get_balance():
    try:
        response = requests.get(ENDPOINT, timeout=10)
        data = response.json()
        return data.get("balanceRemaining", 0.0)
    except Exception as e:
        print(f"Error getting balance: {e}")
        return None

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, 'r') as f:
            return json.load(f)
    return {
        "hcai_helpers_ts": None,
        "hcai_help_ts": None,
        "hackclub_ai_ts": None,
        "last_graph_file_id": None
    }

def save_state(state):
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f)

def log_balance_history(balance, timestamp):
    history = []
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, 'r') as f:
                history = json.load(f)
        except Exception:
            pass
    history.append({
        "timestamp": timestamp,
        "balance": balance
    })
    with open(HISTORY_FILE, 'w') as f:
        json.dump(history, f)

def generate_trend_graph():
    if not os.path.exists(HISTORY_FILE):
        return None
    try:
        with open(HISTORY_FILE, 'r') as f:
            history = json.load(f)
    except Exception:
        return None

    if not history:
        return None

    timestamps = []
    balances = []
    for entry in history:
        try:
            dt = datetime.datetime.fromisoformat(entry["timestamp"])
            timestamps.append(dt)
        except Exception:
            timestamps.append(entry["timestamp"])
        balances.append(entry["balance"])

    plt.figure(figsize=(10, 5))
    plt.plot(timestamps, balances, marker='o', linestyle='-', color='b')
    plt.title("AI API Balance Trend")
    plt.xlabel("Time")
    plt.ylabel("Balance ($)")
    plt.grid(True)
    plt.gcf().autofmt_xdate()

    graph_path = "balance_trend.png"
    plt.savefig(graph_path, bbox_inches='tight')
    plt.close()
    return graph_path

def manage_channel_alert(channel_id, state_key, should_alert, alert_text, state):
    active_ts = state.get(state_key)
    if should_alert:
        if not active_ts:
            try:
                res = client.chat_postMessage(channel=channel_id, text=alert_text)
                new_ts = res["ts"]
                client.pins_add(channel=channel_id, timestamp=new_ts)
                state[state_key] = new_ts
                print(f"[{channel_id}] Alert posted and pinned.")
            except SlackApiError as e:
                print(f"[{channel_id}] Error posting alert: {e.response['error']}")
    else:
        if active_ts:
            try:
                try:
                    client.pins_remove(channel=channel_id, timestamp=active_ts)
                except SlackApiError:
                    pass

                try:
                    replies_resp = client.conversations_replies(channel=channel_id, ts=active_ts)
                    replies = replies_resp.get("messages", [])
                    for reply in reversed(replies):
                        reply_ts = reply.get("ts")
                        if reply_ts != active_ts:
                            client.chat_delete(channel=channel_id, ts=reply_ts)
                except SlackApiError:
                    pass

                client.chat_delete(channel=channel_id, ts=active_ts)

                try:
                    history_resp = client.conversations_history(channel=channel_id, limit=50)
                    messages = history_resp.get("messages", [])
                    for msg in messages:
                        subtype = msg.get("subtype")
                        if subtype in ("pinned_item", "unpinned_item"):
                            item = msg.get("item", {})
                            if item.get("message", {}).get("ts") == active_ts or item.get("ts") == active_ts:
                                client.chat_delete(channel=channel_id, ts=msg["ts"])
                                print(f"[{channel_id}] Deleted system pin notification message.")
                except Exception as e:
                    print(f"[{channel_id}] Error cleaning system pin notifications: {e}")

                state[state_key] = None
                print(f"[{channel_id}] Balance recovered. Alert and pin notifications removed.")
            except SlackApiError as e:
                print(f"[{channel_id}] Error clearing alert: {e.response['error']}")

def run_bot_cycle():
    balance = get_balance()
    if balance is None:
        print("Skipping cycle due to API error.")
        return

    print(f"Current Balance: ${balance:.2f}")
    
    timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
    log_balance_history(balance, timestamp)
    
    graph_path = generate_trend_graph()
    
    state = load_state()
    
    if graph_path:
        old_file_id = state.get("last_graph_file_id")
        if old_file_id:
            try:
                client.files_delete(file=old_file_id)
                print(f"Deleted old graph file: {old_file_id}")
            except SlackApiError as e:
                print(f"Error deleting old graph: {e.response['error']}")
        
        new_file_id = None
        new_permalink = None
        try:
            upload_res = client.files_upload_v2(
                file=graph_path,
                title="AI API Balance Trend"
            )
            new_file_id = upload_res["file"]["id"]
            new_permalink = upload_res["file"]["permalink"]
            state["last_graph_file_id"] = new_file_id
            print(f"Uploaded new graph file: {new_file_id}")
        except SlackApiError as e:
            print(f"Error uploading graph to Slack: {e.response['error']}")
        
        if CANVAS_ID:
            try:
                markdown_payload = (
                    f"# AI API Balance Status\n\n"
                    f"**Current Balance:** ${balance:.2f}\n"
                    f"**Last Updated:** {timestamp}\n\n"
                    f"Balance trend:\n\n"
                )
                if new_permalink:
                    markdown_payload += f"![AI API Balance Trend]({new_permalink})\n"

                client.api_call(
                    "canvases.edit",
                    json={
                        "canvas_id": CANVAS_ID,
                        "changes": [
                            {
                                "operation": "replace",
                                "document_content": {
                                    "type": "markdown",
                                    "markdown": markdown_payload
                                }
                            }
                        ]
                    }
                )
                print("Canvas updated successfully.")
            except SlackApiError as e:
                print(f"Error updating canvas: {e.response['error']}")

    msg_helpers = f"WARNING: AI API balance is dropping! Only ${balance:.2f} remaining. <@U059VC0UDEU>"
    msg_normal = f"CRITICAL: AI API balance is negative! Currently at ${balance:.2f}."

    manage_channel_alert(
        channel_id=hcai_helpers_channel,
        state_key="hcai_helpers_ts",
        should_alert=(balance < 10.0),
        alert_text=msg_helpers,
        state=state
    )

    manage_channel_alert(
        channel_id=hcai_help_channel,
        state_key="hackclub_ai_ts",
        should_alert=(balance < 0.0),
        alert_text=msg_normal,
        state=state
    )

    manage_channel_alert(
        channel_id=hackclub_ai_channel,
        state_key="hackclub_ai_ts",
        should_alert=(balance < 0.0),
        alert_text=msg_normal,
        state=state
    )

    save_state(state)

if __name__ == "__main__":
    print("Starting Slack status bot...")
    while True:
        run_bot_cycle()
        time.sleep(60)