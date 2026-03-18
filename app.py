import os

# Railway environment fix
PORT = int(os.environ.get("PORT", 3000))
os.environ["PYTHONUNBUFFERED"] = "1"import os
import subprocess
import psutil
import json
import secrets
import shutil
import threading
import time
from datetime import datetime, timedelta
from functools import wraps
from flask import Flask, request, jsonify, session, redirect, send_from_directory, render_template_string

app = Flask(__name__)
app.secret_key = secrets.token_hex(32)
app.permanent_session_lifetime = timedelta(days=30)

# Directory structure
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "bots")
LOG_DIR = os.path.join(BASE_DIR, "logs")
CONFIG_DIR = os.path.join(BASE_DIR, "config")

for dir_path in [UPLOAD_DIR, LOG_DIR, CONFIG_DIR]:
    os.makedirs(dir_path, exist_ok=True)

# Config files
USERS_FILE = os.path.join(CONFIG_DIR, "users.json")
UPLOADS_FILE = os.path.join(CONFIG_DIR, "uploads.json")
SETTINGS_FILE = os.path.join(CONFIG_DIR, "settings.json")

# Default settings
DEFAULT_SETTINGS = {
    "global_upload_limit": 10,
    "max_file_size": 50,  # MB
    "allowed_extensions": [".py", ".js", ".sh"],
    "session_timeout": 30,  # days
    "maintenance_mode": False
}

# Initialize config files
def init_config():
    if not os.path.exists(USERS_FILE):
        # Create default admin user
        default_users = {
            "admin": {
                "password": "Admin@123",
                "is_admin": True,
                "upload_limit": 100,
                "created_at": datetime.now().isoformat(),
                "last_login": None
            }
        }
        with open(USERS_FILE, 'w') as f:
            json.dump(default_users, f, indent=2)
    
    if not os.path.exists(UPLOADS_FILE):
        with open(UPLOADS_FILE, 'w') as f:
            json.dump({}, f, indent=2)
    
    if not os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE, 'w') as f:
            json.dump(DEFAULT_SETTINGS, f, indent=2)

init_config()

# Running bots tracking
running_bots = {}
bot_processes = {}

# ==================== Helper Functions ====================

def load_users():
    with open(USERS_FILE, 'r') as f:
        return json.load(f)

def save_users(users):
    with open(USERS_FILE, 'w') as f:
        json.dump(users, f, indent=2)

def load_uploads():
    with open(UPLOADS_FILE, 'r') as f:
        return json.load(f)

def save_uploads(uploads):
    with open(UPLOADS_FILE, 'w') as f:
        json.dump(uploads, f, indent=2)

def load_settings():
    with open(SETTINGS_FILE, 'r') as f:
        return json.load(f)

def save_settings(settings):
    with open(SETTINGS_FILE, 'w') as f:
        json.dump(settings, f, indent=2)

def get_user_upload_count(username):
    uploads = load_uploads()
    return len(uploads.get(username, []))

def get_user_upload_limit(username):
    users = load_users()
    user = users.get(username, {})
    return user.get('upload_limit', load_settings()['global_upload_limit'])

def format_size(size_bytes):
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} TB"

def clean_old_logs():
    """Clean logs older than 7 days"""
    try:
        cutoff = datetime.now() - timedelta(days=7)
        for root, dirs, files in os.walk(LOG_DIR):
            for file in files:
                if file.endswith('.log'):
                    file_path = os.path.join(root, file)
                    file_time = datetime.fromtimestamp(os.path.getmtime(file_path))
                    if file_time < cutoff:
                        os.remove(file_path)
    except:
        pass

# Start log cleaner thread
def start_log_cleaner():
    while True:
        time.sleep(3600)  # Run every hour
        clean_old_logs()

threading.Thread(target=start_log_cleaner, daemon=True).start()

# ==================== Bot Management ====================

def start_bot(filename, username):
    user_dir = os.path.join(UPLOAD_DIR, username)
    filepath = os.path.join(user_dir, filename)
    
    if not os.path.exists(filepath):
        return None, "File not found"
    
    # Check if already running
    for bot_id, bot in running_bots.items():
        if bot['username'] == username and bot['filename'] == filename:
            if bot_processes.get(bot_id, {}).poll() is None:
                return None, "Bot already running"
    
    # Create log directory
    bot_log_dir = os.path.join(LOG_DIR, username)
    os.makedirs(bot_log_dir, exist_ok=True)
    
    # Start bot
    log_path = os.path.join(bot_log_dir, f"{filename}.log")
    log_file = open(log_path, "a")
    
    # Write start marker
    log_file.write(f"\n{'='*50}\n")
    log_file.write(f"Bot started at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    log_file.write(f"{'='*50}\n\n")
    log_file.flush()
    
    try:
        proc = subprocess.Popen(
            ["python", filepath],
            stdout=log_file,
            stderr=log_file,
            text=True
        )
        
        bot_id = f"{username}_{filename}_{int(time.time())}"
        running_bots[bot_id] = {
            "filename": filename,
            "username": username,
            "start_time": datetime.now().isoformat(),
            "log_path": log_path,
            "pid": proc.pid
        }
        bot_processes[bot_id] = proc
        
        return bot_id, "Bot started successfully"
    except Exception as e:
        return None, f"Failed to start bot: {str(e)}"

def stop_bot(bot_id):
    if bot_id not in running_bots or bot_id not in bot_processes:
        return False, "Bot not found"
    
    try:
        proc = bot_processes[bot_id]
        
        # Try graceful termination first
        proc.terminate()
        
        # Wait for process to end
        for _ in range(10):  # Wait up to 5 seconds
            if proc.poll() is not None:
                break
            time.sleep(0.5)
        
        # Force kill if still running
        if proc.poll() is None:
            proc.kill()
        
        # Write stop marker to log
        bot = running_bots[bot_id]
        with open(bot['log_path'], 'a') as f:
            f.write(f"\n{'='*50}\n")
            f.write(f"Bot stopped at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"{'='*50}\n\n")
        
        # Cleanup
        del running_bots[bot_id]
        del bot_processes[bot_id]
        
        return True, "Bot stopped successfully"
    except Exception as e:
        return False, f"Failed to stop bot: {str(e)}"

def get_bot_status(bot_id):
    if bot_id not in bot_processes:
        return "stopped"
    
    proc = bot_processes[bot_id]
    if proc.poll() is None:
        return "running"
    return "stopped"

# ==================== Authentication Decorators ====================

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            if request.path.startswith('/api/'):
                return jsonify({"error": "Unauthorized"}), 401
            return redirect('/login')
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            if request.path.startswith('/api/'):
                return jsonify({"error": "Unauthorized"}), 401
            return redirect('/login')
        
        users = load_users()
        user = users.get(session['user_id'], {})
        if not user.get('is_admin', False):
            if request.path.startswith('/api/'):
                return jsonify({"error": "Admin access required"}), 403
            return redirect('/dashboard')
        
        return f(*args, **kwargs)
    return decorated_function

# ==================== HTML Templates ====================

LOGIN_PAGE = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Sulav Hosting - Login</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
        }

        body {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
        }

        .login-container {
            background: white;
            border-radius: 20px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
            width: 90%;
            max-width: 400px;
            padding: 40px;
        }

        .logo {
            text-align: center;
            margin-bottom: 30px;
        }

        .logo h1 {
            font-size: 2rem;
            background: linear-gradient(135deg, #667eea, #764ba2);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            margin-bottom: 10px;
        }

        .logo p {
            color: #666;
            font-size: 0.9rem;
        }

        .form-group {
            margin-bottom: 20px;
        }

        .form-group label {
            display: block;
            margin-bottom: 5px;
            color: #333;
            font-weight: 500;
        }

        .form-group input {
            width: 100%;
            padding: 12px;
            border: 2px solid #e0e0e0;
            border-radius: 10px;
            font-size: 1rem;
            transition: border-color 0.3s;
        }

        .form-group input:focus {
            outline: none;
            border-color: #667eea;
        }

        .login-btn {
            width: 100%;
            padding: 12px;
            background: linear-gradient(135deg, #667eea, #764ba2);
            color: white;
            border: none;
            border-radius: 10px;
            font-size: 1rem;
            font-weight: 600;
            cursor: pointer;
            transition: transform 0.3s, box-shadow 0.3s;
        }

        .login-btn:hover {
            transform: translateY(-2px);
            box-shadow: 0 10px 20px rgba(102, 126, 234, 0.4);
        }

        .error-message {
            background: #f8d7da;
            color: #721c24;
            padding: 10px;
            border-radius: 8px;
            margin-bottom: 20px;
            display: none;
        }

        .footer {
            text-align: center;
            margin-top: 20px;
            color: #666;
            font-size: 0.8rem;
        }
    </style>
</head>
<body>
    <div class="login-container">
        <div class="logo">
            <h1>Sulav Hosting</h1>
            <p>Professional Bot Management Panel</p>
        </div>
        
        <div id="errorMessage" class="error-message"></div>
        
        <form id="loginForm">
            <div class="form-group">
                <label>Username</label>
                <input type="text" id="username" required>
            </div>
            
            <div class="form-group">
                <label>Password</label>
                <input type="password" id="password" required>
            </div>
            
            <button type="submit" class="login-btn">Login</button>
        </form>
        
        <div class="footer">
            &copy; 2024 Sulav Hosting. All rights reserved.
        </div>
    </div>

    <script>
        document.getElementById('loginForm').addEventListener('submit', async (e) => {
            e.preventDefault();
            
            const username = document.getElementById('username').value;
            const password = document.getElementById('password').value;
            
            try {
                const response = await fetch('/login', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({ username, password })
                });
                
                const data = await response.json();
                
                if (response.ok) {
                    window.location.href = data.redirect;
                } else {
                    document.getElementById('errorMessage').style.display = 'block';
                    document.getElementById('errorMessage').textContent = data.error;
                }
            } catch (error) {
                document.getElementById('errorMessage').style.display = 'block';
                document.getElementById('errorMessage').textContent = 'Login failed. Please try again.';
            }
        });
    </script>
</body>
</html>
'''

DASHBOARD_PAGE = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Sulav Hosting - Dashboard</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
        }

        body {
            background: #f5f5f5;
        }

        /* Sidebar */
        .sidebar {
            position: fixed;
            left: 0;
            top: 0;
            bottom: 0;
            width: 260px;
            background: linear-gradient(180deg, #2c3e50 0%, #1a252f 100%);
            color: white;
            overflow-y: auto;
        }

        .sidebar-header {
            padding: 30px 20px;
            text-align: center;
            border-bottom: 1px solid rgba(255,255,255,0.1);
        }

        .sidebar-header h2 {
            font-size: 1.5rem;
            margin-bottom: 5px;
        }

        .sidebar-header p {
            font-size: 0.8rem;
            opacity: 0.7;
        }

        .nav-item {
            padding: 15px 25px;
            display: flex;
            align-items: center;
            gap: 10px;
            color: rgba(255,255,255,0.7);
            cursor: pointer;
            transition: all 0.3s;
            border-left: 3px solid transparent;
        }

        .nav-item:hover {
            background: rgba(255,255,255,0.1);
            color: white;
        }

        .nav-item.active {
            background: rgba(255,255,255,0.15);
            color: white;
            border-left-color: #667eea;
        }

        .nav-item i {
            width: 20px;
            font-size: 1.1rem;
        }

        /* Main Content */
        .main-content {
            margin-left: 260px;
            padding: 30px;
        }

        .top-bar {
            background: white;
            padding: 20px 30px;
            border-radius: 15px;
            box-shadow: 0 5px 15px rgba(0,0,0,0.05);
            margin-bottom: 30px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }

        .page-title h1 {
            font-size: 1.8rem;
            color: #333;
        }

        .page-title p {
            color: #666;
            margin-top: 5px;
        }

        .user-info {
            display: flex;
            align-items: center;
            gap: 20px;
        }

        .user-badge {
            background: #667eea;
            color: white;
            padding: 8px 15px;
            border-radius: 20px;
            font-size: 0.9rem;
        }

        .logout-btn {
            padding: 8px 15px;
            background: #f44336;
            color: white;
            border: none;
            border-radius: 8px;
            cursor: pointer;
            transition: background 0.3s;
        }

        .logout-btn:hover {
            background: #d32f2f;
        }

        /* Cards */
        .stats-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
            gap: 25px;
            margin-bottom: 30px;
        }

        .stat-card {
            background: white;
            border-radius: 15px;
            padding: 25px;
            box-shadow: 0 5px 15px rgba(0,0,0,0.05);
            display: flex;
            align-items: center;
            gap: 20px;
        }

        .stat-icon {
            width: 60px;
            height: 60px;
            border-radius: 12px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 1.8rem;
        }

        .stat-icon.blue { background: #e3f2fd; color: #1976d2; }
        .stat-icon.green { background: #e8f5e9; color: #388e3c; }
        .stat-icon.purple { background: #f3e5f5; color: #7b1fa2; }
        .stat-icon.orange { background: #fff3e0; color: #f57c00; }

        .stat-info h3 {
            font-size: 0.9rem;
            color: #666;
            margin-bottom: 5px;
        }

        .stat-info .value {
            font-size: 1.8rem;
            font-weight: bold;
            color: #333;
        }

        .stat-info .sub {
            font-size: 0.8rem;
            color: #999;
        }

        /* Sections */
        .section {
            background: white;
            border-radius: 15px;
            padding: 25px;
            margin-bottom: 30px;
            box-shadow: 0 5px 15px rgba(0,0,0,0.05);
        }

        .section-title {
            font-size: 1.3rem;
            color: #333;
            margin-bottom: 20px;
            display: flex;
            align-items: center;
            gap: 10px;
        }

        /* Upload Area */
        .upload-area {
            border: 2px dashed #667eea;
            border-radius: 12px;
            padding: 40px;
            text-align: center;
            cursor: pointer;
            transition: all 0.3s;
            margin-bottom: 20px;
        }

        .upload-area:hover {
            background: #f8f9ff;
            border-color: #764ba2;
        }

        .upload-area i {
            font-size: 3rem;
            color: #667eea;
            margin-bottom: 15px;
        }

        .upload-area p {
            color: #666;
            margin-bottom: 5px;
        }

        .upload-area .small {
            font-size: 0.8rem;
            color: #999;
        }

        /* File List */
        .file-list {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
            gap: 15px;
            margin-top: 20px;
        }

        .file-item {
            background: #f8f9fa;
            border-radius: 12px;
            padding: 15px;
            display: flex;
            align-items: center;
            gap: 15px;
        }

        .file-icon {
            width: 40px;
            height: 40px;
            border-radius: 8px;
            background: #e3f2fd;
            color: #1976d2;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 1.2rem;
        }

        .file-info {
            flex: 1;
        }

        .file-name {
            font-weight: 600;
            color: #333;
            margin-bottom: 3px;
            word-break: break-all;
        }

        .file-meta {
            font-size: 0.7rem;
            color: #999;
        }

        .file-actions {
            display: flex;
            gap: 5px;
        }

        /* Bot List */
        .bot-list {
            margin-top: 20px;
        }

        .bot-item {
            background: #f8f9fa;
            border-radius: 12px;
            padding: 15px;
            margin-bottom: 10px;
            display: flex;
            align-items: center;
            justify-content: space-between;
        }

        .bot-info h4 {
            color: #333;
            margin-bottom: 5px;
        }

        .bot-info p {
            font-size: 0.8rem;
            color: #666;
        }

        .bot-status {
            padding: 4px 10px;
            border-radius: 20px;
            font-size: 0.7rem;
            font-weight: 600;
        }

        .status-running {
            background: #e8f5e9;
            color: #388e3c;
        }

        .status-stopped {
            background: #ffebee;
            color: #d32f2f;
        }

        /* Log Box */
        .log-box {
            background: #1e1e1e;
            color: #d4d4d4;
            padding: 15px;
            border-radius: 10px;
            font-family: 'Courier New', monospace;
            height: 300px;
            overflow-y: auto;
            margin: 20px 0;
            white-space: pre-wrap;
            font-size: 0.9rem;
        }

        .log-controls {
            display: flex;
            gap: 10px;
            margin-bottom: 15px;
        }

        .log-controls select {
            flex: 1;
            padding: 10px;
            border: 1px solid #ddd;
            border-radius: 8px;
        }

        /* Buttons */
        .btn {
            padding: 8px 15px;
            border: none;
            border-radius: 8px;
            cursor: pointer;
            font-weight: 500;
            transition: all 0.3s;
        }

        .btn-sm {
            padding: 5px 10px;
            font-size: 0.8rem;
        }

        .btn-primary {
            background: #667eea;
            color: white;
        }

        .btn-primary:hover {
            background: #5a67d8;
        }

        .btn-success {
            background: #48bb78;
            color: white;
        }

        .btn-success:hover {
            background: #38a169;
        }

        .btn-danger {
            background: #f56565;
            color: white;
        }

        .btn-danger:hover {
            background: #e53e3e;
        }

        .btn-warning {
            background: #ed8936;
            color: white;
        }

        /* Progress Bar */
        .progress-bar {
            width: 100%;
            height: 8px;
            background: #e2e8f0;
            border-radius: 4px;
            overflow: hidden;
            margin: 10px 0;
        }

        .progress-fill {
            height: 100%;
            background: linear-gradient(90deg, #667eea, #764ba2);
            transition: width 0.3s;
        }

        /* Alert */
        .alert {
            padding: 15px 20px;
            border-radius: 10px;
            margin-bottom: 20px;
            display: none;
            position: fixed;
            top: 20px;
            right: 20px;
            z-index: 9999;
            animation: slideIn 0.3s;
        }

        @keyframes slideIn {
            from {
                transform: translateX(100%);
                opacity: 0;
            }
            to {
                transform: translateX(0);
                opacity: 1;
            }
        }

        .alert-success {
            background: #c6f6d5;
            color: #22543d;
            border: 1px solid #9ae6b4;
        }

        .alert-error {
            background: #fed7d7;
            color: #742a2a;
            border: 1px solid #fc8181;
        }

        /* Modal */
        .modal {
            display: none;
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: rgba(0,0,0,0.5);
            z-index: 10000;
            align-items: center;
            justify-content: center;
        }

        .modal-content {
            background: white;
            border-radius: 15px;
            padding: 25px;
            max-width: 500px;
            width: 90%;
            animation: modalSlideIn 0.3s;
        }

        @keyframes modalSlideIn {
            from {
                transform: translateY(-50px);
                opacity: 0;
            }
            to {
                transform: translateY(0);
                opacity: 1;
            }
        }

        .modal-title {
            font-size: 1.3rem;
            color: #333;
            margin-bottom: 15px;
        }

        .modal-body {
            margin-bottom: 20px;
            color: #666;
        }

        .modal-footer {
            display: flex;
            gap: 10px;
            justify-content: flex-end;
        }

        /* Loading Spinner */
        .spinner {
            border: 3px solid #f3f3f3;
            border-top: 3px solid #667eea;
            border-radius: 50%;
            width: 30px;
            height: 30px;
            animation: spin 1s linear infinite;
            display: inline-block;
        }

        @keyframes spin {
            0% { transform: rotate(0deg); }
            100% { transform: rotate(360deg); }
        }

        /* Responsive */
        @media (max-width: 768px) {
            .sidebar {
                width: 70px;
            }
            
            .sidebar-header h2, .sidebar-header p, .nav-item span {
                display: none;
            }
            
            .main-content {
                margin-left: 70px;
            }
            
            .nav-item {
                justify-content: center;
            }
            
            .nav-item i {
                margin-right: 0;
            }
        }
    </style>
</head>
<body>
    <!-- Alert -->
    <div id="alert" class="alert"></div>

    <!-- Modal -->
    <div id="modal" class="modal">
        <div class="modal-content">
            <h3 id="modalTitle" class="modal-title"></h3>
            <div id="modalBody" class="modal-body"></div>
            <div class="modal-footer">
                <button class="btn btn-danger" onclick="hideModal()">Cancel</button>
                <button id="modalConfirm" class="btn btn-primary">Confirm</button>
            </div>
        </div>
    </div>

    <!-- Sidebar -->
    <div class="sidebar">
        <div class="sidebar-header">
            <h2>Sulav</h2>
            <p>Hosting Panel</p>
        </div>
        
        <div class="nav-item active" onclick="showSection('dashboard')">
            <i>📊</i>
            <span>Dashboard</span>
        </div>
        
        <div class="nav-item" onclick="showSection('files')">
            <i>📁</i>
            <span>My Files</span>
        </div>
        
        <div class="nav-item" onclick="showSection('bots')">
            <i>🤖</i>
            <span>Running Bots</span>
        </div>
        
        <div class="nav-item" onclick="showSection('logs')">
            <i>📋</i>
            <span>Logs</span>
        </div>
        
        <div id="adminNavItem" class="nav-item" onclick="showSection('admin')" style="display: none;">
            <i>⚙️</i>
            <span>Admin Panel</span>
        </div>
    </div>

    <!-- Main Content -->
    <div class="main-content">
        <div class="top-bar">
            <div class="page-title">
                <h1 id="pageTitle">Dashboard</h1>
                <p id="pageSubtitle">Welcome back, <span id="usernameDisplay"></span></p>
            </div>
            
            <div class="user-info">
                <span class="user-badge" id="userBadge">User</span>
                <button class="logout-btn" onclick="logout()">Logout</button>
            </div>
        </div>

        <!-- Dashboard Section -->
        <div id="dashboardSection">
            <div class="stats-grid">
                <div class="stat-card">
                    <div class="stat-icon blue">
                        📄
                    </div>
                    <div class="stat-info">
                        <h3>Files Uploaded</h3>
                        <div class="value" id="uploadCount">0</div>
                        <div class="sub" id="uploadLimit">Limit: 0</div>
                    </div>
                </div>

                <div class="stat-card">
                    <div class="stat-icon green">
                        🤖
                    </div>
                    <div class="stat-info">
                        <h3>Running Bots</h3>
                        <div class="value" id="runningCount">0</div>
                    </div>
                </div>

                <div class="stat-card">
                    <div class="stat-icon purple">
                        📊
                    </div>
                    <div class="stat-info">
                        <h3>Total Uploads</h3>
                        <div class="value" id="totalUploads">0</div>
                    </div>
                </div>

                <div class="stat-card">
                    <div class="stat-icon orange">
                        💾
                    </div>
                    <div class="stat-info">
                        <h3>Storage Used</h3>
                        <div class="value" id="storageUsed">0 MB</div>
                    </div>
                </div>
            </div>

            <div class="section">
                <h2 class="section-title">
                    📋 Quick Actions
                </h2>
                
                <div style="display: flex; gap: 10px; flex-wrap: wrap;">
                    <button class="btn btn-primary" onclick="showSection('files')">
                        Upload New Bot
                    </button>
                    <button class="btn btn-success" onclick="showSection('bots')">
                        View Running Bots
                    </button>
                    <button class="btn btn-warning" onclick="showSection('logs')">
                        Check Logs
                    </button>
                </div>
            </div>

            <div class="section">
                <h2 class="section-title">
                    📊 System Status
                </h2>
                
                <div id="systemStatus">
                    Loading system status...
                </div>
            </div>
        </div>

        <!-- Files Section -->
        <div id="filesSection" style="display: none;">
            <div class="section">
                <h2 class="section-title">
                    📤 Upload New File
                </h2>
                
                <div class="upload-area" onclick="document.getElementById('fileInput').click()">
                    <i>📁</i>
                    <p>Click to upload or drag and drop</p>
                    <p class="small">Supported: .py, .js, .sh (Max: <span id="maxFileSize">50</span>MB)</p>
                    <input type="file" id="fileInput" style="display: none;" onchange="uploadFile()">
                </div>
                
                <div id="uploadProgress" class="progress-bar" style="display: none;">
                    <div class="progress-fill" id="uploadProgressFill" style="width: 0%;"></div>
                </div>
            </div>

            <div class="section">
                <h2 class="section-title">
                    📁 My Files
                </h2>
                
                <div id="fileList" class="file-list">
                    Loading files...
                </div>
            </div>
        </div>

        <!-- Bots Section -->
        <div id="botsSection" style="display: none;">
            <div class="section">
                <h2 class="section-title">
                    🤖 Running Bots
                </h2>
                
                <div id="botList" class="bot-list">
                    Loading bots...
                </div>
            </div>
        </div>

        <!-- Logs Section -->
        <div id="logsSection" style="display: none;">
            <div class="section">
                <h2 class="section-title">
                    📋 View Logs
                </h2>
                
                <div class="log-controls">
                    <select id="logFileSelect">
                        <option value="">Select a file</option>
                    </select>
                    <button class="btn btn-primary" onclick="loadLogs()">View Logs</button>
                    <button class="btn btn-warning" onclick="refreshLogs()">Refresh</button>
                </div>
                
                <div id="logBox" class="log-box">
                    Select a file to view logs
                </div>
            </div>
        </div>

        <!-- Admin Section -->
        <div id="adminSection" style="display: none;">
            <div class="section">
                <h2 class="section-title">
                    👥 User Management
                </h2>
                
                <button class="btn btn-primary" onclick="showAddUserModal()" style="margin-bottom: 20px;">
                    + Add New User
                </button>
                
                <div id="userList"></div>
            </div>

            <div class="section">
                <h2 class="section-title">
                    ⚙️ System Settings
                </h2>
                
                <div id="settingsForm">
                    <div style="margin-bottom: 15px;">
                        <label>Global Upload Limit:</label>
                        <input type="number" id="globalUploadLimit" class="form-control">
                    </div>
                    
                    <div style="margin-bottom: 15px;">
                        <label>Max File Size (MB):</label>
                        <input type="number" id="maxFileSizeSetting" class="form-control">
                    </div>
                    
                    <button class="btn btn-primary" onclick="saveSettings()">Save Settings</button>
                </div>
            </div>

            <div class="section">
                <h2 class="section-title">
                    📊 System Monitor
                </h2>
                
                <div id="systemMonitor">
                    Loading system stats...
                </div>
            </div>
        </div>
    </div>

    <script>
        // State
        let currentUser = null;
        let isAdmin = false;
        let refreshInterval = null;
        
        // Initialize
        document.addEventListener('DOMContentLoaded', () => {
            loadUserData();
            startRefreshInterval();
        });
        
        // Show section
        function showSection(section) {
            // Update nav items
            document.querySelectorAll('.nav-item').forEach(item => {
                item.classList.remove('active');
            });
            event.currentTarget.classList.add('active');
            
            // Hide all sections
            document.getElementById('dashboardSection').style.display = 'none';
            document.getElementById('filesSection').style.display = 'none';
            document.getElementById('botsSection').style.display = 'none';
            document.getElementById('logsSection').style.display = 'none';
            document.getElementById('adminSection').style.display = 'none';
            
            // Show selected section
            document.getElementById(section + 'Section').style.display = 'block';
            
            // Update page title
            const titles = {
                'dashboard': 'Dashboard',
                'files': 'My Files',
                'bots': 'Running Bots',
                'logs': 'Logs',
                'admin': 'Admin Panel'
            };
            document.getElementById('pageTitle').textContent = titles[section];
            
            // Load section data
            if (section === 'files') loadFiles();
            if (section === 'bots') loadBots();
            if (section === 'logs') loadLogFileList();
            if (section === 'admin' && isAdmin) loadAdminData();
        }
        
        // Load user data
        async function loadUserData() {
            try {
                const response = await fetch('/api/user/stats');
                const data = await response.json();
                
                if (response.ok) {
                    currentUser = data;
                    isAdmin = data.is_admin || false;
                    
                    // Update UI
                    document.getElementById('usernameDisplay').textContent = data.username;
                    document.getElementById('userBadge').textContent = isAdmin ? 'Admin' : 'User';
                    document.getElementById('uploadCount').textContent = data.upload_count;
                    document.getElementById('uploadLimit').textContent = `Limit: ${data.upload_limit}`;
                    document.getElementById('runningCount').textContent = data.running_bots?.length || 0;
                    document.getElementById('totalUploads').textContent = data.uploads?.length || 0;
                    
                    // Calculate storage used
                    let totalSize = 0;
                    data.uploads?.forEach(file => {
                        totalSize += file.size || 0;
                    });
                    document.getElementById('storageUsed').textContent = formatSize(totalSize);
                    
                    // Show admin nav if admin
                    if (isAdmin) {
                        document.getElementById('adminNavItem').style.display = 'flex';
                    }
                    
                    // Load system status
                    loadSystemStatus();
                }
            } catch (error) {
                showAlert('Failed to load user data', 'error');
            }
        }
        
        // Load system status
        async function loadSystemStatus() {
            try {
                const response = await fetch('/api/system');
                const data = await response.json();
                
                document.getElementById('systemStatus').innerHTML = `
                    <p>CPU Usage: ${data.cpu}%</p>
                    <p>RAM Usage: ${data.ram}%</p>
                    <p>Running Bots: ${data.running_bots?.length || 0}</p>
                `;
            } catch (error) {
                document.getElementById('systemStatus').innerHTML = 'Failed to load system status';
            }
        }
        
        // Load files
        async function loadFiles() {
            try {
                const response = await fetch('/api/user/stats');
                const data = await response.json();
                
                if (!data.uploads || data.uploads.length === 0) {
                    document.getElementById('fileList').innerHTML = '<p>No files uploaded yet.</p>';
                    return;
                }
                
                let html = '';
                data.uploads.forEach(file => {
                    html += `
                        <div class="file-item">
                            <div class="file-icon">📄</div>
                            <div class="file-info">
                                <div class="file-name">${file.filename}</div>
                                <div class="file-meta">
                                    ${formatSize(file.size)} • ${new Date(file.uploaded_at).toLocaleString()}
                                </div>
                            </div>
                            <div class="file-actions">
                                <button class="btn btn-sm btn-success" onclick="startBot('${file.filename}')">Start</button>
                                <button class="btn btn-sm btn-danger" onclick="deleteFile('${file.filename}')">Delete</button>
                            </div>
                        </div>
                    `;
                });
                
                document.getElementById('fileList').innerHTML = html;
            } catch (error) {
                document.getElementById('fileList').innerHTML = 'Failed to load files';
            }
        }
        
        // Load bots
        async function loadBots() {
            try {
                const response = await fetch('/api/user/stats');
                const data = await response.json();
                
                if (!data.running_bots || data.running_bots.length === 0) {
                    document.getElementById('botList').innerHTML = '<p>No bots running.</p>';
                    return;
                }
                
                let html = '';
                data.running_bots.forEach(bot => {
                    html += `
                        <div class="bot-item">
                            <div class="bot-info">
                                <h4>${bot.filename}</h4>
                                <p>Started: ${new Date(bot.start_time).toLocaleString()}</p>
                            </div>
                            <div>
                                <span class="bot-status status-running">Running</span>
                                <button class="btn btn-sm btn-danger" onclick="stopBot('${bot.id}')">Stop</button>
                            </div>
                        </div>
                    `;
                });
                
                document.getElementById('botList').innerHTML = html;
            } catch (error) {
                document.getElementById('botList').innerHTML = 'Failed to load bots';
            }
        }
        
        // Load log file list
        async function loadLogFileList() {
            try {
                const response = await fetch('/api/user/stats');
                const data = await response.json();
                
                let options = '<option value="">Select a file</option>';
                data.uploads?.forEach(file => {
                    options += `<option value="${file.filename}">${file.filename}</option>`;
                });
                
                document.getElementById('logFileSelect').innerHTML = options;
            } catch (error) {
                console.error('Failed to load log files');
            }
        }
        
        // Load logs
        async function loadLogs() {
            const filename = document.getElementById('logFileSelect').value;
            if (!filename) {
                showAlert('Please select a file', 'error');
                return;
            }
            
            try {
                const response = await fetch(`/api/user/logs/${filename}`);
                const logs = await response.text();
                
                document.getElementById('logBox').textContent = logs || 'No logs available';
            } catch (error) {
                document.getElementById('logBox').textContent = 'Failed to load logs';
            }
        }
        
        // Refresh logs
        function refreshLogs() {
            loadLogs();
        }
        
        // Upload file
        async function uploadFile() {
            const fileInput = document.getElementById('fileInput');
            const file = fileInput.files[0];
            
            if (!file) return;
            
            const formData = new FormData();
            formData.append('file', file);
            
            // Show progress
            document.getElementById('uploadProgress').style.display = 'block';
            
            try {
                const response = await fetch('/api/user/upload', {
                    method: 'POST',
                    body: formData
                });
                
                const data = await response.json();
                
                if (response.ok) {
                    showAlert('File uploaded successfully!', 'success');
                    fileInput.value = '';
                    loadFiles();
                    loadUserData();
                } else {
                    showAlert(data.error || 'Upload failed', 'error');
                }
            } catch (error) {
                showAlert('Upload failed', 'error');
            } finally {
                document.getElementById('uploadProgress').style.display = 'none';
            }
        }
        
        // Start bot
        async function startBot(filename) {
            try {
                const response = await fetch('/api/user/start', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({ filename })
                });
                
                const data = await response.json();
                
                if (response.ok) {
                    showAlert(`Bot ${filename} started successfully!`, 'success');
                    loadBots();
                    loadUserData();
                    
                    // Write to log
                    const logBox = document.getElementById('logBox');
                    if (logBox) {
                        logBox.textContent += `\\n[${new Date().toLocaleTimeString()}] Started bot: ${filename}\\n`;
                    }
                } else {
                    showAlert(data.error || 'Failed to start bot', 'error');
                }
            } catch (error) {
                showAlert('Failed to start bot', 'error');
            }
        }
        
        // Stop bot
        async function stopBot(botId) {
            try {
                const response = await fetch('/api/user/stop', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({ bot_id: botId })
                });
                
                const data = await response.json();
                
                if (response.ok) {
                    showAlert('Bot stopped successfully!', 'success');
                    loadBots();
                    loadUserData();
                } else {
                    showAlert(data.error || 'Failed to stop bot', 'error');
                }
            } catch (error) {
                showAlert('Failed to stop bot', 'error');
            }
        }
        
        // Delete file
        function deleteFile(filename) {
            showModal(
                'Delete File',
                `Are you sure you want to delete ${filename}?`,
                async () => {
                    try {
                        const response = await fetch('/api/user/delete', {
                            method: 'POST',
                            headers: {
                                'Content-Type': 'application/json'
                            },
                            body: JSON.stringify({ filename })
                        });
                        
                        if (response.ok) {
                            showAlert('File deleted successfully!', 'success');
                            loadFiles();
                            loadUserData();
                        } else {
                            showAlert('Failed to delete file', 'error');
                        }
                    } catch (error) {
                        showAlert('Failed to delete file', 'error');
                    }
                }
            );
        }
        
        // Load admin data
        async function loadAdminData() {
            try {
                const response = await fetch('/api/admin/stats');
                const data = await response.json();
                
                // Load users
                let userHtml = '<table style="width:100%; border-collapse: collapse;">';
                userHtml += `
                    <tr>
                        <th style="text-align:left; padding:10px;">Username</th>
                        <th style="text-align:left; padding:10px;">Role</th>
                        <th style="text-align:left; padding:10px;">Files</th>
                        <th style="text-align:left; padding:10px;">Limit</th>
                        <th style="text-align:left; padding:10px;">Actions</th>
                    </tr>
                `;
                
                data.users.forEach(user => {
                    userHtml += `
                        <tr>
                            <td style="padding:10px;">${user.username}</td>
                            <td style="padding:10px;">${user.is_admin ? 'Admin' : 'User'}</td>
                            <td style="padding:10px;">${user.upload_count}/${user.upload_limit}</td>
                            <td style="padding:10px;">
                                <input type="number" id="limit_${user.username}" value="${user.upload_limit}" style="width:70px;">
                            </td>
                            <td style="padding:10px;">
                                <button class="btn btn-sm btn-primary" onclick="updateUserLimit('${user.username}')">Update</button>
                                ${!user.is_admin ? `<button class="btn btn-sm btn-danger" onclick="deleteUser('${user.username}')">Delete</button>` : ''}
                            </td>
                        </tr>
                    `;
                });
                
                userHtml += '</table>';
                document.getElementById('userList').innerHTML = userHtml;
                
                // Load settings
                document.getElementById('globalUploadLimit').value = data.settings.global_upload_limit;
                document.getElementById('maxFileSizeSetting').value = data.settings.max_file_size;
                
                // Load system monitor
                document.getElementById('systemMonitor').innerHTML = `
                    <p>CPU: ${data.system.cpu}%</p>
                    <p>RAM: ${data.system.ram}%</p>
                    <p>Disk: ${data.system.disk}%</p>
                    <p>Total Users: ${data.total_users}</p>
                    <p>Total Uploads: ${data.total_uploads}</p>
                    <p>Running Bots: ${data.system.running_bots}</p>
                `;
            } catch (error) {
                console.error('Failed to load admin data');
            }
        }
        
        // Update user limit
        async function updateUserLimit(username) {
            const limit = document.getElementById(`limit_${username}`).value;
            
            try {
                const response = await fetch('/api/admin/users', {
                    method: 'PUT',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({
                        username: username,
                        upload_limit: parseInt(limit)
                    })
                });
                
                if (response.ok) {
                    showAlert('User limit updated successfully!', 'success');
                } else {
                    showAlert('Failed to update user limit', 'error');
                }
            } catch (error) {
                showAlert('Failed to update user limit', 'error');
            }
        }
        
        // Delete user
        function deleteUser(username) {
            showModal(
                'Delete User',
                `Are you sure you want to delete user ${username}?`,
                async () => {
                    try {
                        const response = await fetch('/api/admin/users', {
                            method: 'DELETE',
                            headers: {
                                'Content-Type': 'application/json'
                            },
                            body: JSON.stringify({ username })
                        });
                        
                        if (response.ok) {
                            showAlert('User deleted successfully!', 'success');
                            loadAdminData();
                        } else {
                            showAlert('Failed to delete user', 'error');
                        }
                    } catch (error) {
                        showAlert('Failed to delete user', 'error');
                    }
                }
            );
        }
        
        // Show add user modal
        function showAddUserModal() {
            const modal = document.getElementById('modal');
            document.getElementById('modalTitle').textContent = 'Add New User';
            document.getElementById('modalBody').innerHTML = `
                <input type="text" id="newUsername" placeholder="Username" style="width:100%; padding:10px; margin-bottom:10px;">
                <input type="password" id="newPassword" placeholder="Password" style="width:100%; padding:10px; margin-bottom:10px;">
                <input type="number" id="newUserLimit" placeholder="Upload Limit" style="width:100%; padding:10px; margin-bottom:10px;">
                <label>
                    <input type="checkbox" id="newUserIsAdmin"> Is Admin
                </label>
            `;
            
            document.getElementById('modalConfirm').onclick = async () => {
                const username = document.getElementById('newUsername').value;
                const password = document.getElementById('newPassword').value;
                const limit = document.getElementById('newUserLimit').value;
                const isAdmin = document.getElementById('newUserIsAdmin').checked;
                
                if (!username || !password) {
                    showAlert('Username and password required', 'error');
                    return;
                }
                
                try {
                    const response = await fetch('/api/admin/users', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json'
                        },
                        body: JSON.stringify({
                            username,
                            password,
                            upload_limit: parseInt(limit) || 10,
                            is_admin: isAdmin
                        })
                    });
                    
                    if (response.ok) {
                        showAlert('User created successfully!', 'success');
                        hideModal();
                        loadAdminData();
                    } else {
                        const data = await response.json();
                        showAlert(data.error || 'Failed to create user', 'error');
                    }
                } catch (error) {
                    showAlert('Failed to create user', 'error');
                }
            };
            
            modal.style.display = 'flex';
        }
        
        // Save settings
        async function saveSettings() {
            const settings = {
                global_upload_limit: parseInt(document.getElementById('globalUploadLimit').value),
                max_file_size: parseInt(document.getElementById('maxFileSizeSetting').value)
            };
            
            try {
                const response = await fetch('/api/admin/settings', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify(settings)
                });
                
                if (response.ok) {
                    showAlert('Settings saved successfully!', 'success');
                } else {
                    showAlert('Failed to save settings', 'error');
                }
            } catch (error) {
                showAlert('Failed to save settings', 'error');
            }
        }
        
        // Show modal
        function showModal(title, body, onConfirm) {
            const modal = document.getElementById('modal');
            document.getElementById('modalTitle').textContent = title;
            document.getElementById('modalBody').innerHTML = body;
            document.getElementById('modalConfirm').onclick = () => {
                onConfirm();
                hideModal();
            };
            modal.style.display = 'flex';
        }
        
        // Hide modal
        function hideModal() {
            document.getElementById('modal').style.display = 'none';
        }
        
        // Show alert
        function showAlert(message, type) {
            const alert = document.getElementById('alert');
            alert.textContent = message;
            alert.className = `alert alert-${type}`;
            alert.style.display = 'block';
            
            setTimeout(() => {
                alert.style.display = 'none';
            }, 5000);
        }
        
        // Format size
        function formatSize(bytes) {
            const units = ['B', 'KB', 'MB', 'GB'];
            let size = bytes;
            let unitIndex = 0;
            
            while (size >= 1024 && unitIndex < units.length - 1) {
                size /= 1024;
                unitIndex++;
            }
            
            return `${size.toFixed(1)} ${units[unitIndex]}`;
        }
        
        // Logout
        async function logout() {
            window.location.href = '/logout';
        }
        
        // Start refresh interval
        function startRefreshInterval() {
            if (refreshInterval) clearInterval(refreshInterval);
            
            refreshInterval = setInterval(() => {
                const visibleSection = document.querySelector('.section[style*="block"]');
                if (visibleSection?.id === 'botsSection') loadBots();
                if (visibleSection?.id === 'dashboardSection') loadSystemStatus();
            }, 5000);
        }
    </script>
</body>
</html>
'''

# ==================== Routes ====================

@app.route('/')
def index():
    if 'user_id' in session:
        users = load_users()
        if users.get(session['user_id'], {}).get('is_admin'):
            return redirect('/admin')
        return redirect('/dashboard')
    return redirect('/login')

@app.route('/login')
def login_page():
    return LOGIN_PAGE

@app.route('/login', methods=['POST'])
def login_post():
    data = request.json
    username = data.get('username')
    password = data.get('password')
    
    users = load_users()
    user = users.get(username)
    
    if user and user['password'] == password:
        session.permanent = True
        session['user_id'] = username
        
        # Update last login
        user['last_login'] = datetime.now().isoformat()
        save_users(users)
        
        return jsonify({
            'success': True,
            'redirect': '/admin' if user.get('is_admin') else '/dashboard'
        })
    
    return jsonify({'error': 'Invalid credentials'}), 401

@app.route('/logout')
def logout():
    session.clear()
    return redirect('/login')

@app.route('/dashboard')
@login_required
def dashboard():
    return DASHBOARD_PAGE

@app.route('/admin')
@admin_required
def admin():
    return DASHBOARD_PAGE

# ==================== API Routes ====================

@app.route('/api/user/stats')
@login_required
def user_stats():
    username = session['user_id']
    users = load_users()
    uploads = load_uploads()
    user_uploads = uploads.get(username, [])
    
    # Calculate total size
    total_size = 0
    for upload in user_uploads:
        filepath = os.path.join(UPLOAD_DIR, username, upload['filename'])
        if os.path.exists(filepath):
            upload['exists'] = True
            upload['size'] = os.path.getsize(filepath)
            total_size += upload['size']
        else:
            upload['exists'] = False
    
    # Get user's running bots
    user_bots = []
    for bot_id, bot in running_bots.items():
        if bot['username'] == username:
            bot['id'] = bot_id
            bot['status'] = get_bot_status(bot_id)
            user_bots.append(bot)
    
    return jsonify({
        'username': username,
        'is_admin': users.get(username, {}).get('is_admin', False),
        'upload_count': len(user_uploads),
        'upload_limit': get_user_upload_limit(username),
        'total_size': total_size,
        'running_bots': user_bots,
        'uploads': user_uploads
    })

@app.route('/api/user/upload', methods=['POST'])
@login_required
def user_upload():
    username = session['user_id']
    
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400
    
    # Check file extension
    settings = load_settings()
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in settings['allowed_extensions']:
        return jsonify({'error': f'File type not allowed. Allowed: {", ".join(settings["allowed_extensions"])}'}), 400
    
    # Check upload limit
    upload_count = get_user_upload_count(username)
    upload_limit = get_user_upload_limit(username)
    
    if upload_count >= upload_limit:
        return jsonify({'error': f'Upload limit reached ({upload_limit} files)'}), 400
    
    # Check file size
    file.seek(0, os.SEEK_END)
    file_size = file.tell()
    file.seek(0)
    
    max_size = settings['max_file_size'] * 1024 * 1024
    if file_size > max_size:
        return jsonify({'error': f'File too large (max {settings["max_file_size"]}MB)'}), 400
    
    # Save file
    user_dir = os.path.join(UPLOAD_DIR, username)
    os.makedirs(user_dir, exist_ok=True)
    
    filepath = os.path.join(user_dir, file.filename)
    
    # Check if file exists
    if os.path.exists(filepath):
        return jsonify({'error': 'File already exists'}), 400
    
    file.save(filepath)
    
    # Update uploads record
    uploads = load_uploads()
    if username not in uploads:
        uploads[username] = []
    
    uploads[username].append({
        'filename': file.filename,
        'uploaded_at': datetime.now().isoformat(),
        'size': file_size
    })
    save_uploads(uploads)
    
    # Write to log
    log_path = os.path.join(LOG_DIR, username, f"{file.filename}.log")
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    with open(log_path, 'a') as f:
        f.write(f"\n{'='*50}\n")
        f.write(f"File uploaded at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"Filename: {file.filename}\n")
        f.write(f"Size: {format_size(file_size)}\n")
        f.write(f"{'='*50}\n\n")
    
    return jsonify({'success': True, 'message': 'File uploaded successfully'})

@app.route('/api/user/start', methods=['POST'])
@login_required
def user_start():
    username = session['user_id']
    data = request.json
    filename = data.get('filename')
    
    bot_id, message = start_bot(filename, username)
    
    if bot_id:
        return jsonify({'success': True, 'bot_id': bot_id, 'message': message})
    return jsonify({'error': message}), 400

@app.route('/api/user/stop', methods=['POST'])
@login_required
def user_stop():
    username = session['user_id']
    data = request.json
    bot_id = data.get('bot_id')
    
    bot = running_bots.get(bot_id)
    if not bot or bot['username'] != username:
        return jsonify({'error': 'Bot not found'}), 404
    
    success, message = stop_bot(bot_id)
    
    if success:
        return jsonify({'success': True, 'message': message})
    return jsonify({'error': message}), 400

@app.route('/api/user/logs/<filename>')
@login_required
def user_logs(filename):
    username = session['user_id']
    log_path = os.path.join(LOG_DIR, username, f"{filename}.log")
    
    if os.path.exists(log_path):
        with open(log_path, 'r') as f:
            return f.read()
    
    return 'No logs available', 404

@app.route('/api/user/delete', methods=['POST'])
@login_required
def user_delete():
    username = session['user_id']
    data = request.json
    filename = data.get('filename')
    
    # Stop any running bots with this filename
    for bot_id, bot in list(running_bots.items()):
        if bot['username'] == username and bot['filename'] == filename:
            stop_bot(bot_id)
    
    # Delete file
    filepath = os.path.join(UPLOAD_DIR, username, filename)
    if os.path.exists(filepath):
        os.remove(filepath)
    
    # Remove from uploads record
    uploads = load_uploads()
    if username in uploads:
        uploads[username] = [u for u in uploads[username] if u['filename'] != filename]
        save_uploads(uploads)
    
    return jsonify({'success': True})

@app.route('/api/admin/stats')
@admin_required
def admin_stats():
    users = load_users()
    uploads = load_uploads()
    
    total_users = len(users)
    total_uploads = sum(len(uploads.get(u, [])) for u in users)
    total_bots = len(running_bots)
    
    # System stats
    system_stats = {
        'cpu': psutil.cpu_percent(interval=1),
        'ram': psutil.virtual_memory().percent,
        'disk': psutil.disk_usage('/').percent,
        'running_bots': total_bots
    }
    
    # User details
    user_details = []
    for username, user_data in users.items():
        user_uploads = uploads.get(username, [])
        user_bots = len([b for b in running_bots.values() if b['username'] == username])
        
        user_details.append({
            'username': username,
            'is_admin': user_data.get('is_admin', False),
            'upload_limit': user_data.get('upload_limit', load_settings()['global_upload_limit']),
            'upload_count': len(user_uploads),
            'running_bots': user_bots,
            'created_at': user_data.get('created_at', 'Unknown'),
            'last_login': user_data.get('last_login', 'Never')
        })
    
    return jsonify({
        'total_users': total_users,
        'total_uploads': total_uploads,
        'system': system_stats,
        'users': user_details,
        'settings': load_settings()
    })

@app.route('/api/admin/users', methods=['POST', 'PUT', 'DELETE'])
@admin_required
def admin_users():
    if request.method == 'POST':
        data = request.json
        username = data.get('username')
        password = data.get('password')
        is_admin = data.get('is_admin', False)
        upload_limit = data.get('upload_limit', load_settings()['global_upload_limit'])
        
        users = load_users()
        if username in users:
            return jsonify({'error': 'User already exists'}), 400
        
        users[username] = {
            'password': password,
            'is_admin': is_admin,
            'upload_limit': upload_limit,
            'created_at': datetime.now().isoformat(),
            'last_login': None
        }
        save_users(users)
        
        return jsonify({'success': True})
    
    elif request.method == 'PUT':
        data = request.json
        username = data.get('username')
        
        users = load_users()
        if username not in users:
            return jsonify({'error': 'User not found'}), 404
        
        if 'upload_limit' in data:
            users[username]['upload_limit'] = data['upload_limit']
        if 'is_admin' in data:
            users[username]['is_admin'] = data['is_admin']
        if 'password' in data and data['password']:
            users[username]['password'] = data['password']
        
        save_users(users)
        return jsonify({'success': True})
    
    elif request.method == 'DELETE':
        data = request.json
        username = data.get('username')
        
        users = load_users()
        if username not in users:
            return jsonify({'error': 'User not found'}), 404
        
        # Don't allow deleting yourself
        if username == session['user_id']:
            return jsonify({'error': 'Cannot delete your own account'}), 400
        
        # Stop all user's bots
        for bot_id, bot in list(running_bots.items()):
            if bot['username'] == username:
                stop_bot(bot_id)
        
        # Delete user's files
        user_dir = os.path.join(UPLOAD_DIR, username)
        if os.path.exists(user_dir):
            shutil.rmtree(user_dir)
        
        # Delete user's logs
        user_log_dir = os.path.join(LOG_DIR, username)
        if os.path.exists(user_log_dir):
            shutil.rmtree(user_log_dir)
        
        # Remove from users list
        del users[username]
        save_users(users)
        
        # Remove from uploads
        uploads = load_uploads()
        if username in uploads:
            del uploads[username]
            save_uploads(uploads)
        
        return jsonify({'success': True})

@app.route('/api/admin/settings', methods=['GET', 'POST'])
@admin_required
def admin_settings():
    if request.method == 'GET':
        return jsonify(load_settings())
    
    elif request.method == 'POST':
        settings = load_settings()
        data = request.json
        
        if 'global_upload_limit' in data:
            settings['global_upload_limit'] = int(data['global_upload_limit'])
        if 'max_file_size' in data:
            settings['max_file_size'] = int(data['max_file_size'])
        if 'allowed_extensions' in data:
            settings['allowed_extensions'] = data['allowed_extensions']
        
        save_settings(settings)
        return jsonify({'success': True})

@app.route('/api/system')
def system():
    return jsonify({
        'cpu': psutil.cpu_percent(),
        'ram': psutil.virtual_memory().percent,
        'disk': psutil.disk_usage('/').percent,
        'running_bots': list(running_bots.keys())
    })

# ==================== Run Application ====================

if __name__ == "__main__":
    import os

    # Better logging
    os.environ["PYTHONUNBUFFERED"] = "1"

    port = int(os.environ.get("PORT", 3000))

    print("="*50)
    print("Sulav Hosting Panel (Railway Ready)")
    print("="*50)
    print(f"Running on port: {port}")
    print("="*50)

    app.run(host="0.0.0.0", port=port)