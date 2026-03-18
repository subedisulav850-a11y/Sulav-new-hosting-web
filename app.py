import os
import subprocess
import psutil
import json
import secrets
import shutil
import threading
import time
import hashlib
from datetime import datetime, timedelta
from functools import wraps
from flask import Flask, request, jsonify, session, redirect, send_from_directory, render_template_string

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(32))
app.permanent_session_lifetime = timedelta(days=30)

# Directory structure for Railway (persistent storage)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "data/bots")
LOG_DIR = os.path.join(BASE_DIR, "data/logs")
CONFIG_DIR = os.path.join(BASE_DIR, "data/config")

for dir_path in [UPLOAD_DIR, LOG_DIR, CONFIG_DIR]:
    os.makedirs(dir_path, exist_ok=True)

# Config files
USERS_FILE = os.path.join(CONFIG_DIR, "users.json")
UPLOADS_FILE = os.path.join(CONFIG_DIR, "uploads.json")
SETTINGS_FILE = os.path.join(CONFIG_DIR, "settings.json")

# Default settings
DEFAULT_SETTINGS = {
    "global_upload_limit": 10,
    "max_file_size": 100,  # MB
    "allowed_extensions": [".py", ".js", ".sh", ".txt"],
    "session_timeout": 30
}

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

# Initialize config files
def init_config():
    if not os.path.exists(USERS_FILE):
        admin_pass = os.environ.get('ADMIN_PASSWORD', 'Admin@123')
        default_users = {
            "admin": {
                "password": hash_password(admin_pass),
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

# ==================== Bot Management ====================

def start_bot(filename, username):
    user_dir = os.path.join(UPLOAD_DIR, username)
    filepath = os.path.join(user_dir, filename)
    
    if not os.path.exists(filepath):
        return None, "File not found"
    
    # Create log directory
    bot_log_dir = os.path.join(LOG_DIR, username)
    os.makedirs(bot_log_dir, exist_ok=True)
    
    # Start bot
    log_path = os.path.join(bot_log_dir, f"{filename}.log")
    log_file = open(log_path, "a")
    
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
        proc.terminate()
        
        for _ in range(10):
            if proc.poll() is not None:
                break
            time.sleep(0.5)
        
        if proc.poll() is None:
            proc.kill()
        
        bot = running_bots[bot_id]
        with open(bot['log_path'], 'a') as f:
            f.write(f"\n{'='*50}\n")
            f.write(f"Bot stopped at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"{'='*50}\n\n")
        
        del running_bots[bot_id]
        del bot_processes[bot_id]
        
        return True, "Bot stopped successfully"
    except Exception as e:
        return False, f"Failed to stop bot: {str(e)}"

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
            return jsonify({"error": "Unauthorized"}), 401
        users = load_users()
        if not users.get(session['user_id'], {}).get('is_admin', False):
            return jsonify({"error": "Admin required"}), 403
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
        * { margin: 0; padding: 0; box-sizing: border-box; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; }
        body { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); min-height: 100vh; display: flex; align-items: center; justify-content: center; }
        .login-container { background: white; border-radius: 20px; box-shadow: 0 20px 60px rgba(0,0,0,0.3); width: 90%; max-width: 400px; padding: 40px; }
        .logo { text-align: center; margin-bottom: 30px; }
        .logo h1 { font-size: 2rem; background: linear-gradient(135deg, #667eea, #764ba2); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
        .form-group { margin-bottom: 20px; }
        .form-group input { width: 100%; padding: 12px; border: 2px solid #e0e0e0; border-radius: 10px; font-size: 1rem; }
        .form-group input:focus { outline: none; border-color: #667eea; }
        .login-btn { width: 100%; padding: 12px; background: linear-gradient(135deg, #667eea, #764ba2); color: white; border: none; border-radius: 10px; font-size: 1rem; font-weight: 600; cursor: pointer; }
        .login-btn:hover { transform: translateY(-2px); }
        .error-message { background: #f8d7da; color: #721c24; padding: 10px; border-radius: 8px; margin-bottom: 20px; display: none; }
    </style>
</head>
<body>
    <div class="login-container">
        <div class="logo">
            <h1>Sulav Hosting</h1>
            <p>Bot Management Panel</p>
        </div>
        <div id="errorMessage" class="error-message"></div>
        <form id="loginForm">
            <div class="form-group">
                <input type="text" id="username" placeholder="Username" required>
            </div>
            <div class="form-group">
                <input type="password" id="password" placeholder="Password" required>
            </div>
            <button type="submit" class="login-btn">Login</button>
        </form>
    </div>
    <script>
        document.getElementById('loginForm').addEventListener('submit', async (e) => {
            e.preventDefault();
            const username = document.getElementById('username').value;
            const password = document.getElementById('password').value;
            
            try {
                const response = await fetch('/api/login', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
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
                document.getElementById('errorMessage').textContent = 'Login failed';
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
        * { margin: 0; padding: 0; box-sizing: border-box; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; }
        body { background: #f5f5f5; }
        .navbar { background: white; padding: 1rem 2rem; box-shadow: 0 2px 10px rgba(0,0,0,0.1); display: flex; justify-content: space-between; align-items: center; }
        .logo { font-size: 1.5rem; font-weight: bold; background: linear-gradient(135deg, #667eea, #764ba2); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
        .user-info { display: flex; align-items: center; gap: 20px; }
        .logout-btn { padding: 8px 15px; background: #f44336; color: white; border: none; border-radius: 8px; cursor: pointer; }
        .container { max-width: 1200px; margin: 2rem auto; padding: 0 2rem; }
        .stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 20px; margin-bottom: 30px; }
        .stat-card { background: white; border-radius: 15px; padding: 20px; box-shadow: 0 5px 15px rgba(0,0,0,0.05); }
        .stat-card h3 { color: #666; font-size: 0.9rem; margin-bottom: 10px; }
        .stat-card .value { font-size: 2rem; font-weight: bold; color: #333; }
        .section { background: white; border-radius: 15px; padding: 25px; margin-bottom: 30px; box-shadow: 0 5px 15px rgba(0,0,0,0.05); }
        .section-title { font-size: 1.3rem; color: #333; margin-bottom: 20px; }
        .upload-area { border: 2px dashed #667eea; border-radius: 10px; padding: 40px; text-align: center; cursor: pointer; margin-bottom: 20px; }
        .upload-area:hover { background: #f8f9ff; }
        .file-list { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 15px; }
        .file-item { background: #f8f9fa; border-radius: 10px; padding: 15px; display: flex; justify-content: space-between; align-items: center; }
        .btn { padding: 8px 15px; border: none; border-radius: 5px; cursor: pointer; font-weight: 500; margin: 0 5px; }
        .btn-primary { background: #667eea; color: white; }
        .btn-success { background: #48bb78; color: white; }
        .btn-danger { background: #f56565; color: white; }
        .btn-warning { background: #ed8936; color: white; }
        .alert { padding: 15px; border-radius: 10px; margin-bottom: 20px; display: none; position: fixed; top: 20px; right: 20px; z-index: 9999; }
        .alert-success { background: #c6f6d5; color: #22543d; }
        .alert-error { background: #fed7d7; color: #742a2a; }
        .log-box { background: #1e1e1e; color: #d4d4d4; padding: 15px; border-radius: 10px; font-family: monospace; height: 300px; overflow-y: auto; white-space: pre-wrap; margin: 20px 0; }
        .bot-list { margin-top: 20px; }
        .bot-item { background: #f8f9fa; border-radius: 10px; padding: 15px; margin-bottom: 10px; display: flex; justify-content: space-between; align-items: center; }
        .status-running { color: #48bb78; font-weight: bold; }
        .status-stopped { color: #f56565; font-weight: bold; }
        select, input { padding: 10px; border: 1px solid #ddd; border-radius: 5px; width: 100%; margin-bottom: 10px; }
        .nav-links { display: flex; gap: 20px; }
        .nav-link { cursor: pointer; padding: 5px 10px; border-radius: 5px; }
        .nav-link:hover { background: #f0f0f0; }
        .nav-link.active { background: #667eea; color: white; }
    </style>
</head>
<body>
    <nav class="navbar">
        <div class="logo">Sulav Hosting</div>
        <div class="nav-links">
            <span class="nav-link active" onclick="showSection('dashboard')">Dashboard</span>
            <span class="nav-link" onclick="showSection('files')">Files</span>
            <span class="nav-link" onclick="showSection('bots')">Bots</span>
            <span class="nav-link" onclick="showSection('logs')">Logs</span>
            <span class="nav-link" id="adminLink" style="display:none;" onclick="showSection('admin')">Admin</span>
        </div>
        <div class="user-info">
            <span id="username"></span>
            <button class="logout-btn" onclick="logout()">Logout</button>
        </div>
    </nav>

    <div class="container">
        <div id="alert" class="alert"></div>

        <!-- Dashboard Section -->
        <div id="dashboardSection">
            <div class="stats-grid">
                <div class="stat-card">
                    <h3>Files Uploaded</h3>
                    <div class="value" id="uploadCount">0</div>
                    <div id="uploadLimit"></div>
                </div>
                <div class="stat-card">
                    <h3>Running Bots</h3>
                    <div class="value" id="runningCount">0</div>
                </div>
                <div class="stat-card">
                    <h3>Storage Used</h3>
                    <div class="value" id="storageUsed">0 MB</div>
                </div>
                <div class="stat-card">
                    <h3>System CPU</h3>
                    <div class="value" id="systemCpu">0%</div>
                </div>
            </div>
        </div>

        <!-- Files Section -->
        <div id="filesSection" style="display:none;">
            <div class="section">
                <h2 class="section-title">Upload Bot</h2>
                <div class="upload-area" onclick="document.getElementById('fileInput').click()">
                    <p>Click to upload or drag and drop</p>
                    <p class="small">Supported: .py, .js, .sh (Max: <span id="maxFileSize">100</span>MB)</p>
                    <input type="file" id="fileInput" style="display: none;" onchange="uploadFile()">
                </div>
            </div>

            <div class="section">
                <h2 class="section-title">My Files</h2>
                <div id="fileList" class="file-list">
                    Loading files...
                </div>
            </div>
        </div>

        <!-- Bots Section -->
        <div id="botsSection" style="display:none;">
            <div class="section">
                <h2 class="section-title">Running Bots</h2>
                <div id="botList" class="bot-list">
                    Loading bots...
                </div>
            </div>
        </div>

        <!-- Logs Section -->
        <div id="logsSection" style="display:none;">
            <div class="section">
                <h2 class="section-title">View Logs</h2>
                <select id="logFileSelect">
                    <option value="">Select a file</option>
                </select>
                <button class="btn btn-primary" onclick="loadLogs()">View Logs</button>
                <button class="btn btn-warning" onclick="refreshLogs()">Refresh</button>
                <div id="logBox" class="log-box">
                    Select a file to view logs
                </div>
            </div>
        </div>

        <!-- Admin Section -->
        <div id="adminSection" style="display:none;">
            <div class="section">
                <h2 class="section-title">User Management</h2>
                <button class="btn btn-primary" onclick="showAddUserModal()">Add User</button>
                <div id="userList" style="margin-top:20px;"></div>
            </div>
            
            <div class="section">
                <h2 class="section-title">Settings</h2>
                <input type="number" id="globalUploadLimit" placeholder="Global Upload Limit">
                <input type="number" id="maxFileSizeSetting" placeholder="Max File Size (MB)">
                <button class="btn btn-primary" onclick="saveSettings()">Save Settings</button>
            </div>
        </div>
    </div>

    <script>
        let currentUser = null;
        let isAdmin = false;

        document.addEventListener('DOMContentLoaded', () => {
            loadUserData();
            setInterval(loadSystemStats, 5000);
        });

        function showSection(section) {
            document.querySelectorAll('.nav-link').forEach(el => el.classList.remove('active'));
            event.target.classList.add('active');
            
            document.getElementById('dashboardSection').style.display = 'none';
            document.getElementById('filesSection').style.display = 'none';
            document.getElementById('botsSection').style.display = 'none';
            document.getElementById('logsSection').style.display = 'none';
            document.getElementById('adminSection').style.display = 'none';
            
            document.getElementById(section + 'Section').style.display = 'block';
            
            if (section === 'files') loadFiles();
            if (section === 'bots') loadBots();
            if (section === 'logs') loadLogFileList();
            if (section === 'admin' && isAdmin) loadAdminData();
        }

        async function loadUserData() {
            try {
                const response = await fetch('/api/user/stats');
                const data = await response.json();
                
                if (response.ok) {
                    currentUser = data;
                    isAdmin = data.is_admin;
                    document.getElementById('username').textContent = data.username;
                    document.getElementById('uploadCount').textContent = data.upload_count;
                    document.getElementById('uploadLimit').textContent = `Limit: ${data.upload_limit}`;
                    document.getElementById('runningCount').textContent = data.running_bots?.length || 0;
                    document.getElementById('storageUsed').textContent = formatSize(data.total_size || 0);
                    
                    if (isAdmin) {
                        document.getElementById('adminLink').style.display = 'inline';
                    }
                    
                    loadSystemStats();
                }
            } catch (error) {
                showAlert('Failed to load user data', 'error');
            }
        }

        async function loadSystemStats() {
            try {
                const response = await fetch('/api/system');
                const data = await response.json();
                document.getElementById('systemCpu').textContent = data.cpu + '%';
            } catch (error) {}
        }

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
                            <div>
                                <strong>${file.filename}</strong><br>
                                <small>${formatSize(file.size)} • ${new Date(file.uploaded_at).toLocaleString()}</small>
                            </div>
                            <div>
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
                            <div>
                                <strong>${bot.filename}</strong><br>
                                <small>Started: ${new Date(bot.start_time).toLocaleString()}</small>
                            </div>
                            <div>
                                <span class="status-running">● Running</span>
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

        function loadLogFileList() {
            if (!currentUser?.uploads) return;
            
            let options = '<option value="">Select a file</option>';
            currentUser.uploads.forEach(file => {
                options += `<option value="${file.filename}">${file.filename}</option>`;
            });
            document.getElementById('logFileSelect').innerHTML = options;
        }

        async function uploadFile() {
            const fileInput = document.getElementById('fileInput');
            const file = fileInput.files[0];
            
            if (!file) return;
            
            const formData = new FormData();
            formData.append('file', file);
            
            try {
                const response = await fetch('/api/user/upload', {
                    method: 'POST',
                    body: formData
                });
                
                const data = await response.json();
                
                if (response.ok) {
                    showAlert('File uploaded successfully!', 'success');
                    fileInput.value = '';
                    loadUserData();
                    loadFiles();
                } else {
                    showAlert(data.error || 'Upload failed', 'error');
                }
            } catch (error) {
                showAlert('Upload failed', 'error');
            }
        }

        async function startBot(filename) {
            try {
                const response = await fetch('/api/user/start', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ filename })
                });
                
                const data = await response.json();
                
                if (response.ok) {
                    showAlert(`Bot started!`, 'success');
                    loadBots();
                    loadUserData();
                } else {
                    showAlert(data.error || 'Failed to start bot', 'error');
                }
            } catch (error) {
                showAlert('Failed to start bot', 'error');
            }
        }

        async function stopBot(botId) {
            try {
                const response = await fetch('/api/user/stop', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ bot_id: botId })
                });
                
                if (response.ok) {
                    showAlert('Bot stopped!', 'success');
                    loadBots();
                    loadUserData();
                }
            } catch (error) {
                showAlert('Failed to stop bot', 'error');
            }
        }

        async function deleteFile(filename) {
            if (!confirm(`Delete ${filename}?`)) return;
            
            try {
                const response = await fetch('/api/user/delete', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ filename })
                });
                
                if (response.ok) {
                    showAlert('File deleted!', 'success');
                    loadUserData();
                    loadFiles();
                }
            } catch (error) {
                showAlert('Delete failed', 'error');
            }
        }

        async function loadLogs() {
            const filename = document.getElementById('logFileSelect').value;
            if (!filename) {
                showAlert('Select a file', 'error');
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

        function refreshLogs() {
            loadLogs();
        }

        async function loadAdminData() {
            try {
                const response = await fetch('/api/admin/stats');
                const data = await response.json();
                
                let html = '<table style="width:100%; border-collapse: collapse;">';
                html += '<tr><th>Username</th><th>Role</th><th>Files</th><th>Limit</th><th>Actions</th></tr>';
                
                data.users.forEach(user => {
                    html += `
                        <tr>
                            <td style="padding:10px;">${user.username}</td>
                            <td style="padding:10px;">${user.is_admin ? 'Admin' : 'User'}</td>
                            <td style="padding:10px;">${user.upload_count}/${user.upload_limit}</td>
                            <td style="padding:10px;"><input type="number" id="limit_${user.username}" value="${user.upload_limit}" style="width:70px;"></td>
                            <td style="padding:10px;">
                                <button class="btn btn-sm btn-primary" onclick="updateUserLimit('${user.username}')">Update</button>
                                ${!user.is_admin ? `<button class="btn btn-sm btn-danger" onclick="deleteUser('${user.username}')">Delete</button>` : ''}
                            </td>
                        </tr>
                    `;
                });
                
                html += '</table>';
                document.getElementById('userList').innerHTML = html;
                
                document.getElementById('globalUploadLimit').value = data.settings.global_upload_limit;
                document.getElementById('maxFileSizeSetting').value = data.settings.max_file_size;
            } catch (error) {}
        }

        async function updateUserLimit(username) {
            const limit = document.getElementById(`limit_${username}`).value;
            
            try {
                const response = await fetch('/api/admin/users', {
                    method: 'PUT',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ username, upload_limit: parseInt(limit) })
                });
                
                if (response.ok) {
                    showAlert('User limit updated!', 'success');
                }
            } catch (error) {}
        }

        async function deleteUser(username) {
            if (!confirm(`Delete user ${username}?`)) return;
            
            try {
                const response = await fetch('/api/admin/users', {
                    method: 'DELETE',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ username })
                });
                
                if (response.ok) {
                    showAlert('User deleted!', 'success');
                    loadAdminData();
                }
            } catch (error) {}
        }

        async function saveSettings() {
            const settings = {
                global_upload_limit: parseInt(document.getElementById('globalUploadLimit').value),
                max_file_size: parseInt(document.getElementById('maxFileSizeSetting').value)
            };
            
            try {
                const response = await fetch('/api/admin/settings', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(settings)
                });
                
                if (response.ok) {
                    showAlert('Settings saved!', 'success');
                }
            } catch (error) {}
        }

        function showAddUserModal() {
            const username = prompt('Enter username:');
            if (!username) return;
            
            const password = prompt('Enter password:');
            if (!password) return;
            
            const limit = prompt('Enter upload limit:', '10');
            
            fetch('/api/admin/users', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    username,
                    password,
                    upload_limit: parseInt(limit)
                })
            }).then(response => {
                if (response.ok) {
                    showAlert('User created!', 'success');
                    loadAdminData();
                }
            });
        }

        function formatSize(bytes) {
            if (bytes === 0) return '0 B';
            const units = ['B', 'KB', 'MB', 'GB'];
            const i = Math.floor(Math.log(bytes) / Math.log(1024));
            return (bytes / Math.pow(1024, i)).toFixed(1) + ' ' + units[i];
        }

        function showAlert(message, type) {
            const alert = document.getElementById('alert');
            alert.textContent = message;
            alert.className = `alert alert-${type}`;
            alert.style.display = 'block';
            setTimeout(() => alert.style.display = 'none', 3000);
        }

        function logout() {
            window.location.href = '/api/logout';
        }
    </script>
</body>
</html>
'''

# ==================== Routes ====================

@app.route('/')
def index():
    if 'user_id' in session:
        return redirect('/dashboard')
    return redirect('/login')

@app.route('/login')
def login_page():
    return LOGIN_PAGE

@app.route('/dashboard')
@login_required
def dashboard():
    return DASHBOARD_PAGE

# ==================== API Routes ====================

@app.route('/api/login', methods=['POST'])
def api_login():
    data = request.json
    username = data.get('username')
    password = data.get('password')
    
    users = load_users()
    user = users.get(username)
    
    if user and user['password'] == hash_password(password):
        session.permanent = True
        session['user_id'] = username
        user['last_login'] = datetime.now().isoformat()
        save_users(users)
        return jsonify({'success': True, 'redirect': '/dashboard'})
    
    return jsonify({'error': 'Invalid credentials'}), 401

@app.route('/api/logout')
def api_logout():
    session.clear()
    return redirect('/login')

@app.route('/api/user/stats')
@login_required
def user_stats():
    username = session['user_id']
    users = load_users()
    uploads = load_uploads()
    user_uploads = uploads.get(username, [])
    
    total_size = 0
    for upload in user_uploads:
        filepath = os.path.join(UPLOAD_DIR, username, upload['filename'])
        if os.path.exists(filepath):
            upload['size'] = os.path.getsize(filepath)
            total_size += upload['size']
    
    user_bots = []
    for bot_id, bot in running_bots.items():
        if bot['username'] == username:
            bot['id'] = bot_id
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
    
    settings = load_settings()
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in settings['allowed_extensions']:
        return jsonify({'error': f'File type not allowed'}), 400
    
    upload_count = get_user_upload_count(username)
    upload_limit = get_user_upload_limit(username)
    
    if upload_count >= upload_limit:
        return jsonify({'error': f'Upload limit reached'}), 400
    
    file.seek(0, os.SEEK_END)
    file_size = file.tell()
    file.seek(0)
    
    max_size = settings['max_file_size'] * 1024 * 1024
    if file_size > max_size:
        return jsonify({'error': f'File too large (max {settings["max_file_size"]}MB)'}), 400
    
    user_dir = os.path.join(UPLOAD_DIR, username)
    os.makedirs(user_dir, exist_ok=True)
    
    filepath = os.path.join(user_dir, file.filename)
    
    if os.path.exists(filepath):
        return jsonify({'error': 'File already exists'}), 400
    
    file.save(filepath)
    
    uploads = load_uploads()
    if username not in uploads:
        uploads[username] = []
    
    uploads[username].append({
        'filename': file.filename,
        'uploaded_at': datetime.now().isoformat(),
        'size': file_size
    })
    save_uploads(uploads)
    
    # Create log file
    log_path = os.path.join(LOG_DIR, username, f"{file.filename}.log")
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    with open(log_path, 'a') as f:
        f.write(f"\n{'='*50}\n")
        f.write(f"File uploaded at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"Filename: {file.filename}\n")
        f.write(f"Size: {format_size(file_size)}\n")
        f.write(f"{'='*50}\n\n")
    
    return jsonify({'success': True})

@app.route('/api/user/start', methods=['POST'])
@login_required
def user_start():
    username = session['user_id']
    data = request.json
    filename = data.get('filename')
    
    bot_id, message = start_bot(filename, username)
    
    if bot_id:
        return jsonify({'success': True, 'bot_id': bot_id})
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
        return jsonify({'success': True})
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
    
    for bot_id, bot in list(running_bots.items()):
        if bot['username'] == username and bot['filename'] == filename:
            stop_bot(bot_id)
    
    filepath = os.path.join(UPLOAD_DIR, username, filename)
    if os.path.exists(filepath):
        os.remove(filepath)
    
    log_path = os.path.join(LOG_DIR, username, f"{filename}.log")
    if os.path.exists(log_path):
        os.remove(log_path)
    
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
    
    system_stats = {
        'cpu': psutil.cpu_percent(interval=1),
        'ram': psutil.virtual_memory().percent,
        'disk': psutil.disk_usage('/').percent,
        'running_bots': total_bots
    }
    
    user_details = []
    for username, user_data in users.items():
        user_uploads = uploads.get(username, [])
        user_bots = len([b for b in running_bots.values() if b['username'] == username])
        
        user_details.append({
            'username': username,
            'is_admin': user_data.get('is_admin', False),
            'upload_limit': user_data.get('upload_limit', load_settings()['global_upload_limit']),
            'upload_count': len(user_uploads),
            'running_bots': user_bots
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
        upload_limit = data.get('upload_limit', load_settings()['global_upload_limit'])
        
        users = load_users()
        if username in users:
            return jsonify({'error': 'User exists'}), 400
        
        users[username] = {
            'password': hash_password(password),
            'is_admin': False,
            'upload_limit': upload_limit,
            'created_at': datetime.now().isoformat()
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
        if 'password' in data and data['password']:
            users[username]['password'] = hash_password(data['password'])
        
        save_users(users)
        return jsonify({'success': True})
    
    elif request.method == 'DELETE':
        data = request.json
        username = data.get('username')
        
        users = load_users()
        if username not in users:
            return jsonify({'error': 'User not found'}), 404
        
        if username == session['user_id']:
            return jsonify({'error': 'Cannot delete yourself'}), 400
        
        del users[username]
        save_users(users)
        
        return jsonify({'success': True})

@app.route('/api/admin/settings', methods=['POST'])
@admin_required
def admin_settings():
    settings = load_settings()
    data = request.json
    
    if 'global_upload_limit' in data:
        settings['global_upload_limit'] = int(data['global_upload_limit'])
    if 'max_file_size' in data:
        settings['max_file_size'] = int(data['max_file_size'])
    
    save_settings(settings)
    return jsonify({'success': True})

@app.route('/api/system')
def system():
    try:
        return jsonify({
            'cpu': psutil.cpu_percent(),
            'ram': psutil.virtual_memory().percent,
            'running_bots': len(running_bots)
        })
    except:
        return jsonify({'cpu': 0, 'ram': 0, 'running_bots': 0})

# ==================== Run Application ====================

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 3000))
    app.run(host='0.0.0.0', port=port, debug=True)