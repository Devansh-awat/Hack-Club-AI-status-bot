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
from http.server import BaseHTTPRequestHandler, HTTPServer
import threading

load_dotenv()

SLACK_TOKEN = os.getenv("SLACK_TOKEN")
CANVAS_ID = os.getenv("CANVAS_ID")

hcai_helpers_channel = "C0BDHH4SGDT"
hcai_help_channel = "C0BDLT68ENN"
hackclub_ai_channel = "C099S1LLFFU"

ENDPOINT = "https://ai.hackclub.com/up"
STATE_FILE = "bot_json"
HISTORY_FILE = "history.json"

client = WebClient(token=SLACK_TOKEN, retry_handlers=[])
db_lock = threading.Lock()

def get_status_data():
    try:
        response = requests.get(ENDPOINT, timeout=10)
        return response.json()
    except Exception as e:
        print(f"Error getting status data: {e}")
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

def log_status_history(status_data):
    if "timestamp" not in status_data:
        status_data["timestamp"] = int(time.time() * 1000)
    with db_lock:
        history = []
        if os.path.exists(HISTORY_FILE):
            try:
                with open(HISTORY_FILE, 'r') as f:
                    history = json.load(f)
            except Exception:
                pass
        history.append(status_data)
        with open(HISTORY_FILE, 'w') as f:
            json.dump(history, f)

def get_full_history():
    with db_lock:
        history = []
        if os.path.exists(HISTORY_FILE):
            try:
                with open(HISTORY_FILE, 'r') as f:
                    history = json.load(f)
            except Exception:
                pass
    
    formatted_history = []
    for entry in history:
        if "balanceRemaining" in entry:
            formatted_history.append(entry)
        elif "balance" in entry:
            iso_ts = entry.get("timestamp")
            epoch_ms = 0
            if iso_ts:
                try:
                    dt = datetime.datetime.fromisoformat(iso_ts)
                    epoch_ms = int(dt.timestamp() * 1000)
                except Exception:
                    pass
            formatted_history.append({
                "status": "unknown",
                "balanceRemaining": entry["balance"],
                "dailyKeyUsageRemaining": None,
                "replicateUnusedCredit": None,
                "timestamp": epoch_ms
            })
    return formatted_history

def calculate_usage_rate_and_predictions(current_balance):
    history = get_full_history()
    if not history:
        return "Insufficient data", "Insufficient data", "Insufficient data"

    now = datetime.datetime.now(datetime.timezone.utc)
    cutoff = now - datetime.timedelta(days=5)

    entries = []
    for entry in history:
        ts = entry.get("timestamp")
        if isinstance(ts, (int, float)):
            dt = datetime.datetime.fromtimestamp(ts / 1000.0, tz=datetime.timezone.utc)
        else:
            continue
        
        bal = entry.get("balanceRemaining")
        if bal is None:
            continue
            
        entries.append((dt, bal))

    # Sort chronologically
    entries.sort(key=lambda x: x[0])

    # Filter for entries in the last 5 days
    entries_5d = [e for e in entries if e[0] >= cutoff]

    if len(entries_5d) < 2:
        return "Insufficient data", "Insufficient data", "Insufficient data"

    total_decrease = 0.0
    for i in range(1, len(entries_5d)):
        prev_dt, prev_bal = entries_5d[i-1]
        curr_dt, curr_bal = entries_5d[i]
        
        # Ensure refill doesn't confuse it
        delta = prev_bal - curr_bal
        if delta > 0:
            total_decrease += delta

    # Total time span in seconds
    time_span_seconds = (entries_5d[-1][0] - entries_5d[0][0]).total_seconds()
    
    if time_span_seconds < 60:
        return "Insufficient data", "Insufficient data", "Insufficient data"

    rate_per_second = total_decrease / time_span_seconds

    if rate_per_second <= 0:
        return "Never (no usage)", "Never (no usage)", "Never (no usage)"

    rate_per_day = rate_per_second * 24 * 3600

    def format_time_remaining(target):
        if current_balance <= target:
            return "Already reached"
        seconds_remaining = (current_balance - target) / rate_per_second
        
        td = datetime.timedelta(seconds=seconds_remaining)
        days = td.days
        hours, remainder = divmod(td.seconds, 3600)
        minutes, _ = divmod(remainder, 60)
        
        parts = []
        if days > 0:
            parts.append(f"{days}d")
        if hours > 0:
            parts.append(f"{hours}h")
        if minutes > 0 or not parts:
            parts.append(f"{minutes}m")
        return " ".join(parts)

    time_to_10 = format_time_remaining(10.0)
    time_to_0 = format_time_remaining(0.0)
    rate_str = f"${rate_per_day:.2f}/day"

    return time_to_10, time_to_0, rate_str

def generate_trend_graph():
    history = get_full_history()
    if not history:
        return None

    timestamps = []
    balances = []
    for entry in history:
        ts = entry.get("timestamp") # epoch ms
        if isinstance(ts, (int, float)):
            dt = datetime.datetime.fromtimestamp(ts / 1000.0, tz=datetime.timezone.utc)
        else:
            dt = ts
        timestamps.append(dt)
        balances.append(entry.get("balanceRemaining", 0.0))

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
            try:
                client.chat_update(channel=channel_id, ts=active_ts, text=alert_text)
                print(f"[{channel_id}] Alert updated with current balance.")
            except SlackApiError as e:
                print(f"[{channel_id}] Error updating alert: {e.response['error']}")
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
    status_data = get_status_data()
    if status_data is None:
        print("Skipping cycle due to API error.")
        return

    balance = status_data.get("balanceRemaining", 0.0)
    print(f"Current Balance: ${balance:.2f}")
    
    log_status_history(status_data)
    
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
        
        canvas_image_url = f"https://hcai.devansh.hackclub.app/balance_trend.png?t={int(time.time())}"

        if CANVAS_ID:
            try:
                time_to_10, time_to_0, rate_per_day = calculate_usage_rate_and_predictions(balance)
                
                # Format last updated time: e.g., "July 4 2:18 pm UTC"
                now_utc = datetime.datetime.now(datetime.timezone.utc)
                time_str = now_utc.strftime("%B %-d %-I:%M ") + now_utc.strftime("%p").lower() + " UTC"

                markdown_payload = (
                    f"# AI API Balance Status\n\n"
                    f"**Current Balance:** ${balance:.2f}\n\n"
                    f"**Usage Rate:** {rate_per_day}\n\n"
                    f"**Estimated time to reach $10:** {time_to_10}\n\n"
                    f"**Estimated time to reach $0:** {time_to_0}\n\n"
                    f"Balance trend:\n\n"
                )
                if canvas_image_url:
                    markdown_payload += f"![AI API Balance Trend]({canvas_image_url})\n\n"
                
                markdown_payload += f"Last updated {time_str}\n"

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

    msg_helpers = f"<@U059VC0UDEU> ${balance:.2f} remaining, please top it up or we'll see 100+msgs in <#{hcai_help_channel}> and <#{hackclub_ai_channel}> "
    msg_normal = f"Hack Club AI is out of balance, currently at ${balance:.2f} (yes again!)"

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

class HistoryRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/history':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            
            try:
                formatted_history = get_full_history()
                response_json = json.dumps(formatted_history)
                self.wfile.write(response_json.encode('utf-8'))
            except Exception as e:
                print(f"Error serving history: {e}")
                self.wfile.write(b"[]")
        elif self.path.startswith('/balance_trend.png'):
            if os.path.exists('balance_trend.png'):
                self.send_response(200)
                self.send_header('Content-Type', 'image/png')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                with open('balance_trend.png', 'rb') as f:
                    self.wfile.write(f.read())
            else:
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b"Not Found")
        elif self.path == '/':
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(b"<h1>Hack Club AI Status Bot History API</h1><p>Visit <a href='/history'>/history</a> for the JSON data.</p>")
        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not Found")

    def do_HEAD(self):
        if self.path == '/history' or self.path == '/' or self.path.startswith('/balance_trend.png'):
            self.send_response(200)
            if self.path == '/history':
                self.send_header('Content-Type', 'application/json')
            elif self.path.startswith('/balance_trend.png'):
                self.send_header('Content-Type', 'image/png')
            else:
                self.send_header('Content-Type', 'text/html')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

import socket

class HTTPServerV6(HTTPServer):
    address_family = socket.AF_INET6

def start_http_server():
    server_address = ('', 8000)
    try:
        httpd = HTTPServerV6(server_address, HistoryRequestHandler)
        print("Starting HTTP server on port 8000 (IPv6 dual-stack)...")
        httpd.serve_forever()
    except Exception as e:
        print(f"Failed to start HTTP server: {e}")

if __name__ == "__main__":
    server_thread = threading.Thread(target=start_http_server, daemon=True)
    server_thread.start()
    
    print("Starting Slack status bot...")
    while True:
        run_bot_cycle()
        time.sleep(60)