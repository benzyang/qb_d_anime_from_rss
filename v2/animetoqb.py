import yaml
import feedparser
import qbittorrentapi
import requests
import os
import re
import logging
import asyncio
import time
import schedule
from telegram import Bot
from pathlib import Path
from urllib.parse import quote, unquote
from urllib.request import getproxies


logfile = 'torrent.log'
configfile = 'config.yaml'
torrents_checked = set()



# 配置 log
def setup_logging(logfile: str, 
				level = logging.INFO, 
				format_string: str = '%(asctime)s - %(levelname)s - %(message)s',
				datefmt: str = '%Y-%m-%d %H:%M:%S'
				) -> logging.Logger:
	
	Path(logfile).touch(exist_ok=True)
	print(f"{logfile} created.")

	logging.basicConfig(level=level, format=format_string, datefmt=datefmt, filename=logfile, filemode='a', encoding='utf-8')

	# 创建一个StreamHandler输出到终端
	console_handler = logging.StreamHandler()
	console_handler.setLevel(level)	
	console_handler.setFormatter(logging.Formatter(format_string, datefmt=datefmt))
	# 添加StreamHandler到根logger
	logging.getLogger().addHandler(console_handler)
	logger = logging.getLogger(__name__)

	return logger
	

def get_checked_torrent(file):
	if os.path.exists(file):
		try:
			with open(file, 'r', encoding="utf-8") as file:
				torrents_checked = set(map(str.strip, file))
			logging.info(f"Get all checked torrents: {len(torrents_checked)}.")
			
			return torrents_checked
		except (IOError, OSError) as e:
			logging.error(f"Error accessing file '{file}': {e}")
			return set()
	else:
		return set()
	

def load_yaml(filepath):
    """加载 YAML 文件内容."""
    try:
        with open(filepath, 'r', encoding='utf-8') as file:
            # 安全加载，防止潜在的安全漏洞
            data = yaml.safe_load(file)
            return data
    except FileNotFoundError:
        print(f"文件未找到: {filepath}")
        return None
    except yaml.YAMLError as e:
        print(f"YAML 解析错误: {e}")
        return None


def setup_proxy(proxies):
	if proxies is not None:
		logger.info(f'Proxy from config: {proxies}.')
	else:
		# 获取当前系统代理
		proxies = getproxies()
		logger.info(f'Current system proxy: {proxies}.')
		if not proxies:
			print('No system proxy, please set manually.')
        
	return proxies


def download_torrent(url, save_path="."):
	headers = {
		'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36'
	}
	try:
		response = requests.get(url=url, headers=headers, proxies=proxies, timeout=10)
		response.raise_for_status()  # 检查 HTTP 状态码，如果不是 200 则抛出异常

		# 从 Content-Disposition 头部获取文件名，如果不存在则从 URL 中提取
		content_disposition = response.headers.get('Content-Disposition')
		if content_disposition:
			filename = re.findall("filename=\"(.+?)\"", content_disposition)
			if filename:
				filename = filename[0]
			else:
				filename = re.findall(r"filename\*=UTF-8''(.+)", content_disposition)
				if filename:
					filename = unquote(filename[0]) # 对文件名进行URL解码
				else:
					filename = url.split("/")[-1]
		else:
			filename = url.split("/")[-1]

		# 创建保存目录（如果不存在）
		os.makedirs(save_path, exist_ok=True)

		# 处理文件名重复的情况
		filepath = os.path.join(save_path, filename)
		base, ext = os.path.splitext(filepath)
		if os.path.exists(filepath):
			logger.info('Torrent already downloaded.')
			return filepath

		# 分块写入(8KB)，用于大文件
		# with open(filepath, 'wb') as f:
		# 	for chunk in response.iter_content(chunk_size=8192):
		# 		f.write(chunk)
		with open(filepath, "wb") as f:
			f.write(response.content)
		print(f"Torrent download success: {filepath}")

		return filepath

	except requests.exceptions.RequestException as e:
		print(f"Torrent download failed. Error: {e}")
		return None
	except Exception as e:
		print(f"Torrent download failed. Unknown error: {e}")
		return None


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


def qb_download(qb_config, url, filter):
	save_dir = Path(filter['save_path'])
	save_dir.mkdir(parents=True, exist_ok=True)

	conn_info = dict(
		host="localhost",
		port=8080,
		username=qb_config[0],
		password=qb_config[1],
	)

	# use a context manager
	with qbittorrentapi.Client(**conn_info) as qbt_client:
		result = qbt_client.torrents_add(
			torrent_files=url,
			save_path=save_dir,
			tags=filter['tags'],
			content_layout=filter['content_layout']
		)
		if result == "Ok.":
			return True
		else:
			raise Exception("Failed to add torrent.")
		

def try_send_message(config, text):
    message = text
    try:
        asyncio.run(send_message(message, config[0], config[1]))
    except Exception as e:
        logger.error(e)
        print('Push failed.')


# 向 tgbot 发送信息
async def send_message(message, TELEGRAM_BOT_TOKEN, USER_ID):
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    async with bot:
        await bot.send_message(chat_id=USER_ID, text=message)
		

# 保存新增的 torrents_checked
def save_checked(torrents_add, torrent_checked_file):
	path = Path(torrent_checked_file)
	path.parent.mkdir(parents=True, exist_ok=True)
	try:
		with open(torrent_checked_file, 'a', encoding="utf-8") as file:
			file.write('\n'.join(map(str, torrents_add)))	# map将列表内所有元素转换为str
			file.write('\n')
		return True
	except Exception as e:
		logger.error(f"An error occurred while writing the file: {e}")
		return False



def check_rss(tg_config, qb_config, config_data, checked):
	c = 0
	torrents_add = []

	# read config for each rss_url
	for anime_config in config_data:
		# parse the current rss_url
		logger.info(f"Parsing '{anime_config['filter']}': '{anime_config['rss_url']}'")
		rss_url = anime_config['rss_url']
		feed = feedparser.parse(rss_url)

		for entry in feed.entries:
			title = entry.title
            
			# if torrent is checked, quit
			if title in checked :
				break

			# match each anime_filter		
			for anime_filter in anime_config['anime_filter']:
				# satisfy the conditions
				if check_must_contain(title, anime_filter['must_contain']) and check_must_not_contain(title, anime_filter['must_not_contain']):
					torrents_add.append(title)
					# find url of the torrent
					for link in entry.links:
						if link.type == 'application/x-bittorrent':
							torrent_url = link.href
							break
					logger.info(f"Update: {title}: '{torrent_url}'.")
				else:
					continue
				
				# download the torrent
				torrent = download_torrent(torrent_url, torrent_path)
				if torrent is not None:
					# sent to qb
					qb_status = qb_download(qb_config, torrent, anime_filter)
					# notify tgbot
					if qb_status:
						logger.info(f"Qb start downloading '{title}'.")
						try_send_message(tg_config, text=f'{title}\nSent to qb.')
						c += 1
				else:
					try_send_message(tg_config, text=f'{title}\nFailed to download torrent.')
					continue

	# add all new titles to the torrent_saved_txt
	save_checked(torrents_add, torrent_checked_file)

	print(c, 'Anime added.')



def job():
	global proxies, torrent_path, torrent_checked_file

	print("Loading config......")
	config_data = load_yaml(configfile)

	if config_data:
		#  read misc settings
		token = config_data[0]['TELEGRAM_BOT_TOKEN']
		id = config_data[0]['USER_ID']
		username = config_data[0]['username']
		password = config_data[0]['password']
		torrent_path = config_data[0]['torrent_path']
		torrent_checked_file = config_data[0]['torrent_checked_file']
		proxies = config_data[0]['proxies']
		tg_config = [token, id]
		qb_config = [username, password]

		proxies = setup_proxy(proxies)
		torrents_checked = get_checked_torrent(torrent_checked_file)

		check_rss(tg_config, qb_config, config_data[1:], torrents_checked)
	else:
		logger.warning("config.yaml is empty")


if __name__ == '__main__':
	# Set up logging with a custom log file, level, and format
	logger = setup_logging("myapp.log", level=logging.INFO, format_string="%(asctime)s - %(name)s - %(levelname)s - %(message)s")

	job()
	schedule.every(6).hours.do(job)
	while True:
		schedule.run_pending()
		time.sleep(60 * 60)
