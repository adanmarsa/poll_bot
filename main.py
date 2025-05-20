import time
import requests
import json
import csv
import logging
import sys
import pytz
import os
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

# === LOGGING ===
logging.basicConfig(
    filename='poll_detector.log',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
console_handler = logging.StreamHandler()
console_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
logger.addHandler(console_handler)

# === CONFIG ===
try:
    BEARER_TOKEN = os.getenv('BEARER_TOKEN')
    TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
    TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')
    OUTPUT_CSV = os.getenv('OUTPUT_CSV', 'output.csv')
    GT_TOKEN = os.getenv('GT_TOKEN')
    GIST_ID = os.getenv('GIST_ID')

    for var in [BEARER_TOKEN, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, GT_TOKEN, GIST_ID]:
        if not var:
            raise ValueError("Missing required environment variable.")
except Exception as e:
    logger.error(f"❌ Config error: {e}")
    sys.exit(1)

# === CONSTANTS ===
RULE_VALUE = '-is:retweet from:LacaNew'   # Added candidate names
CANDIDATE_NAMES = ["Ruto", "William Ruto", "Rigathi", "Gachagua", "Matiangi", "Musyoka", "Omtatah", "Maraga", "Kalonzo"]
BLOCKLIST = ["movie", "food", "sport", "city", "music"]  # Re-enabled blocklist
HEADERS = {'Authorization': f'Bearer {BEARER_TOKEN}', 'Content-type': 'application/json'}

# === GIST STORAGE ===
def fetch_processed_ids_from_gist():
    gist_url = f"https://api.github.com/gists/{GIST_ID}"  # Fixed URL
    headers = {"Authorization": f"token {GT_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    try:
        response = requests.get(gist_url, headers=headers, timeout=10)
        if response.status_code != 200:
            logger.error(f"Gist fetch failed: {response.status_code} - {response.text}")
            return set()
        content = response.json()["files"]["processed_tweets.txt"]["content"]
        return set(line.strip() for line in content.splitlines() if line.strip() and not line.startswith("#"))
    except Exception as e:
        logger.error(f"Gist fetch error: {e}")
        return set()

def update_gist_with_new_ids(existing_ids, new_ids):
    try:
        all_ids = existing_ids.union(new_ids)
        content = "\n".join(sorted(all_ids))
        payload = {"files": {"processed_tweets.txt": {"content": content}}}
        gist_url = f"https://api.github.com/gists/{GIST_ID}"  # Fixed URL
        headers = {"Authorization": f"token {GT_TOKEN}", "Accept": "application/vnd.github.v3+json"}
        response = requests.patch(gist_url, headers=headers, json=payload, timeout=10)
        if response.status_code != 200:
            logger.error(f"Gist update failed: {response.status_code} - {response.text}")
        else:
            logger.info("✅ Gist updated with new tweet IDs.")
    except Exception as e:
        logger.error(f"Gist update error: {e}")

# === TWITTER SEARCH ===
def search_recent_tweets():
    logger.info("🔍 Searching for recent tweets...")
    now = datetime.now(pytz.UTC)
    start_time = (now - timedelta(minutes=120)).strftime("%Y-%m-%dT%H:%M:%SZ")
    logger.debug(f"Generated start_time: {start_time}")

    search_url = (
        f"https://api.twitter.com/2/tweets/search/recent"
        f"?query={requests.utils.quote(RULE_VALUE)}"
        f"&tweet.fields=created_at,attachments,author_id,text"
        f"&expansions=attachments.poll_ids,author_id"
        f"&poll.fields=duration_minutes,end_datetime,voting_status,options"
        f"&user.fields=username"
        f"&start_time={start_time}"
        f"&max_results=20"
    )

    for attempt in range(3):
        try:
            response = requests.get(search_url, headers=HEADERS, timeout=10)
            if response.status_code != 200:
                logger.error(f"Twitter error: {response.status_code} - {response.text}")
                if response.status_code == 429:
                    logger.info("Rate limit hit, retrying after 60 seconds...")
                    time.sleep(60)
                    continue
                return
            data = response.json()
            logger.debug(f"Twitter API response: {json.dumps(data, indent=2)}")
            break
        except Exception as e:
            logger.error(f"Twitter request error: {e}")
            time.sleep(2)
            continue
    else:
        logger.error("❌ Twitter API failed after 3 retries")
        return

    if not data.get("data"):
        logger.info("No relevant tweets found.")
        return

    processed_ids = fetch_processed_ids_from_gist()
    new_ids = set()

    for tweet in data["data"]:
        tweet_id = tweet["id"]
        logger.debug(f"Processing tweet ID {tweet_id}: {tweet.get('text')}")
        if tweet_id in processed_ids:
            logger.debug(f"Skipping duplicate tweet ID {tweet_id}")
            continue
        full_data = {"data": tweet, "includes": data.get("includes", {})}
        if handle_poll_tweet(full_data):
            new_ids.add(tweet_id)

    if new_ids:
        update_gist_with_new_ids(processed_ids, new_ids)

# === POLL HANDLER ===
def handle_poll_tweet(data):
    tweet = data.get('data', {})
    tweet_id = tweet.get('id', 'Unknown')
    
    # Check for poll attachments
    attachments = tweet.get('attachments', {})
    poll_ids = attachments.get('poll_ids', [])
    logger.debug(f"Tweet {tweet_id} attachments: {attachments}")
    
    if not poll_ids:
        logger.debug(f"No poll IDs found in tweet {tweet_id}")
        return False

    # Check for poll data in includes
    polls = data.get("includes", {}).get("polls", [])
    logger.debug(f"Tweet {tweet_id} polls: {polls}")
    
    if not polls:
        logger.debug(f"No polls found in tweet {tweet_id} despite poll_ids")
        return False

    users = data.get("includes", {}).get("users", [{}])
    username = users[0].get('username', 'Unknown') if users else 'Unknown'

    for poll in polls:
        tweet_text = tweet.get("text", "")
        options = poll.get("options", [])
        logger.debug(f"Poll options for tweet {tweet_id}: {[opt['label'] for opt in options]}")
        
        # Skip if no options or blocklist words are present
        if not options:
            logger.debug(f"Tweet {tweet_id} filtered: empty poll options")
            return False
        if any(word in tweet_text.lower() for word in BLOCKLIST):
            logger.debug(f"Tweet {tweet_id} filtered: blocklist match ({[word for word in BLOCKLIST if word in tweet_text.lower()]})")
            return False
        
        # Check for candidate names in poll options
        if not any(candidate.lower() in opt['label'].lower() for opt in options for candidate in CANDIDATE_NAMES):
            logger.debug(f"Tweet {tweet_id} filtered: no candidate names in options")
            return False

        # Process valid poll
        utc_zone = pytz.UTC
        eat_zone = pytz.timezone('Africa/Nairobi')
        try:
            created_at_dt = datetime.strptime(tweet['created_at'], "%Y-%m-%dT%H:%M:%S.%fZ")
            poll_end_dt = datetime.strptime(poll['end_datetime'], "%Y-%m-%dT%H:%M:%S.%fZ")
        except ValueError:
            created_at_dt = datetime.strptime(tweet['created_at'], "%Y-%m-%dT%H:%M:%SZ")
            poll_end_dt = datetime.strptime(poll['end_datetime'], "%Y-%m-%dT%H:%M:%SZ")
        created_at_eat = utc_zone.localize(created_at_dt).astimezone(eat_zone).strftime("%Y-%m-%d %H:%M:%S")
        poll_end_eat = utc_zone.localize(poll_end_dt).astimezone(eat_zone).strftime("%Y-%m-%d %H:%M:%S")

        tweet_url = f"https://x.com/i/web/status/{tweet_id}"
        alert_text = (
            f"📊 *Kenya President Poll Detected!*\n"
            f"👤 @`{username}`\n"
            f"🕒 `{created_at_eat}`\n"
            f"🔗 [View Tweet]({tweet_url})\n"
            f"🗳️ *Text:* {tweet_text}\n"
            f"📅 Ends: `{poll_end_eat}`\n"
            f"⏱️ Duration: {poll.get('duration_minutes')} min\n"
            f"*Options:*\n" + "\n".join([f"- `{opt['label']}`" for opt in options])
        )

        logger.info(f"Sending Telegram alert for tweet {tweet_id}")
        send_telegram_alert(alert_text)
        save_poll_to_csv(tweet_id, tweet.get("author_id"), username, tweet_text, tweet['created_at'],
                         poll['end_datetime'], poll.get("duration_minutes"), poll.get("voting_status"), options)
        return True
    return False

# === TELEGRAM ALERT ===
def send_telegram_alert(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {'chat_id': TELEGRAM_CHAT_ID, 'text': message, 'parse_mode': 'Markdown'}
    for attempt in range(3):
        try:
            r = requests.post(url, json=payload, timeout=10)
            if r.status_code == 200:
                logger.info("✅ Telegram alert sent")
                return
            logger.error(f"Telegram error: {r.status_code} - {r.text}")
        except Exception as e:
            logger.error(f"Telegram request error: {e}")
        time.sleep(2)
    logger.error("❌ Telegram alert failed after retries")

# === CSV STORAGE ===
def save_poll_to_csv(tweet_id, author_id, username, tweet_text, created_at, poll_end, duration, voting_status, options):
    file_exists = os.path.exists(OUTPUT_CSV)
    try:
        with open(OUTPUT_CSV, 'a', newline='', encoding='utf-8') as file:
            writer = csv.writer(file)
            if not file_exists:
                writer.writerow(['Tweet ID', 'Author ID', 'Username', 'Tweet Text', 'Created At', 'Poll End',
                                 'Duration (min)', 'Status', 'Options'])
            options_str = "; ".join([opt['label'] for opt in options])
            writer.writerow([tweet_id, author_id, username, tweet_text, created_at, poll_end, duration, voting_status, options_str])
            logger.info(f"📥 Tweet {tweet_id} saved to CSV")
    except Exception as e:
        logger.error(f"CSV save error: {e}")

# === MAIN ===
if __name__ == '__main__':
    try:
        search_recent_tweets()
        logger.info("✅ Script completed successfully")
    except Exception as e:
        logger.error(f"❌ Unexpected error: {e}")
        sys.exit(1)
