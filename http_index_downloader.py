import os
import re
import time
import requests
from urllib.parse import urljoin, urlparse, unquote
from bs4 import BeautifulSoup
import argparse
import sys
import concurrent.futures
import logging
import threading
from queue import Queue

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger('HTTPDirectoryDownloader')

class HTTPDirectoryDownloader:
    def __init__(self, base_url, output_dir="downloads", max_workers=10, 
                 username=None, password=None, proxy=None, session=None):
        """
        HTTP目录下载器
        
        :param base_url: 起始URL
        :param output_dir: 本地输出目录
        :param max_workers: 最大并发下载线程数
        :param username: HTTP基本认证用户名
        :param password: HTTP基本认证密码
        :param proxy: HTTP代理地址 (格式: http://proxy:port)
        """
        self.base_url = base_url
        self.output_dir = output_dir
        self.max_workers = max_workers
        self.username = username
        self.password = password
        self.proxy = proxy
        self.session = session or requests.Session()
        self.downloaded_files = 0
        self.skipped_files = 0
        self.failed_files = 0
        self.total_files = 0
        self.start_time = time.time()
        self.processed_urls = set()
        self.lock = threading.Lock()
        self.file_queue = Queue()
        
        # 配置会话
        if username and password:
            self.session.auth = (username, password)
            
        # 配置代理
        if proxy:
            logger.info(f"使用代理: {proxy}")
            self.session.proxies = {
                'http': proxy,
                'https': proxy
            }
        
        # 设置用户代理
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Connection': 'keep-alive'
        })
        
        # 设置超时
        self.session.timeout = 30
        
        # 确保输出目录存在
        os.makedirs(self.output_dir, exist_ok=True)
    
    def get_absolute_url(self, url):
        """获取绝对URL"""
        return urljoin(self.base_url, url)
    
    def extract_links(self, url):
        """从HTML页面中提取文件和目录链接"""
        try:
            logger.info(f"扫描目录: {url}")
            response = self.session.get(url)
            response.raise_for_status()
            
            # 检查内容类型
            content_type = response.headers.get('Content-Type', '').lower()
            if 'text/html' not in content_type:
                logger.warning(f"非HTML内容: {url} (Content-Type: {content_type})")
                return [], []
            
            soup = BeautifulSoup(response.text, 'html.parser')
            files = []
            directories = []
            
            # 查找所有链接
            for link in soup.find_all('a', href=True):
                href = link['href']
                
                # 跳过无效链接
                if not href or href.startswith('?') or href.startswith('#'):
                    continue
                
                # 解码URL编码
                decoded_href = unquote(href)
                
                # 跳过父目录链接
                if decoded_href == '../' or decoded_href == '..':
                    continue
                
                # 处理目录链接
                if decoded_href.endswith('/'):
                    directories.append(decoded_href)
                else:
                    files.append(decoded_href)
            
            logger.info(f"在 {url} 中找到 {len(files)} 个文件和 {len(directories)} 个子目录")
            return files, directories
        
        except requests.exceptions.RequestException as e:
            logger.error(f"扫描目录失败: {url} - {str(e)}")
            return [], []
    
    def download_file(self, url, local_path):
        """下载单个文件"""
        # 检查文件是否已存在
        if os.path.exists(local_path):
            file_size = os.path.getsize(local_path)
            headers = {'Range': f'bytes={file_size}-'} if file_size > 0 else {}
            resume = file_size > 0
        else:
            headers = {}
            resume = False
            file_size = 0
        
        try:
            # 发送请求
            response = self.session.get(url, headers=headers, stream=True)
            response.raise_for_status()
            
            # 处理部分内容响应
            if resume and response.status_code == 206:
                mode = 'ab'
                logger.info(f"恢复下载: {url} (从 {file_size} 字节开始)")
            else:
                mode = 'wb'
                if resume:
                    logger.warning(f"服务器不支持断点续传: {url}, 重新下载")
            
            # 获取文件总大小
            content_length = response.headers.get('Content-Length')
            if content_length:
                total_size = int(content_length) + file_size
            else:
                total_size = 0
            
            # 创建目录
            os.makedirs(os.path.dirname(local_path), exist_ok=True)
            
            # 下载文件
            with open(local_path, mode) as f:
                downloaded = file_size
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
            
            # 验证下载大小
            actual_size = os.path.getsize(local_path)
            if total_size > 0 and actual_size != total_size:
                logger.warning(f"文件大小不匹配: {url} (预期: {total_size}, 实际: {actual_size})")
                return False
            
            logger.info(f"下载完成: {url} -> {local_path}")
            return True
        
        except requests.exceptions.RequestException as e:
            logger.error(f"下载失败: {url} - {str(e)}")
            # 删除可能损坏的文件
            if os.path.exists(local_path) and os.path.getsize(local_path) == 0:
                os.remove(local_path)
            return False
    
    def file_download_worker(self):
        """文件下载工作线程"""
        while True:
            item = self.file_queue.get()
            if item is None:  # 终止信号
                break
                
            url, local_path = item
            success = self.download_file(url, local_path)
            
            with self.lock:
                if success:
                    self.downloaded_files += 1
                else:
                    self.failed_files += 1
                    
            self.file_queue.task_done()
    
    def process_directory(self, url, local_path):
        """处理目录及其内容"""
        # 避免重复处理
        if url in self.processed_urls:
            return
        self.processed_urls.add(url)
        
        # 确保URL以斜杠结尾
        if not url.endswith('/'):
            url += '/'
        
        # 创建本地目录
        os.makedirs(local_path, exist_ok=True)
        
        # 提取目录内容
        files, directories = self.extract_links(url)
        
        # 处理文件
        for file in files:
            file_url = self.get_absolute_url(url + file)
            file_local_path = os.path.join(local_path, file)
            
            with self.lock:
                self.total_files += 1
                
            # 添加到下载队列
            self.file_queue.put((file_url, file_local_path))
        
        # 处理子目录
        for directory in directories:
            dir_url = self.get_absolute_url(url + directory)
            dir_local_path = os.path.join(local_path, directory)
            self.process_directory(dir_url, dir_local_path)
    
    def start(self):
        """开始下载过程"""
        logger.info("="*60)
        logger.info(f"开始下载: {self.base_url}")
        logger.info(f"目标目录: {os.path.abspath(self.output_dir)}")
        logger.info(f"并发线程: {self.max_workers}")
        if self.proxy:
            logger.info(f"使用代理: {self.proxy}")
        logger.info("="*60)
        
        # 启动下载工作线程
        workers = []
        for _ in range(self.max_workers):
            t = threading.Thread(target=self.file_download_worker)
            t.daemon = True
            t.start()
            workers.append(t)
        
        # 解析基本URL
        parsed_url = urlparse(self.base_url)
        
        # 创建根目录
        root_dir_name = parsed_url.path.strip('/').split('/')[-1] if parsed_url.path != '/' else parsed_url.netloc
        root_local_path = os.path.join(self.output_dir, root_dir_name)
        
        # 开始处理目录结构
        self.process_directory(self.base_url, root_local_path)
        
        # 等待所有文件添加到队列
        time.sleep(1)
        
        # 等待文件下载完成
        self.file_queue.join()
        
        # 通知工作线程退出
        for _ in range(self.max_workers):
            self.file_queue.put(None)
        for t in workers:
            t.join()
        
        # 计算统计信息
        elapsed = time.time() - self.start_time
        total_files = self.downloaded_files + self.failed_files
        
        logger.info("\n" + "="*60)
        logger.info("下载统计:")
        logger.info(f"成功下载: {self.downloaded_files} 个文件")
        logger.info(f"下载失败: {self.failed_files} 个文件")
        logger.info(f"跳过文件: {self.skipped_files} 个")
        if total_files > 0:
            success_rate = (self.downloaded_files / total_files) * 100
            logger.info(f"成功率: {success_rate:.1f}%")
        logger.info(f"总耗时: {elapsed:.1f} 秒")
        logger.info(f"平均速度: {total_files/(elapsed+0.001):.2f} 文件/秒")
        logger.info(f"数据位置: {os.path.abspath(root_local_path)}")
        logger.info("="*60)
        
        return self.downloaded_files > 0

def parse_url_auth(url):
    """从URL解析认证信息"""
    parsed = urlparse(url)
    username = parsed.username
    password = parsed.password
    
    # 从URL中移除认证信息
    clean_url = url.replace(f"{parsed.username}:{parsed.password}@", "", 1) if username and password else url
    return clean_url, username, password

def main():
    parser = argparse.ArgumentParser(description='HTTP目录下载工具')
    parser.add_argument('url', help='起始URL (格式: https://user:pass@host/path/)')
    parser.add_argument('--output', help='输出目录', default='downloads')
    parser.add_argument('--threads', type=int, help='并发线程数', default=10)
    parser.add_argument('--username', help='HTTP基本认证用户名', default=None)
    parser.add_argument('--password', help='HTTP基本认证密码', default=None)
    parser.add_argument('--proxy', help='HTTP代理地址 (格式: http://proxy:port)', default=None)
    parser.add_argument('--verbose', help='详细输出模式', action='store_true')
    
    args = parser.parse_args()
    
    # 设置日志级别
    if args.verbose:
        logger.setLevel(logging.DEBUG)
        # 启用requests的详细日志
        logging.getLogger("urllib3").setLevel(logging.DEBUG)
    else:
        logger.setLevel(logging.INFO)
    
    # 解析URL中的认证信息
    clean_url, url_username, url_password = parse_url_auth(args.url)
    
    # 优先使用显式指定的认证信息
    username = args.username or url_username
    password = args.password or url_password
    
    # 创建下载器
    downloader = HTTPDirectoryDownloader(
        base_url=clean_url,
        output_dir=args.output,
        max_workers=args.threads,
        username=username,
        password=password,
        proxy=args.proxy
    )
    
    # 开始下载
    logger.info("启动下载进程...")
    success = downloader.start()
    
    if not success:
        logger.error("下载失败，请检查错误信息")
        sys.exit(1)
    else:
        logger.info("下载完成!")

if __name__ == "__main__":
    main()