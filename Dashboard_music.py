import logging
import requests
import subprocess
import random
import time
import json
import urllib.parse
from flask import Flask, request, Response, stream_with_context, jsonify
from ytmusicapi import YTMusic
import yt_dlp
from datetime import datetime
import threading
from collections import deque
import html

app = Flask(__name__)

# ========== LOG DASHBOARD SETUP ==========
# Tạo buffer lưu logs cho dashboard
LOG_BUFFER = deque(maxlen=200)  # Lưu tối đa 200 log entries
LOG_LEVELS = ['INFO', 'WARNING', 'ERROR', 'DEBUG']


# Custom handler để capture logs vào buffer
class DashboardLogHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.setFormatter(logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        ))

    def emit(self, record):
        try:
            log_entry = {
                'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'level': record.levelname,
                'message': self.format(record),
                'module': record.name,
                'color': self.get_color(record.levelname)
            }
            LOG_BUFFER.append(log_entry)
        except:
            pass

    def get_color(self, level):
        colors = {
            'INFO': 'info',
            'WARNING': 'warning',
            'ERROR': 'error',
            'DEBUG': 'debug',
            'CRITICAL': 'error'
        }
        return colors.get(level, 'info')


# Cấu hình logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# Thêm handler cho dashboard
dashboard_handler = DashboardLogHandler()
dashboard_handler.setLevel(logging.INFO)
logger.addHandler(dashboard_handler)

# Khởi tạo YouTube Music API
try:
    ytmusic = YTMusic()
    logger.info("✅ YouTube Music API đã khởi tạo thành công")
except Exception as e:
    logger.error(f"❌ Không thể khởi tạo YouTube Music API: {e}")
    ytmusic = None

# DANH SÁCH COBALT SERVERS (dự phòng)
COBALT_INSTANCES = [
    "https://co.wuk.sh",
    "https://api.cobalt.best",
    "https://cobalt.tools",
    "https://cobalt.pub",
]

# Cache cho các stream đã tìm thấy
stream_cache = {}
CACHE_DURATION = 1800  # 30 phút
MAX_CACHE_SIZE = 100

# Danh sách user agents
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]

# Biến thống kê server
SERVER_STATS = {
    'start_time': datetime.now(),
    'total_requests': 0,
    'successful_streams': 0,
    'failed_streams': 0,
    'cache_hits': 0,
    'cache_misses': 0,
    'last_stream_time': None
}


# ========== TIỆN ÍCH HỖ TRỢ ==========

def get_user_agent():
    """Lấy user agent ngẫu nhiên"""
    return random.choice(USER_AGENTS)


def update_stats(request_type, success=True):
    """Cập nhật thống kê server"""
    SERVER_STATS['total_requests'] += 1
    if request_type == 'stream':
        if success:
            SERVER_STATS['successful_streams'] += 1
        else:
            SERVER_STATS['failed_streams'] += 1
        SERVER_STATS['last_stream_time'] = datetime.now()
    elif request_type == 'cache':
        if success:
            SERVER_STATS['cache_hits'] += 1
        else:
            SERVER_STATS['cache_misses'] += 1


def cleanup_cache():
    """Dọn dẹp cache cũ"""
    current_time = time.time()
    expired_keys = []

    for key, (cache_time, url) in stream_cache.items():
        if current_time - cache_time > CACHE_DURATION:
            expired_keys.append(key)

    for key in expired_keys:
        del stream_cache[key]

    if expired_keys:
        logger.info(f"🗑️ Đã xóa {len(expired_keys)} mục cache hết hạn")

    if len(stream_cache) > MAX_CACHE_SIZE:
        sorted_items = sorted(stream_cache.items(), key=lambda x: x[1][0])
        keys_to_remove = [k for k, _ in sorted_items[:len(sorted_items) - MAX_CACHE_SIZE]]
        for key in keys_to_remove:
            del stream_cache[key]
        logger.info(f"🗑️ Đã xóa {len(keys_to_remove)} mục cache vượt quá giới hạn")


def search_with_ytmusic(query):
    """Tìm link bài hát qua YouTube Music"""
    if not ytmusic:
        logger.error("YouTube Music API chưa khởi tạo")
        return None

    try:
        logger.info(f"🔍 Đang tìm kiếm: {query}")

        results = ytmusic.search(query, filter='songs')
        if results:
            video_id = results[0].get('videoId')
            title = results[0].get('title')
            artists = results[0].get('artists', [])
            artist_names = ", ".join([a.get('name', '') for a in artists]) if artists else ""

            if video_id:
                link = f"https://www.youtube.com/watch?v={video_id}"
                logger.info(f"✅ Tìm thấy bài hát: {title} - {artist_names} ({link})")
                return link

        logger.info("🔄 Thử tìm kiếm video thường...")
        results = ytmusic.search(query, filter='videos')
        if results:
            video_id = results[0].get('videoId')
            title = results[0].get('title')
            if video_id:
                link = f"https://www.youtube.com/watch?v={video_id}"
                logger.info(f"✅ Tìm thấy video: {title} ({link})")
                return link

        logger.warning(f"⚠️ Không tìm thấy kết quả cho: {query}")
        return None

    except Exception as e:
        logger.error(f"❌ Lỗi tìm kiếm YouTube Music: {e}")
        return None


def get_best_audio_url_ytdlp(youtube_url):
    """Lấy URL audio bằng yt-dlp"""
    try:
        ydl_opts = {
            'format': 'bestaudio[ext=m4a]/bestaudio/best',
            'quiet': True,
            'no_warnings': True,
            'extract_flat': False,
            'socket_timeout': 15,
            'noplaylist': True,
            'http_headers': {
                'User-Agent': get_user_agent(),
                'Accept': '*/*',
                'Accept-Language': 'en-US,en;q=0.9',
                'Accept-Encoding': 'gzip, deflate',
            },
            'extractor_args': {
                'youtube': {
                    'player_client': ['android', 'web'],
                    'player_skip': ['webpage']
                }
            }
        }

        logger.info(f"🎯 Đang lấy audio URL với yt-dlp: {youtube_url[:80]}...")

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(youtube_url, download=False)

            if 'url' in info:
                audio_url = info['url']
                duration = info.get('duration', 0)
                title = info.get('title', 'Unknown')

                logger.info(f"✅ Tìm thấy audio: {title} ({duration}s)")
                logger.debug(f"🔗 Audio URL: {audio_url[:100]}...")
                return audio_url
            else:
                formats = info.get('formats', [])
                for fmt in formats:
                    if fmt.get('acodec') != 'none' and fmt.get('vcodec') == 'none':
                        audio_url = fmt.get('url')
                        if audio_url:
                            logger.info(f"✅ Tìm thấy audio từ format: {fmt.get('format_note', 'unknown')}")
                            return audio_url

                logger.warning("⚠️ Không tìm thấy audio URL trong info")
                return None

    except yt_dlp.utils.DownloadError as e:
        logger.error(f"❌ Lỗi download yt-dlp: {str(e)[:200]}")
        return None
    except Exception as e:
        logger.error(f"❌ Lỗi yt-dlp không xác định: {e}")
        return None


def get_audio_stream_from_cobalt_fallback(youtube_url):
    """Fallback với Cobalt nếu cần"""
    payload = {
        "url": youtube_url,
        "aFormat": "mp3",
        "isAudioOnly": True,
        "filenamePattern": "basic",
        "disableMetadata": False,
        "youtubeMusic": False
    }

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": get_user_agent(),
        "Origin": "https://co.wuk.sh",
        "Referer": "https://co.wuk.sh/"
    }

    instances = COBALT_INSTANCES.copy()
    random.shuffle(instances)

    for instance in instances:
        try:
            logger.info(f"🔄 Thử Cobalt instance: {instance}")

            response = requests.post(
                f"{instance}/api/json",
                json=payload,
                headers=headers,
                timeout=15,
                allow_redirects=True
            )

            if response.status_code == 200:
                data = response.json()
                status = data.get('status', '')

                if status == 'redirect' and 'url' in data:
                    audio_url = data['url']
                    logger.info(f"✅ Cobalt thành công (redirect): {audio_url[:80]}...")
                    return audio_url
                elif 'url' in data:
                    audio_url = data['url']
                    logger.info(f"✅ Cobalt thành công: {audio_url[:80]}...")
                    return audio_url
                elif 'audio' in data:
                    audio_url = data['audio']
                    logger.info(f"✅ Cobalt thành công (audio field): {audio_url[:80]}...")
                    return audio_url
                else:
                    logger.warning(f"⚠️ Cobalt không có URL: {data}")
            else:
                logger.warning(f"⚠️ Cobalt status code: {response.status_code}")

        except requests.exceptions.Timeout:
            logger.warning(f"⏰ Cobalt timeout: {instance}")
            continue
        except requests.exceptions.ConnectionError:
            logger.warning(f"🔌 Cobalt connection error: {instance}")
            continue
        except Exception as e:
            logger.warning(f"⚠️ Cobalt lỗi {instance}: {e}")
            continue

    logger.error("❌ Tất cả Cobalt instances đều thất bại")
    return None


def get_direct_stream_url(query):
    """Lấy URL stream trực tiếp, sử dụng cache"""
    cleanup_cache()

    cache_key = query.strip().lower()

    if cache_key in stream_cache:
        cache_time, audio_url = stream_cache[cache_key]
        if time.time() - cache_time < CACHE_DURATION:
            logger.info(f"🎵 Sử dụng cache cho: {query}")
            update_stats('cache', success=True)
            return audio_url
        else:
            del stream_cache[cache_key]

    logger.info(f"🔍 Đang xử lý query: {query}")

    youtube_link = query
    if not (query.startswith("http://") or query.startswith("https://")):
        found_link = search_with_ytmusic(query)
        if found_link:
            youtube_link = found_link
        else:
            logger.error(f"❌ Không tìm thấy bài hát: {query}")
            update_stats('cache', success=False)
            return None

    audio_url = get_best_audio_url_ytdlp(youtube_link)

    if not audio_url:
        logger.info("🔄 yt-dlp thất bại, thử Cobalt...")
        audio_url = get_audio_stream_from_cobalt_fallback(youtube_link)

    if audio_url:
        stream_cache[cache_key] = (time.time(), audio_url)
        logger.info(f"💾 Đã lưu vào cache: {cache_key}")
        return audio_url

    logger.error(f"❌ Không thể lấy audio URL cho: {query}")
    update_stats('cache', success=False)
    return None


def get_video_info(youtube_url):
    """Lấy thông tin video từ YouTube"""
    try:
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'extract_flat': False,
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(youtube_url, download=False)

            return {
                'title': info.get('title', 'Unknown'),
                'artist': info.get('artist') or info.get('uploader', 'Unknown'),
                'duration': info.get('duration', 0),
                'thumbnail': info.get('thumbnail', ''),
                'description': info.get('description', '')[:200] + '...' if info.get('description') else '',
            }
    except:
        return {
            'title': 'Unknown',
            'artist': 'Unknown',
            'duration': 0,
            'thumbnail': '',
            'description': '',
        }


# ========== ENDPOINTS CHÍNH ==========

@app.route('/')
def home():
    """Trang chủ với web player"""
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cache_size = len(stream_cache)

    # Tính uptime
    uptime = datetime.now() - SERVER_STATS['start_time']
    uptime_str = str(uptime).split('.')[0]

    html = f'''
    <!DOCTYPE html>
    <html lang="vi">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>🎵 Xiaozhi Music Server</title>
        <style>
            * {{
                margin: 0;
                padding: 0;
                box-sizing: border-box;
            }}

            body {{
                font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                background: linear-gradient(135deg, #FF6B35 0%, #1E90FF 100%);
                min-height: 100vh;
                color: white;
                padding: 20px;
            }}

            .container {{
                max-width: 1200px;
                margin: 0 auto;
                background: rgba(255, 255, 255, 0.1);
                backdrop-filter: blur(10px);
                border-radius: 20px;
                padding: 30px;
                box-shadow: 0 10px 30px rgba(0, 0, 0, 0.3);
            }}

            .header {{
                text-align: center;
                margin-bottom: 30px;
                padding-bottom: 20px;
                border-bottom: 2px solid rgba(255, 255, 255, 0.2);
            }}

            h1 {{
                font-size: 2.5rem;
                margin-bottom: 10px;
                text-shadow: 2px 2px 4px rgba(0, 0, 0, 0.3);
            }}

            .subtitle {{
                font-size: 1.1rem;
                opacity: 0.9;
                margin-bottom: 20px;
            }}

            .stats-grid {{
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
                gap: 20px;
                margin-bottom: 30px;
            }}

            .stat-card {{
                background: rgba(255, 255, 255, 0.15);
                border-radius: 15px;
                padding: 20px;
                text-align: center;
                transition: transform 0.3s;
            }}

            .stat-card:hover {{
                transform: translateY(-5px);
                background: rgba(255, 255, 255, 0.2);
            }}

            .stat-value {{
                font-size: 2rem;
                font-weight: bold;
                margin: 10px 0;
                color: #4CAF50;
            }}

            .stat-label {{
                font-size: 0.9rem;
                opacity: 0.8;
            }}

            .log-container {{
                background: rgba(0, 0, 0, 0.3);
                border-radius: 15px;
                padding: 20px;
                margin-bottom: 30px;
                max-height: 400px;
                overflow-y: auto;
            }}

            .log-header {{
                display: flex;
                justify-content: space-between;
                align-items: center;
                margin-bottom: 15px;
                padding-bottom: 10px;
                border-bottom: 1px solid rgba(255, 255, 255, 0.2);
            }}

            .log-entry {{
                padding: 10px 15px;
                margin: 5px 0;
                border-radius: 8px;
                font-family: 'Courier New', monospace;
                font-size: 0.9rem;
                border-left: 4px solid #4CAF50;
                background: rgba(255, 255, 255, 0.05);
            }}

            .log-timestamp {{
                color: #aaa;
                font-size: 0.8rem;
                margin-right: 10px;
            }}

            .log-level {{
                padding: 2px 8px;
                border-radius: 4px;
                font-size: 0.8rem;
                font-weight: bold;
                margin-right: 10px;
            }}

            .level-info {{ background: #2196F3; color: white; }}
            .level-warning {{ background: #FF9800; color: white; }}
            .level-error {{ background: #F44336; color: white; }}
            .level-debug {{ background: #9C27B0; color: white; }}

            .search-section {{
                background: rgba(255, 255, 255, 0.15);
                border-radius: 15px;
                padding: 25px;
                margin-bottom: 30px;
            }}

            .search-box {{
                display: flex;
                gap: 10px;
                margin-bottom: 20px;
            }}

            input[type="text"] {{
                flex: 1;
                padding: 15px;
                border: none;
                border-radius: 10px;
                font-size: 16px;
                background: rgba(255, 255, 255, 0.9);
                transition: all 0.3s;
            }}

            input[type="text"]:focus {{
                outline: none;
                background: white;
                box-shadow: 0 0 10px rgba(102, 126, 234, 0.5);
            }}

            button {{
                padding: 15px 30px;
                background: #4CAF50;
                color: white;
                border: none;
                border-radius: 10px;
                cursor: pointer;
                font-size: 16px;
                font-weight: bold;
                transition: all 0.3s;
                display: flex;
                align-items: center;
                gap: 8px;
            }}

            button:hover {{
                background: #45a049;
                transform: translateY(-2px);
                box-shadow: 0 5px 15px rgba(0, 0, 0, 0.2);
            }}

            .player-container {{
                background: rgba(0, 0, 0, 0.2);
                border-radius: 15px;
                padding: 20px;
                margin-top: 20px;
                display: none;
            }}

            audio {{
                width: 100%;
                border-radius: 10px;
                margin-top: 10px;
            }}

            .tabs {{
                display: flex;
                margin-bottom: 20px;
                border-bottom: 2px solid rgba(255, 255, 255, 0.2);
            }}

            .tab {{
                padding: 10px 20px;
                cursor: pointer;
                border-radius: 8px 8px 0 0;
                margin-right: 5px;
                transition: all 0.3s;
            }}

            .tab:hover {{
                background: rgba(255, 255, 255, 0.1);
            }}

            .tab.active {{
                background: rgba(255, 255, 255, 0.2);
                border-bottom: 3px solid #4CAF50;
            }}

            .tab-content {{
                display: none;
            }}

            .tab-content.active {{
                display: block;
            }}

            .endpoint-list {{
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
                gap: 15px;
            }}

            .endpoint-item {{
                background: rgba(255, 255, 255, 0.1);
                padding: 15px;
                border-radius: 10px;
                font-family: monospace;
                font-size: 14px;
                border-left: 4px solid #4CAF50;
            }}

            .method {{
                color: #FFD700;
                font-weight: bold;
            }}

            .url {{
                color: #4CAF50;
                word-break: break-all;
            }}

            .description {{
                font-size: 12px;
                opacity: 0.8;
                margin-top: 5px;
            }}

            .footer {{
                text-align: center;
                margin-top: 30px;
                padding-top: 20px;
                border-top: 1px solid rgba(255, 255, 255, 0.2);
                font-size: 14px;
                opacity: 0.8;
            }}

            @media (max-width: 768px) {{
                .container {{
                    padding: 15px;
                }}

                .stats-grid {{
                    grid-template-columns: 1fr;
                }}

                .search-box {{
                    flex-direction: column;
                }}

                button {{
                    width: 100%;
                    justify-content: center;
                }}
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>🎵 Xiaozhi Audio Server</h1>
                <div class="subtitle">Stream Audio từ YouTube và YouTube Music</div>
            </div>

            <div class="tabs">
                <div class="tab active" onclick="switchTab('dashboard')">📊 Dashboard</div>
                <div class="tab" onclick="switchTab('player')">🎵 Player</div>
                <div class="tab" onclick="switchTab('endpoints')">📡 API</div>
                <div class="tab" onclick="switchTab('logs')">📝 Logs</div>
            </div>

            <!-- DASHBOARD TAB -->
            <div id="dashboard" class="tab-content active">
                <div class="stats-grid">
                    <div class="stat-card">
                        <div class="stat-label">🕒 Uptime</div>
                        <div class="stat-value">{uptime_str}</div>
                        <div class="stat-label">Since {SERVER_STATS['start_time'].strftime('%Y-%m-%d %H:%M')}</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-label">📊 Total Requests</div>
                        <div class="stat-value">{SERVER_STATS['total_requests']}</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-label">✅ Successful Streams</div>
                        <div class="stat-value">{SERVER_STATS['successful_streams']}</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-label">❌ Failed Streams</div>
                        <div class="stat-value">{SERVER_STATS['failed_streams']}</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-label">💾 Cache Size</div>
                        <div class="stat-value">{cache_size}</div>
                        <div class="stat-label">Hits: {SERVER_STATS['cache_hits']} | Misses: {SERVER_STATS['cache_misses']}</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-label">🔄 Last Stream</div>
                        <div class="stat-value">
                            {SERVER_STATS['last_stream_time'].strftime('%H:%M:%S') if SERVER_STATS['last_stream_time'] else 'N/A'}
                        </div>
                        <div class="stat-label">
                            {SERVER_STATS['last_stream_time'].strftime('%Y-%m-%d') if SERVER_STATS['last_stream_time'] else ''}
                        </div>
                    </div>
                </div>

                <div class="log-container">
                    <div class="log-header">
                        <h3>📝 Recent Logs</h3>
                        <button onclick="refreshLogs()" style="padding: 8px 15px; font-size: 14px;">🔄 Refresh</button>
                    </div>
                    <div id="recentLogs">
                        {get_recent_logs_html()}
                    </div>
                </div>

                <div style="display: flex; gap: 15px; margin-top: 20px;">
                    <button onclick="clearCache()" style="background: #FF9800;">🗑️ Clear Cache</button>
                    <button onclick="refreshDashboard()" style="background: #2196F3;">🔄 Refresh Dashboard</button>
                    <button onclick="downloadLogs()" style="background: #9C27B0;">📥 Download Logs</button>
                </div>
            </div>

            <!-- PLAYER TAB -->
            <div id="player" class="tab-content">
                <div class="search-section">
                    <h2 style="margin-bottom: 20px;">🎤 Tìm kiếm bài hát</h2>
                    <div class="search-box">
                        <input type="text" id="songInput" 
                               placeholder="Nhập tên bài hát, ca sĩ hoặc link YouTube...">
                        <button onclick="playSong()">
                            <span>🎵</span> Phát nhạc
                        </button>
                    </div>

                    <div class="player-container" id="playerContainer">
                        <h3>🎧 Đang phát:</h3>
                        <div id="nowPlaying">Chưa có bài hát nào</div>
                        <audio id="audioPlayer" controls>
                            Trình duyệt của bạn không hỗ trợ audio player.
                        </audio>
                    </div>
                </div>
            </div>

            <!-- ENDPOINTS TAB -->
            <div id="endpoints" class="tab-content">
                <h3>📡 API Endpoints</h3>
                <div class="endpoint-list">
                    <div class="endpoint-item">
                        <div><span class="method">GET</span> <span class="url">/</span></div>
                        <div class="description">Trang chủ với web player</div>
                    </div>
                    <div class="endpoint-item">
                        <div><span class="method">GET</span> <span class="url">/stream?q=[bài hát]</span></div>
                        <div class="description">Stream MP3 cho web player</div>
                    </div>
                    <div class="endpoint-item">
                        <div><span class="method">GET</span> <span class="url">/play?q=[bài hát]</span></div>
                        <div class="description">Trang play đơn giản</div>
                    </div>
                    <div class="endpoint-item">
                        <div><span class="method">GET</span> <span class="url">/stream_pcm?song=[bài hát]&singer=[ca sĩ]</span></div>
                        <div class="description">API JSON cho ESP32</div>
                    </div>
                    <div class="endpoint-item">
                        <div><span class="method">GET</span> <span class="url">/esp32_stream?song=[bài hát]&singer=[ca sĩ]</span></div>
                        <div class="description">Stream MP3 cho ESP32</div>
                    </div>
                    <div class="endpoint-item">
                        <div><span class="method">GET</span> <span class="url">/api/music?q=[bài hát]</span></div>
                        <div class="description">API thông tin bài hát (JSON)</div>
                    </div>
                    <div class="endpoint-item">
                        <div><span class="method">GET</span> <span class="url">/download?q=[bài hát]</span></div>
                        <div class="description">Tải bài hát MP3</div>
                    </div>
                    <div class="endpoint-item">
                        <div><span class="method">GET</span> <span class="url">/status</span></div>
                        <div class="description">Trạng thái server (JSON)</div>
                    </div>
                    <div class="endpoint-item">
                        <div><span class="method">GET</span> <span class="url">/logs</span></div>
                        <div class="description">Xem logs server (JSON)</div>
                    </div>
                    <div class="endpoint-item">
                        <div><span class="method">GET</span> <span class="url">/stats</span></div>
                        <div class="description">Thống kê server (JSON)</div>
                    </div>
                </div>
            </div>

            <!-- LOGS TAB -->
            <div id="logs" class="tab-content">
                <div class="log-container" style="max-height: 500px;">
                    <div class="log-header">
                        <h3>📋 Server Logs (Last 200 entries)</h3>
                        <div>
                            <button onclick="refreshLogs()" style="margin-right: 10px; padding: 8px 15px;">🔄 Refresh</button>
                            <button onclick="downloadLogs()" style="background: #9C27B0; padding: 8px 15px;">📥 Download</button>
                        </div>
                    </div>
                    <div id="allLogs">
                        {get_all_logs_html()}
                    </div>
                </div>

                <div style="margin-top: 20px; text-align: center;">
                    <div style="display: inline-flex; gap: 10px; background: rgba(255,255,255,0.1); padding: 10px; border-radius: 10px;">
                        <span style="display: inline-flex; align-items: center; margin-right: 15px;">
                            <span class="level-info" style="margin-right: 5px;">INFO</span> Information
                        </span>
                        <span style="display: inline-flex; align-items: center; margin-right: 15px;">
                            <span class="level-warning" style="margin-right: 5px;">WARN</span> Warning
                        </span>
                        <span style="display: inline-flex; align-items: center; margin-right: 15px;">
                            <span class="level-error" style="margin-right: 5px;">ERROR</span> Error
                        </span>
                        <span style="display: inline-flex; align-items: center;">
                            <span class="level-debug" style="margin-right: 5px;">DEBUG</span> Debug
                        </span>
                    </div>
                </div>
            </div>

            <div class="footer">
                <p>🎶 Xiaozhi Music Server v2.1 | Powered by Flask, yt-dlp & YouTube Music API</p>
                <p style="margin-top: 5px; font-size: 12px;">
                    Server: {current_time} | Uptime: {uptime_str} | Cache: {cache_size} | ESP32 Audio Optimized
                </p>
            </div>
        </div>

        <script>
            function switchTab(tabName) {{
                // Hide all tabs
                document.querySelectorAll('.tab-content').forEach(tab => {{
                    tab.classList.remove('active');
                }});
                document.querySelectorAll('.tab').forEach(tab => {{
                    tab.classList.remove('active');
                }});

                // Show selected tab
                document.getElementById(tabName).classList.add('active');
                document.querySelector(`[onclick="switchTab('${{tabName}}')"]`).classList.add('active');
            }}

            function playSong() {{
                const input = document.getElementById('songInput').value.trim();
                if (!input) {{
                    alert('Vui lòng nhập tên bài hát hoặc link YouTube!');
                    return;
                }}

                const player = document.getElementById('audioPlayer');
                const playerContainer = document.getElementById('playerContainer');
                const nowPlaying = document.getElementById('nowPlaying');

                playerContainer.style.display = 'block';
                nowPlaying.textContent = 'Đang tải: ' + input + '...';

                player.src = '/stream?q=' + encodeURIComponent(input);
                player.load();

                player.play().then(() => {{
                    nowPlaying.textContent = 'Đang phát: ' + input;
                    console.log('Đang phát:', input);
                }}).catch(e => {{
                    nowPlaying.textContent = 'Lỗi phát nhạc: ' + e.message;
                    console.error('Play error:', e);
                }});
            }}

            function refreshDashboard() {{
                location.reload();
            }}

            function refreshLogs() {{
                fetch('/logs?format=html')
                    .then(response => response.text())
                    .then(html => {{
                        const activeTab = document.querySelector('.tab-content.active').id;
                        if (activeTab === 'dashboard') {{
                            document.getElementById('recentLogs').innerHTML = html;
                        }} else if (activeTab === 'logs') {{
                            document.getElementById('allLogs').innerHTML = html;
                        }}
                    }})
                    .catch(error => console.error('Error refreshing logs:', error));
            }}

            function clearCache() {{
                if (confirm('Bạn có chắc muốn xóa toàn bộ cache?')) {{
                    fetch('/clear_cache')
                        .then(response => response.json())
                        .then(data => {{
                            alert(data.message);
                            refreshDashboard();
                        }})
                        .catch(error => console.error('Error clearing cache:', error));
                }}
            }}

            function downloadLogs() {{
                fetch('/logs?format=json')
                    .then(response => response.json())
                    .then(data => {{
                        const blob = new Blob([JSON.stringify(data, null, 2)], {{type: 'application/json'}});
                        const url = window.URL.createObjectURL(blob);
                        const a = document.createElement('a');
                        a.href = url;
                        a.download = `server-logs-${{new Date().toISOString().split('T')[0]}}.json`;
                        document.body.appendChild(a);
                        a.click();
                        document.body.removeChild(a);
                        window.URL.revokeObjectURL(url);
                    }})
                    .catch(error => console.error('Error downloading logs:', error));
            }}

            // Auto-refresh logs every 10 seconds
            setInterval(refreshLogs, 10000);

            // Handle Enter key in search box
            document.getElementById('songInput').addEventListener('keypress', function(e) {{
                if (e.key === 'Enter') {{
                    playSong();
                }}
            }});
        </script>
    </body>
    </html>
    '''
    return html


def get_recent_logs_html():
    """Lấy HTML cho recent logs (10 entries gần nhất)"""
    logs = list(LOG_BUFFER)[-10:]  # Lấy 10 logs gần nhất
    html_parts = []

    for log in reversed(logs):  # Hiển thị mới nhất trước
        level_class = f"level-{log['color']}"
        html_parts.append(f'''
        <div class="log-entry">
            <div style="display: flex; align-items: center; margin-bottom: 5px;">
                <span class="log-timestamp">{log['timestamp']}</span>
                <span class="log-level {level_class}">{log['level']}</span>
                <span style="flex: 1; font-weight: bold;">{html.escape(log['module'])}</span>
            </div>
            <div>{html.escape(log['message'])}</div>
        </div>
        ''')

    return ''.join(
        html_parts) if html_parts else '<div style="text-align: center; padding: 20px; opacity: 0.7;">No logs yet</div>'


def get_all_logs_html():
    """Lấy HTML cho tất cả logs"""
    logs = list(LOG_BUFFER)
    html_parts = []

    for log in reversed(logs):  # Hiển thị mới nhất trước
        level_class = f"level-{log['color']}"
        html_parts.append(f'''
        <div class="log-entry">
            <div style="display: flex; align-items: center; margin-bottom: 5px;">
                <span class="log-timestamp">{log['timestamp']}</span>
                <span class="log-level {level_class}">{log['level']}</span>
                <span style="flex: 1; font-weight: bold;">{html.escape(log['module'])}</span>
            </div>
            <div>{html.escape(log['message'])}</div>
        </div>
        ''')

    return ''.join(
        html_parts) if html_parts else '<div style="text-align: center; padding: 20px; opacity: 0.7;">No logs yet</div>'


@app.route('/play')
def play_page():
    """Trang play đơn giản"""
    query = request.args.get('q', '').strip()
    if not query:
        return '''
        <!DOCTYPE html>
        <html>
        <head>
            <title>Lỗi - Xiaozhi Music</title>
            <style>
                body { font-family: Arial; padding: 50px; text-align: center; }
                .error { background: #ffe6e6; padding: 20px; border-radius: 10px; }
            </style>
        </head>
        <body>
            <div class="error">
                <h2>❌ Thiếu tên bài hát</h2>
                <p>Vui lòng thêm ?q=tên_bài_hát vào URL</p>
                <p>Ví dụ: <code>/play?q=shape+of+you</code></p>
                <p><a href="/">🏠 Quay lại trang chủ</a></p>
            </div>
        </body>
        </html>
        '''

    encoded_query = urllib.parse.quote(query)

    html = f'''
    <!DOCTYPE html>
    <html>
    <head>
        <title>Đang phát: {query}</title>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <style>
            body {{
                font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                min-height: 100vh;
                margin: 0;
                padding: 20px;
                color: white;
                display: flex;
                justify-content: center;
                align-items: center;
            }}

            .player-container {{
                background: rgba(255, 255, 255, 0.1);
                backdrop-filter: blur(10px);
                border-radius: 20px;
                padding: 40px;
                max-width: 600px;
                width: 100%;
                text-align: center;
                box-shadow: 0 10px 30px rgba(0, 0, 0, 0.3);
            }}

            h1 {{
                margin-bottom: 30px;
                font-size: 2rem;
                text-shadow: 2px 2px 4px rgba(0, 0, 0, 0.3);
            }}

            audio {{
                width: 100%;
                margin: 20px 0;
                border-radius: 10px;
            }}

            .controls {{
                display: flex;
                justify-content: center;
                gap: 15px;
                margin-top: 30px;
                flex-wrap: wrap;
            }}

            a {{
                display: inline-block;
                padding: 12px 25px;
                background: rgba(255, 255, 255, 0.2);
                color: white;
                text-decoration: none;
                border-radius: 10px;
                transition: all 0.3s;
                border: 2px solid rgba(255, 255, 255, 0.3);
            }}

            a:hover {{
                background: rgba(255, 255, 255, 0.3);
                transform: translateY(-2px);
            }}

            .download-btn {{
                background: #4CAF50;
                border-color: #45a049;
            }}

            .home-btn {{
                background: #2196F3;
                border-color: #1976D2;
            }}

            @media (max-width: 600px) {{
                .player-container {{
                    padding: 20px;
                }}

                .controls {{
                    flex-direction: column;
                }}

                a {{
                    width: 100%;
                    text-align: center;
                }}
            }}
        </style>
    </head>
    <body>
        <div class="player-container">
            <h1>🎵 Đang phát: {query}</h1>
            <audio controls autoplay>
                <source src="/stream?q={encoded_query}" type="audio/mpeg">
                Trình duyệt của bạn không hỗ trợ audio element.
            </audio>
            <div class="controls">
                <a href="/download?q={encoded_query}" class="download-btn">📥 Tải xuống MP3</a>
                <a href="/" class="home-btn">🏠 Trang chủ</a>
                <a href="/api/music?q={encoded_query}" class="api-btn">📊 API Info</a>
            </div>
        </div>

        <script>
            document.addEventListener('DOMContentLoaded', function() {{
                const audio = document.querySelector('audio');
                audio.play().catch(e => {{
                    console.log('Autoplay blocked:', e);
                }});
            }});
        </script>
    </body>
    </html>
    '''
    return html


@app.route('/stream')
def stream_music():
    """Stream MP3 audio (cho web player)"""
    query = request.args.get('q', '').strip()
    if not query:
        return "❌ Thiếu tên bài hát. Sử dụng: /stream?q=tên_bài_hát", 400

    logger.info(f"🎵 Stream request: {query}")

    audio_url = get_direct_stream_url(query)

    if not audio_url:
        logger.error(f"❌ Không thể lấy stream cho: {query}")
        update_stats('stream', success=False)
        return f"❌ Không tìm thấy bài hát: {query}", 404

    logger.info(f"✅ Bắt đầu stream MP3: {query}")
    update_stats('stream', success=True)

    ffmpeg_cmd = [
        'ffmpeg',
        '-reconnect', '1',
        '-reconnect_streamed', '1',
        '-reconnect_delay_max', '5',
        '-i', audio_url,
        '-f', 'mp3',
        '-acodec', 'libmp3lame',
        '-ar', '44100',
        '-ac', '2',
        '-b:a', '192k',
        '-bufsize', '512k',
        '-max_delay', '500000',
        '-vn',
        '-'
    ]

    def generate():
        process = None
        try:
            logger.info(f"🚀 Bắt đầu FFmpeg stream...")
            process = subprocess.Popen(
                ffmpeg_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=524288
            )

            def read_stderr():
                try:
                    while process.poll() is None:
                        line = process.stderr.readline()
                        if line:
                            logger.debug(f"FFmpeg: {line.decode().strip()}")
                except:
                    pass

            stderr_thread = threading.Thread(target=read_stderr, daemon=True)
            stderr_thread.start()

            bytes_sent = 0
            start_time = time.time()

            while True:
                if process.poll() is not None:
                    logger.info("FFmpeg process đã kết thúc")
                    break

                data = process.stdout.read(8192)
                if not data:
                    if process.poll() is not None:
                        break
                    time.sleep(0.01)
                    continue

                bytes_sent += len(data)
                yield data

                if bytes_sent % (1024 * 1024) == 0:
                    elapsed = time.time() - start_time
                    mb_sent = bytes_sent / (1024 * 1024)
                    logger.info(f"📤 Đã stream: {mb_sent:.1f}MB ({mb_sent / elapsed:.1f} MB/s)")

        except GeneratorExit:
            logger.info("⏹️ Client đã ngắt kết nối stream")
        except Exception as e:
            logger.error(f"❌ Lỗi stream: {e}")
        finally:
            if process:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except:
                    process.kill()
                logger.info("✅ FFmpeg process đã dừng")

    return Response(
        stream_with_context(generate()),
        mimetype='audio/mpeg',
        headers={
            'Content-Type': 'audio/mpeg',
            'Cache-Control': 'no-cache, no-store, must-revalidate',
            'Pragma': 'no-cache',
            'Expires': '0',
            'Accept-Ranges': 'bytes',
            'Content-Disposition': f'inline; filename="{urllib.parse.quote(query)}.mp3"'
        }
    )


# ========== OPTIMIZED ENDPOINTS CHO ESP32 ==========

@app.route('/stream_pcm')
def stream_pcm():
    """Endpoint tương thích với ESP32 (trả về JSON)"""
    song = request.args.get('song', '').strip()
    singer = request.args.get('singer', '').strip()

    if not song:
        return jsonify({
            "error": "Thiếu tham số song",
            "artist": "",
            "title": "",
            "audio_url": "",
            "lyric_url": ""
        }), 400

    query = song
    if singer and singer.lower() != "youtube":
        query = f"{song} {singer}"

    logger.info(f"🎯 ESP32 JSON request: song={song}, singer={singer}")

    audio_url = get_direct_stream_url(query)

    if not audio_url:
        logger.error(f"❌ Không tìm thấy bài hát: {query}")
        update_stats('stream', success=False)
        return jsonify({
            "error": f"Không tìm thấy bài hát: {query}",
            "artist": singer if singer else "",
            "title": song,
            "audio_url": "",
            "lyric_url": ""
        }), 404

    update_stats('stream', success=True)

    try:
        video_info = get_video_info(audio_url)
        title = video_info['title']
        artist = video_info['artist']

        if singer and artist == 'Unknown':
            artist = singer
    except:
        title = song
        artist = singer if singer else "Unknown"

    stream_url = f"http://{request.host}/esp32_stream?song={urllib.parse.quote(song)}&singer={urllib.parse.quote(singer)}"

    logger.info(f"✅ ESP32 JSON response: title={title}, artist={artist}")

    return jsonify({
        "artist": artist,
        "title": title,
        "audio_url": stream_url,
        "lyric_url": "",
        "error": "",
        "bitrate": 128,
        "sample_rate": 44100,
        "channels": 2
    })


@app.route('/esp32_stream')
def esp32_stream():
    """Stream audio cho ESP32 - OPTIMIZED"""
    song = request.args.get('song', '').strip()
    singer = request.args.get('singer', '').strip()

    if not song:
        return "❌ Missing song parameter", 400

    query = song
    if singer and singer.lower() != "youtube":
        query = f"{song} {singer}"

    logger.info(f"🔌 ESP32 Stream request: {query}")

    audio_url = get_direct_stream_url(query)

    if not audio_url:
        logger.error(f"❌ Không tìm thấy bài hát: {query}")
        update_stats('stream', success=False)
        return "❌ Song not found", 404

    logger.info(f"✅ ESP32 Audio URL: {audio_url[:100]}...")
    update_stats('stream', success=True)

    # ⭐ PERFECT BALANCE: RAM vs Audio Quality
    ffmpeg_cmd = [
        'ffmpeg',
        '-reconnect', '1',
        '-reconnect_streamed', '1',
        '-reconnect_delay_max', '5',
        '-i', audio_url,
        '-f', 'mp3',
        '-acodec', 'libmp3lame',
        '-ar', '24000',  # ⭐ 24kHz (tối ưu: ghi nhận tần số tới 12kHz, đủ cho nhạc)
        '-ac', '2',  # ⭐ Stereo (chất lượng, không bị kém)
        '-b:a', '80k',  # ⭐ 80kbps (cân bằng lí tưởng: chất lượng tốt + RAM đủ)
        '-q:a', '7',  # ⭐ Quality 7 = ~80kbps VBR (động, linh hoạt)
        '-bufsize', '160k',  # ⭐ Buffer 160KB (đủ cho streaming mượt)
        '-fflags', '+discardcorrupt',
        '-max_muxing_queue_size', '640',
        '-vn',
        '-'
    ]

    def generate():
        process = None
        try:
            logger.info(f"🚀 Bắt đầu FFmpeg stream cho ESP32...")
            process = subprocess.Popen(
                ffmpeg_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=262144
            )

            def read_stderr():
                try:
                    while process.poll() is None:
                        line = process.stderr.readline()
                        if b'error' in line.lower() or b'invalid' in line.lower():
                            logger.warning(f"FFmpeg: {line.decode().strip()}")
                except:
                    pass

            stderr_thread = threading.Thread(target=read_stderr, daemon=True)
            stderr_thread.start()

            bytes_sent = 0
            chunk_size = 4096
            chunks_sent = 0
            start_time = time.time()

            while True:
                if process.poll() is not None:
                    logger.info("FFmpeg process đã kết thúc")
                    final = process.stdout.read()
                    if final:
                        yield final
                    break

                try:
                    data = process.stdout.read(chunk_size)
                    if not data:
                        time.sleep(0.001)
                        continue

                    bytes_sent += len(data)
                    chunks_sent += 1
                    yield data

                    if chunks_sent % 50 == 0:
                        elapsed = time.time() - start_time
                        kb_sent = bytes_sent / 1024
                        kb_per_sec = kb_sent / elapsed if elapsed > 0 else 0
                        logger.info(f"📤 ESP32: {kb_sent:.1f}KB ({kb_per_sec:.1f} KB/s)")

                except Exception as e:
                    logger.error(f"❌ Read error: {e}")
                    break

        except GeneratorExit:
            logger.info("⏹️ ESP32 client disconnected")
        except Exception as e:
            logger.error(f"❌ Stream error: {e}")
        finally:
            if process:
                try:
                    process.terminate()
                    process.wait(timeout=2)
                except:
                    try:
                        process.kill()
                    except:
                        pass
                logger.info("✅ FFmpeg stopped")

    return Response(
        stream_with_context(generate()),
        mimetype='audio/mpeg',
        headers={
            'Content-Type': 'audio/mpeg',
            'Cache-Control': 'no-cache, no-store, must-revalidate',
            'Pragma': 'no-cache',
            'Connection': 'keep-alive',
            'Transfer-Encoding': 'chunked',
            'X-Content-Type-Options': 'nosniff'
        }
    )


# ========== CÁC ENDPOINTS KHÁC ==========

@app.route('/api/music')
def api_music():
    """API trả về JSON với thông tin bài hát"""
    query = request.args.get('q', '').strip()
    if not query:
        return jsonify({
            "success": False,
            "error": "Thiếu tên bài hát",
            "code": 400
        }), 400

    logger.info(f"📊 API request: {query}")

    audio_url = get_direct_stream_url(query)

    if not audio_url:
        return jsonify({
            "success": False,
            "error": f"Không tìm thấy bài hát: {query}",
            "code": 404
        }), 404

    video_info = get_video_info(audio_url)

    return jsonify({
        "success": True,
        "data": {
            "query": query,
            "title": video_info['title'],
            "artist": video_info['artist'],
            "duration": video_info['duration'],
            "thumbnail": video_info['thumbnail'],
            "description": video_info['description'],
            "audio_url": audio_url,
            "stream_url": f"/stream?q={urllib.parse.quote(query)}",
            "download_url": f"/download?q={urllib.parse.quote(query)}",
            "api_url": f"/api/music?q={urllib.parse.quote(query)}"
        },
        "timestamp": int(time.time())
    })


@app.route('/download')
def download_music():
    """Tải bài hát dưới dạng file MP3"""
    query = request.args.get('q', '').strip()
    if not query:
        return "❌ Thiếu tên bài hát", 400

    logger.info(f"📥 Download request: {query}")

    audio_url = get_direct_stream_url(query)

    if not audio_url:
        return "❌ Không tìm thấy bài hát", 404

    try:
        video_info = get_video_info(audio_url)
        filename = f"{video_info['title']} - {video_info['artist']}".replace('/', '_').replace('\\', '_')
        if len(filename) > 100:
            filename = filename[:100]
    except:
        filename = urllib.parse.quote(query)

    def generate():
        ffmpeg_cmd = [
            'ffmpeg',
            '-i', audio_url,
            '-f', 'mp3',
            '-acodec', 'libmp3lame',
            '-ar', '44100',
            '-ac', '2',
            '-b:a', '192k',
            '-vn',
            '-'
        ]

        process = subprocess.Popen(ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        try:
            bytes_sent = 0
            while True:
                data = process.stdout.read(8192)
                if not data:
                    break
                bytes_sent += len(data)
                yield data

                if bytes_sent % (1024 * 1024) == 0:
                    logger.debug(f"📥 Đã gửi {bytes_sent // (1024 * 1024)}MB")

        except Exception as e:
            logger.error(f"❌ Lỗi download stream: {e}")
        finally:
            process.terminate()
            process.wait()

    headers = {
        'Content-Disposition': f'attachment; filename="{filename}.mp3"',
        'Content-Type': 'audio/mpeg',
        'Cache-Control': 'no-cache, no-store'
    }

    return Response(stream_with_context(generate()), headers=headers)


@app.route('/status')
def status():
    """Check server status"""
    cleanup_cache()

    uptime = datetime.now() - SERVER_STATS['start_time']

    return jsonify({
        "status": "running",
        "server": "Xiaozhi Music Server",
        "version": "2.1",
        "uptime_seconds": int(uptime.total_seconds()),
        "uptime_human": str(uptime).split('.')[0],
        "start_time": SERVER_STATS['start_time'].isoformat(),
        "current_time": datetime.now().isoformat(),
        "cache_size": len(stream_cache),
        "cache_max_size": MAX_CACHE_SIZE,
        "cache_duration_seconds": CACHE_DURATION,
        "stats": SERVER_STATS,
        "esp32_optimization": "enabled",
        "endpoints": [
            {"method": "GET", "path": "/", "description": "Home page with dashboard"},
            {"method": "GET", "path": "/stream?q=<query>", "description": "Web stream"},
            {"method": "GET", "path": "/esp32_stream?song=<song>&singer=<singer>", "description": "ESP32 stream"},
            {"method": "GET", "path": "/stream_pcm?song=<song>&singer=<singer>", "description": "ESP32 JSON API"},
            {"method": "GET", "path": "/api/music?q=<query>", "description": "Music info API"},
            {"method": "GET", "path": "/status", "description": "Server status"},
            {"method": "GET", "path": "/stats", "description": "Server statistics"},
            {"method": "GET", "path": "/logs", "description": "Server logs"}
        ],
        "timestamp": int(time.time())
    })


@app.route('/stats')
def stats():
    """Get server statistics"""
    uptime = datetime.now() - SERVER_STATS['start_time']

    stats_data = {
        "server_stats": SERVER_STATS,
        "cache_stats": {
            "current_size": len(stream_cache),
            "max_size": MAX_CACHE_SIZE,
            "duration_seconds": CACHE_DURATION,
            "hit_rate": SERVER_STATS['cache_hits'] / max(SERVER_STATS['cache_hits'] + SERVER_STATS['cache_misses'],
                                                         1) * 100
        },
        "performance": {
            "uptime_seconds": int(uptime.total_seconds()),
            "requests_per_hour": SERVER_STATS['total_requests'] / (
                        uptime.total_seconds() / 3600) if uptime.total_seconds() > 0 else 0,
            "success_rate": SERVER_STATS['successful_streams'] / max(
                SERVER_STATS['successful_streams'] + SERVER_STATS['failed_streams'], 1) * 100
        },
        "log_stats": {
            "total_logs": len(LOG_BUFFER),
            "max_logs": LOG_BUFFER.maxlen if hasattr(LOG_BUFFER, 'maxlen') else 200
        }
    }

    return jsonify(stats_data)


@app.route('/logs')
def get_logs():
    """Get server logs"""
    format_type = request.args.get('format', 'json')

    logs_list = list(LOG_BUFFER)

    if format_type == 'html':
        return get_all_logs_html()
    else:
        return jsonify({
            "total_logs": len(logs_list),
            "max_logs": LOG_BUFFER.maxlen if hasattr(LOG_BUFFER, 'maxlen') else 200,
            "logs": logs_list
        })


@app.route('/clear_cache')
def clear_cache():
    """Xóa cache"""
    global stream_cache
    count = len(stream_cache)
    stream_cache.clear()

    logger.info(f"🗑️ Đã xóa toàn bộ cache ({count} mục)")

    return jsonify({
        "success": True,
        "message": f"Đã xóa {count} mục cache",
        "cache_size": 0,
        "timestamp": int(time.time())
    })


@app.route('/debug')
def debug():
    """Debug endpoint"""
    return jsonify({
        "client": {
            "ip": request.remote_addr,
            "user_agent": request.headers.get('User-Agent'),
            "method": request.method,
        },
        "server": {
            "host": request.host,
            "timestamp": int(time.time()),
        },
        "cache": {
            "size": len(stream_cache),
            "max_size": MAX_CACHE_SIZE
        }
    })


@app.errorhandler(404)
def not_found(error):
    """Xử lý 404 error"""
    return jsonify({
        "error": "Not Found",
        "message": "Endpoint không tồn tại",
        "path": request.path
    }), 404


@app.errorhandler(500)
def internal_error(error):
    """Xử lý 500 error"""
    logger.error(f"❌ Internal Server Error: {error}")
    return jsonify({
        "error": "Internal Server Error",
        "message": "Đã xảy ra lỗi server",
        "timestamp": int(time.time())
    }), 500


# ========== KHỞI CHẠY SERVER ==========

if __name__ == '__main__':
    app_start_time = time.time()

    try:
        result = subprocess.run(['ffmpeg', '-version'], capture_output=True, text=True)
        if result.returncode == 0:
            logger.info("✅ FFmpeg đã sẵn sàng")
            lines = result.stdout.split('\n')
            if lines:
                logger.info(f"🔧 FFmpeg version: {lines[0]}")
        else:
            logger.error("❌ FFmpeg không khả dụng")
            exit(1)
    except FileNotFoundError:
        logger.error("❌ FFmpeg không được cài đặt")
        logger.info("📝 Hướng dẫn cài đặt ffmpeg:")
        logger.info("  Ubuntu/Debian: sudo apt-get install ffmpeg")
        logger.info("  macOS: brew install ffmpeg")
        logger.info("  Windows: Tải từ https://ffmpeg.org/download.html")
        exit(1)

    try:
        import yt_dlp

        logger.info(f"✅ yt-dlp sẵn sàng")
    except ImportError as e:
        logger.error(f"❌ Không thể import yt-dlp: {e}")
        exit(1)

    try:
        import flask

        logger.info(f"✅ Flask sẵn sàng")
    except ImportError as e:
        logger.error(f"❌ Không thể import Flask: {e}")
        exit(1)

    logger.info("=" * 60)
    logger.info("🎵 Xiaozhi Audio Server v2.1 - MINHHOANGCODIENTU")
    logger.info("📡 Đang khởi động server...")
    logger.info(f"🕒 Thời gian: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"📊 Log dashboard enabled: {len(LOG_BUFFER)} logs in buffer")
    logger.info("=" * 60)

    app.run(
        host='0.0.0.0',
        port=7879,
        debug=False,
        threaded=True,
        use_reloader=False
    )