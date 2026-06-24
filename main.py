# PyQt6 PyQt6-WebEngine dnspython
import sys
import os
import socket
import json
import time
import hashlib
import random
import urllib.request
import urllib.parse
import dns.resolver
from datetime import datetime
from PyQt6.QtCore import *
from PyQt6.QtWidgets import *
from PyQt6.QtGui import QAction, QColor, QIcon, QKeySequence, QShortcut
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import (
    QWebEnginePage, QWebEngineDownloadRequest, QWebEngineSettings, QWebEngineProfile, QWebEngineScript
)
# ==================== ffmpeg H.265/HEVC 解码器（内联集成） ====================
# 使用 Microsoft Edge/Google Chrome 浏览器的解决方案
import subprocess
import threading
import tempfile
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

def get_ffmpeg_path():
    """获取 ffmpeg 可执行文件路径"""
    app_dir = get_app_dir()
    for p in [os.path.join(app_dir, "ffmpeg.exe"),
              os.path.join(app_dir, "libs", "ffmpeg.exe"),
              os.path.join(app_dir, "ffmpeg", "bin", "ffmpeg.exe")]:
        if os.path.exists(p):
            return p
    return "ffmpeg"

def check_ffmpeg():
    """检查 ffmpeg 是否可用并支持 HEVC"""
    fp = get_ffmpeg_path()
    try:
        r = subprocess.run([fp, "-version"], capture_output=True, text=True, timeout=5,
                           creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
        if r.returncode != 0: return False, None, "ffmpeg not found"
        c = subprocess.run([fp, "-codecs"], capture_output=True, text=True, timeout=5,
                           creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
        has_hevc = "hevc" in c.stdout.lower()
        ver = r.stdout.split('\n')[0] if r.stdout else "unknown"
        return True, fp, ver if has_hevc else f"{ver} (HEVC not found)"
    except: return False, None, "ffmpeg not found"

class FFmpegDecoder:
    def __init__(self, log=None):
        self.log = log
        self.avail, self.path, self.ver = check_ffmpeg()
        self._procs = []
        if self.log:
            self.log(f"[ffmpeg] {'OK: '+self.ver if self.avail else 'unavailable: '+self.ver}")

    def cleanup(self):
        for p in self._procs:
            try: p.terminate(); p.wait(timeout=3)
            except: pass
        self._procs.clear()

class VideoProxyHandler(BaseHTTPRequestHandler):
    decoder = None
    def log_message(self, *a): pass
    def do_GET(self):
        p = urlparse(self.path)
        if p.path == "/health":
            self._json({"status":"ok","ffmpeg":self.decoder and self.decoder.avail})
        elif p.path == "/transcode":
            self._transcode(parse_qs(p.query))
        else:
            self.send_error(404)
    def _json(self, d):
        self.send_response(200)
        self.send_header("Content-Type","application/json")
        self.send_header("Access-Control-Allow-Origin","*")
        self.end_headers()
        self.wfile.write(json.dumps(d).encode())
    def _transcode(self, qs):
        url = qs.get("url",[None])[0]
        if not url: self.send_error(400); return
        if not self.decoder or not self.decoder.avail: self.send_error(503); return
        self.send_response(200)
        self.send_header("Content-Type","video/mp4")
        self.send_header("Access-Control-Allow-Origin","*")
        self.send_header("Cache-Control","no-cache")
        self.end_headers()
        out = tempfile.mktemp(suffix=".mp4")
        try:
            cmd = [self.decoder.path, "-y", "-c:v", "hevc", "-hwaccel","auto",
                   "-i", url, "-c:v","libx264","-preset","ultrafast","-tune","zerolatency",
                   "-crf","23", "-c:a","aac", "-b:a","128k",
                   "-f","mp4","-movflags","frag_keyframe+empty_moov+faststart", out]
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 creationflags=subprocess.CREATE_NO_WINDOW if sys.platform=="win32" else 0)
            p.wait(timeout=120)
            if os.path.exists(out):
                with open(out,'rb') as f:
                    self.wfile.write(f.read())
                os.unlink(out)
        except: pass
    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin","*")
        self.send_header("Access-Control-Allow-Methods","GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers","*")
        self.end_headers()

class VideoProxyServer:
    def __init__(self, host="127.0.0.1", port=0, logger=None):
        self.host, self.port, self.logger = host, port, logger
        self.server, self.thread = None, None
        self.decoder = FFmpegDecoder(log=(logger.info if logger else None))
        VideoProxyHandler.decoder = self.decoder
    def start(self):
        try:
            self.server = HTTPServer((self.host, self.port), VideoProxyHandler)
            self.port = self.server.server_port
            self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
            self.thread.start()
            if self.logger: self.logger.info(f"[video_proxy] started on {self.host}:{self.port}")
            return self.host, self.port
        except Exception as e:
            if self.logger: self.logger.error(f"[video_proxy] start failed: {e}")
            return None, None
    def stop(self):
        if self.server: self.server.shutdown(); self.decoder.cleanup()
        if self.logger: self.logger.info("[video_proxy] stopped")
    def proxy_url(self): return f"http://{self.host}:{self.port}"
    def hevc_js(self):
        avail = str(self.decoder.avail).lower()
        proxy = self.proxy_url()
        return f"""
(function(){{
    if (window.top !== window) return;
    window._lssHevcDecoder = {{ available:{avail}, proxyUrl:'{proxy}', transcodeUrl:function(u){{ return '{proxy}/transcode?url='+encodeURIComponent(u); }} }};
    console.log('[LSS HEVC] proxy ready, available:',{avail});
    if({avail}){{
        function setupObserver() {{
            var root = document.body || document.documentElement;
            if (!root) return;
            new MutationObserver(function(muts){{ muts.forEach(function(m){{ m.addedNodes.forEach(function(n){{
                if(n.tagName==='VIDEO'||n.tagName==='SOURCE') checkSrc(n);
                if(n.querySelectorAll) n.querySelectorAll('video,source').forEach(checkSrc);
            }})}})}}).observe(root,{{childList:true,subtree:true}});
        }}
        function checkSrc(el){{ if(el._lssH265)return; el._lssH265=true;
            var s=el.src||el.getAttribute('src');
            if(s&&(/\\.(hevc|h265|265)/i.test(s))){{ el.setAttribute('data-original-src',s); el.src=window._lssHevcDecoder.transcodeUrl(s); }}
        }}
        if (document.readyState === 'loading') {{
            document.addEventListener('DOMContentLoaded', setupObserver);
        }} else {{
            setupObserver();
        }}
    }}
}})();"""

_video_proxy_server = None
def get_vps(logger=None):
    global _video_proxy_server
    if _video_proxy_server is None: _video_proxy_server = VideoProxyServer(logger=logger)
    return _video_proxy_server
def start_video_proxy(logger=None): return get_vps(logger).start()
def stop_video_proxy():
    global _video_proxy_server
    if _video_proxy_server: _video_proxy_server.stop(); _video_proxy_server = None

# 国内优先 DNS 列表
CUSTOM_DNS = [
    "223.5.5.5", "223.6.6.6",
    "114.114.114.114",
    "119.29.29.29", "119.28.28.28",
    "180.76.76.76",
    "180.184.1.1", "180.184.2.2",
    "1.1.1.1", "1.0.0.1",
    "8.8.8.8", "8.8.4.4",
    "9.9.9.9"
]

def get_app_dir():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

# 日志系统
class Logger:
    """日志管理系统"""
    def __init__(self, enabled=True):
        self.start_time = time.time()
        self.enabled = enabled  # 是否启用日志记录
        self.log_dir = os.path.join(get_app_dir(), "logs")
        
        if self.enabled:
            os.makedirs(self.log_dir, exist_ok=True)
            
            # 生成日志文件名：20260613091530_log.log
            timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
            self.log_file = os.path.join(self.log_dir, f"{timestamp}_log.log")
            
            # 创建日志文件
            with open(self.log_file, 'w', encoding='utf-8') as f:
                pass
            
            self.write("main/info", "started")
        else:
            self.log_file = None
    
    def get_elapsed_ms(self):
        """获取从程序开始运行的毫秒数"""
        return int((time.time() - self.start_time) * 1000)
    
    def write(self, level, message):
        """写入日志"""
        # 如果未启用日志，不记录任何内容
        if not self.enabled:
            return
        
        elapsed = self.get_elapsed_ms()
        log_line = f"{elapsed} [{level}] {message}\n"
        
        # 写入文件
        if self.log_file:
            with open(self.log_file, 'a', encoding='utf-8') as f:
                f.write(log_line)
    
    def info(self, message):
        """普通信息"""
        self.write("main/info", message)
    
    def warning(self, message):
        """警告信息（不影响程序运行）"""
        self.write("main/warning", message)
    
    def error(self, message):
        """错误信息（会导致程序崩溃）"""
        self.write("main/error", message)
    
    def js_warning(self, message):
        """JavaScript警告"""
        self.write("js/warning", message)
    
    def js_error(self, message):
        """JavaScript错误"""
        self.write("js/error", message)

# 先加载设置
SETTINGS_PATH = os.path.join(get_app_dir(), "resources", "custom", "data", "setting.txt")

DEFAULT_SETTINGS = {
    "dns_mode": "auto",
    "custom_dns": "",
    "theme": "system",
    "show_dns_status": "true",
    "dns_error_color": "#cc0000",
    "dns_ok_color": "#008800",
    "home_background": "default_background.jpg",
    "home_shortcuts_list": '[{"name":"百度","url":"https://www.baidu.com","icon":""},{"name":"必应","url":"https://www.bing.com","icon":""},{"name":"哔哩哔哩","url":"https://www.bilibili.com","icon":""},{"name":"DeepSeek","url":"https://chat.deepseek.com","icon":""}]',
    "language": "简体中文",
    "full_isolation": "false",
    "history": "[]",
    "media_volume": "1.0",
    "download_threads": "8",
    "shortcuts": '{"settings":"Ctrl+Shift+I","appearance":"Ctrl+Shift+A","dns":"Ctrl+Shift+N","volume":"Ctrl+Shift+V","privacy":"Ctrl+Shift+P","download":"Ctrl+Shift+D","download_settings":"Ctrl+Shift+S"}'
}

def load_settings():
    settings = DEFAULT_SETTINGS.copy()
    if os.path.exists(SETTINGS_PATH):
        try:
            with open(SETTINGS_PATH, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if '=' in line:
                        key, value = line.split('=', 1)
                        settings[key.strip()] = value.strip()
        except Exception:
            pass
    return settings

def save_settings(settings):
    """保存设置并记录更改到日志"""
    global global_settings
    
    # 比较新旧设置的差异
    changed_settings = []
    for key in settings:
        if key not in global_settings or global_settings[key] != settings[key]:
            old_value = global_settings.get(key, '(not set)')
            new_value = settings[key]
            changed_settings.append(f"{key}: {old_value} -> {new_value}")
    
    # 记录设置更改
    if changed_settings:
        for change in changed_settings:
            logger.info(f"set setting - {change}")
    
    # 保存设置
    os.makedirs(os.path.dirname(SETTINGS_PATH), exist_ok=True)
    with open(SETTINGS_PATH, 'w', encoding='utf-8') as f:
        for key, value in settings.items():
            f.write(f"{key}={value}\n")
    
    # 更新全局设置
    global_settings = settings.copy()

global_settings = load_settings()

# 根据设置决定是否启用日志（完全隔离时不记录日志）
logger_enabled = global_settings.get("full_isolation", "false") != "true"
logger = Logger(logger_enabled)

# 全局异常处理器 - 确保程序崩溃时也能记录错误
def exception_handler(exc_type, exc_value, exc_traceback):
    """捕获所有未处理的异常并记录到日志"""
    if issubclass(exc_type, KeyboardInterrupt):
        # 用户中断（Ctrl+C），正常退出
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return
    
    # 格式化异常信息
    import traceback
    error_msg = ''.join(traceback.format_exception(exc_type, exc_value, exc_traceback))
    
    # 记录到日志
    logger.error(f"Uncaught exception:\n{error_msg}")
    
    # 同时打印到控制台（原始的stderr）
    original_stderr.write(f"\n[CRITICAL ERROR] Uncaught exception:\n{error_msg}\n")
    original_stderr.flush()

# 设置全局异常处理器
sys.excepthook = exception_handler

# atexit处理器 - 确保程序退出时日志被正确保存
import atexit

def cleanup_log():
    """程序退出时的清理工作"""
    try:
        # 确保日志文件被写入
        if hasattr(logger, 'log_file') and logger.log_file:
            logger.write("main/info", "program exited")
            # 再次flush确保写入
            with open(logger.log_file, 'a', encoding='utf-8') as f:
                f.flush()
                f.flush()
    except:
        pass

atexit.register(cleanup_log)

# 重定向stdout和stderr
class StreamToLogger:
    """将stdout/stderr重定向到日志系统"""
    def __init__(self, logger, level):
        self.logger = logger
        self.level = level
        self.linebuf = ''
    
    def write(self, message):
        if message.strip():  # 忽略空消息
            self.logger.write(self.level, message.strip())
    
    def flush(self):
        pass

# 保存原始的stdout和stderr
original_stdout = sys.stdout
original_stderr = sys.stderr

# 重定向标准输出和错误输出
sys.stdout = StreamToLogger(logger, "main/info")
sys.stderr = StreamToLogger(logger, "main/error")

# Language support - dynamically load from resources/languages folder
def get_available_languages():
    """扫描语言文件夹，返回可用语言列表（文件名去掉.txt后缀）"""
    lang_dir = os.path.join(get_app_dir(), "resources", "languages")
    languages = {}
    if os.path.exists(lang_dir):
        for filename in os.listdir(lang_dir):
            if filename.endswith('.txt') and not filename.startswith('_'):
                lang_name = filename[:-4]  # 去掉 .txt 后缀
                languages[lang_name] = lang_name
    return languages

LANGUAGES = get_available_languages()

def get_language_path(lang_name):
    lang_path = os.path.join(get_app_dir(), "resources", "languages", lang_name + ".txt")
    return lang_path

def load_language(lang_name):
    lang_path = get_language_path(lang_name)
    if not os.path.exists(lang_path):
        available = get_available_languages()
        if available:
            chinese_names = ["简体中文", "Chinese", "中文", "chinese", "Simplified Chinese", "zh-CN"]
            for name in chinese_names:
                if name in available:
                    lang_path = get_language_path(name)
                    break
            else:
                lang_path = get_language_path(list(available.keys())[0])
        else:
            return {}
    lang_dict = {}
    try:
        with open(lang_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    if '=' in line:
                        key, value = line.split('=', 1)
                        lang_dict[key.strip()] = value.strip()
    except:
        pass
    return lang_dict

# 使用设置中的语言，如果没有则使用第一个可用语言
lang = load_language(global_settings.get("language", list(LANGUAGES.keys())[0] if LANGUAGES else "简体中文"))

def tr(key):
    return lang.get(key, key)

# 界面语言到翻译API语言代码的映射
LANGUAGE_TO_TRANSLATE_CODE = {
    "简体中文": "zh",
    "繁體中文": "cht",
    "English": "en",
    "日本語": "jp",
    "한국어": "kor",
    "Français": "fra",
    "Deutsch": "de",
    "Español": "spa",
    "Русский": "ru",
    "Português": "pt",
    "Italiano": "it",
    "العربية": "ara",
    "Polski": "pl",
    "Tiếng Việt": "vie",
    "Türkçe": "tr",
}

# 百度翻译 API 配置
BAIDU_TRANSLATE_APP_ID = "lss190216-lssbrowser-114514-0001"
BAIDU_TRANSLATE_SECRET_KEY = "R1Zn_d8ttc4t68qs0lf83rje0"

def baidu_translate_text(text, target_lang="zh"):
    """使用百度翻译 API 翻译文本"""
    if not text or len(text) > 5000:
        return None
    
    salt = str(random.randint(32768, 65536))
    sign_str = BAIDU_TRANSLATE_APP_ID + text + salt + BAIDU_TRANSLATE_SECRET_KEY
    sign = hashlib.md5(sign_str.encode('utf-8')).hexdigest()
    
    url = "https://fanyi-api.baidu.com/api/trans/vip/translate"
    params = {
        "q": text,
        "from": "auto",
        "to": target_lang,
        "appid": BAIDU_TRANSLATE_APP_ID,
        "salt": salt,
        "sign": sign
    }
    
    try:
        query_string = "&".join([f"{k}={urllib.parse.quote_plus(v)}" for k, v in params.items()])
        full_url = f"{url}?{query_string}"
        
        req = urllib.request.Request(full_url)
        req.add_header('User-Agent', 'Mozilla/5.0')
        
        with urllib.request.urlopen(req, timeout=10) as response:
            result = json.loads(response.read().decode('utf-8'))
            
            if 'trans_result' in result:
                translated = ''.join([item['dst'] for item in result['trans_result']])
                return translated
            elif 'error_code' in result:
                logger.warning(f"baidu translate error: {result.get('error_code', 'unknown')}")
                return None
    except Exception as e:
        logger.warning(f"baidu translate failed: {e}")
        return None
    
    return None

def escape_js_string(s):
    """转义字符串中的反斜杠和单引号，用于安全嵌入 JavaScript 字符串字面量"""
    return str(s).replace('\\', '\\\\').replace("'", "\\'")

def add_history(url, title):
    url_str = url.toString() if hasattr(url, 'toString') else str(url)
    
    # 记录历史记录到日志
    logger.info(f"add history - {title} ({url_str})")
    
    history_str = global_settings.get("history", "[]")
    try:
        history = eval(history_str)
    except:
        history = []
    
    new_entry = {
        "url": url_str,
        "title": title,
        "date": QDateTime.currentDateTime().toString(Qt.DateFormat.ISODate)
    }
    
    history = [h for h in history if h["url"] != new_entry["url"]]
    history.append(new_entry)
    
    if len(history) > 100:
        history = history[-100:]
    
    global_settings["history"] = str(history)
    save_settings(global_settings)

# 系统下载文件夹
def get_download_path():
    return os.path.join(os.path.expanduser("~"), "Downloads")

# 主页路径
def get_home_page_url():
    home_path = os.path.join(get_app_dir(), "resources", "home.html")
    return QUrl.fromLocalFile(home_path)

# 全局DNS解析器
dns_resolver = dns.resolver.Resolver()
dns_resolver.nameservers = CUSTOM_DNS
dns_resolver.lifetime = 2


# ==================== 多线程下载引擎 ====================
import threading
import urllib.request
from queue import Queue

class MultiThreadDownloader:
    """多线程下载引擎"""
    
    def __init__(self, url, save_path, num_threads=8, logger=None):
        self.url = url
        self.save_path = save_path
        self.num_threads = num_threads
        self.logger = logger
        
        self.total_size = 0
        self.downloaded = 0
        self.speed = 0
        self.speed_history = []
        self.state = "pending"  # pending, downloading, completed, failed, paused
        self.chunks = []  # 每个线程负责的区间
        self.threads = []
        self.stop_flag = False
        self.lock = threading.Lock()
        self.start_time = 0
        self.last_update_time = 0
        self.last_downloaded = 0
        self.temp_files = []
        
        # 支持断点续传的临时文件
        self.temp_dir = os.path.join(os.path.dirname(save_path), ".lss_download_temp")
        os.makedirs(self.temp_dir, exist_ok=True)
        
    def get_file_size(self):
        """获取文件大小，判断是否支持多线程"""
        try:
            request = urllib.request.Request(self.url, method='HEAD')
            request.add_header('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')
            
            # 尝试多个DNS解析
            parsed = urllib.parse.urlparse(self.url)
            hostname = parsed.hostname
            
            try:
                # 使用自定义DNS解析
                answers = dns_resolver.resolve(hostname, 'A')
                ip = str(answers[0])
                # 创建socket连接
                socket.setdefaulttimeout(10)
            except:
                pass
            
            with urllib.request.urlopen(request, timeout=15) as response:
                self.total_size = int(response.headers.get('Content-Length', 0))
                accept_ranges = response.headers.get('Accept-Ranges', '')
                supports_range = accept_ranges.lower() == 'bytes'
                
                if self.logger:
                    self.logger.info(f"[multi_download] File size: {self.total_size}, Accept-Ranges: {supports_range}")
                
                return self.total_size, supports_range
        except Exception as e:
            if self.logger:
                self.logger.error(f"[multi_download] Failed to get file size: {e}")
            return 0, False
    
    def calculate_chunks(self):
        """计算每个线程下载的区间"""
        if self.total_size <= 0 or self.num_threads <= 1:
            return [(0, self.total_size - 1 if self.total_size > 0 else 0)]
        
        chunk_size = self.total_size // self.num_threads
        chunks = []
        for i in range(self.num_threads):
            start = i * chunk_size
            end = start + chunk_size - 1 if i < self.num_threads - 1 else self.total_size - 1
            chunks.append((start, end))
        return chunks
    
    def download_chunk(self, chunk_index, start, end):
        """下载指定区间"""
        if self.stop_flag:
            return
        
        temp_file = os.path.join(self.temp_dir, f"{chunk_index}.tmp")
        self.temp_files.append(temp_file)
        
        # 检查是否有已下载的部分（断点续传）
        downloaded_in_chunk = 0
        if os.path.exists(temp_file):
            downloaded_in_chunk = os.path.getsize(temp_file)
            start += downloaded_in_chunk
        
        if end >= 0 and start > end:
            return
        
        try:
            request = urllib.request.Request(self.url)
            request.add_header('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')
            
            if end >= 0:
                request.add_header('Range', f'bytes={start}-{end}')
                if self.logger:
                    self.logger.info(f"[multi_download] Chunk {chunk_index}: Range bytes={start}-{end}")
            elif start > 0:
                request.add_header('Range', f'bytes={start}-')
                if self.logger:
                    self.logger.info(f"[multi_download] Chunk {chunk_index}: Range bytes={start}- (resume)")
            else:
                if self.logger:
                    self.logger.info(f"[multi_download] Chunk {chunk_index}: No Range header (full download)")
            
            with urllib.request.urlopen(request, timeout=30) as response:
                status = response.getcode()
                if self.logger:
                    self.logger.info(f"[multi_download] Chunk {chunk_index}: Response status {status}")
                
                with open(temp_file, 'ab') as f:
                    while not self.stop_flag:
                        chunk_data = response.read(8192)
                        if not chunk_data:
                            break
                        f.write(chunk_data)
                        f.flush()
                        
                        with self.lock:
                            self.downloaded += len(chunk_data)
                            if self.logger and self.downloaded % 1024000 < 8192:
                                self.logger.info(f"[multi_download] Chunk {chunk_index}: downloaded={self.downloaded}")
                            
        except Exception as e:
            if not self.stop_flag:
                with self.lock:
                    if self.logger:
                        self.logger.warning(f"[multi_download] Chunk {chunk_index} error: {e}")
    
    def update_speed(self):
        """更新下载速度"""
        current_time = time.perf_counter()
        if self.last_update_time > 0:
            time_delta = current_time - self.last_update_time
            bytes_delta = self.downloaded - self.last_downloaded
            if time_delta > 0:
                self.speed = bytes_delta / time_delta
                if self.logger and bytes_delta > 0:
                    self.logger.info(f"[multi_download] speed: {self.speed:.2f} B/s, downloaded: {self.downloaded}")
                self.speed_history.append({'time': current_time, 'speed': self.speed})
                if len(self.speed_history) > 10:
                    self.speed_history.pop(0)
        
        self.last_update_time = current_time
        self.last_downloaded = self.downloaded
    
    def start(self):
        """开始下载（在后台线程中执行）"""
        self.state = "downloading"
        self.start_time = time.perf_counter()
        self.stop_flag = False
        
        # 在后台线程中启动下载
        start_thread = threading.Thread(target=self._start_download)
        start_thread.daemon = True
        start_thread.start()
    
    def _start_download(self):
        """实际启动下载（后台线程）"""
        # 获取文件大小
        file_size, supports_range = self.get_file_size()
        
        if file_size == 0 or not supports_range:
            # 无法获取文件大小或不支持分块下载，使用单线程下载整个文件
            self.num_threads = 1
            self.chunks = [(0, -1)]  # -1 表示下载到文件末尾
        else:
            self.chunks = self.calculate_chunks()
        
        if self.logger:
            self.logger.info(f"[multi_download] Starting with {self.num_threads} threads, chunks: {self.chunks}")
        
        # 创建下载线程
        for i, (start, end) in enumerate(self.chunks):
            thread = threading.Thread(target=self.download_chunk, args=(i, start, end))
            thread.daemon = True
            thread.start()
            self.threads.append(thread)
        
        # 启动速度监控线程
        monitor_thread = threading.Thread(target=self._monitor_progress)
        monitor_thread.daemon = True
        monitor_thread.start()
    
    def _monitor_progress(self):
        """监控下载进度"""
        while not self.stop_flag:
            self.update_speed()
            
            # 检查是否所有线程完成
            all_done = all(not t.is_alive() for t in self.threads)
            if all_done:
                # 如果 total_size > 0，检查是否下载完成
                if self.total_size > 0 and self.downloaded >= self.total_size:
                    self._merge_files()
                    self.state = "completed"
                    break
                elif self.total_size == 0 and self.downloaded > 0:
                    # 无法获取文件大小时，只要下载了数据就认为完成
                    self._merge_files()
                    self.state = "completed"
                    break
            
            time.sleep(0.2)
    
    def _merge_files(self):
        """合并临时文件"""
        try:
            with open(self.save_path, 'wb') as output:
                for i in range(len(self.chunks)):
                    temp_file = os.path.join(self.temp_dir, f"{i}.tmp")
                    if os.path.exists(temp_file):
                        with open(temp_file, 'rb') as f:
                            output.write(f.read())
                        # 删除临时文件
                        os.unlink(temp_file)
            
            # 清理临时目录
            try:
                os.rmdir(self.temp_dir)
            except:
                pass
            
            if self.logger:
                self.logger.info(f"[multi_download] File merged: {self.save_path}")
        except Exception as e:
            if self.logger:
                self.logger.error(f"[multi_download] Merge failed: {e}")
            self.state = "failed"
    
    def pause(self):
        """暂停下载"""
        self.stop_flag = True
        self.state = "paused"
    
    def resume(self):
        """恢复下载"""
        if self.state == "paused":
            self.stop_flag = False
            self.state = "downloading"
            self.threads.clear()
            
            # 重新计算剩余区间
            for i, (start, end) in enumerate(self.chunks):
                temp_file = os.path.join(self.temp_dir, f"{i}.tmp")
                if os.path.exists(temp_file):
                    downloaded_in_chunk = os.path.getsize(temp_file)
                    start += downloaded_in_chunk
                
                if start <= end:
                    thread = threading.Thread(target=self.download_chunk, args=(i, start, end))
                    thread.daemon = True
                    thread.start()
                    self.threads.append(thread)
            
            # 重新启动监控
            monitor_thread = threading.Thread(target=self._monitor_progress)
            monitor_thread.daemon = True
            monitor_thread.start()
    
    def cancel(self):
        """取消下载"""
        self.stop_flag = True
        self.state = "failed"
        
        for thread in self.threads:
            try:
                thread.join(timeout=5)
            except:
                pass
        
        import shutil
        if os.path.exists(self.temp_dir):
            try:
                shutil.rmtree(self.temp_dir)
            except Exception as e:
                if self.logger:
                    self.logger.warning(f"[multi_download] Failed to remove temp dir: {e}")
    
    def get_progress_percent(self):
        """获取进度百分比"""
        if self.total_size > 0:
            return int(self.downloaded / self.total_size * 100)
        return 0
    
    def format_size(self, size):
        """格式化大小"""
        if size >= 1024 * 1024 * 1024:
            return f"{size / 1024 / 1024 / 1024:.2f} GB"
        elif size >= 1024 * 1024:
            return f"{size / 1024 / 1024:.2f} MB"
        elif size >= 1024:
            return f"{size / 1024:.2f} KB"
        else:
            return f"{size} B"
    
    def format_speed(self):
        """格式化速度"""
        speed = self.speed
        if speed >= 1024 * 1024:
            return f"{speed / 1024 / 1024:.2f} MB/s"
        elif speed >= 1024:
            return f"{speed / 1024:.2f} KB/s"
        else:
            return f"{speed:.0f} B/s"


class DownloadTask:
    def __init__(self, download_item=None, file_name="", save_path="", url="", use_multi_thread=False, num_threads=8):
        self.download_item = download_item  # QWebEngineDownloadRequest（浏览器内置下载）
        self.multi_thread_downloader = None  # MultiThreadDownloader（多线程下载）
        self.file_name = file_name
        self.save_path = save_path
        self.url = url
        self.total_bytes = 0
        self.received_bytes = 0
        self.speed = 0
        self.start_time = time.perf_counter()
        self.speed_history = []
        self.state = "downloading"
        self.mime_type = ""
        self.use_multi_thread = use_multi_thread
        self.num_threads = num_threads
        
        # 如果使用多线程下载，创建 MultiThreadDownloader
        if use_multi_thread and url:
            self.multi_thread_downloader = MultiThreadDownloader(
                url, save_path, num_threads, logger
            )
    
    def start_multi_thread(self):
        """启动多线程下载"""
        if self.multi_thread_downloader:
            self.multi_thread_downloader.start()
    
    def cancel(self):
        """取消下载"""
        if self.multi_thread_downloader:
            self.multi_thread_downloader.cancel()
        elif self.download_item:
            self.download_item.cancel()
        self.state = "failed"

    def update_progress(self, received, total):
        self.received_bytes = received
        if total > 0:
            self.total_bytes = total

        current_time = time.perf_counter()
        if len(self.speed_history) > 0:
            last_time = self.speed_history[-1]['time']
            last_recv = self.speed_history[-1]['received']
            time_delta = current_time - last_time
            if time_delta >= 0.5:
                bytes_delta = received - last_recv
                if bytes_delta > 0:
                    self.speed = bytes_delta / time_delta
                    self.speed_history.append({'time': current_time, 'received': received})
                    if len(self.speed_history) > 10:
                        self.speed_history.pop(0)
        else:
            self.speed_history.append({'time': current_time, 'received': received})
    
    def update_from_multi_thread(self):
        """从多线程下载器更新进度"""
        if self.multi_thread_downloader:
            self.received_bytes = self.multi_thread_downloader.downloaded
            self.total_bytes = self.multi_thread_downloader.total_size
            self.speed = self.multi_thread_downloader.speed
            self.state = self.multi_thread_downloader.state

    def format_size(self, size):
        if size >= 1024 * 1024 * 1024:
            return f"{size / 1024 / 1024 / 1024:.2f} GB"
        elif size >= 1024 * 1024:
            return f"{size / 1024 / 1024:.2f} MB"
        elif size >= 1024:
            return f"{size / 1024:.2f} KB"
        else:
            return f"{size} B"

    def format_speed(self):
        speed = self.speed
        if speed >= 1024 * 1024:
            return f"{speed / 1024 / 1024:.2f} MB/s"
        elif speed >= 1024:
            return f"{speed / 1024:.2f} KB/s"
        else:
            return f"{speed:.0f} B/s"

    def get_progress_percent(self):
        if self.total_bytes > 0:
            return int(self.received_bytes / self.total_bytes * 100)
        return 0


class DownloadItemWidget(QWidget):
    def __init__(self, task, parent=None):
        super().__init__(parent)
        self.task = task
        self.init_ui()

    def init_ui(self):
        main_layout = QHBoxLayout()
        main_layout.setContentsMargins(10, 8, 10, 8)
        main_layout.setSpacing(15)

        self.file_icon_label = QLabel()
        self.file_icon_label.setFixedSize(40, 40)
        self.file_icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.file_icon_label.setText("📄")
        self.file_icon_label.setStyleSheet("font-size: 24px;")

        info_layout = QVBoxLayout()
        info_layout.setSpacing(4)

        self.file_name_label = QLabel(self.task.file_name)
        self.file_name_label.setStyleSheet("font-size: 14px; font-weight: bold; color: #333;")
        self.file_name_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedHeight(6)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                border: none;
                background-color: #E0E0E0;
                border-radius: 3px;
            }
            QProgressBar::chunk {
                background-color: #4CAF50;
                border-radius: 3px;
            }
        """)

        status_layout = QHBoxLayout()
        status_layout.setSpacing(8)

        self.status_label = QLabel(tr("downloading"))
        self.status_label.setStyleSheet("font-size: 12px; color: #666;")

        self.speed_label = QLabel("0 B/s")
        self.speed_label.setStyleSheet("font-size: 12px; color: #999;")

        self.size_label = QLabel(f"0 / {self.task.format_size(self.task.total_bytes)}")
        self.size_label.setStyleSheet("font-size: 12px; color: #999;")

        status_layout.addWidget(self.status_label)
        status_layout.addWidget(self.speed_label)
        status_layout.addStretch()
        status_layout.addWidget(self.size_label)

        self.open_btn = QPushButton()
        self.open_btn.setFixedSize(32, 32)
        self.open_btn.setText("▶")
        self.open_btn.setStyleSheet("""
            QPushButton {
                border: none;
                font-size: 24px;
                background-color: transparent;
            }
            QPushButton:hover {
                background-color: #E0E0E0;
                border-radius: 16px;
            }
        """)
        self.open_btn.clicked.connect(self.open_file)
        self.open_btn.setVisible(False)

        self.open_folder_btn = QPushButton()
        self.open_folder_btn.setFixedSize(32, 32)
        self.open_folder_btn.setText("📁")
        self.open_folder_btn.setStyleSheet("""
            QPushButton {
                border: none;
                font-size: 16px;
                background-color: transparent;
            }
            QPushButton:hover {
                background-color: #E0E0E0;
                border-radius: 16px;
            }
        """)
        self.open_folder_btn.clicked.connect(self.open_folder)

        self.delete_btn = QPushButton()
        self.delete_btn.setFixedSize(32, 32)
        self.delete_btn.setText("✕")
        self.delete_btn.setStyleSheet("""
            QPushButton {
                border: none;
                font-size: 16px;
                background-color: transparent;
            }
            QPushButton:hover {
                background-color: #E0E0E0;
                border-radius: 16px;
            }
        """)
        self.delete_btn.clicked.connect(self.delete_task)
        self.delete_btn.setVisible(False)

        file_name_layout = QHBoxLayout()
        file_name_layout.setSpacing(10)
        file_name_layout.addWidget(self.file_name_label, 1)
        file_name_layout.addWidget(self.open_btn)
        file_name_layout.addWidget(self.open_folder_btn)
        file_name_layout.addWidget(self.delete_btn)

        info_layout.addLayout(file_name_layout)
        info_layout.addWidget(self.progress_bar)
        info_layout.addLayout(status_layout)

        main_layout.addWidget(self.file_icon_label)
        main_layout.addLayout(info_layout, 1)

        self.setLayout(main_layout)
        self.setStyleSheet("""
            QWidget {
                background-color: white;
                border-bottom: 1px solid #E0E0E0;
            }
            QWidget:hover {
                background-color: #F5F5F5;
            }
        """)

    def update_display(self):
        progress = self.task.get_progress_percent()
        self.progress_bar.setValue(progress)
        self.speed_label.setText(self.task.format_speed())

        if self.task.total_bytes > 0:
            received = self.task.format_size(self.task.received_bytes)
            total = self.task.format_size(self.task.total_bytes)
            self.size_label.setText(f"{received} / {total}")
        else:
            self.size_label.setText(self.task.format_size(self.task.received_bytes))

        if self.task.state == "downloading":
            self.status_label.setText(tr("downloading"))
            self.status_label.setStyleSheet("font-size: 12px; color: #2196F3;")
            self.open_btn.setVisible(False)
            self.delete_btn.setVisible(True)
        elif self.task.state == "completed":
            self.status_label.setText(tr("download_complete"))
            self.status_label.setStyleSheet("font-size: 12px; color: #4CAF50;")
            self.progress_bar.setStyleSheet("""
                QProgressBar {
                    border: none;
                    background-color: #E0E0E0;
                    border-radius: 3px;
                }
                QProgressBar::chunk {
                    background-color: #4CAF50;
                    border-radius: 3px;
                }
            """)
            self.open_btn.setVisible(True)
            self.delete_btn.setVisible(False)
        elif self.task.state == "failed":
            self.status_label.setText(tr("delete_task"))
            self.status_label.setStyleSheet("font-size: 12px; color: #9E9E9E;")
            self.open_btn.setVisible(False)
            self.delete_btn.setVisible(False)

    def delete_task(self):
        if self.task.state == "downloading":
            self.task.state = "failed"
            self.task.cancel()
            self.update_display()

    def open_file(self):
        if os.path.exists(self.task.save_path):
            os.startfile(self.task.save_path)

    def open_folder(self):
        folder_path = os.path.dirname(self.task.save_path)
        if os.path.exists(folder_path):
            os.startfile(folder_path)
        else:
            os.startfile(get_download_path())


class DownloadManagerWindow(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.download_tasks = {}
        self.multi_thread_tasks = {}  # 多线程下载任务
        self.init_ui()
        
        # 定时器用于更新多线程下载进度
        self.update_timer = QTimer()
        self.update_timer.timeout.connect(self.update_multi_thread_progress)
        self.update_timer.start(200)  # 每200ms更新一次

    def init_ui(self):
        self.setWindowTitle(tr("download"))
        self.setGeometry(100, 100, 600, 500)
        
        icon_path = os.path.join(get_app_dir(), "resources", "icon.ico")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))

        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(0, 0, 0, 0)

        header_widget = QWidget()
        header_widget.setFixedHeight(50)
        header_widget.setStyleSheet("background-color: #FAFAFA; border-bottom: 1px solid #E0E0E0;")
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(15, 0, 15, 0)

        title_label = QLabel(tr("download_content"))
        title_label.setStyleSheet("font-size: 16px; font-weight: bold; color: #333;")

        self.clear_completed_btn = QPushButton(tr("clear_completed"))
        self.clear_completed_btn.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                border: none;
                color: #2196F3;
                font-size: 13px;
            }
            QPushButton:hover {
                color: #1976D2;
            }
        """)
        self.clear_completed_btn.clicked.connect(self.clear_completed)

        header_layout.addWidget(title_label)
        header_layout.addStretch()
        header_layout.addWidget(self.clear_completed_btn)

        header_widget.setLayout(header_layout)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setStyleSheet("border: none;")

        self.download_list_widget = QWidget()
        self.download_list_layout = QVBoxLayout()
        self.download_list_layout.setContentsMargins(0, 0, 0, 0)
        self.download_list_layout.setSpacing(0)
        self.download_list_layout.addStretch()
        self.download_list_widget.setLayout(self.download_list_layout)

        self.scroll_area.setWidget(self.download_list_widget)

        main_layout.addWidget(header_widget)
        main_layout.addWidget(self.scroll_area)

        self.setLayout(main_layout)
        self.setStyleSheet("""
            QWidget {
                background-color: white;
            }
        """)
    
    def update_multi_thread_progress(self):
        """更新多线程下载任务的进度"""
        for task_id, task_data in self.multi_thread_tasks.items():
            task = task_data['task']
            if task.multi_thread_downloader:
                task.update_from_multi_thread()
                if task_data['widget']:
                    task_data['widget'].update_display()

    def add_multi_thread_download(self, url, file_name, save_path, num_threads):
        """添加多线程下载任务"""
        task = DownloadTask(
            download_item=None,
            file_name=file_name,
            save_path=save_path,
            url=url,
            use_multi_thread=True,
            num_threads=num_threads
        )
        task_id = id(task)
        self.multi_thread_tasks[task_id] = {'task': task, 'widget': None, 'visible': False}
        self.download_tasks[task_id] = self.multi_thread_tasks[task_id]  # 也添加到主列表
        
        # 立即显示下载项
        self.show_download_widget(task_id, file_name, save_path)
        
        # 启动多线程下载
        task.start_multi_thread()
        
        logger.info(f"multi-thread download started - {file_name} ({num_threads} threads)")
        
        return task

    def add_download(self, download_item, file_name, save_path):
        task = DownloadTask(download_item=download_item, file_name=file_name, save_path=save_path)
        task_id = id(download_item)
        self.download_tasks[task_id] = {'task': task, 'widget': None, 'visible': False}

        def update_progress_callback():
            received = download_item.receivedBytes()
            total = download_item.totalBytes()
            self.update_download(task_id, received, total)
            if received > 0 and not self.download_tasks[task_id]['visible']:
                self.show_download_widget(task_id, file_name, save_path)

        def update_total_callback(total):
            received = download_item.receivedBytes()
            self.update_download(task_id, received, total)

        def state_changed_callback(state):
            self.on_state_changed(task_id, state)
            if state == QWebEngineDownloadRequest.DownloadState.DownloadInProgress:
                if not self.download_tasks[task_id]['visible'] and self.download_tasks[task_id]['task'].received_bytes == 0:
                    self.show_download_widget(task_id, file_name, save_path)

        download_item.receivedBytesChanged.connect(update_progress_callback)
        download_item.totalBytesChanged.connect(update_total_callback)
        download_item.stateChanged.connect(state_changed_callback)

        return task

    def show_download_widget(self, task_id, file_name, save_path):
        task = self.download_tasks[task_id]['task']
        item_widget = DownloadItemWidget(task)
        self.download_tasks[task_id]['widget'] = item_widget
        self.download_tasks[task_id]['visible'] = True

        self.download_list_layout.insertWidget(
            self.download_list_layout.count() - 1,
            item_widget
        )

    def update_download(self, task_id, received, total):
        if task_id in self.download_tasks:
            task = self.download_tasks[task_id]['task']
            task.update_progress(received, total)
            if self.download_tasks[task_id]['widget']:
                self.download_tasks[task_id]['widget'].update_display()

    def on_state_changed(self, task_id, state):
        if task_id in self.download_tasks:
            task = self.download_tasks[task_id]['task']
            if state == QWebEngineDownloadRequest.DownloadState.DownloadCompleted:
                task.state = "completed"
            elif state == QWebEngineDownloadRequest.DownloadState.DownloadCancelled:
                task.state = "failed"
            if self.download_tasks[task_id]['widget']:
                self.download_tasks[task_id]['widget'].update_display()

    def clear_completed(self):
        to_remove = []
        for task_id, task_data in self.download_tasks.items():
            if task_data['task'].state == "completed" and not task_data['visible']:
                to_remove.append(task_id)

        for task_id in to_remove:
            del self.download_tasks[task_id]

        if to_remove:
            return

        to_remove = []
        for task_id, task_data in self.download_tasks.items():
            if task_data['task'].state == "completed" and task_data['visible']:
                to_remove.append(task_id)

        for task_id in to_remove:
            if self.download_tasks[task_id]['widget']:
                widget = self.download_tasks[task_id]['widget']
                self.download_list_layout.removeWidget(widget)
                widget.deleteLater()
            del self.download_tasks[task_id]


# ------------------- 修复跳转的最小代码 -------------------
class FixJumpPage(QWebEnginePage):
    def __init__(self, parent):
        super().__init__(parent)
        self.main = parent.parent()
        self.setup_settings()
        # 防止重复注入脚本（每个 profile 只注入一次）
        if not hasattr(FixJumpPage, '_scripts_injected') or not FixJumpPage._scripts_injected:
            self.setup_scripts()
            FixJumpPage._scripts_injected = True
    
    def contextMenuEvent(self, event):
        # 创建带有开发者工具选项的右键菜单
        menu = QMenu(self.view())
        inspect_action = menu.addAction("检查元素 (Ctrl+Shift+I)")
        menu.addSeparator()
        back_action = menu.addAction(self.tr("back"))
        forward_action = menu.addAction(self.tr("forward"))
        reload_action = menu.addAction("刷新")
        menu.addSeparator()
        view_source_action = menu.addAction("查看页面源代码")
        
        action = menu.exec(event.globalPos())
        if action == inspect_action:
            self.triggerPageAction(QWebEnginePage.WebAction.InspectElement)
        elif action == back_action:
            self.triggerPageAction(QWebEnginePage.WebAction.Back)
        elif action == forward_action:
            self.triggerPageAction(QWebEnginePage.WebAction.Forward)
        elif action == reload_action:
            self.triggerPageAction(QWebEnginePage.WebAction.Reload)
        elif action == view_source_action:
            self.triggerPageAction(QWebEnginePage.WebAction.ViewSource)
    
    def setup_settings(self):
        settings = self.settings()
        settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.PluginsEnabled, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.FullScreenSupportEnabled, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalStorageEnabled, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanAccessClipboard, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.WebGLEnabled, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.Accelerated2dCanvasEnabled, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.AutoLoadImages, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanPaste, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.DnsPrefetchEnabled, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.ErrorPageEnabled, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.HyperlinkAuditingEnabled, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.ScrollAnimatorEnabled, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.ScreenCaptureEnabled, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.ShowScrollBars, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.SpatialNavigationEnabled, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.TouchIconsEnabled, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.WebRTCPublicInterfacesOnly, False)
    
    def setup_scripts(self):
        # navigator.userAgent 已通过 setHttpUserAgent() 在 C++ 层面设置，
        # 不要在 JS 中重复定义，否则可能导致冲突
        js_code = """
            Object.defineProperty(navigator, 'platform', {
                get: function() { return 'Win32'; }
            });
            Object.defineProperty(navigator, 'vendor', {
                get: function() { return 'Google Inc.'; }
            });
            window.chrome = {
                runtime: {
                    sendMessage: function() {},
                    connect: function() { return { onMessage: { addListener: function() {} }, onDisconnect: { addListener: function() {} }, postMessage: function() {}, disconnect: function() {} }; },
                    getManifest: function() { return {}; },
                    getURL: function() { return ''; },
                    onMessage: { addListener: function() {} },
                    onConnect: { addListener: function() {} },
                    lastError: undefined
                },
                webstore: {},
                tabs: {
                    query: function() { return Promise.resolve([]); },
                    create: function() { return Promise.resolve({}); }
                },
                storage: {
                    local: {
                        get: function() { return Promise.resolve({}); },
                        set: function() { return Promise.resolve(); }
                    },
                    sync: {
                        get: function() { return Promise.resolve({}); },
                        set: function() { return Promise.resolve(); }
                    }
                }
            };
            try {
                (function() {
                    var _nativeUAD = null;
                    try { _nativeUAD = navigator.userAgentData; } catch(e) {}
                    
                    var _fakeUAD = {
                        brands: [
                            { brand: 'Google Chrome', version: '120' },
                            { brand: 'Not;A;Brand', version: '8' },
                            { brand: 'Chromium', version: '120' }
                        ],
                        platform: 'Windows',
                        mobile: false,
                        getHighEntropyValues: function(hints) {
                            return Promise.resolve({
                                platform: 'Windows',
                                platformVersion: '10.0.0',
                                architecture: 'x86',
                                model: '',
                                uaFullVersion: '120.0.6099.216',
                                bitness: '64'
                            });
                        }
                    };
                    
                    // 如果原生存在，用我们的版本替换；如果不存在，创建模拟对象
                    if (_nativeUAD) {
                        // 总是用我们的版本替换 getHighEntropyValues，避免原生实现的 Illegal invocation
                        _nativeUAD.getHighEntropyValues = _fakeUAD.getHighEntropyValues;
                        if (!_nativeUAD.brands) _nativeUAD.brands = _fakeUAD.brands;
                        if (!_nativeUAD.platform) _nativeUAD.platform = _fakeUAD.platform;
                    } else {
                        Object.defineProperty(navigator, 'userAgentData', {
                            get: function() { return _fakeUAD; },
                            configurable: true
                        });
                    }
                })();
            } catch(e) {}
        """
        script = QWebEngineScript()
        script.setSourceCode(js_code)
        script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
        script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
        script.setRunsOnSubFrames(True)
        self.profile().scripts().insert(script)
        
        error_js_code = f"""
            (function() {{
                function checkForErrorPage() {{
                    var body = document.body;
                    if (body && (body.innerHTML.indexOf('err_no_internet') > -1 || 
                                 body.innerHTML.indexOf('net::ERR_') > -1 ||
                                 body.innerHTML.indexOf('ERR_INTERNET_DISCONNECTED') > -1 ||
                                 body.innerHTML.indexOf('T-Rex') > -1 ||
                                 body.innerHTML.indexOf('chrome-error://') > -1)) {{
                        
                        // 获取实际访问的URL
                        var currentUrl = window.location.href;
                        var actualUrl = currentUrl;
                        
                        // 尝试从页面中提取真实的URL
                        var text = body.innerText || body.textContent;
                        var urlMatch = text.match(/https?:\\/\\/[^\\s]+/);
                        if (urlMatch) {{
                            actualUrl = urlMatch[0];
                        }}
                        
                        // 尝试从浏览器历史记录获取
                        if (actualUrl.startsWith('chrome-error://')) {{
                            try {{
                                if (window.history && window.history.length > 0) {{
                                    var entries = performance.getEntriesByType('navigation');
                                    if (entries.length > 0) {{
                                        actualUrl = entries[0].name || actualUrl;
                                    }}
                                }}
                            }} catch(e) {{}}
                        }}
                        
                        var errorMessage = '{tr("network_unreachable")}';
                        
                        // 尝试从页面中提取更多错误信息
                        var errMatch = text.match(/net::ERR_[A-Z_]+/);
                        if (errMatch) {{
                            errorMessage = errMatch[0];
                        }}
                        
                        document.body.innerHTML = '';
                        var style = document.createElement('style');
                        style.innerHTML = `
                            * {{ margin: 0; padding: 0; box-sizing: border-box; }}
                            body {{ 
                                font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; 
                                background: white;
                                min-height: 100vh;
                            }}
                            .container {{ 
                                position: absolute;
                                top: 5%;
                                left: 8%;
                                width: 50%;
                                text-align: left;
                                padding: 0;
                            }}
                            .icon {{ 
                                font-size: 80px; 
                                margin-bottom: 40px;
                                display: block;
                            }}
                            .url-text {{
                                font-size: 14px;
                                color: #666;
                                margin-bottom: 5px;
                                text-align: left;
                            }}
                            .error-text {{
                                font-size: 14px;
                                color: #666;
                                margin-bottom: 40px;
                                text-align: left;
                            }}
                            .suggestions {{
                                text-align: left;
                                color: #333;
                                margin-bottom: 30px;
                                font-size: 15px;
                            }}
                            .suggestions ul {{
                                margin-top: 10px;
                                margin-left: 25px;
                            }}
                            .suggestions li {{
                                margin-bottom: 8px;
                            }}
                            .btn {{ 
                                background: #1a73e8; 
                                color: white; 
                                border: none; 
                                padding: 12px 30px; 
                                font-size: 16px; 
                                border-radius: 4px; 
                                cursor: pointer; 
                                font-weight: 500;
                                display: inline-block;
                                text-align: center;
                                width: auto;
                                margin: 0;
                                float: left;
                                clear: both;
                            }}
                            .btn:hover {{ 
                                background: #1765cc;
                            }}
                        `;
                        document.head.appendChild(style);
                        
                        var container = document.createElement('div');
                        container.className = 'container';
                        container.innerHTML = `
                            <div class="icon">🌐</div>
                            <div class="url-text">` + actualUrl + `</div>
                            <div class="error-text">` + errorMessage + `</div>
                            <div class="suggestions">
                                <div>{tr('suggestions_title')}</div>
                                <ul>
                                    <li>{tr('suggestion_1')}</li>
                                    <li>{tr('suggestion_2')}</li>
                                    <li>{tr('suggestion_3')}</li>
                                    <li>{tr('suggestion_4')}</li>
                                </ul>
                            </div>
                            <button class="btn" onclick="location.reload()">{tr('retry')}</button>
                        `;
                        document.body.appendChild(container);
                        return true;
                    }}
                    return false;
                }}
                
                if (!checkForErrorPage()) {{
                    var observer = new MutationObserver(function() {{
                        checkForErrorPage();
                    }});
                    observer.observe(document.body, {{ childList: true, subtree: true }});
                    
                    setTimeout(function() {{
                        checkForErrorPage();
                        observer.disconnect();
                    }}, 100);
                }}
            }})();
        """
        error_script = QWebEngineScript()
        error_script.setSourceCode(error_js_code)
        error_script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentReady)
        error_script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
        error_script.setRunsOnSubFrames(False)
        self.profile().scripts().insert(error_script)
        
        # 添加JavaScript错误监听脚本 - 监听未捕获的错误
        js_listener_code = """
            (function() {
                // 监听未捕获的JavaScript错误
                window.addEventListener('error', function(event) {
                    var message = event.message;
                    if (event.lineno) {
                        message += ' (line ' + event.lineno;
                        if (event.colno) {
                            message += ', col ' + event.colno;
                        }
                        message += ')';
                    }
                    if (event.filename) {
                        message = event.filename + ': ' + message;
                    }
                    // 不调用console，直接抛出错误让PyQt捕获
                    console.error('[JS_ERROR] ' + message);
                });
                
                // 监听未处理的Promise拒绝
                window.addEventListener('unhandledrejection', function(event) {
                    var reason = event.reason;
                    var message = reason ? String(reason) : 'Unknown error';
                    console.error('[JS_ERROR] Unhandled Promise Rejection: ' + message);
                });
            })();
        """
        listener_script = QWebEngineScript()
        listener_script.setSourceCode(js_listener_code)
        listener_script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
        listener_script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
        listener_script.setRunsOnSubFrames(True)
        self.profile().scripts().insert(listener_script)
        
        # LSS HTML5 播放器 Polyfill + 通用视频平台兼容性（内联注入）
        player_js_code = """
(function() {
    'use strict';
    console.log('[LSS] HTML5 Player Polyfill loading...');
    
    // ============================================================
    // 0. 全局配置
    // ============================================================
    var _lssMediaConfig = {
        enableMSE: true,
        enableEME: true,
        enableAudio: true,
        enableVideo: true,
        preferNative: false
    };
    
    // ============================================================
    // 1. HTML5 Video/Audio API 补全
    // ============================================================
    try {
        var _origPlay = HTMLMediaElement.prototype.play;
        if (_origPlay) {
            HTMLMediaElement.prototype.play = function() {
                try { 
                    var p = _origPlay.call(this); 
                    return (p && p.then) ? p : Promise.resolve(); 
                } catch(e) { 
                    return Promise.reject(e); 
                }
            };
        }
    } catch(e) { console.log('[LSS] Play polyfill error:', e); }
    
    // ============================================================
    // 2. canPlayType 补丁（仅用于浏览器兼容性检测，不修改 MSE）
    // ============================================================
    // 策略：canPlayType 返回 'probably' 是为了通过网站的浏览器检测
    // 实际的编解码由 Chromium 原生处理，不需要 polyfill
    try {
        var _origMediaCanPlay = HTMLMediaElement.prototype.canPlayType;
        HTMLMediaElement.prototype.canPlayType = function(type) {
            var r = '';
            try { r = _origMediaCanPlay.call(this, type); } catch(e) {}
            if (r && r !== '') return r;
            var t = type.toLowerCase();
            if (/video\\//i.test(t) || /audio\\//i.test(t)) return 'probably';
            if (/h264|avc|vp[89]|av1|mp4a|aac|opus|vorbis|flac|mp3|wav/.test(t)) return 'probably';
            return r || 'probably';
        };
    } catch(e) { console.log('[LSS] canPlayType error:', e); }
    
    try {
        var _origVideoCanPlay = HTMLVideoElement.prototype.canPlayType;
        HTMLVideoElement.prototype.canPlayType = function(type) {
            var r = '';
            try { r = _origVideoCanPlay.call(this, type); } catch(e) {}
            if (r && r !== '') return r;
            var t = type.toLowerCase();
            if (/video\\//i.test(t) || /h264|avc|vp[89]|av1|mp4|webm/.test(t)) return 'probably';
            return r || 'probably';
        };
    } catch(e) {}
    
    try {
        var _origAudioCanPlay = HTMLAudioElement.prototype.canPlayType;
        HTMLAudioElement.prototype.canPlayType = function(type) {
            var r = '';
            try { r = _origAudioCanPlay.call(this, type); } catch(e) {}
            if (r && r !== '') return r;
            var t = type.toLowerCase();
            if (/audio\\//i.test(t) || /mp3|wav|ogg|opus|aac|flac/.test(t)) return 'probably';
            return r || 'probably';
        };
    } catch(e) {}
    
    // ============================================================
    // 3. MediaSource.isTypeSupported - 对所有视频/音频格式返回 true
    // ============================================================
    // 策略：PyQt6 WebEngine 的 Chromium 对部分格式的 MSE 支持可能受限
    // 返回 true 让播放器尝试创建 SourceBuffer，如果原生不支持会静默失败
    // 这比返回 false 导致播放器直接拒绝播放要好
    try {
        if (window.MediaSource) {
            var _origMSITS = MediaSource.isTypeSupported.bind(MediaSource);
            MediaSource.isTypeSupported = function(type) {
                var nativeResult = false;
                try { nativeResult = _origMSITS(type); } catch(e) {}
                if (nativeResult) return true;
                var t = type.toLowerCase();
                // 对所有视频/音频容器格式返回 true
                if (/video\\//.test(t) || /audio\\//.test(t)) return true;
                if (/application\\/x-mpegurl/.test(t)) return true;
                if (/application\\/vnd.apple.mpegurl/.test(t)) return true;
                if (/video\\/x-flv/.test(t)) return true;
                // 常见编码
                if (/avc1|h264|hev1|hvc1|hevc|h265|vp[89]|av1/.test(t)) return true;
                if (/mp4a|aac|opus|vorbis|flac|mp3|ec-3|ac-3|eac3/.test(t)) return true;
                return true;
            };
        }
    } catch(e) { console.log('[LSS] MediaSource polyfill error:', e); }
    
    // ============================================================
    // 4. SourceBuffer 原生保护 - 不覆盖原生实现
    // ============================================================
    // 注意：PyQt6 WebEngine 的 Chromium 原生支持 SourceBuffer
    // 覆盖原生实现会导致视频播放失败，所以这里不做任何修改
    
    // ============================================================
    // 5. MediaCapabilities 保守补全（与 MediaSource 策略一致）
    // ============================================================
    try {
        if (navigator.mediaCapabilities) {
            var _origDC = navigator.mediaCapabilities.decodingInfo.bind(navigator.mediaCapabilities);
            navigator.mediaCapabilities.decodingInfo = function(config) {
                try {
                    return _origDC(config).then(function(info) {
                        if (!info.supported) {
                            var ct = '';
                            if (config.video) ct = config.video.contentType || '';
                            else if (config.audio) ct = config.audio.contentType || '';
                            ct = ct.toLowerCase();
                            // H.265/HEVC 明确不支持
                            if (/hev1|hvc1|hevc|h265/.test(ct))
                                return { supported: false, smooth: false, powerEfficient: false };
                            // H.264/VP9/AV1/AAC 明确支持
                            if (/avc1|h264|vp[89]|av1|mp4a|aac|opus/.test(ct))
                                return { supported: true, smooth: true, powerEfficient: true, keySystemAccess: {} };
                            // 其他视频格式
                            if (/video\\//.test(ct))
                                return { supported: true, smooth: true, powerEfficient: true };
                            // 其他音频格式
                            if (/audio\\//.test(ct))
                                return { supported: true, smooth: true, powerEfficient: true };
                        }
                        return info;
                    });
                } catch(e) {
                    return Promise.resolve({supported:false,smooth:false,powerEfficient:false,keySystemAccess:{}});
                }
            };
        }
        
        // 添加缺失的 mediaCapabilities
        if (!navigator.mediaCapabilities) {
            navigator.mediaCapabilities = {
                decodingInfo: function(config) {
                    var ct = '';
                    if (config.video) ct = config.video.contentType || '';
                    else if (config.audio) ct = config.audio.contentType || '';
                    ct = ct.toLowerCase();
                    if (/hev1|hvc1|hevc|h265/.test(ct))
                        return Promise.resolve({supported:false,smooth:false,powerEfficient:false});
                    return Promise.resolve({
                        supported: true,
                        smooth: true,
                        powerEfficient: true,
                        keySystemAccess: {}
                    });
                },
                encodingInfo: function(config) {
                    return Promise.resolve({
                        supported: true,
                        smooth: true,
                        powerEfficient: true
                    });
                }
            };
        }
    } catch(e) { console.log('[LSS] MediaCapabilities error:', e); }
    
    // ============================================================
    // 6. EME (Encrypted Media Extensions) 补全
    // ============================================================
    try {
        if (!window.MediaKeys) {
            window.MediaKeys = function() {};
            MediaKeys.prototype.createSession = function() {
                return {
                    sessionId: '',
                    expiration: NaN,
                    closed: Promise.resolve(),
                    keyStatuses: new Map(),
                    generateRequest: function() { return Promise.resolve(); },
                    load: function() { return Promise.resolve(false); },
                    update: function() { return Promise.resolve(); },
                    close: function() { return Promise.resolve(); },
                    remove: function() { return Promise.resolve(); }
                };
            };
            MediaKeys.isTypeSupported = function(type) { return true; };
        }
        
        if (!navigator.requestMediaKeySystemAccess) {
            navigator.requestMediaKeySystemAccess = function(keySystem, configs) {
                return Promise.resolve({
                    keySystem: keySystem || '',
                    getConfiguration: function() { return configs && configs[0] || {}; },
                    createMediaKeys: function() {
                        return Promise.resolve(new MediaKeys());
                    }
                });
            };
        } else {
            var _origRKSA = navigator.requestMediaKeySystemAccess.bind(navigator);
            navigator.requestMediaKeySystemAccess = function(keySystem, configs) {
                return _origRKSA(keySystem, configs).catch(function() {
                    return Promise.resolve({
                        keySystem: keySystem || '',
                        getConfiguration: function() { return configs && configs[0] || {}; },
                        createMediaKeys: function() {
                            return Promise.resolve(new MediaKeys());
                        }
                    });
                });
            };
        }
        
        // 补全 MediaKeySession
        if (!window.MediaKeySession) {
            window.MediaKeySession = function() {};
            MediaKeySession.prototype = {
                sessionId: '',
                expiration: NaN,
                closed: Promise.resolve(),
                keyStatuses: new Map(),
                generateRequest: function() { return Promise.resolve(); },
                load: function() { return Promise.resolve(false); },
                update: function() { return Promise.resolve(); },
                close: function() { return Promise.resolve(); },
                remove: function() { return Promise.resolve(); }
            };
        }
        
        // 补全 MediaKeyStatusMap
        if (!window.MediaKeyStatusMap) {
            window.MediaKeyStatusMap = function() {
                this._map = new Map();
            };
            MediaKeyStatusMap.prototype = {
                size: 0,
                has: function() { return true; },
                get: function() { return 'usable'; },
                forEach: function() {},
                entries: function() { return []; },
                keys: function() { return []; },
                values: function() { return []; }
            };
        }
    } catch(e) { console.log('[LSS] EME polyfill error:', e); }
    
    // ============================================================
    // 7. 画中画 API 补全
    // ============================================================
    try {
        if (HTMLVideoElement.prototype && !HTMLVideoElement.prototype.requestPictureInPicture) {
            HTMLVideoElement.prototype.requestPictureInPicture = function() {
                this._pip = true;
                this.dispatchEvent(new Event('enterpictureinpicture'));
                return Promise.resolve(this);
            };
        }
        if (document && !document.pictureInPictureEnabled) {
            Object.defineProperty(document, 'pictureInPictureEnabled', {get:function(){return true;}});
        }
        if (document && !document.exitPictureInPicture) {
            document.exitPictureInPicture = function() {
                document.querySelectorAll('video').forEach(function(v){ 
                    if(v._pip){ v._pip=false; v.dispatchEvent(new Event('leavepictureinpicture')); }
                });
                return Promise.resolve();
            };
        }
    } catch(e) {}
    
    // ============================================================
    // 8. 全屏 API 补全
    // ============================================================
    try {
        if (Element.prototype && !Element.prototype.requestFullscreen) {
            Element.prototype.requestFullscreen = function() {
                this.dispatchEvent(new Event('fullscreenchange'));
                return Promise.resolve();
            };
        }
        if (document && !document.fullscreenEnabled) {
            Object.defineProperty(document, 'fullscreenEnabled', {get:function(){return true;}});
        }
        if (document && !document.exitFullscreen) {
            document.exitFullscreen = function() {
                var el = document.fullscreenElement;
                if (el) el.dispatchEvent(new Event('fullscreenchange'));
                return Promise.resolve();
            };
        }
        if (document && !document.fullscreenElement) {
            Object.defineProperty(document, 'fullscreenElement', {get:function(){return null;}});
        }
    } catch(e) {}
    
    // ============================================================
    // 9. 全局音频激活
    // ============================================================
    var _lssAudioActivated = false;
    
    function _lssActivateAudioContext() {
        try {
            var ctx = new (window.AudioContext || window.webkitAudioContext)();
            if (ctx && ctx.state === 'suspended') {
                ctx.resume().then(function() {
                    console.log('[LSS Audio] AudioContext resumed');
                }).catch(function() {});
            }
        } catch(e) {}
        
        try {
            document.querySelectorAll('video').forEach(function(v) {
                if (v.audioTracks && v.audioTracks.length > 0) {
                    v.audioTracks.forEach(function(track) {
                        if (track.enabled === false) {
                            track.enabled = true;
                            console.log('[LSS Audio] Enabled audio track');
                        }
                    });
                }
            });
        } catch(e) {}
    }
    
    function _lssUnmuteMediaElement(el) {
        try {
            if (el.muted === true) el.muted = false;
            if (el.volume === 0) el.volume = 1;
            el.preload = 'auto';
        } catch(e) {}
    }
    
    function _lssUnmuteAll(root) {
        try {
            (root || document).querySelectorAll('video,audio').forEach(_lssUnmuteMediaElement);
        } catch(e) {}
    }
    
    function _lssObserveNewMedia(root) {
        try {
            new MutationObserver(function(muts) {
                muts.forEach(function(m) {
                    m.addedNodes.forEach(function(n) {
                        if (n.tagName === 'VIDEO' || n.tagName === 'AUDIO') {
                            _lssUnmuteMediaElement(n);
                        }
                        if (n.querySelectorAll) {
                            n.querySelectorAll('video,audio').forEach(_lssUnmuteMediaElement);
                        }
                    });
                });
            }).observe(root || document.body, { childList: true, subtree: true });
        } catch(e) {}
    }
    
    function _lssHandleUserInteraction() {
        _lssActivateAudioContext();
        _lssUnmuteAll();
        if (!_lssAudioActivated) {
            _lssAudioActivated = true;
            console.log('[LSS Audio] Audio activated by user interaction');
        }
    }
    
    document.addEventListener('click', _lssHandleUserInteraction, true);
    document.addEventListener('touchstart', _lssHandleUserInteraction, true);
    document.addEventListener('keydown', _lssHandleUserInteraction, true);
    document.addEventListener('mousedown', _lssHandleUserInteraction, true);
    document.addEventListener('play', _lssHandleUserInteraction, true);
    
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', function() {
            _lssUnmuteAll();
            _lssObserveNewMedia();
        });
    } else {
        _lssUnmuteAll();
        _lssObserveNewMedia();
    }
    
    // ============================================================
    // 9.5. 通用 API 增强和错误修复
    // ============================================================
    try {
        // 修复 JSON.parse 对 "undefined" 字符串的处理
        var _origJSONParse = JSON.parse.bind(JSON);
        JSON.parse = function(text, reviver) {
            if (text === undefined || text === 'undefined') {
                return undefined;
            }
            try {
                return _origJSONParse(text, reviver);
            } catch(e) {
                // 如果解析失败且文本看起来像 undefined，返回 undefined
                if (String(text).toLowerCase() === 'undefined') {
                    return undefined;
                }
                throw e;
            }
        };
    } catch(e) {}
    
    // 修复 requestAnimationFrame 兼容性
    try {
        if (!window.requestAnimationFrame) {
            window.requestAnimationFrame = function(callback) {
                return setTimeout(callback, 1000 / 60);
            };
        }
        if (!window.cancelAnimationFrame) {
            window.cancelAnimationFrame = function(id) {
                clearTimeout(id);
            };
        }
    } catch(e) {}
    
    // 修复 IntersectionObserver 兼容性
    try {
        if (!window.IntersectionObserver) {
            window.IntersectionObserver = function(callback, options) {
                this.observe = function() {};
                this.unobserve = function() {};
                this.disconnect = function() {};
            };
        }
    } catch(e) {}
    
    // 修复 ResizeObserver 兼容性
    try {
        if (!window.ResizeObserver) {
            window.ResizeObserver = function(callback) {
                this.observe = function() {};
                this.unobserve = function() {};
                this.disconnect = function() {};
            };
        }
    } catch(e) {}
    
    // 修复 Web Animations API
    try {
        if (!Element.prototype.animate) {
            Element.prototype.animate = function() {
                return {
                    play: function() {},
                    pause: function() {},
                    cancel: function() {},
                    finish: function() {},
                    reverse: function() {},
                    onfinish: null,
                    oncancel: null,
                    currentTime: 0,
                    playbackRate: 1,
                    playState: 'finished'
                };
            };
        }
    } catch(e) {}
    
    // ============================================================
    // 10. 通用视频平台检测欺骗
    // ============================================================
    try {
        // 检测并绕过 video element 检测
        var _origVideo = document.createElement.bind(document);
        document.createElement = function(tagName, options) {
            var el = _origVideo(tagName, options);
            if (tagName.toLowerCase() === 'video') {
                // 标记为已处理
                el._lssPatched = true;
                // 拦截 src setter
                var _origSrcSetter = Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype, 'src')?.set;
                if (_origSrcSetter) {
                    Object.defineProperty(el, 'src', {
                        set: function(val) {
                            console.log('[LSS] Video src set:', val ? val.substring(0, 80) : 'none');
                            _origSrcSetter.call(this, val);
                        },
                        get: function() { return this._src || ''; }
                    });
                }
            }
            return el;
        };
    } catch(e) {}
    
    // ============================================================
    // 11. 特定视频平台兼容性处理
    // ============================================================
    var _host = location.hostname || '';
    
    // Bilibili - 音频修复
    if (_host.indexOf('bilibili.com') !== -1 || _host.indexOf('bilivideo.com') !== -1) {
        console.log('[LSS] Bilibili detected, applying patches');
        try {
            // 诊断：检查原生 MSE 编解码支持
            if (window.MediaSource && window.MediaSource.isTypeSupported) {
                var _diagFormats = [
                    'video/mp4; codecs="avc1.42E01E"',
                    'video/mp4; codecs="avc1.640028"',
                    'video/mp4; codecs="hev1.1.6.L93.90"',
                    'audio/mp4; codecs="mp4a.40.2"',
                    'audio/mp4; codecs="mp4a.40.5"',
                    'audio/webm; codecs="opus"',
                    'audio/mp4; codecs="ec-3"'
                ];
                _diagFormats.forEach(function(f) {
                    console.log('[LSS Diag] MSE native supports: ' + f + ' = ' + MediaSource.isTypeSupported(f));
                });
            }
            
            // 1. 拦截 play() 确保解除静音
            var _origBLPlay = HTMLMediaElement.prototype.play;
            HTMLMediaElement.prototype.play = function() {
                try {
                    if (this.muted) this.muted = false;
                    if (this.volume === 0) this.volume = 1;
                } catch(e) {}
                return _origBLPlay.apply(this, arguments);
            };
            
            // 2. 定期检查所有 media 元素，强制恢复音频
            setInterval(function() {
                try {
                    document.querySelectorAll('video,audio').forEach(function(v) {
                        if (!v.paused) {
                            if (v.muted) { v.muted = false; console.log('[LSS Audio] Auto-unmuted'); }
                            if (v.volume === 0) { v.volume = 1; console.log('[LSS Audio] Auto-volume'); }
                        }
                        // 确保 preload 正确
                        if (v.preload === 'none') v.preload = 'auto';
                    });
                } catch(e) {}
            }, 1000);
            
            // 3. 用户交互时激活 AudioContext
            var _blAudioCtx = null;
            document.addEventListener('click', function() {
                try {
                    if (!_blAudioCtx) {
                        _blAudioCtx = new (window.AudioContext || window.webkitAudioContext)();
                    }
                    if (_blAudioCtx && _blAudioCtx.state === 'suspended') {
                        _blAudioCtx.resume().then(function() {
                            console.log('[LSS Audio] Bilibili AudioContext resumed');
                        }).catch(function() {});
                    }
                } catch(e) {}
            }, true);
        } catch(e) {}
    }
    
    // YouTube
    if (_host.indexOf('youtube.com') !== -1 || _host.indexOf('googlevideo.com') !== -1) {
        console.log('[LSS] YouTube detected, applying patches');
        // YouTube 使用原生 Chromium 编解码，不需要修改 MSE
    }
    
    // 腾讯视频
    if (_host.indexOf('qq.com') !== -1) {
        console.log('[LSS] Tencent Video detected, applying patches');
        try {
            // 1. 模拟腾讯视频客户端检测成功
            window.__tencentVideoClientReady = true;
            window.txvpClient = {
                isReady: true,
                version: '1.0.0',
                checkAlive: function() { return Promise.resolve(true); },
                openVideo: function() { return Promise.resolve(); }
            };
            
            // 2. 拦截本地端口检测 WebSocket
            if (window.WebSocket) {
                var _origWebSocket = window.WebSocket;
                window.WebSocket = function(url, protocols) {
                    if (url && url.indexOf && (url.indexOf('127.0.0.1') !== -1 || url.indexOf('localhost') !== -1 || url.indexOf('0.0.0.0') !== -1)) {
                        console.log('[LSS] Intercepted localhost WebSocket:', url);
                        var mockWS = {
                            url: url, readyState: 1,
                            send: function() {}, close: function() {},
                            addEventListener: function(event, handler) {
                                if (event === 'open') setTimeout(handler, 10);
                            },
                            removeEventListener: function() {},
                            onopen: null, onclose: null, onmessage: null, onerror: null
                        };
                        setTimeout(function() { if (mockWS.onopen) mockWS.onopen(); }, 10);
                        return mockWS;
                    }
                    return new _origWebSocket(url, protocols);
                };
                window.WebSocket.prototype = _origWebSocket.prototype;
                window.WebSocket.CONNECTING = _origWebSocket.CONNECTING;
                window.WebSocket.OPEN = _origWebSocket.OPEN;
                window.WebSocket.CLOSING = _origWebSocket.CLOSING;
                window.WebSocket.CLOSED = _origWebSocket.CLOSED;
            }
            
            // 3. 拦截本地端口检测的 fetch 请求
            if (window.fetch) {
                var _origFetch = window.fetch.bind(window);
                window.fetch = function(url, options) {
                    var urlStr = typeof url === 'string' ? url : (url && url.href ? url.href : '');
                    if (urlStr && (urlStr.indexOf('127.0.0.1') !== -1 || urlStr.indexOf('localhost') !== -1 || urlStr.indexOf('0.0.0.0') !== -1)) {
                        console.log('[LSS] Intercepted localhost fetch:', urlStr);
                        return Promise.resolve(new Response('OK', { status: 200, statusText: 'OK' }));
                    }
                    return _origFetch(url, options);
                };
            }
            
            // 4. 拦截本地端口检测的 XMLHttpRequest
            if (window.XMLHttpRequest) {
                var _TXOrigXHR = XMLHttpRequest;
                window.XMLHttpRequest = function() {
                    var _realXHR = new _TXOrigXHR();
                    var _lssIntercepted = false;
                    var _lssUrl = '';
                    var _lssReadyState = 0;
                    var _lssStatus = 0;
                    var _lssResponseText = '';
                    var _lssResponseHeaders = 'Content-Type: application/json\\r\\n';
                    var self = this;
                    
                    self.open = function(method, url, async, user, password) {
                        _lssUrl = url || '';
                        if (_lssUrl.indexOf && (_lssUrl.indexOf('127.0.0.1') !== -1 || _lssUrl.indexOf('localhost') !== -1 || _lssUrl.indexOf('0.0.0.0') !== -1)) {
                            _lssIntercepted = true;
                            console.log('[LSS] Intercepted localhost XHR:', _lssUrl);
                        } else {
                            _lssIntercepted = false;
                            try { _realXHR.open(method, url, async, user, password); } catch(e) {}
                        }
                    };
                    
                    self.send = function(data) {
                        if (_lssIntercepted) {
                            setTimeout(function() {
                                _lssReadyState = 4; _lssStatus = 200;
                                _lssResponseText = '{"result":0,"msg":"ok","data":{"alive":true,"version":"1.0.0"}}';
                                if (self.onreadystatechange) {
                                    try { self.onreadystatechange.call(self); } catch(e) { console.log('[LSS] onreadystatechange error:', e); }
                                }
                                if (self.onload) {
                                    try { self.onload.call(self); } catch(e) { console.log('[LSS] onload error:', e); }
                                }
                            }, 10);
                            return;
                        }
                        try { _realXHR.send(data); } catch(e) {}
                    };
                    
                    self.abort = function() { if (!_lssIntercepted) try { _realXHR.abort(); } catch(e) {} };
                    self.setRequestHeader = function(h, v) { if (!_lssIntercepted) try { _realXHR.setRequestHeader(h, v); } catch(e) {} };
                    self.getResponseHeader = function(h) {
                        if (_lssIntercepted) return (h && h.toLowerCase() === 'content-type') ? 'application/json' : null;
                        try { return _realXHR.getResponseHeader(h); } catch(e) { return null; }
                    };
                    self.getAllResponseHeaders = function() {
                        if (_lssIntercepted) return _lssResponseHeaders;
                        try { return _realXHR.getAllResponseHeaders(); } catch(e) { return ''; }
                    };
                    self.addEventListener = function() { if (!_lssIntercepted) try { _realXHR.addEventListener.apply(_realXHR, arguments); } catch(e) {} };
                    self.removeEventListener = function() { if (!_lssIntercepted) try { _realXHR.removeEventListener.apply(_realXHR, arguments); } catch(e) {} };
                    self.dispatchEvent = function() { if (!_lssIntercepted) try { return _realXHR.dispatchEvent.apply(_realXHR, arguments); } catch(e) { return true; } return true; };
                    
                    ['onreadystatechange', 'onload', 'onerror', 'onprogress', 'onabort', 'ontimeout', 'onloadstart', 'onloadend'].forEach(function(evt) {
                        Object.defineProperty(self, evt, {
                            get: function() { return _realXHR[evt]; },
                            set: function(val) { _realXHR[evt] = val; },
                            configurable: true, enumerable: true
                        });
                    });
                    
                    Object.defineProperty(self, 'readyState', {
                        get: function() { return _lssIntercepted ? _lssReadyState : _realXHR.readyState; },
                        configurable: true, enumerable: true
                    });
                    Object.defineProperty(self, 'status', {
                        get: function() { return _lssIntercepted ? _lssStatus : _realXHR.status; },
                        configurable: true, enumerable: true
                    });
                    Object.defineProperty(self, 'statusText', {
                        get: function() { return _lssIntercepted ? 'OK' : _realXHR.statusText; },
                        configurable: true, enumerable: true
                    });
                    Object.defineProperty(self, 'responseText', {
                        get: function() { return _lssIntercepted ? _lssResponseText : _realXHR.responseText; },
                        configurable: true, enumerable: true
                    });
                    Object.defineProperty(self, 'response', {
                        get: function() { return _lssIntercepted ? _lssResponseText : _realXHR.response; },
                        configurable: true, enumerable: true
                    });
                    Object.defineProperty(self, 'responseXML', {
                        get: function() { return _lssIntercepted ? null : _realXHR.responseXML; },
                        configurable: true, enumerable: true
                    });
                    Object.defineProperty(self, 'timeout', {
                        get: function() { return _lssIntercepted ? 0 : _realXHR.timeout; },
                        set: function(val) { if (_lssIntercepted) return; try { _realXHR.timeout = val; } catch(e) {} },
                        configurable: true, enumerable: true
                    });
                    Object.defineProperty(self, 'withCredentials', {
                        get: function() { return _lssIntercepted ? false : _realXHR.withCredentials; },
                        set: function(val) { if (!_lssIntercepted) try { _realXHR.withCredentials = val; } catch(e) {} },
                        configurable: true, enumerable: true
                    });
                };
                window.XMLHttpRequest.prototype = _TXOrigXHR.prototype;
                window.XMLHttpRequest.UNSENT = _TXOrigXHR.UNSENT;
                window.XMLHttpRequest.OPENED = _TXOrigXHR.OPENED;
                window.XMLHttpRequest.HEADERS_RECEIVED = _TXOrigXHR.HEADERS_RECEIVED;
                window.XMLHttpRequest.LOADING = _TXOrigXHR.LOADING;
                window.XMLHttpRequest.DONE = _TXOrigXHR.DONE;
            }
        } catch(e) { console.log('[LSS] Tencent Video patch error:', e); }
    }
    
    // 爱奇艺
    if (_host.indexOf('iqiyi.com') !== -1) {
        console.log('[LSS] iQiyi detected, applying patches');
        // iQiyi 使用原生 Chromium 编解码，不需要修改 MSE
    }
    
    // 优酷
    if (_host.indexOf('youku.com') !== -1) {
        console.log('[LSS] Youku detected, applying patches');
        // 优酷使用原生 Chromium 编解码，不需要修改 MSE
    }
    
    // 芒果TV
    if (_host.indexOf('mgtv.com') !== -1) {
        console.log('[LSS] MGTV detected, applying patches');
        // 芒果TV使用原生 Chromium 编解码，不需要修改 MSE
    }
    
    // 好看视频 (Haokan / Baidu)
    if (_host.indexOf('haokan.baidu.com') !== -1 || _host.indexOf('haokan.com') !== -1 || _host.indexOf('baijiahao.baidu.com') !== -1) {
        console.log('[LSS] Haokan/Baidu Video detected, applying patches');
        try {
            // 模拟本地客户端检测
            window.__bdClientReady = true;
            
            // 增强 canPlayType
            var _origHKVideo = HTMLVideoElement.prototype.canPlayType;
            HTMLVideoElement.prototype.canPlayType = function(type) {
                var r = '';
                try { r = _origHKVideo.call(this, type); } catch(e) {}
                if (r && r !== '') return r;
                var t = type.toLowerCase();
                if (/video\\//.test(t) || /audio\\//.test(t)) return 'probably';
                if (/h264|avc|mp4|hev1|hvc1/.test(t)) return 'probably';
                if (/mp4a|aac|opus/.test(t)) return 'probably';
                return 'probably';
            };
            
            // 增强 MediaSource.isTypeSupported
            var _origHKMSE = window.MediaSource && MediaSource.isTypeSupported.bind(MediaSource);
            if (window.MediaSource && _origHKMSE) {
                MediaSource.isTypeSupported = function(type) {
                    try { if (_origHKMSE(type)) return true; } catch(e) {}
                    var t = type.toLowerCase();
                    if (/video\\/mp4/.test(t) || /audio\\/mp4/.test(t)) return true;
                    if (/video\\/webm/.test(t) || /audio\\/webm/.test(t)) return true;
                    if (/avc1|h264|hev1|hvc1|mp4a|aac|opus/.test(t)) return true;
                    return true;
                };
            }
        } catch(e) {}
    }
    
    // 通用处理 - 确保基本视频功能正常
    if (window.top === window) {
        console.log('[LSS] Applying universal video patches');
    }
    
    console.log('[LSS] HTML5 Player Polyfill loaded successfully');
})();
"""
        player_script = QWebEngineScript()
        player_script.setSourceCode(player_js_code)
        player_script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
        player_script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
        player_script.setRunsOnSubFrames(True)
        self.profile().scripts().insert(player_script)
        logger.info("html5 player polyfill injected (inline)")
        
        # HEVC 解码器代理注入
        try:
            proxy_server = get_vps(logger)
            hevc_js = proxy_server.hevc_js()
            hevc_script = QWebEngineScript()
            hevc_script.setSourceCode(hevc_js)
            hevc_script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
            hevc_script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
            hevc_script.setRunsOnSubFrames(True)
            self.profile().scripts().insert(hevc_script)
            logger.info("hevc decoder proxy injected")
        except Exception as e:
            logger.warning(f"hevc decoder proxy injection failed: {str(e)}")
    
    def createWindow(self, t):
        tab = self.main.add_new_tab()
        return tab.page()
    
    def javaScriptConsoleMessage(self, level, message, lineNumber, sourceID):
        """捕获JavaScript控制台消息"""
        # PyQt6.QtWebEngineCore.QWebEnginePage.JavaScriptConsoleMessageLevel
        # 0 = Info, 1 = Warning, 2 = Error
        if level == 1:  # Warning
            logger.js_warning(f"js: {message}")
        elif level == 2:  # Error
            logger.js_error(f"js: {message}")
        else:  # Info
            logger.info(f"js: {message}")

# 网络连通性检测（TCP端口检测）
def check_connectivity(ip, port=443, timeout=2):
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((ip, port))
        sock.close()
        if result == 0:
            return (True, tr("network_normal"))
        else:
            return (False, f"{tr('network_unreachable')}(端口{port}关闭)")
    except socket.timeout:
        return (False, tr("network_timeout"))
    except Exception as e:
        return (False, f"{tr('network_check_failed')}:{str(e)[:15]}")

# 通用DNS+网络双重校验方法
def resolve_and_check_domain(host):
    if not host:
        return (False, tr("dns_resolving"))
    try:
        ans = dns_resolver.resolve(host, "A")
        ip = ans[0].address
        used_dns = ans.nameserver
        dns_text = f"{tr('dns_ok')}:{used_dns} | {host} → {ip}"
    except dns.resolver.NXDOMAIN:
        return (False, f"{tr('dns_error')}:{host} {tr('dns_not_exist')}")
    except dns.resolver.Timeout:
        return (False, f"{tr('dns_error')}:{host} {tr('dns_resolve_timeout')}")
    except Exception as e:
        return (False, f"{tr('dns_error')}:{host} | {tr('dns_resolve_error')}")
    conn_success, conn_text = check_connectivity(ip)
    if conn_success:
        return (True, f"{dns_text} | {conn_text}")
    else:
        return (False, f"{dns_text} | {conn_text}")

def get_login_data_path():
    """获取登录数据存储路径"""
    return os.path.join(get_app_dir(), "resources", "custom", "data", "login_data")

class BrowserTab(QWebEngineView):
    def __init__(self, parent_win):
        super().__init__(parent_win)
        self.parent_win = parent_win
        self.setPage(FixJumpPage(self))
        self.page().profile().setHttpUserAgent("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.6099.216 Safari/537.36")
        
        # 根据完全隔离设置决定是否使用持久化存储
        if global_settings.get("full_isolation", "false") == "false":
            # 未开启完全隔离，使用持久化存储
            login_data_path = get_login_data_path()
            os.makedirs(login_data_path, exist_ok=True)
            self.page().profile().setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.AllowPersistentCookies)
            self.page().profile().setCachePath(os.path.join(get_app_dir(), "cache"))
            self.page().profile().setPersistentStoragePath(login_data_path)
        else:
            # 开启完全隔离，不使用持久化存储
            self.page().profile().setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.NoPersistentCookies)
        
        self.loadFinished.connect(self.on_load_finish)
        self.urlChanged.connect(self.on_url_changed)
        self.is_home_page = False  # 标记是否是主页
        self.last_history_url = ""  # 记录最后一条历史记录的URL，避免重复

    def is_home_page_url(self, qurl):
        # 检查是否是主页URL
        home_url = get_home_page_url()
        return qurl.isLocalFile() and qurl.path() == home_url.path()

    def on_url_changed(self, qurl):
        # 记录URL变化到日志
        url_str = qurl.toString()
        if not qurl.isLocalFile() and url_str.startswith('http'):
            logger.info(f"navigate to {url_str}")
        
        if self.is_home_page_url(qurl):
            self.is_home_page = True
            self.parent_win.set_dns_text(tr("dns_waiting"), True)
            # 清空URL栏在父窗口中处理
        else:
            self.is_home_page = False
            if qurl.isLocalFile():
                self.parent_win.set_dns_text(tr("local_file"), True)
            else:
                host = qurl.host()
                status, text = resolve_and_check_domain(host)
                self.parent_win.set_dns_text(text, status)
        
        # 记录历史记录（延迟一下，让页面标题更新）
        if not self.is_home_page_url(qurl) and not qurl.isLocalFile():
            if url_str != self.last_history_url:
                QTimer.singleShot(1000, lambda u=url_str: self.record_history_delayed(u))
    
    def record_history_delayed(self, url_str):
        if url_str != self.last_history_url:
            title = self.title()
            if title and title != "about:blank":
                add_history(QUrl(url_str), title)
                self.last_history_url = url_str
    
    def on_load_finish(self, ok):
        url = self.url()
        
        # 记录页面加载结果到日志
        if ok:
            logger.info(f"page loaded: {url.toString()}")
        else:
            logger.warning(f"page load failed: {url.toString()}")
        
        if self.is_home_page_url(url):
            self.is_home_page = True
            self.parent_win.set_dns_text(tr("dns_waiting"), True)
            self.setWindowTitle(tr("home_page_title"))
            # 加载主页设置和替换文本
            if ok:
                bg = global_settings.get("home_background", "default_background.jpg")
                shortcuts_list = global_settings.get("home_shortcuts_list", "[]")
                # 对路径进行转义，防止特殊字符导致 JS 语法错误
                bg_escaped = escape_js_string(bg.replace("\\", "/"))
                shortcuts_escaped = escape_js_string(shortcuts_list)
                js_code = f"""
                    localStorage.setItem('lss_home_background', '{bg_escaped}');
                    localStorage.setItem('lss_home_shortcuts_list', '{shortcuts_escaped}');
                    loadSettings();
                    loadShortcuts();
                    renderShortcuts();

                    // 替换文本
                    function replaceText(oldText, newText) {{
                        var walker = document.createTreeWalker(
                            document.body,
                            NodeFilter.SHOW_TEXT,
                            null,
                            false
                        );
                        var node;
                        while (node = walker.nextNode()) {{
                            node.nodeValue = node.nodeValue.replace(new RegExp(oldText, 'g'), newText);
                        }}
                    }}

                    // 替换各个文本
                    replaceText('搜索或输入网址...', '{escape_js_string(tr("search_placeholder"))}');
                    replaceText('按 Tab 键可直接聚焦到搜索框', '{escape_js_string(tr("tab_focus_search"))}');
                    replaceText('新建快捷方式', '{escape_js_string(tr("new_shortcut"))}');
                    replaceText('快捷方式名称', '{escape_js_string(tr("shortcut_name"))}');
                    replaceText('网站URL（如 https://www.example.com）', '{escape_js_string(tr("enter_url"))}');
                    replaceText('取消', '{escape_js_string(tr("cancel"))}');
                    replaceText('确定', '{escape_js_string(tr("ok"))}');
                    replaceText('添加快捷方式', '{escape_js_string(tr("add_shortcut"))}');
                    replaceText('请输入快捷方式名称', '{escape_js_string(tr("enter_shortcut_name"))}');
                    replaceText('请输入网站URL', '{escape_js_string(tr("enter_shortcut_url"))}');
                    replaceText('百度', '{escape_js_string(tr("baidu"))}');
                    replaceText('Google', '{escape_js_string(tr("google"))}');
                    replaceText('GitHub', '{escape_js_string(tr("github"))}');
                    replaceText('Bilibili', '{escape_js_string(tr("bilibili"))}');
                    replaceText('知乎', '{escape_js_string(tr("zhihu"))}');
                    replaceText('微博', '{escape_js_string(tr("weibo"))}');
                    replaceText('CSDN', '{escape_js_string(tr("csdn"))}');
                    replaceText('简书', '{escape_js_string(tr("jianshu"))}');

                    // 替换占位符
                    var searchInput = document.getElementById('searchInput');
                    if (searchInput) {{
                        searchInput.placeholder = '{escape_js_string(tr("search_placeholder"))}';
                    }}
                """
                self.page().runJavaScript(js_code)
        else:
                self.is_home_page = False
                if url.isLocalFile():
                    self.parent_win.set_dns_text(tr("local_file"), True)
                else:
                    host = url.host()
                    status, text = resolve_and_check_domain(host)
                    # 如果页面已经成功加载，说明网络是正常的
                    # TCP端口检测可能被防火墙阻止，但HTTP连接正常工作
                    if ok and not status:
                        # 页面加载成功但端口检测失败，信任页面加载结果
                        self.parent_win.set_dns_text(text, True)
                    else:
                        self.parent_win.set_dns_text(text, status)
                    if ok:
                        # 添加到历史记录
                        url_str = url.toString()
                        if url_str != self.last_history_url:
                            title = self.title()
                            add_history(url, title)
                            self.last_history_url = url_str

class AcceleratedBrowser(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(tr("window_title"))
        self.setGeometry(0, 0, 1200, 600)
        self.tab_widget = QTabWidget()
        self.tab_widget.setTabsClosable(True)
        self.tab_widget.tabCloseRequested.connect(self.close_tab)
        self.tab_widget.currentChanged.connect(self.tab_changed)
        self.setCentralWidget(self.tab_widget)
        self.create_nav_bar()
        self.create_status_bar()
        self.add_new_tab(get_home_page_url(), tr("home_page_title"))
        self.download_last_recv = 0
        self.download_last_time = 0
        self.download_manager = DownloadManagerWindow()
        self.download_manager_window = None
        
        # 在主窗口级别连接下载请求信号（只连接一次）
        QWebEngineProfile.defaultProfile().downloadRequested.connect(self.on_download)
        
        # 初始化快捷键
        self.shortcuts = {}
        self.setup_shortcuts()
    
    def setup_shortcuts(self):
        """从设置中读取并绑定快捷键"""
        # 清除现有快捷键
        for sc in self.shortcuts.values():
            sc.setEnabled(False)
            sc.deleteLater()
        self.shortcuts.clear()
        
        # 加载快捷键配置
        default_shortcuts = {
            "settings": "Ctrl+Shift+I",
            "appearance": "Ctrl+Shift+A",
            "dns": "Ctrl+Shift+N",
            "volume": "Ctrl+Shift+V",
            "privacy": "Ctrl+Shift+P",
            "download": "Ctrl+Shift+D",
            "download_settings": "Ctrl+Shift+S"
        }
        
        try:
            shortcuts_json = global_settings.get("shortcuts", "")
            if shortcuts_json:
                saved = json.loads(shortcuts_json)
                default_shortcuts.update(saved)
        except:
            pass
        
        # 绑定快捷键到对应功能
        shortcut_actions = {
            "settings": self.show_settings,
            "appearance": self.show_appearance_settings,
            "dns": self.show_dns_settings,
            "volume": self.show_volume_settings,
            "privacy": self.show_privacy_settings,
            "download": self.show_download_manager,
            "download_settings": self.show_download_settings_dialog
        }
        
        for key, seq_str in default_shortcuts.items():
            if key in shortcut_actions:
                try:
                    sc = QShortcut(QKeySequence(seq_str), self)
                    sc.activated.connect(shortcut_actions[key])
                    self.shortcuts[key] = sc
                except:
                    pass
    
    def update_shortcuts(self):
        """更新快捷键（设置更改后调用）"""
        self.setup_shortcuts()
    
    def show_download_settings_dialog(self):
        """显示下载设置对话框"""
        dialog = DownloadSettingsDialog(self)
        dialog.exec()
    
    def on_download(self, download: QWebEngineDownloadRequest):
        """处理下载请求（在主窗口级别只处理一次）"""
        file_name = download.suggestedFileName()
        download_url = download.url().toString()
        save_path = os.path.join(get_download_path(), file_name)
        
        num_threads = int(global_settings.get("download_threads", "8"))
        
        logger.info(f"download started - {file_name} (multi-thread: {num_threads})")
        
        download.cancel()
        
        self.download_manager.add_multi_thread_download(
            download_url,
            file_name,
            save_path,
            num_threads
        )
        self.show_download_manager()
    
    def closeEvent(self, event):
        for task_id, task_data in self.download_manager.download_tasks.items():
            task = task_data['task']
            if task.state == "downloading":
                task.cancel()
        
        data_settings_path = os.path.join(get_app_dir(), "resources", "custom", "data", "setting.txt")
        current_setting_path = os.path.join(get_app_dir(), "resources", "custom", "backup", "current_setting.txt")
        
        # Save current settings (including history) to data/setting.txt
        save_settings(global_settings)
        
        # Check if full isolation is enabled
        if global_settings.get("full_isolation", "false") == "true":
            # If full isolation is enabled, use current_setting.txt to overwrite data/setting.txt
            # This will remove the history recorded in this session
            if os.path.exists(current_setting_path):
                current_settings = {}
                with open(current_setting_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            key, value = line.split("=", 1)
                            current_settings[key.strip()] = value.strip()
                
                # Keep full_isolation from current global_settings
                current_settings["full_isolation"] = "true"
                
                # Save current_settings to data/setting.txt
                with open(data_settings_path, "w", encoding="utf-8") as f:
                    for key, value in current_settings.items():
                        f.write(f"{key}={value}\n")
        else:
            # If full isolation is NOT enabled, save current setting.txt to current_setting.txt
            # This includes history, so history persists across sessions
            if os.path.exists(data_settings_path):
                with open(data_settings_path, "r", encoding="utf-8") as f:
                    content = f.read()
                with open(current_setting_path, "w", encoding="utf-8") as f:
                    f.write(content)
        
        # 停止视频代理服务器
        try:
            stop_video_proxy()
            logger.info("video proxy server stopped")
        except Exception:
            pass
        
        event.accept()

    def add_new_tab(self, url=None, title=tr("new_tab") if "new_tab" in lang else "新标签页"):
        browser = BrowserTab(self)
        if url:
            browser.setUrl(url)
        else:
            browser.setUrl(get_home_page_url())
        idx = self.tab_widget.addTab(browser, title)
        self.tab_widget.setCurrentIndex(idx)
        browser.titleChanged.connect(lambda t: self.tab_widget.setTabText(idx, t))
        browser.urlChanged.connect(self.url_changed)
        return browser

    def close_tab(self, idx):
        if self.tab_widget.count() > 1:
            self.tab_widget.removeTab(idx)

    def tab_changed(self, idx):
        w = self.tab_widget.currentWidget()
        if w:
            # 检查是否是主页
            if hasattr(w, 'is_home_page_url') and w.is_home_page_url(w.url()):
                self.url_bar.setText("")
                self.set_dns_text(tr("wait_resolve"), True)
            else:
                self.url_bar.setText(w.url().toString())
                host = w.url().host()
                if host:
                    status, text = resolve_and_check_domain(host)
                    self.set_dns_text(text, status)
                else:
                    self.set_dns_text(tr("wait_resolve"), True)

    def url_changed(self, qurl):
        # 检查是否是主页
        current_widget = self.tab_widget.currentWidget()
        if current_widget and hasattr(current_widget, 'is_home_page_url') and current_widget.is_home_page_url(qurl):
            self.url_bar.setText("")
        else:
            self.url_bar.setText(qurl.toString())

    def create_nav_bar(self):
        nav = QToolBar()
        self.addToolBar(nav)
        back = QAction(tr("back"), self)
        back.triggered.connect(lambda: self.tab_widget.currentWidget().back())
        nav.addAction(back)
        forward = QAction(tr("forward"), self)
        forward.triggered.connect(lambda: self.tab_widget.currentWidget().forward())
        nav.addAction(forward)
        refresh = QAction(tr("retry"), self)
        refresh.triggered.connect(lambda: self.tab_widget.currentWidget().reload())
        nav.addAction(refresh)
        home = QAction(tr("home"), self)
        home.triggered.connect(lambda: self.go_home())
        nav.addAction(home)
        
        new_tab = QAction("＋", self)
        new_tab.triggered.connect(self.add_new_tab)
        nav.addAction(new_tab)
        self.url_bar = QLineEdit()
        self.url_bar.returnPressed.connect(self.go_url)
        nav.addWidget(self.url_bar)
        
        self.translate_btn = QPushButton(tr("translate"))
        self.translate_btn.setToolTip(tr("translate_tooltip"))
        self.translate_btn.clicked.connect(self.translate_page_auto)
        self.translate_menu = QMenu(self)
        self.build_translate_menu()
        self.translate_btn.setMenu(self.translate_menu)
        nav.addWidget(self.translate_btn)
        
        self.more_menu = QMenu(self)

        self.download_action = QAction(tr("download"), self)
        self.download_action.triggered.connect(self.show_download_manager)
        self.more_menu.addAction(self.download_action)

        self.history_action = QAction(tr("history"), self)
        self.history_action.triggered.connect(self.show_history)
        self.more_menu.addAction(self.history_action)

        self.settings_action = QAction(tr("settings"), self)
        self.settings_action.triggered.connect(self.show_settings)
        self.more_menu.addAction(self.settings_action)

        self.languages_action = QAction(tr("languages"), self)
        self.languages_action.triggered.connect(self.show_languages_settings)
        self.more_menu.addAction(self.languages_action)

        self.about_action = QAction(tr("about"), self)
        self.about_action.triggered.connect(self.show_about)
        self.more_menu.addAction(self.about_action)

        self.more_btn = QPushButton(tr("more"))
        self.more_btn.setMenu(self.more_menu)
        nav.addWidget(self.more_btn)
        
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        nav.addWidget(self.progress_bar)

    def show_download_manager(self):
        if not self.download_manager_window:
            self.download_manager_window = self.download_manager
            self.download_manager_window.show()
        else:
            self.download_manager_window.show()
            self.download_manager_window.activateWindow()

    def show_history(self):
        dialog = HistoryDialog(self)
        dialog.exec()
    
    def show_settings(self):
        dialog = SettingsDialog(self)
        dialog.exec()
    
    def show_appearance_settings(self):
        dialog = AppearanceSettingsDialog(self)
        dialog.exec()

    def go_url(self):
        url = self.url_bar.text().strip()
        if not url.startswith(("http://", "https://", "file:///")):
            url = "https://" + url
        self.tab_widget.currentWidget().setUrl(QUrl(url))

    def build_translate_menu(self):
        """构建翻译菜单"""
        self.translate_menu.clear()
        
        current_lang_name = global_settings.get("language", "简体中文")
        current_lang_code = LANGUAGE_TO_TRANSLATE_CODE.get(current_lang_name, "zh")
        
        translate_now_action = QAction(tr("translate_to") + f" {current_lang_name}", self)
        translate_now_action.triggered.connect(self.translate_page_auto)
        self.translate_menu.addAction(translate_now_action)
        
        self.translate_menu.addSeparator()
        
        change_lang_menu = QMenu(tr("change_target_lang"), self)
        for lang_name, lang_code in LANGUAGE_TO_TRANSLATE_CODE.items():
            action = QAction(lang_name, self)
            action.triggered.connect(lambda checked, name=lang_name, code=lang_code: self.translate_page(code, name))
            change_lang_menu.addAction(action)
        self.translate_menu.addMenu(change_lang_menu)
        
        self.translate_menu.addSeparator()
        restore_action = QAction(tr("translate_restore"), self)
        restore_action.triggered.connect(self.restore_original_page)
        self.translate_menu.addAction(restore_action)

    def translate_page_auto(self):
        """自动使用用户设置的语言翻译当前页面"""
        current_lang_name = global_settings.get("language", "简体中文")
        current_lang_code = LANGUAGE_TO_TRANSLATE_CODE.get(current_lang_name, "zh")
        self.translate_page(current_lang_code, current_lang_name)

    def translate_page(self, target_lang, lang_name):
        """使用百度翻译翻译当前页面"""
        current_browser = self.tab_widget.currentWidget()
        if not current_browser:
            return
        
        url = current_browser.url().toString()
        if not url or url == "about:blank" or url.startswith("file:///"):
            QMessageBox.information(self, tr("translate"), tr("translate_local_unsupported"))
            return
        
        if not hasattr(self, 'original_urls'):
            self.original_urls = {}
        if not hasattr(self, 'translated_tabs'):
            self.translated_tabs = {}
        
        tab_id = id(current_browser)
        if tab_id not in self.original_urls:
            self.original_urls[tab_id] = url
        
        self.translate_btn.setText(tr("translating"))
        self.translate_btn.setEnabled(False)
        
        js_get_text = """
            (function() {
                var walker = document.createTreeWalker(
                    document.body,
                    NodeFilter.SHOW_TEXT,
                    null,
                    false
                );
                var texts = [];
                var node;
                while (node = walker.nextNode()) {
                    var text = node.nodeValue.trim();
                    if (text && text.length > 0 && text.length < 2000) {
                        texts.push(text);
                    }
                }
                return texts.join('\\n---SEPARATOR---\\n');
            })();
        """
        
        current_browser.page().runJavaScript(js_get_text, lambda result: self._process_translate_result(result, target_lang, lang_name, current_browser))

    def _process_translate_result(self, text_content, target_lang, lang_name, browser):
        """处理获取到的页面文本并进行翻译"""
        if not text_content:
            self.translate_btn.setText(tr("translate"))
            self.translate_btn.setEnabled(True)
            QMessageBox.information(self, tr("translate"), tr("translate_no_content"))
            return
        
        translated = baidu_translate_text(text_content, target_lang)
        
        if translated:
            segments = translated.split('---SEPARATOR---')
            original_segments = text_content.split('---SEPARATOR---')
            
            if len(segments) == len(original_segments):
                replacements = {}
                for i, orig in enumerate(original_segments):
                    if orig.strip():
                        replacements[orig.strip()] = segments[i] if i < len(segments) else orig
                
                replacements_json = json.dumps(replacements, ensure_ascii=False)
                
                js_replace = """
                    (function(replacements) {
                        var walker = document.createTreeWalker(
                            document.body,
                            NodeFilter.SHOW_TEXT,
                            null,
                            false
                        );
                        var node;
                        while (node = walker.nextNode()) {
                            var text = node.nodeValue.trim();
                            if (text && replacements[text]) {
                                node.nodeValue = replacements[text];
                            }
                        }
                    })(replacements_data);
                """
                js_replace = js_replace.replace('replacements_data', replacements_json)
                browser.page().runJavaScript(js_replace)
                
                tab_id = id(browser)
                self.translated_tabs[tab_id] = lang_name
                logger.info(f"page translated to {lang_name}")
        
        self.translate_btn.setText(tr("translate"))
        self.translate_btn.setEnabled(True)

    def restore_original_page(self):
        """恢复翻译前的原始页面"""
        current_browser = self.tab_widget.currentWidget()
        if not current_browser:
            return
        
        if not hasattr(self, 'original_urls'):
            return
        
        tab_id = id(current_browser)
        original_url = self.original_urls.get(tab_id)
        if original_url:
            current_browser.setUrl(QUrl(original_url))
            del self.original_urls[tab_id]
            logger.info("restore original page")
        else:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.information(self, tr("translate"), tr("translate_no_original"))

    def go_home(self):
        home_url = get_home_page_url()
        self.url_bar.setText("")
        self.set_dns_text(tr("wait_resolve"), True)
        self.tab_widget.currentWidget().setUrl(home_url)

    def show_languages_settings(self):
        dialog = LanguageSettingsDialog(self)
        dialog.exec()

    def create_status_bar(self):
        self.dns_label = QLabel(tr("dns_waiting"))
        self.down_label = QLabel("")
        self.statusBar().addWidget(self.dns_label)
        self.statusBar().addPermanentWidget(self.down_label)
        
        show_dns = global_settings.get("show_dns_status", "true") == "true"
        self.dns_label.setVisible(show_dns)

    def set_dns_text(self, text, status=False):
        show_dns = global_settings.get("show_dns_status", "true") == "true"
        self.dns_label.setVisible(show_dns)
        
        if show_dns:
            self.dns_label.setText(text)
            if status:
                ok_color = global_settings.get("dns_ok_color", "#008800")
                self.dns_label.setStyleSheet(f"color:{ok_color};font-weight:bold")
            else:
                error_color = global_settings.get("dns_error_color", "#cc0000")
                self.dns_label.setStyleSheet(f"color:{error_color};font-weight:bold")

    def show_about(self):
        dialog = AboutDialog(self)
        dialog.exec()

    def show_dns_settings(self):
        dialog = DNSSettingsDialog(self)
        dialog.exec()
    
    def show_volume_settings(self):
        dialog = VolumeSettingsDialog(self)
        dialog.exec()
    
    def show_privacy_settings(self):
        dialog = PrivacySettingsDialog(self)
        dialog.exec()

    def download_progress(self, recv, total):
        current_time = time.perf_counter()

        def format_speed(speed_val):
            if speed_val >= 1024 * 1024 * 1024:
                return f"{speed_val / 1024 / 1024 / 1024:.2f} GB/s"
            elif speed_val >= 1024 * 1024:
                return f"{speed_val / 1024 / 1024:.2f} MB/s"
            elif speed_val >= 1024:
                return f"{speed_val / 1024:.2f} KB/s"
            else:
                return f"{speed_val:.0f} B/s"

        if not hasattr(self, 'download_speed'):
            self.download_speed = 0

        if self.download_last_time > 0:
            time_delta = current_time - self.download_last_time
            bytes_delta = recv - self.download_last_recv
            if time_delta >= 0.5 and bytes_delta > 0:
                speed = bytes_delta / time_delta
                if hasattr(self, 'download_speed_history'):
                    self.download_speed_history.append(speed)
                    if len(self.download_speed_history) > 3:
                        self.download_speed_history.pop(0)
                    self.download_speed = sum(self.download_speed_history) / len(self.download_speed_history)
                else:
                    self.download_speed_history = [speed]
                    self.download_speed = speed

        self.download_last_recv = recv
        self.download_last_time = current_time
        speed_text = format_speed(self.download_speed)

        if total > 0:
            per = int(recv / total * 100)
            if per >= 100:
                self.down_label.setText(f"<font color='green'>{tr('download_complete')}</font>")
                QTimer.singleShot(5000, lambda: self.down_label.setText(""))
                self.download_last_recv = 0
                self.download_last_time = 0
                self.download_speed = 0
                self.download_speed_history = []
            else:
                self.down_label.setText(f"{tr('downloading')}：{per}% ({speed_text})")
        elif recv > 0 and total == 0:
            size_mb = recv / 1024 / 1024
            self.down_label.setText(f"{tr('downloading')}：{size_mb:.2f}MB ({speed_text})")


class HistoryDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("history"))
        self.setFixedSize(500, 400)
        
        layout = QVBoxLayout()
        
        self.history_list = QListWidget()
        self.load_history()
        layout.addWidget(self.history_list)
        
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        
        self.clear_btn = QPushButton(tr("clear_history"))
        self.clear_btn.clicked.connect(self.clear_history)
        button_layout.addWidget(self.clear_btn)
        
        self.close_btn = QPushButton(tr("close"))
        self.close_btn.clicked.connect(self.close)
        button_layout.addWidget(self.close_btn)
        
        layout.addLayout(button_layout)
        self.setLayout(layout)
        
        self.history_list.itemDoubleClicked.connect(self.open_history_item)
    
    def load_history(self):
        self.history_list.clear()
        history_str = global_settings.get("history", "[]")
        try:
            history = eval(history_str)
            for item in reversed(history):
                url = item.get("url", "")
                title = item.get("title", url)
                date = item.get("date", "")
                list_item = QListWidgetItem(f"{title}\n{url}")
                if date:
                    list_item.setToolTip(date)
                list_item.setData(Qt.ItemDataRole.UserRole, url)
                self.history_list.addItem(list_item)
        except:
            pass
    
    def clear_history(self):
        global_settings["history"] = "[]"
        save_settings(global_settings)
        self.load_history()
    
    def open_history_item(self, item):
        url = item.data(Qt.ItemDataRole.UserRole)
        if url and hasattr(self.parent(), 'add_new_tab'):
            self.parent().add_new_tab(QUrl(url))
            self.close()


class PrivacySettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("privacy_settings"))
        self.setFixedSize(300, 180)
        
        layout = QVBoxLayout()
        
        self.full_isolation_check = QCheckBox(tr("full_isolation"))
        self.full_isolation_check.setChecked(global_settings.get("full_isolation", "false") == "true")
        layout.addWidget(self.full_isolation_check)
        
        self.reset_btn = QPushButton(tr("restore_default_settings"))
        self.reset_btn.clicked.connect(self.restore_default_settings)
        layout.addWidget(self.reset_btn)
        
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        
        self.ok_btn = QPushButton(tr("ok"))
        self.ok_btn.clicked.connect(self.save_settings)
        button_layout.addWidget(self.ok_btn)
        
        self.cancel_btn = QPushButton(tr("cancel"))
        self.cancel_btn.clicked.connect(self.close)
        button_layout.addWidget(self.cancel_btn)
        
        layout.addLayout(button_layout)
        self.setLayout(layout)
    
    def save_settings(self):
        global_settings["full_isolation"] = "true" if self.full_isolation_check.isChecked() else "false"
        save_settings(global_settings)
        self.close()
    
    def restore_default_settings(self):
        from PyQt6.QtWidgets import QMessageBox
        
        reply = QMessageBox.question(
            self,
            tr("confirm_reset_title") if "confirm_reset_title" in lang else "确认恢复默认设置",
            tr("confirm_reset_message") if "confirm_reset_message" in lang else "确定要恢复所有默认设置吗？\n\n此操作将：\n- 重置所有浏览器设置\n- 清空浏览历史记录\n- 删除登录数据和缓存\n- 清空日志文件\n\n此操作不可撤销！",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        
        if reply != QMessageBox.StandardButton.Yes:
            return
        
        default_settings = DEFAULT_SETTINGS.copy()
        save_settings(default_settings)
        
        global global_settings
        global_settings = default_settings.copy()
        
        import shutil
        
        login_data_path = get_login_data_path()
        if os.path.exists(login_data_path):
            shutil.rmtree(login_data_path)
        
        logs_path = os.path.join(get_app_dir(), "logs")
        if os.path.exists(logs_path):
            for filename in os.listdir(logs_path):
                file_path = os.path.join(logs_path, filename)
                try:
                    if os.path.isfile(file_path) or os.path.islink(file_path):
                        os.unlink(file_path)
                    elif os.path.isdir(file_path):
                        shutil.rmtree(file_path)
                except Exception:
                    pass
        
        QMessageBox.information(
            self,
            tr("reset_success_title") if "reset_success_title" in lang else "恢复成功",
            tr("reset_success_message") if "reset_success_message" in lang else "所有设置已恢复为默认值。",
            QMessageBox.StandardButton.Ok
        )
        
        self.close()


class VolumeSettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("media_volume"))
        self.setFixedSize(350, 180)
        
        icon_path = os.path.join(get_app_dir(), "resources", "icon.ico")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))
        
        self.settings = global_settings.copy()
        
        layout = QVBoxLayout()
        
        # 音量滑块
        volume_row = QHBoxLayout()
        volume_row.addWidget(QLabel(tr("volume_label") + ":"))
        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setMinimum(0)
        self.volume_slider.setMaximum(100)
        self.volume_slider.setValue(int(float(self.settings.get("media_volume", "1.0")) * 100))
        self.volume_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.volume_slider.setTickInterval(10)
        self.volume_slider.valueChanged.connect(self._on_volume_changed)
        volume_row.addWidget(self.volume_slider)
        self.volume_value_label = QLabel(f"{self.volume_slider.value()}%")
        self.volume_value_label.setMinimumWidth(40)
        self.volume_value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        volume_row.addWidget(self.volume_value_label)
        layout.addLayout(volume_row)
        
        layout.addStretch()
        
        # 按钮
        button_layout = QHBoxLayout()
        self.reset_btn = QPushButton(tr("reset"))
        self.reset_btn.clicked.connect(self.reset_to_default)
        self.apply_btn = QPushButton(tr("apply"))
        self.apply_btn.clicked.connect(self.apply_settings)
        self.cancel_btn = QPushButton(tr("cancel"))
        self.cancel_btn.clicked.connect(self.close)
        button_layout.addWidget(self.reset_btn)
        button_layout.addStretch()
        button_layout.addWidget(self.apply_btn)
        button_layout.addWidget(self.cancel_btn)
        layout.addLayout(button_layout)
        
        self.setLayout(layout)
    
    def _on_volume_changed(self, value):
        self.volume_value_label.setText(f"{value}%")
        # 实时预览音量
        vol = value / 100.0
        if hasattr(self.parent(), 'tab_widget'):
            for i in range(self.parent().tab_widget.count()):
                browser = self.parent().tab_widget.widget(i)
                if hasattr(browser, 'page'):
                    browser.page().runJavaScript(f"""
                        document.querySelectorAll('video').forEach(function(v){{ v.volume={vol}; v.muted=({value}==0); }});
                        document.querySelectorAll('audio').forEach(function(a){{ a.volume={vol}; a.muted=({value}==0); }});
                    """)
    
    def reset_to_default(self):
        self.volume_slider.setValue(100)
        self.volume_value_label.setText("100%")
    
    def apply_settings(self):
        vol = self.volume_slider.value() / 100.0
        self.settings["media_volume"] = str(vol)
        save_settings(self.settings)
        global global_settings
        global_settings = self.settings.copy()
        
        # 应用音量到所有标签
        if hasattr(self.parent(), 'tab_widget'):
            for i in range(self.parent().tab_widget.count()):
                browser = self.parent().tab_widget.widget(i)
                if hasattr(browser, 'page'):
                    browser.page().runJavaScript(f"""
                        document.querySelectorAll('video').forEach(function(v){{ v.volume={vol}; v.muted=({self.volume_slider.value()}==0); }});
                        document.querySelectorAll('audio').forEach(function(a){{ a.volume={vol}; a.muted=({self.volume_slider.value()}==0); }});
                    """)
        
        self.close()


class ShortcutSettingsDialog(QDialog):
    """快捷键管理对话框"""
    DEFAULT_SHORTCUTS = {
        "settings": "Ctrl+Shift+I",
        "appearance": "Ctrl+Shift+A",
        "dns": "Ctrl+Shift+N",
        "volume": "Ctrl+Shift+V",
        "privacy": "Ctrl+Shift+P",
        "download": "Ctrl+Shift+D",
        "download_settings": "Ctrl+Shift+S"
    }
    
    SHORTCUT_LABELS = {
        "settings": "设置",
        "appearance": "外观设置",
        "dns": "DNS设置",
        "volume": "音量设置",
        "privacy": "隐私设置",
        "download": "下载管理",
        "download_settings": "下载设置"
    }
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("快捷键管理")
        self.setFixedSize(400, 380)
        
        icon_path = os.path.join(get_app_dir(), "resources", "icon.ico")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))
        
        self.settings = global_settings.copy()
        
        layout = QVBoxLayout()
        
        # 快捷键列表
        self.shortcut_inputs = {}
        for key, default_seq in self.DEFAULT_SHORTCUTS.items():
            row = QHBoxLayout()
            label = QLabel(self.SHORTCUT_LABELS.get(key, key) + ":")
            label.setMinimumWidth(100)
            row.addWidget(label)
            
            input_field = QLineEdit()
            input_field.setPlaceholderText("例如: Ctrl+Shift+I")
            # 加载已保存的快捷键
            saved_shortcuts = self._load_shortcuts()
            input_field.setText(saved_shortcuts.get(key, default_seq))
            input_field.setMinimumWidth(180)
            row.addWidget(input_field)
            
            self.shortcut_inputs[key] = input_field
            layout.addLayout(row)
        
        layout.addStretch()
        
        # 按钮
        button_layout = QHBoxLayout()
        self.reset_btn = QPushButton("恢复默认")
        self.reset_btn.clicked.connect(self.reset_to_default)
        self.apply_btn = QPushButton("应用")
        self.apply_btn.clicked.connect(self.apply_settings)
        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.clicked.connect(self.close)
        button_layout.addWidget(self.reset_btn)
        button_layout.addStretch()
        button_layout.addWidget(self.apply_btn)
        button_layout.addWidget(self.cancel_btn)
        layout.addLayout(button_layout)
        
        self.setLayout(layout)
    
    def _load_shortcuts(self):
        """从设置中加载快捷键配置"""
        try:
            shortcuts_json = self.settings.get("shortcuts", "")
            if shortcuts_json:
                return json.loads(shortcuts_json)
        except:
            pass
        return self.DEFAULT_SHORTCUTS.copy()
    
    def reset_to_default(self):
        """恢复默认快捷键"""
        for key, default_seq in self.DEFAULT_SHORTCUTS.items():
            if key in self.shortcut_inputs:
                self.shortcut_inputs[key].setText(default_seq)
    
    def apply_settings(self):
        """应用快捷键设置"""
        shortcuts = {}
        for key, input_field in self.shortcut_inputs.items():
            seq = input_field.text().strip()
            if seq:
                shortcuts[key] = seq
        
        self.settings["shortcuts"] = json.dumps(shortcuts, ensure_ascii=False)
        save_settings(self.settings)
        global global_settings
        global_settings = self.settings.copy()
        
        # 通知父窗口更新快捷键
        if hasattr(self.parent(), 'update_shortcuts'):
            self.parent().update_shortcuts()
        
        self.close()


class DownloadSettingsDialog(QDialog):
    """下载设置对话框"""
    
    DEFAULT_THREADS = 8
    MIN_THREADS = 1
    MAX_THREADS = 999
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("download_settings"))
        self.setFixedSize(350, 200)
        
        icon_path = os.path.join(get_app_dir(), "resources", "icon.ico")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))
        
        self.settings = global_settings.copy()
        
        layout = QVBoxLayout()
        
        # 线程数设置
        threads_row = QHBoxLayout()
        threads_row.addWidget(QLabel(tr("download_threads_label") + ":"))
        
        self.threads_spinbox = QSpinBox()
        self.threads_spinbox.setMinimum(self.MIN_THREADS)
        self.threads_spinbox.setMaximum(self.MAX_THREADS)
        self.threads_spinbox.setValue(int(self.settings.get("download_threads", str(self.DEFAULT_THREADS))))
        self.threads_spinbox.setToolTip(tr("download_threads_tooltip"))
        threads_row.addWidget(self.threads_spinbox)
        
        self.threads_value_label = QLabel(tr("download_threads_unit"))
        self.threads_value_label.setMinimumWidth(40)
        threads_row.addWidget(self.threads_value_label)
        
        threads_row.addStretch()
        layout.addLayout(threads_row)
        
        # 说明文字
        info_label = QLabel(tr("download_threads_info") if "download_threads_info" in lang else "推荐使用 32 线程，更多线程可能不会提高速度")
        info_label.setStyleSheet("color: #666; font-size: 12px;")
        info_label.setWordWrap(True)
        layout.addWidget(info_label)
        
        layout.addStretch()
        
        # 按钮
        button_layout = QHBoxLayout()
        self.reset_btn = QPushButton(tr("reset"))
        self.reset_btn.clicked.connect(self.reset_to_default)
        self.apply_btn = QPushButton(tr("apply"))
        self.apply_btn.clicked.connect(self.apply_settings)
        self.cancel_btn = QPushButton(tr("cancel"))
        self.cancel_btn.clicked.connect(self.close)
        button_layout.addWidget(self.reset_btn)
        button_layout.addStretch()
        button_layout.addWidget(self.apply_btn)
        button_layout.addWidget(self.cancel_btn)
        layout.addLayout(button_layout)
        
        self.setLayout(layout)
    
    def reset_to_default(self):
        """恢复默认设置"""
        self.threads_spinbox.setValue(self.DEFAULT_THREADS)
    
    def apply_settings(self):
        """应用设置"""
        threads = self.threads_spinbox.value()
        self.settings["download_threads"] = str(threads)
        save_settings(self.settings)
        global global_settings
        global_settings = self.settings.copy()
        
        logger.info(f"set download threads to {threads}")
        self.close()


class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("settings"))
        self.setFixedSize(280, 260)
        
        layout = QVBoxLayout()
        layout.setSpacing(8)
        
        self.appearance_btn = QPushButton(tr("appearance"))
        self.appearance_btn.setMinimumHeight(36)
        self.appearance_btn.clicked.connect(self.show_appearance_settings)
        layout.addWidget(self.appearance_btn)
        
        self.dns_btn = QPushButton(tr("dns"))
        self.dns_btn.setMinimumHeight(36)
        self.dns_btn.clicked.connect(self.show_dns_settings)
        layout.addWidget(self.dns_btn)
        
        self.download_btn = QPushButton(tr("download_settings"))
        self.download_btn.setMinimumHeight(36)
        self.download_btn.clicked.connect(self.show_download_settings)
        layout.addWidget(self.download_btn)
        
        self.volume_btn = QPushButton(tr("media_volume"))
        self.volume_btn.setMinimumHeight(36)
        self.volume_btn.clicked.connect(self.show_volume_settings)
        layout.addWidget(self.volume_btn)
        
        self.privacy_btn = QPushButton(tr("privacy_settings"))
        self.privacy_btn.setMinimumHeight(36)
        self.privacy_btn.clicked.connect(self.show_privacy_settings)
        layout.addWidget(self.privacy_btn)
        
        self.shortcut_btn = QPushButton("快捷键管理")
        self.shortcut_btn.setMinimumHeight(36)
        self.shortcut_btn.clicked.connect(self.show_shortcut_settings)
        layout.addWidget(self.shortcut_btn)
        
        layout.addStretch()
        self.setLayout(layout)
    
    def show_appearance_settings(self):
        dialog = AppearanceSettingsDialog(self.parent())
        dialog.exec()
    
    def show_shortcut_settings(self):
        dialog = ShortcutSettingsDialog(self.parent())
        dialog.exec()
    
    def show_dns_settings(self):
        dialog = DNSSettingsDialog(self.parent())
        dialog.exec()
    
    def show_download_settings(self):
        dialog = DownloadSettingsDialog(self.parent())
        dialog.exec()
    
    def show_volume_settings(self):
        dialog = VolumeSettingsDialog(self.parent())
        dialog.exec()
    
    def show_privacy_settings(self):
        dialog = PrivacySettingsDialog(self.parent())
        dialog.exec()


class ShortcutEditorDialog(QDialog):
    DEFAULT_SHORTCUTS = [
        {"name": "百度", "url": "https://www.baidu.com", "icon": ""},
        {"name": "必应", "url": "https://www.bing.com", "icon": ""},
        {"name": "哔哩哔哩", "url": "https://www.bilibili.com", "icon": ""},
        {"name": "DeepSeek", "url": "https://chat.deepseek.com", "icon": ""}
    ]
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("edit_shortcuts"))
        self.setFixedSize(600, 500)
        self.parent_window = parent
        
        icon_path = os.path.join(get_app_dir(), "resources", "icon.ico")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))
        
        self.layout = QVBoxLayout(self)
        
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_content = QWidget()
        self.scroll_layout = QVBoxLayout(self.scroll_content)
        self.scroll_area.setWidget(self.scroll_content)
        self.layout.addWidget(self.scroll_area)
        
        self.add_btn = QPushButton(tr("add_shortcut"))
        self.add_btn.clicked.connect(self.add_shortcut)
        self.layout.addWidget(self.add_btn)
        
        self.restore_btn = QPushButton(tr("restore_defaults"))
        self.restore_btn.clicked.connect(self.restore_defaults)
        self.layout.addWidget(self.restore_btn)
        
        self.load_shortcuts()
        self.load_shortcuts_from_localstorage()
    
    def load_shortcuts(self):
        try:
            shortcuts_json = global_settings.get("home_shortcuts_list", "[]")
            self.shortcuts = json.loads(shortcuts_json)
            if not self.shortcuts:
                self.shortcuts = self.DEFAULT_SHORTCUTS.copy()
        except:
            self.shortcuts = self.DEFAULT_SHORTCUTS.copy()
    
    def load_shortcuts_from_localstorage(self):
        if hasattr(self.parent_window, 'tab_widget'):
            for i in range(self.parent_window.tab_widget.count()):
                browser = self.parent_window.tab_widget.widget(i)
                if hasattr(browser, 'is_home_page_url') and browser.is_home_page_url(browser.url()):
                    browser.page().runJavaScript("""
                        var saved = localStorage.getItem('lss_home_shortcuts_list');
                        if (saved) {
                            saved;
                        } else {
                            '[]';
                        }
                    """, lambda result: self.update_shortcuts_from_js(result))
    
    def update_shortcuts_from_js(self, result):
        try:
            self.shortcuts = json.loads(result)
            if not self.shortcuts:
                self.shortcuts = self.DEFAULT_SHORTCUTS.copy()
            self.save_shortcuts()
            self.render_shortcuts()
        except:
            pass
    
    def save_shortcuts(self):
        global_settings["home_shortcuts_list"] = json.dumps(self.shortcuts, ensure_ascii=False)
        save_settings(global_settings)
        self.refresh_parent_home()
    
    def refresh_parent_home(self):
        if hasattr(self.parent_window, 'tab_widget'):
            for i in range(self.parent_window.tab_widget.count()):
                browser = self.parent_window.tab_widget.widget(i)
                if hasattr(browser, 'is_home_page_url') and browser.is_home_page_url(browser.url()):
                    browser.reload()
    
    def render_shortcuts(self):
        while self.scroll_layout.count() > 0:
            item = self.scroll_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        
        for idx, shortcut in enumerate(self.shortcuts):
            row = QWidget()
            row_layout = QHBoxLayout(row)
            
            name_label = QLabel(shortcut.get("name", ""))
            name_label.setStyleSheet("font-weight: bold;")
            row_layout.addWidget(name_label)
            
            url_label = QLabel(shortcut.get("url", ""))
            url_label.setStyleSheet("color: #666; font-size: 11px;")
            row_layout.addWidget(url_label, 1)
            
            edit_btn = QPushButton(tr("edit"))
            edit_btn.clicked.connect(lambda checked, i=idx: self.edit_shortcut(i))
            row_layout.addWidget(edit_btn)
            
            delete_btn = QPushButton(tr("delete"))
            delete_btn.setStyleSheet("background-color: #ff4444; color: white;")
            delete_btn.clicked.connect(lambda checked, i=idx: self.delete_shortcut(i))
            row_layout.addWidget(delete_btn)
            
            self.scroll_layout.addWidget(row)
        
        self.scroll_layout.addStretch()
    
    def add_shortcut(self):
        dialog = ShortcutInputDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.shortcuts.append({"name": dialog.name, "url": dialog.url, "icon": ""})
            self.save_shortcuts()
            self.render_shortcuts()
    
    def edit_shortcut(self, idx):
        dialog = ShortcutInputDialog(self, self.shortcuts[idx]["name"], self.shortcuts[idx]["url"])
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.shortcuts[idx]["name"] = dialog.name
            self.shortcuts[idx]["url"] = dialog.url
            self.save_shortcuts()
            self.render_shortcuts()
    
    def delete_shortcut(self, idx):
        del self.shortcuts[idx]
        self.save_shortcuts()
        self.render_shortcuts()
    
    def restore_defaults(self):
        self.shortcuts = self.DEFAULT_SHORTCUTS.copy()
        self.save_shortcuts()
        self.load_shortcuts()
        self.render_shortcuts()


class ShortcutInputDialog(QDialog):
    def __init__(self, parent=None, name="", url=""):
        super().__init__(parent)
        self.setWindowTitle(tr("new_shortcut"))
        self.setFixedSize(400, 180)
        
        layout = QVBoxLayout(self)
        
        form_layout = QFormLayout()
        
        self.name_input = QLineEdit(name)
        form_layout.addRow(tr("shortcut_name") + ":", self.name_input)
        
        self.url_input = QLineEdit(url)
        form_layout.addRow(tr("enter_url") + ":", self.url_input)
        
        layout.addLayout(form_layout)
        
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        
        cancel_btn = QPushButton(tr("cancel"))
        cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(cancel_btn)
        
        ok_btn = QPushButton(tr("ok"))
        ok_btn.clicked.connect(self.accept)
        ok_btn.setStyleSheet("background-color: #2196F3; color: white;")
        button_layout.addWidget(ok_btn)
        
        layout.addLayout(button_layout)
    
    @property
    def name(self):
        return self.name_input.text().strip()
    
    @property
    def url(self):
        url = self.url_input.text().strip()
        if url and not url.startswith("http://") and not url.startswith("https://") and not url.startswith("file:///"):
            url = "https://" + url
        return url


class AboutDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("about_lss_browser"))
        self.setFixedSize(400, 250)
        
        icon_path = os.path.join(get_app_dir(), "resources", "icon.ico")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))
        
        layout = QVBoxLayout()
        
        info_text = ""
        info_path = os.path.join(get_app_dir(), "resources", "info", "info.txt")
        if os.path.exists(info_path):
            with open(info_path, 'r', encoding='utf-8') as f:
                info_text = f.read()
        
        self.text_edit = QTextEdit()
        self.text_edit.setReadOnly(True)
        self.text_edit.setText(info_text)
        self.text_edit.setStyleSheet("""
            QTextEdit {
                background-color: #f8f8f8;
                border: 1px solid #e0e0e0;
                border-radius: 4px;
                padding: 10px;
                font-family: Consolas, monospace;
                font-size: 12px;
            }
        """)
        layout.addWidget(self.text_edit)
        
        button_layout = QHBoxLayout()
        self.ok_btn = QPushButton(tr("ok"))
        self.ok_btn.clicked.connect(self.close)
        button_layout.addStretch()
        button_layout.addWidget(self.ok_btn)
        layout.addLayout(button_layout)
        
        self.setLayout(layout)


class LanguageSettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Languages")
        self.setFixedSize(350, 200)
        
        icon_path = os.path.join(get_app_dir(), "resources", "icon.ico")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))
        
        self.settings = global_settings.copy()
        
        layout = QVBoxLayout()
        
        label = QLabel("Language:")
        layout.addWidget(label)
        
        self.language_combo = QComboBox()
        for lang_code, lang_name in LANGUAGES.items():
            self.language_combo.addItem(lang_name, lang_code)
        
        current_lang = self.settings.get("language", "Chinese")
        for i in range(self.language_combo.count()):
            if self.language_combo.itemData(i) == current_lang:
                self.language_combo.setCurrentIndex(i)
                break
        
        layout.addWidget(self.language_combo)
        
        layout.addStretch()
        
        button_layout = QHBoxLayout()
        self.apply_btn = QPushButton("Apply and restart")
        self.apply_btn.clicked.connect(self.apply_and_restart)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.close)
        button_layout.addStretch()
        button_layout.addWidget(self.cancel_btn)
        button_layout.addWidget(self.apply_btn)
        layout.addLayout(button_layout)
        
        self.setLayout(layout)
    
    def apply_and_restart(self):
        selected_lang_code = self.language_combo.itemData(self.language_combo.currentIndex())
        self.settings["language"] = selected_lang_code
        save_settings(self.settings)
        global global_settings
        global_settings = self.settings.copy()
        
        self.close()
        import sys
        import subprocess
        
        app_dir = get_app_dir()
        os.chdir(app_dir)
        
        if getattr(sys, 'frozen', False):
            subprocess.Popen([sys.executable])
        else:
            subprocess.Popen([sys.executable, os.path.abspath(sys.argv[0])])
        
        sys.exit(0)


class DNSSettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("dns"))
        self.setFixedSize(400, 300)
        
        icon_path = os.path.join(get_app_dir(), "resources", "icon.ico")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))
        
        self.settings = global_settings.copy()
        
        layout = QVBoxLayout()
        
        self.mode_group = QButtonGroup(self)
        self.auto_radio = QRadioButton(tr("dns_mode_auto"))
        self.custom_radio = QRadioButton(tr("dns_mode_custom"))
        self.mode_group.addButton(self.auto_radio)
        self.mode_group.addButton(self.custom_radio)
        
        if self.settings["dns_mode"] == "custom":
            self.custom_radio.setChecked(True)
        else:
            self.auto_radio.setChecked(True)
        
        self.auto_radio.toggled.connect(self.update_dns_list_visibility)
        
        mode_layout = QVBoxLayout()
        mode_layout.addWidget(self.auto_radio)
        mode_layout.addWidget(self.custom_radio)
        mode_group_box = QGroupBox(tr("dns_mode"))
        mode_group_box.setLayout(mode_layout)
        layout.addWidget(mode_group_box)
        
        self.dns_list_label = QLabel(tr("select_dns"))
        self.dns_list = QComboBox()
        self.dns_list.addItems(CUSTOM_DNS + [tr("other_dns")])
        
        saved_dns = self.settings.get("custom_dns", "")
        if saved_dns in CUSTOM_DNS:
            self.dns_list.setCurrentText(saved_dns)
        else:
            self.dns_list.setCurrentText(tr("other_dns"))
        
        self.other_dns_edit = QLineEdit()
        self.other_dns_edit.setPlaceholderText(tr("enter_dns"))
        if saved_dns and saved_dns not in CUSTOM_DNS:
            self.other_dns_edit.setText(saved_dns)
        
        dns_layout = QVBoxLayout()
        dns_layout.addWidget(self.dns_list_label)
        dns_layout.addWidget(self.dns_list)
        dns_layout.addWidget(self.other_dns_edit)
        self.dns_group_box = QGroupBox(tr("dns_server"))
        self.dns_group_box.setLayout(dns_layout)
        layout.addWidget(self.dns_group_box)
        
        self.dns_list.currentTextChanged.connect(self.update_other_dns_visibility)
        
        button_layout = QHBoxLayout()
        self.reset_btn = QPushButton(tr("reset"))
        self.reset_btn.clicked.connect(self.reset_to_default)
        self.apply_btn = QPushButton(tr("apply"))
        self.apply_btn.clicked.connect(self.apply_settings)
        self.cancel_btn = QPushButton(tr("cancel"))
        self.cancel_btn.clicked.connect(self.close)
        button_layout.addWidget(self.reset_btn)
        button_layout.addStretch()
        button_layout.addWidget(self.apply_btn)
        button_layout.addWidget(self.cancel_btn)
        layout.addLayout(button_layout)
        
        self.setLayout(layout)
        self.update_dns_list_visibility()
    
    def reset_to_default(self):
        self.settings["dns_mode"] = DEFAULT_SETTINGS["dns_mode"]
        self.settings["custom_dns"] = DEFAULT_SETTINGS["custom_dns"]
        self.auto_radio.setChecked(True)
        self.dns_list.setCurrentText(CUSTOM_DNS[0])
        self.other_dns_edit.setText("")
        self.update_dns_list_visibility()
    
    def update_dns_list_visibility(self):
        enabled = self.custom_radio.isChecked()
        self.dns_group_box.setEnabled(enabled)
        self.dns_list_label.setVisible(enabled)
        self.dns_list.setVisible(enabled)
        self.other_dns_edit.setVisible(enabled and self.dns_list.currentText() == tr("other_dns"))
    
    def update_other_dns_visibility(self, text):
        if self.custom_radio.isChecked():
            self.other_dns_edit.setVisible(text == tr("other_dns"))
    
    def apply_settings(self):
        self.settings["dns_mode"] = "custom" if self.custom_radio.isChecked() else "auto"
        
        if self.custom_radio.isChecked():
            if self.dns_list.currentText() == "其他DNS":
                self.settings["custom_dns"] = self.other_dns_edit.text().strip()
            else:
                self.settings["custom_dns"] = self.dns_list.currentText()
        else:
            self.settings["custom_dns"] = ""
        
        save_settings(self.settings)
        global global_settings
        global_settings = self.settings.copy()
        
        if self.settings["dns_mode"] == "custom" and self.settings["custom_dns"]:
            dns_resolver.nameservers = [self.settings["custom_dns"]]
        else:
            dns_resolver.nameservers = CUSTOM_DNS
        
        self.refresh_dns_status()
        self.close()
    
    def refresh_dns_status(self):
        if hasattr(self.parent(), 'tab_widget'):
            current_browser = self.parent().tab_widget.currentWidget()
            if current_browser:
                if hasattr(current_browser, 'is_home_page_url') and current_browser.is_home_page_url(current_browser.url()):
                    self.parent().set_dns_text(tr("wait_resolve"), True)
                else:
                    host = current_browser.url().host()
                    status, text = resolve_and_check_domain(host)
                    self.parent().set_dns_text(text, status)


class AppearanceSettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("appearance"))
        self.setFixedSize(450, 450)
        
        icon_path = os.path.join(get_app_dir(), "resources", "icon.ico")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))
        
        self.settings = global_settings.copy()
        
        main_layout = QVBoxLayout()
        
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_content = QWidget()
        layout = QVBoxLayout(scroll_content)
        scroll_area.setWidget(scroll_content)
        
        theme_group = QGroupBox(tr("theme_settings"))
        theme_layout = QVBoxLayout()
        
        self.theme_group = QButtonGroup(self)
        self.light_radio = QRadioButton(tr("theme_light"))
        self.dark_radio = QRadioButton(tr("theme_dark"))
        self.system_radio = QRadioButton(tr("theme_system"))
        
        self.theme_group.addButton(self.light_radio)
        self.theme_group.addButton(self.dark_radio)
        self.theme_group.addButton(self.system_radio)
        
        current_theme = self.settings.get("theme", "system")
        if current_theme == "light":
            self.light_radio.setChecked(True)
        elif current_theme == "dark":
            self.dark_radio.setChecked(True)
        else:
            self.system_radio.setChecked(True)
        
        theme_layout.addWidget(self.light_radio)
        theme_layout.addWidget(self.dark_radio)
        theme_layout.addWidget(self.system_radio)
        theme_group.setLayout(theme_layout)
        layout.addWidget(theme_group)
        
        home_group = QGroupBox(tr("home_settings"))
        home_layout = QVBoxLayout()
        
        background_row = QHBoxLayout()
        background_row.addWidget(QLabel(tr("background_image") + ":"))
        self.background_label = QLabel("")
        self.background_label.setStyleSheet("border: 1px solid #ccc; min-width: 150px; max-width: 150px;")
        self.background_path = self.settings.get("home_background", "default_background.jpg")
        self.background_label.setText(self.background_path)
        background_row.addWidget(self.background_label)
        self.select_background_btn = QPushButton(tr("select"))
        self.select_background_btn.clicked.connect(self.select_background)
        background_row.addWidget(self.select_background_btn)
        background_row.addStretch()
        
        home_layout.addLayout(background_row)
        
        edit_icons_row = QHBoxLayout()
        self.edit_icons_btn = QPushButton(tr("edit_shortcuts"))
        self.edit_icons_btn.clicked.connect(self.edit_shortcuts)
        edit_icons_row.addWidget(self.edit_icons_btn)
        edit_icons_row.addStretch()
        
        home_layout.addLayout(edit_icons_row)
        home_group.setLayout(home_layout)
        layout.addWidget(home_group)
        
        dns_display_group = QGroupBox(tr("dns_display_settings"))
        dns_display_layout = QVBoxLayout()
        
        self.show_dns_checkbox = QCheckBox(tr("show_dns_status"))
        self.show_dns_checkbox.setChecked(self.settings.get("show_dns_status", "true") == "true")
        self.show_dns_checkbox.stateChanged.connect(self.update_color_widgets_visibility)
        dns_display_layout.addWidget(self.show_dns_checkbox)
        
        color_layout = QVBoxLayout()
        
        error_color_row = QHBoxLayout()
        error_color_row.addWidget(QLabel(tr("error_color") + ":"))
        self.error_color_btn = QPushButton()
        self.error_color_btn.setFixedSize(60, 30)
        self.error_color = self.settings.get("dns_error_color", "#cc0000")
        self.error_color_btn.setStyleSheet(f"background-color: {self.error_color}; border: 1px solid #ccc;")
        self.error_color_btn.clicked.connect(lambda: self.select_color("error"))
        error_color_row.addWidget(self.error_color_btn)
        error_color_row.addStretch()
        
        ok_color_row = QHBoxLayout()
        ok_color_row.addWidget(QLabel(tr("ok_color") + ":"))
        self.ok_color_btn = QPushButton()
        self.ok_color_btn.setFixedSize(60, 30)
        self.ok_color = self.settings.get("dns_ok_color", "#008800")
        self.ok_color_btn.setStyleSheet(f"background-color: {self.ok_color}; border: 1px solid #ccc;")
        self.ok_color_btn.clicked.connect(lambda: self.select_color("ok"))
        ok_color_row.addWidget(self.ok_color_btn)
        ok_color_row.addStretch()
        
        color_layout.addLayout(error_color_row)
        color_layout.addLayout(ok_color_row)
        dns_display_layout.addLayout(color_layout)
        
        dns_display_group.setLayout(dns_display_layout)
        layout.addWidget(dns_display_group)
        
        main_layout.addWidget(scroll_area)
        
        button_layout = QHBoxLayout()
        self.reset_btn = QPushButton(tr("reset"))
        self.reset_btn.clicked.connect(self.reset_to_default)
        self.apply_btn = QPushButton(tr("apply"))
        self.apply_btn.clicked.connect(self.apply_settings)
        self.cancel_btn = QPushButton(tr("cancel"))
        self.cancel_btn.clicked.connect(self.close)
        button_layout.addWidget(self.reset_btn)
        button_layout.addStretch()
        button_layout.addWidget(self.apply_btn)
        button_layout.addWidget(self.cancel_btn)
        main_layout.addLayout(button_layout)
        
        self.setLayout(main_layout)
        self.update_color_widgets_visibility()
    
    def reset_to_default(self):
        self.settings["theme"] = DEFAULT_SETTINGS["theme"]
        self.settings["show_dns_status"] = DEFAULT_SETTINGS["show_dns_status"]
        self.settings["dns_error_color"] = DEFAULT_SETTINGS["dns_error_color"]
        self.settings["dns_ok_color"] = DEFAULT_SETTINGS["dns_ok_color"]
        self.settings["home_background"] = DEFAULT_SETTINGS["home_background"]
        self.system_radio.setChecked(True)
        self.show_dns_checkbox.setChecked(True)
        self.error_color = DEFAULT_SETTINGS["dns_error_color"]
        self.error_color_btn.setStyleSheet(f"background-color: {self.error_color}; border: 1px solid #ccc;")
        self.ok_color = DEFAULT_SETTINGS["dns_ok_color"]
        self.ok_color_btn.setStyleSheet(f"background-color: {self.ok_color}; border: 1px solid #ccc;")
        self.background_path = DEFAULT_SETTINGS["home_background"]
        self.background_label.setText(self.background_path)
        self.update_color_widgets_visibility()
    
    def update_color_widgets_visibility(self):
        enabled = self.show_dns_checkbox.isChecked()
        for i in range(self.layout().count()):
            item = self.layout().itemAt(i)
            if item.widget() and isinstance(item.widget(), QGroupBox):
                group = item.widget()
                if "DNS" in group.title():
                    for j in range(group.layout().count()):
                        sub_item = group.layout().itemAt(j)
                        if sub_item.widget() and sub_item.widget() != self.show_dns_checkbox:
                            sub_item.widget().setVisible(enabled)
    
    def select_color(self, color_type):
        if color_type == "error":
            color = QColorDialog.getColor(QColor(self.error_color), self, tr("select_error_color") if "select_error_color" in lang else "选择异常状态颜色")
            if color.isValid():
                self.error_color = color.name()
                self.error_color_btn.setStyleSheet(f"background-color: {self.error_color}; border: 1px solid #ccc;")
        else:
            color = QColorDialog.getColor(QColor(self.ok_color), self, tr("select_ok_color") if "select_ok_color" in lang else "选择正常状态颜色")
            if color.isValid():
                self.ok_color = color.name()
                self.ok_color_btn.setStyleSheet(f"background-color: {self.ok_color}; border: 1px solid #ccc;")
    
    def select_background(self):
        file_filter = tr("image_files") if "image_files" in lang else "图片文件 (*.png *.jpg *.jpeg *.bmp *.gif)" + ";;" + tr("all_files") if "all_files" in lang else "所有文件 (*)"
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            tr("select_background") if "select_background" in lang else "选择背景图片",
            "",
            file_filter
        )
        if file_path:
            self.background_path = file_path
            self.background_label.setText(os.path.basename(file_path))
    
    def apply_settings(self):
        if self.light_radio.isChecked():
            self.settings["theme"] = "light"
        elif self.dark_radio.isChecked():
            self.settings["theme"] = "dark"
        else:
            self.settings["theme"] = "system"
        
        self.settings["show_dns_status"] = "true" if self.show_dns_checkbox.isChecked() else "false"
        self.settings["dns_error_color"] = self.error_color
        self.settings["dns_ok_color"] = self.ok_color
        self.settings["home_background"] = self.background_path
        
        save_settings(self.settings)
        global global_settings
        global_settings = self.settings.copy()
        
        self.apply_theme()
        self.update_dns_display()
        
        # 把设置同步到主页的localStorage，通过注入JavaScript
        self.apply_home_settings()
        
        self.close()
    
    def apply_home_settings(self):
        if hasattr(self.parent(), 'tab_widget'):
            current_browser = self.parent().tab_widget.currentWidget()
            if current_browser and hasattr(current_browser, 'url'):
                url = current_browser.url()
                home_url = get_home_page_url()
                if url.isLocalFile() and url.path() == home_url.path():
                    shortcuts_list = global_settings.get("home_shortcuts_list", "[]")
                    # 对路径进行转义，防止特殊字符导致 JS 语法错误
                    bg_escaped = self.background_path.replace("\\", "/").replace("'", "\\'")
                    shortcuts_escaped = shortcuts_list.replace("'", "\\'")
                    # 先更新localStorage，然后刷新页面以应用新设置
                    js_code = f"""
                        localStorage.setItem('lss_home_background', '{bg_escaped}');
                        localStorage.setItem('lss_home_shortcuts_list', '{shortcuts_escaped}');
                        // 重新加载设置并渲染快捷按钮
                        loadSettings();
                        renderShortcuts();
                    """
                    current_browser.page().runJavaScript(js_code)
    
    def apply_theme(self):
        theme = global_settings.get("theme", "system")
        if theme == "light":
            app.setStyleSheet("")
        elif theme == "dark":
            app.setStyleSheet("QWidget { background-color: #2b2b2b; color: #ffffff; }")
        else:
            app.setStyleSheet("")
    
    def edit_shortcuts(self):
        dialog = ShortcutEditorDialog(self.parent())
        dialog.exec()
    
    def update_dns_display(self):
        show = global_settings.get("show_dns_status", "true") == "true"
        if hasattr(self.parent(), 'dns_label'):
            self.parent().dns_label.setVisible(show)
            if hasattr(self.parent(), 'tab_widget'):
                current_browser = self.parent().tab_widget.currentWidget()
                if current_browser:
                    if hasattr(current_browser, 'is_home_page_url') and current_browser.is_home_page_url(current_browser.url()):
                        self.parent().set_dns_text(tr("wait_resolve"), True)
                    else:
                        host = current_browser.url().host()
                        status, text = resolve_and_check_domain(host)
                        self.parent().set_dns_text(text, status)


def set_app_icon():
    icon_path = os.path.join(get_app_dir(), "resources", "icon.ico")
    if os.path.exists(icon_path):
        app_icon = QIcon(icon_path)
        app.setWindowIcon(app_icon)
        return app_icon
    return None

if __name__ == "__main__":
    if sys.platform == "win32":
        os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = (
            "--autoplay-policy=no-user-gesture-required "
            "--enable-webgl "
            "--enable-accelerated-video-decode "
            "--enable-gpu-rasterization "
            "--enable-features=PlatformHEVCDecoderSupport,VideoPlaybackQuality "
            "--enable-media-internals "
            "--enable-vp9-decoder "
            "--ignore-gpu-blocklist "
            "--enable-zero-copy "
            "--enable-native-gpu-memory-buffers "
            "--use-media-foundation-for-video-decoding "
            "--disable-features=AudioServiceAecDump "
            "--enable-audio-output "
            "--enable-speech-dispatcher "
            "--use-fake-ui-for-media-stream "
            "--allow-running-insecure-content "
            "--disable-web-security "
            "--js-flags=--max-old-space-size=512"
        )
    app = QApplication(sys.argv)
    
    set_app_icon()
    
    # 启动视频代理服务器（用于 H.265/HEVC 解码）
    start_video_proxy(logger)
    
    win = AcceleratedBrowser()
    win.show()
    
    # 处理命令行参数，支持打开 HTML 文件
    if len(sys.argv) > 1:
        for arg in sys.argv[1:]:
            if arg.lower().endswith('.html') or arg.lower().endswith('.htm'):
                file_path = os.path.abspath(arg)
                if os.path.exists(file_path):
                    win.add_new_tab(QUrl.fromLocalFile(file_path), os.path.basename(file_path))
            elif arg.startswith('http://') or arg.startswith('https://'):
                win.add_new_tab(QUrl(arg), arg)
    
    sys.exit(app.exec())