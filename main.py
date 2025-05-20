import requests
import json
import csv
import time
import logging
import configparser
from datetime import datetime
import sys
import pytz
import random

# === SETUP LOGGING ===
logging.basicConfig(
    filename='poll_detector.log',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.DEBUG)
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)

# === LOAD CONFIGURATION ===
config = configparser.ConfigParser()
try:
    config.read('config.ini')
    BEARER_TOKEN = config['Twitter']['BearerToken']
    TELEGRAM_BOT_TOKEN = config['Telegram']['BotToken']
    TELEGRAM_CHAT_ID = config['Telegram']['ChatID']
    OUTPUT_CSV = config['General']['OutputCSV']
except KeyError as e:
    logger.error(f"Config error: Missing {e}")
    print(f"❌ Config error: Missing {e}. Check config.ini.")
    sys.exit(1)

# Twitter filter rule to capture voting-related tweets from Kenya
RULE_VALUE = '-is:retweet (vote OR pick OR choose OR candidate) (place_country:KE OR -place_country:KE)'

# List of candidate names to check in poll options
CANDIDATE_NAMES = [
    "Ruto", "Gachagua", "Matiangi", "Musyoka",
    "Omtatah", "Maraga", "Kalonzo"
]

# Blocklist to skip irrelevant polls
BLOCKLIST = ["movie", "food", "sport", "city", "music"]

# Headers used in all Twitter API requests
HEADERS = {
    'Authorization': f'Bearer {BEARER_TOKEN}',
    'Content-type': 'application/json'
}

# === FUNCTION: Set Twitter Filtering Rules ===
def set_rules():
    """
    Clears existing rules and sets a new rule to capture voting-related tweets from Kenya.
    """
    rules_url = "https://api.twitter.com/2/tweets/search/stream/rules"
    for attempt in range(3):
        try:
            # Get current rules
            current_rules = requests.get(rules_url, headers=HEADERS).json()
            logger.info(f"Attempt {attempt + 1}: Current rules: {current_rules}")
            print(f"Attempt {attempt + 1}: Current rules: {current_rules}")

            # Delete existing rules if any
            if 'data' in current_rules and current_rules['data']:
                rule_ids = [rule['id'] for rule in current_rules['data']]
                delete_payload = {'delete': {'ids': rule_ids}}
                delete_response = requests.post(rules_url, headers=HEADERS, json=delete_payload)
                if delete_response.status_code != 200:
                    logger.error(f"Attempt {attempt + 1}: Failed to delete rules: {delete_response.status_code} - {delete_response.text}")
                    print(f"❌ Attempt {attempt + 1}: Failed to delete rules: {delete_response.status_code} - {delete_response.text}")
                    time.sleep(2)
                    continue
                logger.info(f"Attempt {attempt + 1}: Deleted rules: {rule_ids}")
                print(f"✅ Attempt {attempt + 1}: Deleted rules: {rule_ids}")

            # Add the new rule
            payload = {
                "add": [
                    {"value": RULE_VALUE, "tag": "Kenya President Poll Detector"}
                ]
            }
            add_response = requests.post(rules_url, headers=HEADERS, json=payload)
            if add_response.status_code != 201:
                logger.error(f"Attempt {attempt + 1}: Failed to add rule: {add_response.status_code} - {add_response.text}")
                print(f"❌ Attempt {attempt + 1}: Failed to add rule: {add_response.status_code} - {add_response.text}")
                time.sleep(2)
                continue
            logger.info("Twitter stream rules set successfully.")
            print("✅ Twitter stream rules set successfully.")
            return True
        except requests.exceptions.RequestException as e:
            logger.error(f"Attempt {attempt + 1}: Network error setting rules: {e}")
            print(f"❌ Attempt {attempt + 1}: Network error setting rules: {e}")
            time.sleep(2)
        except Exception as e:
            logger.error(f"Attempt {attempt + 1}: Unexpected error setting rules: {e}")
            print(f"❌ Attempt {attempt + 1}: Unexpected error setting rules: {e}")
            time.sleep(2)
    logger.error("Failed to set rules after retries")
    print("❌ Failed to set rules after retries")
    return False

# === FUNCTION: Start the Twitter Stream ===
def start_stream():
    """
    Connects to Twitter’s filtered stream endpoint and listens for live tweets that match the rule.
    """
    url = (
        "https://api.twitter.com/2/tweets/search/stream"
        "?tweet.fields=created_at,attachments,author_id,text"
        "&expansions=attachments.poll_ids,author_id"
        "&poll.fields=duration_minutes,end_datetime,voting_status,options"
        "&user.fields=username"
    )

    last_heartbeat = time.time()
    retry_count = 0
    max_retries = 10
    base_delay = 15  # Increased from 5 seconds

    while retry_count < max_retries:
        try:
            logger.info("Starting poll detection stream...")
            print("🚀 Starting poll detection stream... Listening for tweets...")
            with requests.get(url, headers=HEADERS, stream=True, timeout=30) as response:
                if response.status_code != 200:
                    logger.error(f"Stream connection failed: {response.status_code} - {response.text}")
                    print(f"❌ Stream connection failed: {response.status_code} - {response.text}")
                    if response.status_code == 429:
                        delay = base_delay * (2 ** retry_count) + random.uniform(0, 1)  # Exponential backoff
                        logger.warning(f"Rate limit hit (429). Retrying in {delay:.2f} seconds...")
                        print(f"⚠️ Rate limit hit (429). Retrying in {delay:.2f} seconds...")
                        time.sleep(delay)
                        retry_count += 1
                        continue
                    raise requests.exceptions.RequestException("Stream connection failed")

                retry_count = 0  # Reset on successful connection
                for line in response.iter_lines():
                    if line:
                        try:
                            data = json.loads(line)
                            poll_data = handle_poll_tweet(data)
                            if poll_data:
                                logger.info(f"Processed poll data: {poll_data}")
                        except json.JSONDecodeError as e:
                            logger.error(f"JSON decode error: {e} - Line: {line}")
                            continue

                    # Heartbeat to confirm stream is active
                    if time.time() - last_heartbeat > 300:  # 5 minutes
                        logger.info("Stream is still active...")
                        print("💓 Stream is still active...")
                        last_heartbeat = time.time()

        except (requests.exceptions.RequestException, Exception) as e:
            logger.error(f"Stream error: {e}. Reconnecting in {base_delay * (2 ** retry_count):.2f} seconds...")
            print(f"❌ Stream error: {e}. Reconnecting in {base_delay * (2 ** retry_count):.2f} seconds...")
            time.sleep(base_delay * (2 ** retry_count))
            retry_count += 1
            if retry_count >= max_retries:
                logger.error("Max retries reached. Exiting...")
                print("❌ Max retries reached. Exiting...")
                sys.exit(1)

# === FUNCTION: Handle Poll Tweet ===
def handle_poll_tweet(data):
    """
    Checks if the tweet has a poll, verifies if poll options contain candidate names,
    and processes the tweet if relevant (alerts Telegram, saves to CSV).
    Returns poll data if a candidate name is found.
    """
    tweet = data.get('data', {})
    if not (polls := data.get("includes", {}).get("polls", [])):
        logger.debug(f"No poll info for tweet ID {tweet.get('id')}: {data}")
        return None

    users = data.get('includes', {}).get('users', [{}])
    username = users[0].get('username', 'Unknown') if users else 'Unknown'

    for poll in polls:
        logger.debug(f"Poll found: {poll}")
        tweet_id = tweet.get("id")
        tweet_text = tweet.get("text", "").encode('utf-8', errors='ignore').decode('utf-8')
        author_id = tweet.get("author_id")
        created_at = tweet.get("created_at")
        poll_end = poll.get("end_datetime")
        duration = poll.get("duration_minutes")
        voting_status = poll.get("voting_status")
        options = poll.get("options", [])

        # Confirm it's a poll with options
        if not options:
            logger.warning(f"Tweet ID {tweet_id} has no poll options, skipping.")
            print(f"⚠️ Tweet ID {tweet_id} has no poll options, skipping.")
            continue

        # Check if tweet is irrelevant based on blocklist
        if any(word in tweet_text.lower() for word in BLOCKLIST):
            logger.info(f"Tweet ID {tweet_id} is a poll but contains blocklist words: {tweet_text}")
            print(f"ℹ️ Tweet ID {tweet_id} is a poll but contains blocklist words: {tweet_text}")
            continue

        # Check if any poll option contains a candidate name (case-insensitive)
        options_str = ", ".join([opt['label'] for opt in options])
        is_candidate_poll = any(
            candidate.lower() in opt['label'].lower()
            for opt in options
            for candidate in CANDIDATE_NAMES
        )

        if not is_candidate_poll:
            logger.info(f"Tweet ID {tweet_id} is a poll but no candidate names found in options: {options_str}")
            print(f"ℹ️ Tweet ID {tweet_id} is a poll but no candidate names found in options: {options_str}")
            continue

        # Log confirmation
        logger.info(f"Tweet ID {tweet_id} is a relevant poll with candidate names")
        print(f"✅ Tweet ID {tweet_id} is a relevant poll with candidate names")

        # Convert timestamps to EAT (UTC+3) for Telegram alert
        utc_zone = pytz.UTC
        eat_zone = pytz.timezone('Africa/Nairobi')
        try:
            created_at_dt = datetime.strptime(created_at, "%Y-%m-%dT%H:%M:%S.%fZ")
            poll_end_dt = datetime.strptime(poll_end, "%Y-%m-%dT%H:%M:%S.%fZ")
        except ValueError:
            created_at_dt = datetime.strptime(created_at, "%Y-%m-%dT%H:%M:%SZ")
            poll_end_dt = datetime.strptime(poll_end, "%Y-%m-%dT%H:%M:%SZ")
        created_at_dt = utc_zone.localize(created_at_dt).astimezone(eat_zone)
        poll_end_dt = utc_zone.localize(poll_end_dt).astimezone(eat_zone)
        created_at_eat = created_at_dt.strftime("%Y-%m-%d %H:%M:%S %Z")
        poll_end_eat = poll_end_dt.strftime("%Y-%m-%d %H:%M:%S %Z")

        tweet_url = f"https://x.com/i/web/status/{tweet_id}"

        # Format alert text for Telegram
        alert_text = (
            f"📊 *Kenya President Poll Detected!*\n"
            f"✅ *Confirmed: Poll contains candidate names*\n"
            f"👤 Username: `@{username}`\n"
            f"🆔 Author ID: `{author_id}`\n"
            f"🕒 Created: `{created_at_eat}`\n"
            f"🔗 [View Tweet]({tweet_url})\n"
            f"🗳️ *Poll Text:* {tweet_text}\n"
            f"📅 Ends: `{poll_end_eat}`\n"
            f"⏱️ Duration: {duration} min\n"
            f"📌 Status: `{voting_status}`\n"
            f"*Poll Options:*\n" +
            "\n".join([f"- `{opt['label']}`" for opt in options])
        )

        # Send alert via Telegram bot
        send_telegram_alert(alert_text)

        # Save tweet + poll data locally
        save_poll_to_csv(tweet_id, author_id, username, tweet_text, created_at, poll_end, duration, voting_status, options)

        # Return poll data for further processing
        poll_data = {
            "tweet_id": tweet_id,
            "username": username,
            "text": tweet_text,
            "options": [opt['label'] for opt in options],
            "is_poll": True,
            "contains_candidates": True
        }
        logger.info(f"Returning poll data: {poll_data}")
        return poll_data

    return None

# === FUNCTION: Send Alert to Telegram ===
def send_telegram_alert(message):
    """
    Sends a Markdown-formatted message to the configured Telegram bot and chat.
    """
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        'chat_id': TELEGRAM_CHAT_ID,
        'text': message,
        'parse_mode': 'Markdown'
    }
    for attempt in range(3):
        try:
            response = requests.post(url, json=payload, timeout=10)
            if response.status_code != 200:
                logger.error(f"Telegram error: {response.status_code} - {response.text}")
                print(f"❌ Telegram error: {response.status_code} - {response.text}")
                time.sleep(2)
                continue
            logger.info("Alert sent to Telegram")
            print("✅ Alert sent to Telegram")
            return
        except requests.exceptions.RequestException as e:
            logger.error(f"Telegram request error: {e}")
            print(f"❌ Telegram request error: {e}")
            time.sleep(2)
    logger.error("Failed to send Telegram alert after retries")
    print("❌ Failed to send Telegram alert after retries")

# === FUNCTION: Save Poll Data to CSV ===
def save_poll_to_csv(tweet_id, author_id, username, tweet_text, created_at, poll_end, duration, voting_status, options):
    """
    Writes tweet and poll details into a local CSV file for storage or review.
    """
    file_exists = False
    try:
        with open(OUTPUT_CSV, 'r', encoding='utf-8'):
            file_exists = True
    except FileNotFoundError:
        pass

    try:
        with open(OUTPUT_CSV, 'a', newline='', encoding='utf-8') as file:
            writer = csv.writer(file)
            if not file_exists:
                writer.writerow(['Tweet ID', 'Author ID', 'Username', 'Tweet Text', 'Created At', 'Poll End', 'Duration (min)', 'Status', 'Options'])
            options_str = "; ".join([opt['label'] for opt in options])
            writer.writerow([tweet_id, author_id, username, tweet_text, created_at, poll_end, duration, voting_status, options_str])
            logger.info(f"Poll tweet {tweet_id} saved to CSV")
            print("📥 Poll tweet saved to CSV")
    except Exception as e:
        logger.error(f"Error saving to CSV: {e}")
        print(f"❌ Error saving to CSV: {e}")

# === MAIN EXECUTION FLOW ===
if __name__ == '__main__':
    try:
        if set_rules():
            start_stream()
        else:
            print("❌ Failed to set Twitter stream rules. Exiting...")
            sys.exit(1)
    except KeyboardInterrupt:
        logger.info("Stopping poll detection stream...")
        print("🛑 Stopping poll detection stream...")
        sys.exit(0)