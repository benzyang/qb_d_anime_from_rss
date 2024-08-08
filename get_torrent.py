# -*- coding: utf-8 -*-

# Update: automatically create save_path in anime_config

import time
import os
import json
import feedparser
import requests
from urllib.request import getproxies
import qbittorrentapi
import logging
import traceback
from pathlib import Path
from telegram import Bot
import asyncio



logfile = 'torrent.log'
torrent_savedfile = 'torrent_checked.txt'
torrents_checked = []


# 配置 log
def setup_logging(logfile):
    Path(logfile).touch(exist_ok=True)
    print(f"{logfile} created.")
    
    # 创建一个StreamHandler输出到终端
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s', '%Y-%m-%d %H:%M:%S')
    console_handler.setFormatter(console_formatter)
    
    logging.basicConfig(filename=logfile, 
                        level=logging.INFO, 
                        format='%(asctime)s - %(levelname)s - %(message)s', 
                        datefmt='%Y-%m-%d %H:%M:%S',
                        encoding='utf-8')
    
    # 添加StreamHandler到根logger
    logging.getLogger().addHandler(console_handler)


# 记录 checked torrent
def get_checked_torrent():
    global torrents_checked
    
    try:
        Path(torrent_savedfile).touch(exist_ok=True)
        logging.info(f"File '{torrent_savedfile}' created.")
        with open(torrent_savedfile, 'r', encoding="utf-8") as file:
            torrents_checked = [line.strip() for line in file.readlines()]
        logging.info(f"Get all checked torrents: {len(torrents_checked)}.")
    except (IOError, OSError) as e:
        logging.error(f"Error accessing file '{torrent_savedfile}': {e}")


# 用 request 解析 rss
def fetch_feed_with_timeout(rss_url, timeout=10):
    try:
        response = requests.get(rss_url, timeout=timeout)
        response.raise_for_status()
        feed_data = response.content
        return feedparser.parse(feed_data)
    
    except requests.exceptions.RequestException as e:
        logging.error(f"Failed to retrieve RSS feed: {e}")

        return None


# 解析 rss
def get_feed(url):
    while(True):
        max_trial = 3
        for i in range(max_trial):
            if i > 0:
                logging.info(f'Trial {i}.')

            try:
                # 默认使用 feedparser
                feed = feedparser.parse(url)
                if feed is not None and len(feed.entries) > 0:
                    logging.info(f"Download RSS url: '{url}'. Start analyzing.")

                    return feed
                
            except Exception as e:
                # 记录详细的错误信息和堆栈跟踪
                logging.error(f"Failed to fetch or parse RSS feed: '{url}'")
                logging.error(traceback.format_exc())
                raise e
                
            # feedparser 失败，使用 request
            logging.info(f"Feedparser failed. Try request. Url: '{url}'")
            try:
                feed = fetch_feed_with_timeout(url, timeout=5)
                if feed is not None and len(feed.entries) > 0:
                    logging.info(f"Download RSS url: '{url}'. Start analyzing.")

                    return feed
                
            except Exception as e:
                # 记录详细的错误信息和堆栈跟踪
                logging.info(f"Request failed. Url: '{url}'")
                logging.error(traceback.format_exc())
                raise e
        
            logging.info('Wait 3s. Try again.')
            time.sleep(3)
            
        time.sleep(4 * 60 * 60)


def check_must_contain(text, must_contain):
    keywords = must_contain.split()
    for keyword in keywords:
        if keyword not in text:
            return False
    return True

def check_must_not_contain(text, must_not_contain):
    keywords = must_not_contain.split('|')
    for keyword in keywords:
        if keyword in text:
            return False
    return True


# 获取该 RSS 的匹配条件
def get_anime_config(url, config_list):
    torrent_settings = []
    for config in config_list:
        if config['rss_url'] == url:
            must_contain = config['must_contain']
            must_not_contain = config['must_not_contain']
            save_path = config['save_path']
            tags = config['tags']
            content_layout = config['content_layout']
            torrent_setting = [must_contain, must_not_contain, save_path, tags, content_layout]
            torrent_settings.append(torrent_setting)
    
    return torrent_settings



# 配置代理
def setup_proxy():
    # 获取当前系统代理
    proxies = getproxies()

    if proxies:
        first_proxy_url = next(iter(proxies.values()))
        logging.info(f'Current system proxy: {first_proxy_url}.')
        proxy_url = {
            "http": first_proxy_url,
            "https": first_proxy_url
        }
    
    else:
        logging.info('No system proxy, please set manually.')
        proxy_url = {
            "http": "http://127.0.0.1:1085",
            "https": "http://127.0.0.1:1085"
        }
    
    return proxy_url


# 下载种子
def download_torrent(url, save_dir, proxies={}):
    save_dir = Path(save_dir)
    save_dir.mkdir(exist_ok=True)
    
    file_name = url.split("/")[-1]
    save_path = os.path.join(save_dir, file_name)

    try:
        response = requests.get(url, timeout=5, proxies=proxies)
        response.raise_for_status() 
        
        with open(save_path, "wb") as f:
            f.write(response.content)
            
        logging.info(f"Torrent download success: {file_name}.")
        return save_path
    
    except requests.exceptions.Timeout:
        logging.error("Torrent download timeout.")
    except requests.exceptions.RequestException as e:
        logging.error(f"Torrent download failed. Eerror: {e}")



def qb_login(username, password):
    global qbt_client

    conn_info = dict(
        host="localhost",
        port=8080,
        username=username,
        password=password,
    )
    qbt_client = qbittorrentapi.Client(**conn_info)

    try:
        qbt_client.auth_log_in()
    except qbittorrentapi.LoginFailed as e:
        logging.error(f'QB login failed: {e}')


# 推送到 qb 下载
def qb_download(url, save_path, tags, content_layout):
    save_dir = Path(save_path)
    save_dir.mkdir(exist_ok=True)

    qbt_client.torrents_add(torrent_files=url, 
                        save_path=save_path, 
                        tags=tags,
                        content_layout=content_layout
                        )
    qbt_client.auth_log_out()


def try_send_message(text, TELEGRAM_BOT_TOKEN, USER_ID):
    message = f"{text}\nSent to qb."
    try:
        asyncio.run(send_message(message, TELEGRAM_BOT_TOKEN, USER_ID))
    except Exception as e:
        logging.error(e)
        print('Push failed.')


# 向 tgbot 发送信息
async def send_message(message, TELEGRAM_BOT_TOKEN, USER_ID):
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    async with bot:
        await bot.send_message(chat_id=USER_ID, text=message)


def check_rss():
    c = 0
    for rss_url in rss_urls:
        torrent_settings = get_anime_config(rss_url, anime_config)

        feed = get_feed(rss_url)
        for entry in feed.entries:
            title = entry.title

            # if torrent is checked, quit
            if any(title in torrent_checked for torrent_checked in torrents_checked):
                break

            for torrent_setting in torrent_settings:
                must_contain = torrent_setting[0]
                must_not_contain = torrent_setting[1]

                if check_must_contain(title, must_contain) and check_must_not_contain(title, must_not_contain):
                    for link in entry.links:
                        if link.rel == 'enclosure' and link.type == 'application/x-bittorrent':
                            torrent_url = link.href
                            break
                    logging.info(f"Update: {title}, '{torrent_url}'.")
                    # print(title)
                    # print(torrent_url)
                    torrents_checked.append(title)
                    with open('torrent_checked.txt', 'a', encoding="utf-8") as file:
                        file.write('\n')
                        file.write(title)

                    torrent = download_torrent(torrent_url, torrent_path, proxy_url)

                    qb_download(url=torrent,
                                save_path=torrent_setting[2],
                                tags=torrent_setting[3],
                                content_layout=torrent_setting[4]
                                )
                    logging.info(f"Qb start downloading '{title}'.")

                    try_send_message(title, TELEGRAM_BOT_TOKEN, USER_ID)
                    c += 1
    print(c, 'Anime added.')


def main():
    global proxy_url, TELEGRAM_BOT_TOKEN, USER_ID, rss_urls, torrent_path, anime_config

    setup_logging(logfile)
    get_checked_torrent()
    proxy_url = setup_proxy()

    with open("config.json", "r", encoding='UTF-8') as f:
        config_list = json.load(f)
    misc_config = config_list[0]
    TELEGRAM_BOT_TOKEN = misc_config['TELEGRAM_BOT_TOKEN']
    USER_ID = misc_config['USER_ID']
    rss_urls = misc_config['rss_urls']
    torrent_path = misc_config['torrent_path']
    username = misc_config['username']
    password = misc_config['password']
    qb_login(username, password)

    while(True):
        # 从 JSON 文件读取数据
        with open("config.json", "r", encoding='UTF-8') as f:
            config_list = json.load(f)
        anime_config = config_list[1:]

        check_rss()
        time.sleep(4 * 60 * 60)



if __name__ == '__main__':
    main()

