import os
import re
import time
import requests
import hashlib
from datetime import datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlparse, unquote
from bs4 import BeautifulSoup
import argparse
import sys
import concurrent.futures
import logging
import threading
from queue import Queue
import shutil

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
                 username=None, password=None, proxy=None, session=None,
                 update_only=False, use_checksum=False, existing_action='skip'):
        """
        HTTP目录下载器
        
        :param base_url: 起始URL
        :param output_dir: 本地输出目录
        :param max_workers: 最大并发下载线程数
        :param username: HTTP基本认证用户名
        :param password: HTTP基本认证密码
        :param proxy: HTTP代理地址
        :param update_only: 仅下载更新的文件
        :param use_checksum: 使用校验和验证
        :param existing_action: 已存在文件处理策略 (skip/overwrite/backup)
        """
        self.base_url = base_url
        self.output_dir = output_dir
        self.max_workers = max_workers
        self.username = username
        self.password = password
        self.proxy = proxy
        self.session = session or requests.Session()
        self.update_only = update_only
        self.use_checksum = use_checksum
        self.existing_action = existing_action
        
        # 统计信息
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
    
    def is_file_updated(self, url, local_path):
        """检查远程文件是否比本地文件更新"""
        try:
            # 获取远程文件的最后修改时间
            response = self.session.head(url)
            remote_last_modified = response.headers.get('Last-Modified')
            if not remote_last_modified:
                logger.debug(f"服务器未提供Last-Modified头部: {url}")
                return True  # 没有时间戳信息，保守处理
            
            # 转换为时间对象
            remote_time = parsedate_to_datetime(remote_last_modified)
            
            # 获取本地文件的修改时间
            local_time = datetime.fromtimestamp(os.path.getmtime(local_path))
            
            # 考虑时间精度差异（1秒容差）
            time_diff = (remote_time - local_time).total_seconds()
            return time_diff > 1
            
        except Exception as e:
            logger.error(f"更新时间检查失败: {url} - {str(e)}")
            return True  # 出错时保守处理
    
    def get_file_checksum(self, file_path):
        """计算文件校验和(SHA-256)"""
        sha256 = hashlib.sha256()
        try:
            with open(file_path, "rb") as f:
                while chunk := f.read(8192):
                    sha256.update(chunk)
            return sha256.hexdigest()
        except Exception as e:
            logger.error(f"校验和计算失败: {file_path} - {str(e)}")
            return None
    
    def is_file_changed(self, url, local_path):
        """比较远程文件与本地文件内容是否一致"""
        try:
            # 获取远程文件校验信息
            response = self.session.head(url)
            remote_etag = response.headers.get('ETag')
            remote_md5 = response.headers.get('Content-MD5')
            
            # 优先使用ETag
            if remote_etag:
                # 获取本地文件ETag（如果存在）
                local_etag = None
                etag_file = local_path + '.etag'
                if os.path.exists(etag_file):
                    with open(etag_file, 'r') as f:
                        local_etag = f.read().strip()
                
                # 比较ETag
                if local_etag and local_etag == remote_etag:
                    return False
                
                # 保存新ETag
                with open(etag_file, 'w') as f:
                    f.write(remote_etag)
                return True
            
            # 次选MD5
            elif remote_md5:
                local_checksum = self.get_file_checksum(local_path)
                if not local_checksum:
                    return True
                
                # 比较MD5 (Base64解码后比较)
                import base64
                decoded_md5 = base64.b64decode(remote_md5).hex()
                return local_checksum != decoded_md5
            
            else:
                logger.debug(f"服务器未提供ETag或Content-MD5: {url}")
                return True  # 没有校验信息，保守处理
            
        except Exception as e:
            logger.error(f"文件变更检查失败: {url} - {str(e)}")
            return True
    
    def backup_file(self, file_path):
        """备份已存在的文件"""
        try:
            backup_path = file_path + '.bak'
            counter = 1
            while os.path.exists(backup_path):
                backup_path = f"{file_path}.bak.{counter}"
                counter += 1
                
            shutil.copy2(file_path, backup_path)
            logger.info(f"已备份文件: {file_path} -> {backup_path}")
            return True
        except Exception as e:
            logger.error(f"文件备份失败: {file_path} - {str(e)}")
            return False
    
    def should_skip_existing(self, url, local_path):
        """决定是否跳过已存在文件"""
        # 文件不存在，需要下载
        if not os.path.exists(local_path):
            return False
        
        # 处理备份策略
        if self.existing_action == 'backup':
            self.backup_file(local_path)
            return False
        
        # 跳过策略直接返回
        if self.existing_action == 'skip':
            return True
        
        # 更新检查策略
        if self.update_only or self.use_checksum:
            if self.use_checksum:
                changed = self.is_file_changed(url, local_path)
                if not changed:
                    logger.info(f"文件内容未改变，跳过: {url}")
                    return True
            else:
                updated = self.is_file_updated(url, local_path)
                if not updated:
                    logger.info(f"文件未更新，跳过: {url}")
                    return True
        
        return False
    
    def download_file(self, url, local_path):
        """下载单个文件"""
        # 检查是否应该跳过已存在文件
        if self.should_skip_existing(url, local_path):
            with self.lock:
                self.skipped_files += 1
            return True
        
        # 准备下载参数
        file_size = 0
        headers = {}
        resume = False
        
        # 检查是否支持断点续传
        if os.path.exists(local_path):
            file_size = os.path.getsize(local_path)
            if file_size > 0:
                headers = {'Range': f'bytes={file_size}-'}
                resume = True
        
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
        logger.info(f"更新模式: {'开启' if self.update_only else '关闭'}")
        logger.info(f"校验和验证: {'开启' if self.use_checksum else '关闭'}")
        logger.info(f"存在文件处理: {self.existing_action}")
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
        total_attempted = self.downloaded_files + self.failed_files + self.skipped_files
        
        logger.info("\n" + "="*60)
        logger.info("下载统计:")
        logger.info(f"总文件数: {total_attempted}")
        logger.info(f"成功下载: {self.downloaded_files}")
        logger.info(f"跳过文件: {self.skipped_files}")
        logger.info(f"下载失败: {self.failed_files}")
        if total_attempted > 0:
            success_rate = (self.downloaded_files / total_attempted) * 100
            skip_rate = (self.skipped_files / total_attempted) * 100
            logger.info(f"下载成功率: {success_rate:.1f}%")
            logger.info(f"文件跳过率: {skip_rate:.1f}%")
        logger.info(f"总耗时: {elapsed:.1f} 秒")
        if elapsed > 0.1:
            speed = total_attempted / elapsed
            logger.info(f"平均速度: {speed:.2f} 文件/秒")
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
    parser.add_argument('--update', action='store_true', 
                   help='仅下载更新的文件(基于时间戳)')
    parser.add_argument('--checksum', action='store_true',
                   help='使用校验和验证文件完整性(更慢但更安全)')
    parser.add_argument('--existing', choices=['skip', 'overwrite', 'backup'], 
                   default='skip', help='处理已存在文件的策略')
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
        proxy=args.proxy,
        update_only=args.update,
        use_checksum=args.checksum,
        existing_action=args.existing
    )
    
    # 开始下载
    logger.info("启动下载进程...")
    success = downloader.start()
    
    if not success and downloader.downloaded_files == 0:
        logger.error("下载失败，请检查错误信息")
        sys.exit(1)
    elif downloader.failed_files > 0:
        logger.warning(f"下载完成，但有 {downloader.failed_files} 个文件失败")
        sys.exit(2)
    else:
        logger.info("下载完成!")

if __name__ == "__main__":
    main()