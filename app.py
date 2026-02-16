# -*- coding: utf-8 -*-
import sys
import io
import re

# Фикс для Windows консоли
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

from flask import Flask, request, send_file, jsonify
from flask_cors import CORS
import os
import uuid
import threading
from datetime import datetime, timedelta
import logging
import time
import subprocess

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('server_debug.log', encoding='utf-8')
    ]
)

logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)

DOWNLOAD_FOLDER = 'downloads'
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

download_tasks = {}

class ProgressParser:
    def __init__(self, task_id):
        self.task_id = task_id
        self.last_percent = 0
        
    def parse_line(self, line):
        if '[download]' in line:
            percent_match = re.search(r'(\d+\.?\d*)%', line)
            if percent_match:
                percent = float(percent_match.group(1))
                speed_match = re.search(r'at\s+([\d\.]+\s*\w+/s)', line)
                speed = speed_match.group(1) if speed_match else 'N/A'
                eta_match = re.search(r'ETA\s+(\d+:\d+|\w+)', line)
                eta = eta_match.group(1) if eta_match else 'N/A'
                
                if self.task_id in download_tasks:
                    download_tasks[self.task_id].update({
                        'progress': percent,
                        'speed': speed,
                        'eta': eta,
                        'status': 'downloading'
                    })
                    
                    if int(percent) % 10 == 0 and int(percent) != self.last_percent:
                        logger.info(f"Task {self.task_id}: {percent}% at {speed}")
                        self.last_percent = int(percent)
                        
                return True
        return False

def check_aria2():
    try:
        result = subprocess.run(['aria2c', '--version'], capture_output=True, text=True)
        return result.returncode == 0
    except:
        return False

HAVE_ARIA2 = check_aria2()

# ==================== ФУНКЦИЯ ОЧИСТКИ REFERER ====================

def clean_referer(referer):
    """Очищает referer от дубликатов и исправляет формат"""
    if not referer:
        return 'https://megacloud.blog/'
    
    # Убираем двойные слеши в домене
    referer = re.sub(r'(https?://)[^/]+', lambda m: m.group(1) + m.group(0).split('//')[1].split('/')[0], referer)
    
    # Если есть дублирование типа aniv//anivox.fun
    parts = referer.split('//')
    if len(parts) > 2:
        # Берем последнюю часть после //
        domain = parts[-1].split('/')[0]
        protocol = parts[0] + '//'
        referer = protocol + domain + '/'
    
    # Убираем множественные слеши
    referer = re.sub(r'(?<!:)//+', '/', referer)
    
    # Добавляем слеш в конце если нет
    if not referer.endswith('/'):
        referer += '/'
    
    # Проверяем что протокол есть
    if not referer.startswith('http'):
        referer = 'https://' + referer.lstrip('/')
    
    return referer

# ==================== ЭНДПОИНТЫ ====================

@app.route('/ping')
def ping():
    return jsonify({'status': 'pong'})

@app.route('/active')
def get_active():
    active = []
    to_delete = []
    
    for task_id, task in download_tasks.items():
        if task['status'] in ['downloading', 'processing', 'started']:
            active.append({
                'task_id': task_id,
                'title': task.get('title', 'Unknown'),
                'progress': task.get('progress', 0),
                'speed': task.get('speed', 'N/A'),
                'eta': task.get('eta', 'N/A'),
                'status': task['status']
            })
        elif task['status'] == 'completed':
            completed_time = datetime.fromisoformat(task.get('completed_at', '2000-01-01'))
            if datetime.now() - completed_time < timedelta(minutes=5):
                active.append({
                    'task_id': task_id,
                    'title': task.get('title', 'Unknown'),
                    'progress': 100,
                    'speed': 'Complete',
                    'eta': 'Done',
                    'status': 'completed'
                })
            else:
                to_delete.append(task_id)
    
    for task_id in to_delete:
        if task_id in download_tasks:
            del download_tasks[task_id]
    
    return jsonify(active)

@app.route('/formats', methods=['POST'])
def get_formats():
    data = request.json
    url = data.get('url')
    referer = data.get('referer', 'https://megacloud.blog/')
    
    if not url:
        return jsonify({'error': 'URL is required'}), 400
    
    # ✅ ОЧИЩАЕМ REFERER
    clean_ref = clean_referer(referer)
    
    logger.info("="*60)
    logger.info(f"GET FORMATS: {url[:100]}...")
    logger.info(f"Original referer: {referer}")
    logger.info(f"Cleaned referer: {clean_ref}")
    
    try:
        cmd = [
            'yt-dlp',
            '--extractor-args', 'generic:impersonate=chrome-120',
            '--referer', clean_ref,
            '--list-formats',
            url
        ]
        
        # Показываем команду с кавычками
        cmd_str = f'yt-dlp --extractor-args "generic:impersonate=chrome-120" --referer "{clean_ref}" --list-formats "{url}"'
        logger.info(f"Running: {cmd_str}")
        
        process = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding='utf-8',
            timeout=30
        )
        
        output = process.stdout + process.stderr
        
        formats = []
        lines = output.split('\n')
        
        for line in lines:
            if line and line[0].isdigit():
                parts = line.split()
                if len(parts) >= 2:
                    format_id = parts[0]
                    format_info = ' '.join(parts[1:])
                    
                    format_type = 'unknown'
                    if 'video' in format_info.lower():
                        format_type = 'video'
                    elif 'audio' in format_info.lower():
                        format_type = 'audio'
                    
                    formats.append({
                        'id': format_id,
                        'info': format_info[:100],
                        'type': format_type
                    })
        
        title = url.split('/')[-1][:50]
        try:
            title_cmd = [
                'yt-dlp',
                '--extractor-args', 'generic:impersonate=chrome-120',
                '--referer', clean_ref,
                '--get-title',
                url
            ]
            title_process = subprocess.run(title_cmd, capture_output=True, text=True, timeout=10)
            if title_process.returncode == 0 and title_process.stdout.strip():
                title = title_process.stdout.strip()
        except:
            pass
        
        return jsonify({
            'formats': formats,
            'count': len(formats),
            'title': title
        })
        
    except subprocess.TimeoutExpired:
        logger.error("Formats timeout")
        return jsonify({'error': 'Request timeout'}), 504
    except Exception as e:
        logger.error(f"Formats error: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/download', methods=['POST'])
def download_video():
    data = request.json
    url = data.get('url')
    format_id = data.get('format_id', 'best')
    referer = data.get('referer', 'https://megacloud.blog/')
    
    if not url:
        return jsonify({'error': 'URL is required'}), 400
    
    # ✅ ОЧИЩАЕМ REFERER
    clean_ref = clean_referer(referer)
    
    logger.info("="*60)
    logger.info(f"DOWNLOAD: {url[:100]}...")
    logger.info(f"FORMAT: {format_id}")
    logger.info(f"Original referer: {referer}")
    logger.info(f"Cleaned referer: {clean_ref}")
    
    task_id = str(uuid.uuid4())
    output_template = os.path.join(DOWNLOAD_FOLDER, f'{task_id}.%(ext)s')
    
    title = url.split('/')[-1][:50]
    try:
        title_cmd = [
            'yt-dlp',
            '--extractor-args', 'generic:impersonate=chrome-120',
            '--referer', clean_ref,
            '--get-title',
            url
        ]
        title_process = subprocess.run(title_cmd, capture_output=True, text=True, timeout=10)
        if title_process.returncode == 0 and title_process.stdout.strip():
            title = title_process.stdout.strip()
    except:
        pass
    
    download_tasks[task_id] = {
        'status': 'started',
        'progress': 0,
        'speed': 'N/A',
        'eta': 'N/A',
        'title': title,
        'created_at': datetime.now().isoformat()
    }
    
    def download_task():
        try:
            # 🔥 КОМАНДА С ОЧИЩЕННЫМ REFERER
            cmd = [
                'yt-dlp',
                '--extractor-args', 'generic:impersonate=chrome-120',
                '--referer', clean_ref,
                '--concurrent-fragments', '10',
                '-o', output_template,
                '-f', format_id,
                url
            ]
            
            # Показываем команду с кавычками
            cmd_str = f'yt-dlp --extractor-args "generic:impersonate=chrome-120" --referer "{clean_ref}" -o "{output_template}" -f {format_id} "{url}"'
            logger.info(f"Task {task_id}: Running: {cmd_str}")
            
            download_tasks[task_id]['status'] = 'downloading'
            
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding='utf-8',
                bufsize=1
            )
            
            parser = ProgressParser(task_id)
            
            for line in process.stdout:
                line = line.strip()
                if line:
                    parser.parse_line(line)
                    logger.debug(f"Task {task_id}: {line}")
            
            return_code = process.wait()
            
            if return_code == 0:
                filename = None
                for file in os.listdir(DOWNLOAD_FOLDER):
                    if file.startswith(task_id):
                        filename = os.path.join(DOWNLOAD_FOLDER, file)
                        break
                
                if filename and os.path.exists(filename):
                    file_size = os.path.getsize(filename)
                    logger.info(f"Task {task_id}: Download completed - {file_size/1024/1024:.1f} MB")
                    
                    download_tasks[task_id].update({
                        'status': 'completed',
                        'progress': 100,
                        'file': filename,
                        'size': file_size,
                        'completed_at': datetime.now().isoformat()
                    })
                else:
                    raise Exception("File not found after download")
            else:
                raise Exception(f"yt-dlp exited with code {return_code}")
                
        except Exception as e:
            logger.error(f"Task {task_id}: Download error - {str(e)}")
            download_tasks[task_id].update({
                'status': 'error',
                'error': str(e)
            })
    
    thread = threading.Thread(target=download_task, daemon=True)
    thread.start()
    
    return jsonify({'task_id': task_id})

@app.route('/status/<task_id>')
def get_status(task_id):
    if task_id in download_tasks:
        task = download_tasks[task_id].copy()
        if 'file' in task:
            del task['file']
        return jsonify(task)
    return jsonify({'status': 'not_found'}), 404

@app.route('/file/<task_id>')
def get_file(task_id):
    if task_id in download_tasks and download_tasks[task_id]['status'] == 'completed':
        file_path = download_tasks[task_id].get('file')
        
        if file_path and os.path.exists(file_path):
            logger.info(f"Sending file: {os.path.basename(file_path)}")
            
            response = send_file(
                file_path,
                as_attachment=True,
                download_name=os.path.basename(file_path),
                mimetype='video/mp4'
            )
            
            @response.call_on_close
            def cleanup():
                try:
                    time.sleep(2)
                    if os.path.exists(file_path):
                        os.remove(file_path)
                        logger.info(f"Deleted: {os.path.basename(file_path)}")
                    if task_id in download_tasks:
                        del download_tasks[task_id]
                except Exception as e:
                    logger.error(f"Cleanup error: {e}")
            
            return response
    
    return jsonify({'error': 'File not ready'}), 404

@app.route('/cleanup', methods=['POST'])
def force_cleanup():
    try:
        count = 0
        for filename in os.listdir(DOWNLOAD_FOLDER):
            filepath = os.path.join(DOWNLOAD_FOLDER, filename)
            if os.path.isfile(filepath):
                os.remove(filepath)
                count += 1
                logger.info(f"Cleaned up: {filename}")
        return jsonify({'message': f'Cleaned up {count} files'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    print("\n" + "="*70)
    print("VIDEO DOWNLOAD SERVER STARTING...")
    print("="*70)
    print(f"Download folder: {os.path.abspath(DOWNLOAD_FOLDER)}")
    print(f"Server URL: http://localhost:5000")
    print("✅ FEATURES:")
    print("  • Auto-clean referer (fixes duplicates like aniv//anivox.fun)")
    print("  • Quoted arguments")
    print("  • Trailing slash added automatically")
    print(f"  • aria2c: {'AVAILABLE' if HAVE_ARIA2 else 'not found'}")
    print("="*70 + "\n")
    
    app.run(host='0.0.0.0', port=5000, debug=True, threaded=True)
