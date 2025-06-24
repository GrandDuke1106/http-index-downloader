# HTTP 目录下载器

一个高效、多线程的 HTTP 目录下载工具，支持代理、认证和断点续传功能，用于递归下载整个 HTTP 目录结构。

## 功能特点

- ✅ 递归下载整个 HTTP 目录结构
- 🔒 支持 HTTP 基本认证（URL 嵌入或命令行参数）
- 🌐 HTTP/HTTPS 代理支持（包括认证代理）
- ⚡ 自定义线程数并发下载
- 🔁 断点续传功能
- 📊 详细的下载统计和进度报告
- 🛡️ 健壮的错误处理和文件完整性检查
- 📁 保留原始目录结构

## 安装

### 依赖要求

- Python 3.6+
- 所需库：`requests`, `beautifulsoup4`

### 安装步骤

```bash
# 克隆仓库
git clone https://github.com/grandduke1106/http-index-downloader.git
cd http-index-downloader

# 安装依赖
pip install requests beautifulsoup4
```

## 使用说明

### 基本用法

```bash
python http_index_downloader.py "https://user:pass@example.com/path/"
```

### 完整语法

```bash
python http_index_downloader.py [URL] [选项]
```

### 选项说明

| 选项 | 描述 | 默认值 |
|------|------|--------|
| `--output DIR` | 输出目录 | `downloads` |
| `--threads N` | 并发下载线程数 | `10` |
| `--username USER` | HTTP 基本认证用户名 | (无) |
| `--password PASS` | HTTP 基本认证密码 | (无) |
| `--proxy URL` | HTTP/HTTPS 代理地址 | (无) |
| `--verbose` | 启用详细日志模式 | (关闭) |

### 使用代理

```bash
# 使用 HTTP 代理
python http_index_downloader.py "https://example.com/path/" --proxy "http://proxy.example.com:8080"

# 使用需要认证的代理
python http_index_downloader.py "https://example.com/path/" --proxy "http://username:password@proxy.example.com:8080"

```

### HTTP基本认证示例

```bash
# URL 中包含认证信息
python http_index_downloader.py "https://user:pass@example.com/path/"

# 使用命令行参数显式指定认证
python http_index_downloader.py "https://example.com/path/" --username user --password pass

# 同时使用代理和认证
python http_index_downloader.py "https://example.com/path/" --proxy "http://username:password@proxy:8080" --username user --password pass
```

### 高级用法

```bash
# 自定义输出目录
python http_index_downloader.py "https://example.com/path/" --output "my_downloads"

# 增加并发线程数（提高下载速度）
python http_index_downloader.py "https://example.com/path/" --threads 20

# 启用详细日志模式（调试用）
python http_index_downloader.py "https://example.com/path/" --verbose
```


## 注意事项

1. **代理支持**：
   - 仅支持 HTTP/HTTPS 代理（不支持 SOCKS）
   - 确保代理服务器允许连接到目标网站
   - 认证代理使用格式：`http://user:pass@proxy:port`

2. **HTTP基本认证**：
   - 优先使用命令行参数提供敏感凭证
   - URL 中的认证信息会被命令行参数覆盖

3. **性能优化**：
   - 小文件下载：使用更多线程（15-20）
   - 大文件下载：使用较少线程（5-10）
   - 网络不佳时：减少线程数或增加超时时间

4. **断点续传**：
   - 程序中断后重新运行会自动继续未完成的下载
   - 部分下载的文件会保留，直到下载完成

5. **目录结构**：
   - 本地目录结构会与远程服务器保持一致
   - 根目录名称基于 URL 路径自动生成

## 常见问题解决

1. **连接超时或失败**
- 检查网络连接和代理设置
- 尝试不使用代理（排除代理问题）
- 增加超时时间（修改代码中的 `timeout` 值）

2. **认证失败**
- 确认用户名和密码正确
- 尝试在 URL 中包含认证信息
- 检查服务器是否接受基本认证

3. **部分文件下载失败**
- 检查磁盘空间是否充足
- 确保有文件写入权限
- 使用 `--verbose` 查看详细错误信息

4. **下载速度慢**
- 增加 `--threads` 参数值
- 尝试不同的代理服务器
- 在网络空闲时段运行下载


## 许可证

本项目采用 [MIT 许可证](LICENSE)。