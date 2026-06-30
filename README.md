# hack club ai status bot

a small slack bot that watches the hack club ai api balance and lets people know when it's running low.

## what it does

- checks the balance from `https://ai.hackclub.com/up` every minute
- logs each reading to `history.json` and draws a trend graph (`balance_trend.png`)
- posts and pins a warning in slack when the balance drops below $10, and a critical alert when it goes negative
- cleans up the alerts and pins once the balance recovers
- uploads the graph to slack and updates a canvas with the current balance

## setup

1. install the deps:

   ```
   pip install requests python-dotenv slack_sdk matplotlib
   ```

2. make a `.env` file with your slack stuff:

   ```
   SLACK_TOKEN=xoxb-your-token
   CANVAS_ID=your-canvas-id
   ```

3. run it:

   ```
   python main.py
   ```

it'll keep running and check the balance once a minute.

## notes

- `bot_json`, `history.json` and `balance_trend.png` are generated at runtime and are gitignored
- channel ids and the user to ping are set near the top of `main.py`, change them to fit your workspace
