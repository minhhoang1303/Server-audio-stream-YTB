![Uploading image.png…]()

🎵 Xiaozhi Audio Server v2.1
Stream Audio từ YouTube và YouTube Music với Dashboard tích hợp

https://img.shields.io/badge/Python-3.8+-blue.svg
https://img.shields.io/badge/Flask-2.0+-green.svg
https://img.shields.io/badge/License-MIT-yellow.svg

📋 Giới thiệu
Xiaozhi Audio Server là một ứng dụng Flask mạnh mẽ cho phép stream và tải nhạc từ YouTube/YouTube Music. Ứng dụng tích hợp dashboard real-time với thống kê chi tiết, log system và hỗ trợ tối ưu cho ESP32.

✨ Tính năng nổi bật
🎛️ Dashboard tích hợp
Real-time Statistics: Uptime, total requests, cache hits/misses

Log System: Hiển thị logs trực tiếp 

Cache Management: Quản lý và xóa cache từ dashboard

API Documentation: Danh sách endpoints đầy đủ

🎵 Chức năng chính
🔍 Tìm kiếm thông minh: Tìm bài hát qua YouTube Music API

📥 Stream MP3: Stream audio trực tiếp từ YouTube

💾 Cache System: Cache kết quả tìm kiếm (30 phút, tối đa 100 items)

⚡ ESP32 Optimized: Endpoint tối ưu cho thiết bị nhúng

📊 Multiple Formats: MP3 streaming với bitrate linh hoạt

🔧 Kỹ thuật
Fallback System: Tự động chuyển đổi giữa yt-dlp và Cobalt API

User-Agent Rotation: Xoay vòng user-agent để tránh bị block

Connection Recovery: Tự động reconnect khi mất kết nối

Error Handling: Xử lý lỗi toàn diện với logging chi tiết

🚀 Cài đặt nhanh
Yêu cầu hệ thống
Python 3.8+

FFmpeg

Internet connection

Cài đặt dependencies
bash
# Tạo virtual environment
python3 -m venv venv
source venv/bin/activate  # Linux/Mac
# hoặc venv\Scripts\activate  # Windows

# Cài đặt thư viện
pip install -r requirements.txt
requirements.txt:

text
flask==2.3.3
requests==2.31.0
ytmusicapi==1.0.2
yt-dlp==2023.10.13
Cài đặt FFmpeg
bash
# Ubuntu/Debian
sudo apt-get install ffmpeg

# macOS
brew install ffmpeg

# Windows: Tải từ https://ffmpeg.org/download.html
🏃‍♂️ Khởi động server
bash
# Chạy server
python Dashboard_music.py

# Server sẽ chạy tại: http://localhost:7879
