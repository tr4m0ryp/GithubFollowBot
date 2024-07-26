import requests
import time
import logging
import sys
import os
import random
from argparse import ArgumentParser
from configparser import ConfigParser
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm
from colorama import Fore, Style, init

# Initialize colorama
init(autoreset=True)

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# GitHub API base URL
BASE_URL = 'https://api.github.com'

# Default headers for GitHub API requests
HEADERS = {
    'Accept': 'application/vnd.github.v3+json',
}

# List of user agents to randomize
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:55.0) Gecko/20100101 Firefox/55.0",
    "Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/50.0.2661.102 Safari/537.36",
    "Mozilla/5.0 (Windows NT 6.1; WOW64; Trident/7.0; AS; rv:11.0) like Gecko",
]

# Global counters
users_followed = 0
total_users = 0

def get_rate_limit():
    response = requests.get(f'{BASE_URL}/rate_limit', headers=HEADERS)
    return response.json()

def countdown(t):
    while t:
        mins, secs = divmod(t, 60)
        timeformat = f'{Fore.CYAN}{mins:02d}:{secs:02d}{Style.RESET_ALL}'
        print(f'Waiting for {timeformat} until next attempt', end='\r')
        sys.stdout.flush()
        time.sleep(1)
        t -= 1
    print()

def wait_for_rate_limit_reset(rate_limit_info, secondary=False):
    if secondary:
        logging.info(Fore.YELLOW + 'Secondary rate limit exceeded. Waiting and checking every 5 minutes.')
        while True:
            countdown(300)  # Wait for 5 minutes
            rate_limit_info = get_rate_limit()
            if rate_limit_info['resources']['core']['remaining'] > 0:
                break
    else:
        reset_time = rate_limit_info['resources']['core']['reset']
        current_time = time.time()
        wait_time = max(0, reset_time - current_time)
        logging.info(Fore.YELLOW + f'Primary rate limit exceeded. Waiting for {wait_time} seconds until reset.')
        countdown(int(wait_time) + 1)  # Adding 1 second buffer

def follow_user(username, wait_time):
    global users_followed, total_users

    url = f'{BASE_URL}/user/following/{username}'
    response = requests.put(url, headers=HEADERS)
    
    if response.status_code == 204:
        users_followed += 1
        logging.info(Fore.GREEN + f'Successfully followed {username} ({users_followed}/{total_users})')
    elif response.status_code == 403:
        rate_limit_info = get_rate_limit()
        wait_for_rate_limit_reset(rate_limit_info)
        follow_user(username, wait_time)
    elif response.status_code == 429:
        rate_limit_info = get_rate_limit()
        wait_for_rate_limit_reset(rate_limit_info, secondary=True)
        follow_user(username, wait_time)
    else:
        logging.error(Fore.RED + f'Failed to follow {username}. Status code: {response.status_code}, Response: {response.text}')
    
    # Wait for a random time between requests to avoid hitting rate limits
    countdown(random.randint(wait_time, wait_time + 10))

def bulk_follow(usernames, max_workers, wait_time):
    global total_users
    total_users = len(usernames)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_username = {executor.submit(follow_user, username, wait_time): username for username in usernames}
        for future in tqdm(as_completed(future_to_username), total=total_users, desc="Following users"):
            username = future_to_username[future]
            try:
                future.result()
            except Exception as e:
                logging.error(Fore.RED + f'Error following {username}: {e}')

def scrape_users(target_username):
    usernames_to_follow = []
    page = 1
    while True:
        url = f'{BASE_URL}/users/{target_username}/followers?page={page}&per_page=100'
        response = requests.get(url, headers=HEADERS)
        if response.status_code == 200:
            followers = response.json()
            if not followers:
                break
            usernames_to_follow.extend(follower['login'] for follower in followers)
            page += 1
            time.sleep(1)  # Sleep to avoid hitting rate limits too quickly
        elif response.status_code == 403:
            rate_limit_info = get_rate_limit()
            wait_for_rate_limit_reset(rate_limit_info)
        elif response.status_code == 429:
            rate_limit_info = get_rate_limit()
            wait_for_rate_limit_reset(rate_limit_info, secondary=True)
        else:
            logging.error(Fore.RED + f'Failed to scrape users. Status code: {response.status_code}, Response: {response.text}')
            break
    return usernames_to_follow

def load_config(config_file):
    config = ConfigParser()
    config.read(config_file)
    return config

def main():
    parser = ArgumentParser(description='Scrape and follow GitHub users.')
    parser.add_argument('-t', '--token', help='GitHub personal access token', required=False)
    parser.add_argument('-u', '--username', help='GitHub username to scrape followers from', required=False)
    parser.add_argument('--config', help='Path to configuration file', default='config.ini')
    parser.add_argument('--wait-time', type=int, default=30, help='Base wait time between follow requests in seconds')
    parser.add_argument('--max-workers', type=int, default=1, help='Maximum number of concurrent follow requests')
    args = parser.parse_args()

    # Load configuration
    config = load_config(args.config)

    token = args.token or config.get('github', 'token', fallback=os.getenv('GITHUB_TOKEN'))
    if not token:
        logging.error(Fore.RED + 'GitHub token is required. Set it via command line, config file, or GITHUB_TOKEN environment variable.')
        sys.exit(1)
    
    target_username = args.username or config.get('github', 'target_username', fallback=None)
    if not target_username:
        logging.error(Fore.RED + 'Target username is required. Set it via command line or config file.')
        sys.exit(1)
    
    HEADERS['Authorization'] = f'token {token}'
    HEADERS['User-Agent'] = random.choice(USER_AGENTS)

    while True:
        usernames_to_follow = scrape_users(target_username)
        
        if not usernames_to_follow:
            logging.info(Fore.YELLOW + 'No users to follow.')
            return

        bulk_follow(usernames_to_follow, args.max_workers, args.wait_time)

        # Select a new target username from the list of followed users
        if users_followed > 0:
            target_username = random.choice(usernames_to_follow)
            logging.info(Fore.CYAN + f'Switching target to {target_username}')
        else:
            logging.info(Fore.YELLOW + 'No new users followed in this iteration. Exiting.')
            break

if __name__ == '__main__':
    main()
