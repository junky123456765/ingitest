from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, send_from_directory, send_from_directory
import sqlite3
import hashlib
import datetime
from datetime import timedelta
import os
import threading
import time
import random
import json
import uuid
import atexit
from collections import defaultdict
from werkzeug.utils import secure_filename
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = 'test_management_secret_key_2024'
app.permanent_session_lifetime = timedelta(hours=2)

# Enable threading for multiple concurrent users (Flask handles this automatically in production)
app.config['THREADED'] = True

# File upload configuration
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size
app.config['UPLOAD_FOLDER'] = 'images'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'bmp', 'webp'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# Database setup
DATABASE = 'testingenium.db'

# Add global dictionary to track login attempts and active sessions
login_attempts = {}  # user_id: {'timestamp': time, 'ip': ip, 'user_agent': user_agent}
session_events = {}  # user_id: {'event': 'login_attempt|session_taken', 'timestamp': time, 'details': {}}

def parse_datetime_string(dt_string):
    """
    Parse datetime string that may or may not contain microseconds
    Handles both formats: 'YYYY-MM-DD HH:MM:SS' and 'YYYY-MM-DD HH:MM:SS.ffffff'
    """
    from datetime import datetime
    
    if not dt_string:
        return datetime.now()
    
    # Try parsing with microseconds first
    try:
        return datetime.strptime(dt_string, '%Y-%m-%d %H:%M:%S.%f')
    except ValueError:
        # If that fails, try without microseconds
        try:
            return datetime.strptime(dt_string, '%Y-%m-%d %H:%M:%S')
        except ValueError:
            # If both fail, try ISO format
            try:
                return datetime.fromisoformat(dt_string.replace('T', ' '))
            except:
                # Last resort: return current time
                print(f"Warning: Could not parse datetime string '{dt_string}', using current time")
                return datetime.now()

def init_db():
    """Initialize the database with required tables"""
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    
    # TestSettings table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS TestSettings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            TestName TEXT NOT NULL UNIQUE,
            NumberTestQuestions INTEGER NOT NULL,
            TimeDuration INTEGER NOT NULL,
            CommonPassword TEXT NOT NULL,
            TestPassword TEXT,
            PassingGrade INTEGER DEFAULT 70,
            AttemptsAllowed INTEGER DEFAULT 3,
            IsActive INTEGER DEFAULT 0,
            CreatedDate DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Add AttemptsAllowed column to existing TestSettings table if it doesn't exist
    try:
        cursor.execute('ALTER TABLE TestSettings ADD COLUMN AttemptsAllowed INTEGER DEFAULT 3')
    except sqlite3.OperationalError:
        # Column already exists
        pass
    
    # Add NumberQuestionsForTest column to existing TestSettings table if it doesn't exist
    try:
        cursor.execute('ALTER TABLE TestSettings ADD COLUMN NumberQuestionsForTest INTEGER')
    except sqlite3.OperationalError:
        # Column already exists
        pass
    
    # CurrentTestSelection table to track which test is currently selected for admin
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS CurrentTestSelection (
            id INTEGER PRIMARY KEY,
            SelectedTestId INTEGER NOT NULL,
            FOREIGN KEY (SelectedTestId) REFERENCES TestSettings (id)
        )
    ''')
    
    # Users table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS Users (
            UserId INTEGER PRIMARY KEY AUTOINCREMENT,
            UserName TEXT UNIQUE NOT NULL,
            UserPassword TEXT,
            IsEligible INTEGER DEFAULT 1,
            SessionTimeout INTEGER DEFAULT 15
        )
    ''')
    
    # UserAnswers table (Questions and Answers)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS UserAnswers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            TestName TEXT NOT NULL,
            Question TEXT NOT NULL,
            Answer1 TEXT NOT NULL,
            Answer2 TEXT NOT NULL,
            Answer3 TEXT NOT NULL,
            Answer4 TEXT NOT NULL,
            Answer5 TEXT,
            CorrectAnswer INTEGER NOT NULL,
            QuestionNumber INTEGER NOT NULL,
            ImageFilename TEXT,
            IsActive INTEGER DEFAULT 1
        )
    ''')
    
    # UsersTestResults table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS UsersTestResults (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            UserId INTEGER NOT NULL,
            TestName TEXT NOT NULL,
            PassFail TEXT NOT NULL,
            Score INTEGER,
            TestDate DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (UserId) REFERENCES Users (UserId)
        )
    ''')
    
    # UserTestSessions table (for tracking active test sessions)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS UserTestSessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            UserId INTEGER NOT NULL,
            TestName TEXT NOT NULL,
            StartTime DATETIME DEFAULT CURRENT_TIMESTAMP,
            EndTime DATETIME,
            IsActive INTEGER DEFAULT 1,
            CurrentPage INTEGER DEFAULT 1,
            TestResultId INTEGER,
            QuestionOrder TEXT,
            FOREIGN KEY (UserId) REFERENCES Users (UserId),
            FOREIGN KEY (TestResultId) REFERENCES UsersTestResults (id)
        )
    ''')
    
    # UserSessions table (for session management)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS UserSessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            UserId INTEGER NOT NULL,
            SessionId TEXT UNIQUE NOT NULL,
            IsAdmin INTEGER DEFAULT 0,
            LoginTime DATETIME DEFAULT CURRENT_TIMESTAMP,
            LastActivity DATETIME DEFAULT CURRENT_TIMESTAMP,
            IsActive INTEGER DEFAULT 1,
            BrowserInfo TEXT,
            FOREIGN KEY (UserId) REFERENCES Users (UserId)
        )
    ''')
    
    # UserResponses table (for storing user's selected answers)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS UserResponses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            UserId INTEGER NOT NULL,
            TestName TEXT NOT NULL,
            QuestionId INTEGER NOT NULL,
            SelectedAnswer INTEGER,
            IsMarked INTEGER DEFAULT 0,
            TestResultId INTEGER,
            FOREIGN KEY (UserId) REFERENCES Users (UserId),
            FOREIGN KEY (TestResultId) REFERENCES UsersTestResults (id)
        )
    ''')
    
    # Create admin user if not exists
    admin_exists = cursor.execute('SELECT COUNT(*) FROM Users WHERE UserName = "admin"').fetchone()[0]
    if admin_exists == 0:
        admin_password_hash = hashlib.sha256('admin123'.encode()).hexdigest()
        cursor.execute('''
            INSERT INTO Users (UserName, UserPassword, IsEligible) 
            VALUES ('admin', ?, 0)
        ''', (admin_password_hash,))
    
    # Create demo user if not exists  
    demo_exists = cursor.execute('SELECT COUNT(*) FROM Users WHERE UserName = "demo"').fetchone()[0]
    if demo_exists == 0:
        demo_password_hash = hashlib.sha256('demo123'.encode()).hexdigest()
        cursor.execute('''
            INSERT INTO Users (UserName, UserPassword, IsEligible) 
            VALUES ('demo', ?, 1)
        ''', (demo_password_hash,))
    
    # Migration: Add UserPassword column if it doesn't exist
    try:
        cursor.execute('ALTER TABLE Users ADD COLUMN UserPassword TEXT')
        conn.commit()
    except sqlite3.OperationalError:
        # Column already exists, no action needed
        pass
    
    # Migration: Add PassingGrade column if it doesn't exist
    try:
        cursor.execute('ALTER TABLE TestSettings ADD COLUMN PassingGrade INTEGER DEFAULT 70')
        conn.commit()
    except sqlite3.OperationalError:
        # Column already exists, no action needed
        pass
    
    # Migration: Add ImageFilename column to UserAnswers if it doesn't exist
    try:
        cursor.execute('ALTER TABLE UserAnswers ADD COLUMN ImageFilename TEXT')
        conn.commit()
    except sqlite3.OperationalError:
        # Column already exists, no action needed
        pass
    
    # Migration: Add TestResultId column to UserTestSessions if it doesn't exist
    try:
        cursor.execute('ALTER TABLE UserTestSessions ADD COLUMN TestResultId INTEGER')
        cursor.execute('ALTER TABLE UserTestSessions ADD FOREIGN KEY (TestResultId) REFERENCES UsersTestResults (id)')
        conn.commit()
    except sqlite3.OperationalError:
        # Column already exists, no action needed
        pass
    
    # Migration: Add TestResultId column to UserResponses if it doesn't exist
    try:
        cursor.execute('ALTER TABLE UserResponses ADD COLUMN TestResultId INTEGER')
        cursor.execute('ALTER TABLE UserResponses ADD FOREIGN KEY (TestResultId) REFERENCES UsersTestResults (id)')
        conn.commit()
    except sqlite3.OperationalError:
        # Column already exists, no action needed
        pass
    
    # Migration: Add QuestionOrder column to UserTestSessions if it doesn't exist
    try:
        cursor.execute('ALTER TABLE UserTestSessions ADD COLUMN QuestionOrder TEXT')
        conn.commit()
    except sqlite3.OperationalError:
        # Column already exists, no action needed
        pass
    
    # Migration: Add SessionTimeout column to Users if it doesn't exist
    try:
        cursor.execute('ALTER TABLE Users ADD COLUMN SessionTimeout INTEGER DEFAULT 15')
        conn.commit()
    except sqlite3.OperationalError:
        # Column already exists, no action needed
        pass
    
    # Migration: Add DOB column to Users if it doesn't exist
    try:
        cursor.execute('ALTER TABLE Users ADD COLUMN DOB DATE')
        conn.commit()
    except sqlite3.OperationalError:
        # Column already exists, no action needed
        pass
    
    # Migration: Add EmployeeID column to Users if it doesn't exist
    try:
        cursor.execute('ALTER TABLE Users ADD COLUMN EmployeeID TEXT')
        conn.commit()
    except sqlite3.OperationalError:
        # Column already exists, no action needed
        pass
    
    # Migration: Add TimeConsumed column to UsersTestResults if it doesn't exist
    try:
        cursor.execute('ALTER TABLE UsersTestResults ADD COLUMN TimeConsumed REAL DEFAULT 0')
        conn.commit()
    except sqlite3.OperationalError:
        # Column already exists, no action needed
        pass
    
    # Migration: Add IP Address tracking table
    try:
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS UserSessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                UserId INTEGER NOT NULL,
                SessionId TEXT UNIQUE NOT NULL,
                LoginTime DATETIME DEFAULT CURRENT_TIMESTAMP,
                LogoutTime DATETIME,
                IPAddress TEXT,
                UserAgent TEXT,
                IsActive INTEGER DEFAULT 1,
                FOREIGN KEY (UserId) REFERENCES Users (UserId)
            )
        ''')
        conn.commit()
    except sqlite3.OperationalError:
        # Table already exists, check for missing columns
        try:
            cursor.execute('ALTER TABLE UserSessions ADD COLUMN IPAddress TEXT')
            conn.commit()
        except sqlite3.OperationalError:
            pass
        try:
            cursor.execute('ALTER TABLE UserSessions ADD COLUMN UserAgent TEXT')
            conn.commit()
        except sqlite3.OperationalError:
            pass
    
    # Migration: Add Answer5 column to UserAnswers if it doesn't exist
    try:
        cursor.execute('ALTER TABLE UserAnswers ADD COLUMN Answer5 TEXT')
        conn.commit()
    except sqlite3.OperationalError:
        # Column already exists, no action needed
        pass
    
    # Migration: Add IsActive column to UserAnswers if it doesn't exist
    try:
        cursor.execute('ALTER TABLE UserAnswers ADD COLUMN IsActive INTEGER DEFAULT 1')
        conn.commit()
    except sqlite3.OperationalError:
        # Column already exists, no action needed
        pass
    
    conn.commit()
    conn.close()

def get_db_connection():
    """Get database connection"""
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

def generate_random_question_order(test_name, user_id, test_result_id):
    """Generate a unique random question order for a user's test session"""
    conn = get_db_connection()
    
    # Get test settings to check NumberQuestionsForTest
    test_settings = conn.execute('''
        SELECT * FROM TestSettings WHERE TestName = ?
    ''', (test_name,)).fetchone()
    
    # Get all ACTIVE questions for this test
    questions = conn.execute('''
        SELECT id FROM UserAnswers 
        WHERE TestName = ? AND IsActive = 1
        ORDER BY QuestionNumber
    ''', (test_name,)).fetchall()
    
    conn.close()
    
    if not questions:
        return []
    
    # Create a list of question IDs
    question_ids = [question['id'] for question in questions]
    
    # Use user_id and test_result_id as seed for consistent randomization
    random.seed(user_id * 1000 + test_result_id)
    random.shuffle(question_ids)
    
    # Reset random seed to avoid affecting other random operations
    random.seed()
    
    # Limit to NumberQuestionsForTest if specified
    if test_settings:
        try:
            num_questions_for_test = test_settings['NumberQuestionsForTest']
            if num_questions_for_test and num_questions_for_test > 0:
                # Limit to the specified number of questions
                question_ids = question_ids[:num_questions_for_test]
        except (KeyError, IndexError, TypeError):
            # Column doesn't exist or is None, use all questions
            pass
    
    return question_ids

def get_questions_in_random_order(test_name, question_order, page, questions_per_page=10):
    """Get questions for a specific page in the randomized order"""
    if not question_order:
        return []
    
    # Calculate offset for pagination
    start_index = (page - 1) * questions_per_page
    end_index = start_index + questions_per_page
    
    # Get question IDs for this page
    page_question_ids = question_order[start_index:end_index]
    
    if not page_question_ids:
        return []
    
    # Get the actual questions from database
    conn = get_db_connection()
    placeholders = ','.join(['?' for _ in page_question_ids])
    
    # Build query to get questions in the specified order
    questions = []
    for question_id in page_question_ids:
        question = conn.execute('''
            SELECT * FROM UserAnswers 
            WHERE TestName = ? AND id = ? AND IsActive = 1
        ''', (test_name, question_id)).fetchone()
        if question:
            questions.append(question)
    
    conn.close()
    return questions

def get_current_test():
    """Get currently selected test for admin"""
    conn = get_db_connection()
    result = conn.execute('''
        SELECT ts.* FROM TestSettings ts
        JOIN CurrentTestSelection cts ON ts.id = cts.SelectedTestId
        WHERE cts.id = 1
    ''').fetchone()
    conn.close()
    return result

def get_active_test():
    """Get active test for users (IsActive = 1)"""
    conn = get_db_connection()
    result = conn.execute('SELECT * FROM TestSettings WHERE IsActive = 1').fetchone()
    conn.close()
    return result

def set_current_test(test_id):
    """Set current test selection for admin"""
    conn = get_db_connection()
    conn.execute('UPDATE CurrentTestSelection SET SelectedTestId = ? WHERE id = 1', (test_id,))
    conn.commit()
    conn.close()

def auto_submit_expired_tests():
    """Background function to automatically submit expired tests"""
    while True:
        try:
            # Get a fresh connection for each iteration
            conn = get_db_connection()
            
            # Find expired test sessions (both EndTime IS NULL and time exceeded)
            expired_sessions = conn.execute('''
                SELECT uts.*, ts.TimeDuration, ts.PassingGrade
                FROM UserTestSessions uts
                JOIN TestSettings ts ON uts.TestName = ts.TestName
                WHERE uts.EndTime IS NULL
                AND datetime('now') > datetime(uts.StartTime, '+' || ts.TimeDuration || ' minutes')
            ''').fetchall()
            
            sessions_processed = 0
            for expired_session in expired_sessions:
                try:
                    user_id = expired_session['UserId']
                    test_name = expired_session['TestName']
                    test_result_id = expired_session['TestResultId']
                    passing_grade = expired_session['PassingGrade']
                    
                    print(f"🕒 Processing expired session: User {user_id}, Test: {test_name}")
                    
                    # Calculate score based on answered questions
                    if test_result_id:
                        user_responses = conn.execute('''
                            SELECT ur.SelectedAnswer, ua.CorrectAnswer 
                            FROM UserResponses ur
                            JOIN UserAnswers ua ON ur.QuestionId = ua.id
                            WHERE ur.UserId = ? AND ur.TestResultId = ?
                        ''', (user_id, test_result_id)).fetchall()
                    else:
                        # Fallback for sessions without TestResultId
                        user_responses = conn.execute('''
                            SELECT ur.SelectedAnswer, ua.CorrectAnswer
                            FROM UserResponses ur
                            JOIN UserAnswers ua ON ur.QuestionId = ua.id
                            WHERE ur.UserId = ? AND ua.TestName = ?
                        ''', (user_id, test_name)).fetchall()
                    
                    # Calculate final score
                    if user_responses:
                        correct_answers = sum(1 for response in user_responses 
                                            if response['SelectedAnswer'] == response['CorrectAnswer'])
                        total_questions = len(user_responses)
                        score = (correct_answers / total_questions * 100) if total_questions > 0 else 0
                    else:
                        score = 0
                    
                    # Use full test duration for auto-submitted tests
                    time_consumed_minutes = expired_session['TimeDuration']
                    
                    # Ensure score is between 0 and 100
                    score = min(100, max(0, score))
                    pass_fail = 'Pass' if score >= passing_grade else 'Fail'
                    
                    # Update the existing test result if TestResultId exists, otherwise insert new
                    if test_result_id:
                        conn.execute('''
                            UPDATE UsersTestResults 
                            SET PassFail = ?, Score = ?, TestDate = CURRENT_TIMESTAMP, TimeConsumed = ?
                            WHERE id = ?
                        ''', (pass_fail, score, time_consumed_minutes, test_result_id))
                    else:
                        # Fallback: insert new result for sessions without TestResultId
                        conn.execute('''
                            INSERT INTO UsersTestResults (UserId, TestName, PassFail, Score, TimeConsumed)
                            VALUES (?, ?, ?, ?, ?)
                        ''', (user_id, test_name, pass_fail, score, time_consumed_minutes))
                    
                    # CRITICAL: Mark session as completed to remove from active sessions
                    conn.execute('''
                        UPDATE UserTestSessions 
                        SET IsActive = 0, EndTime = CURRENT_TIMESTAMP
                        WHERE UserId = ? AND TestName = ? AND EndTime IS NULL
                    ''', (user_id, test_name))
                    
                    print(f"✅ Auto-submitted expired test for user {user_id}: {test_name} (Score: {score:.1f}%, {pass_fail})")
                    sessions_processed += 1
                    
                except Exception as session_error:
                    print(f"❌ Error processing expired session for user {user_id}: {session_error}")
                    continue
            
            if sessions_processed > 0:
                conn.commit()
                print(f"✅ Auto-submitted {sessions_processed} expired test sessions")
            
            conn.close()
            
        except Exception as e:
            print(f"❌ Error in auto_submit_expired_tests: {e}")
        
        # Check every 5 seconds for expired tests (very aggressive for immediate auto-submit)
        time.sleep(5)

# Initialize database when app starts
init_db()

# Start background thread for auto-submission
def start_background_tasks():
    """Start background tasks"""
    auto_submit_thread = threading.Thread(target=auto_submit_expired_tests, daemon=True)
    auto_submit_thread.start()

# Start background tasks
start_background_tasks()

def cleanup_stale_sessions():
    """Clean up old/stale sessions that are more than 24 hours old"""
    conn = get_db_connection()
    
    # Clean up sessions older than 24 hours
    conn.execute('''
        UPDATE UserSessions 
        SET IsActive = 0 
        WHERE IsActive = 1 AND 
              datetime(LastActivity) < datetime('now', '-1 day')
    ''')
    
    conn.commit()
    conn.close()

def manage_user_session(user_id, is_admin=False):
    """Manage user session - check for existing sessions and handle conflicts"""
    browser_info = request.headers.get('User-Agent', 'Unknown')
    session_id = str(uuid.uuid4())
    
    # Clean up stale sessions first
    cleanup_stale_sessions()
    
    conn = get_db_connection()
    
    # Check for existing active sessions (after cleanup)
    existing_sessions = conn.execute('''
        SELECT * FROM UserSessions 
        WHERE UserId = ? AND IsAdmin = ? AND IsActive = 1
    ''', (user_id, 1 if is_admin else 0)).fetchall()
    
    if existing_sessions:
        # User has existing active session(s)
        return {
            'status': 'conflict',
            'message': 'User already logged in from another browser',
            'existing_sessions': len(existing_sessions),
            'session_id': session_id
        }
    
    # Create new session
    conn.execute('''
        INSERT INTO UserSessions (UserId, SessionId, IsAdmin, BrowserInfo)
        VALUES (?, ?, ?, ?)
    ''', (user_id, session_id, 1 if is_admin else 0, browser_info))
    
    conn.commit()
    conn.close()
    
    # Store session info
    session['session_id'] = session_id
    session['user_id'] = user_id
    if is_admin:
        session['admin'] = True
    
    return {
        'status': 'success',
        'session_id': session_id
    }

def takeover_user_session(user_id, is_admin=False):
    """Take over existing user session by deactivating old ones"""
    browser_info = request.headers.get('User-Agent', 'Unknown')
    session_id = str(uuid.uuid4())
    
    conn = get_db_connection()
    
    # Deactivate all existing sessions for this user
    conn.execute('''
        UPDATE UserSessions 
        SET IsActive = 0 
        WHERE UserId = ? AND IsAdmin = ?
    ''', (user_id, 1 if is_admin else 0))
    
    # Create new session
    conn.execute('''
        INSERT INTO UserSessions (UserId, SessionId, IsAdmin, BrowserInfo)
        VALUES (?, ?, ?, ?)
    ''', (user_id, session_id, 1 if is_admin else 0, browser_info))
    
    conn.commit()
    conn.close()
    
    # Store session info
    session['session_id'] = session_id
    session['user_id'] = user_id
    if is_admin:
        session['admin'] = True
    
    return session_id

def validate_session():
    """Validate current session"""
    if 'session_id' not in session or 'user_id' not in session:
        return False
    
    conn = get_db_connection()
    
    # Check if session is still active
    active_session = conn.execute('''
        SELECT * FROM UserSessions 
        WHERE SessionId = ? AND UserId = ? AND IsActive = 1
    ''', (session['session_id'], session['user_id'])).fetchone()
    
    if not active_session:
        conn.close()
        # Clear invalid session
        session.clear()
        return False
    
    # Update last activity
    conn.execute('''
        UPDATE UserSessions 
        SET LastActivity = CURRENT_TIMESTAMP 
        WHERE SessionId = ?
    ''', (session['session_id'],))
    
    conn.commit()
    conn.close()
    
    return True

def logout_user_session():
    """Logout user by deactivating session"""
    if 'session_id' in session:
        conn = get_db_connection()
        
        conn.execute('''
            UPDATE UserSessions 
            SET IsActive = 0 
            WHERE SessionId = ?
        ''', (session['session_id'],))
        
        conn.commit()
        conn.close()
    
    session.clear()

def check_test_attendance(test_id):
    """Check if a test has been attended by any users and return statistics"""
    conn = get_db_connection()
    
    # Check if any users have taken this test
    attendance_check = conn.execute('''
        SELECT COUNT(DISTINCT UserId) as user_count,
               COUNT(*) as total_attempts,
               AVG(CAST(Score AS FLOAT)) as avg_score,
               SUM(CASE WHEN PassFail = 'Pass' THEN 1 ELSE 0 END) as pass_count,
               SUM(CASE WHEN PassFail = 'Fail' THEN 1 ELSE 0 END) as fail_count,
               MIN(TestDate) as first_attempt,
               MAX(TestDate) as last_attempt
        FROM UsersTestResults utr
        JOIN TestSettings ts ON utr.TestName = ts.TestName
        WHERE ts.id = ?
    ''', (test_id,)).fetchone()
    
    conn.close()
    
    has_attendance = attendance_check['user_count'] > 0 if attendance_check else False
    
    return {
        'has_attendance': has_attendance,
        'stats': dict(attendance_check) if has_attendance else None
    }

def get_user_remaining_attempts(user_id, test_name, attempts_allowed):
    """Calculate remaining attempts for a user on a specific test"""
    conn = get_db_connection()
    
    # Count completed attempts (only count tests that have been submitted with scores)
    attempts_used = conn.execute('''
        SELECT COUNT(*) as count
        FROM UsersTestResults 
        WHERE UserId = ? AND TestName = ? 
        AND Score IS NOT NULL AND PassFail IS NOT NULL
    ''', (user_id, test_name)).fetchone()
    
    conn.close()
    
    used_count = attempts_used['count'] if attempts_used else 0
    remaining = max(0, attempts_allowed - used_count)
    
    return {
        'attempts_used': used_count,
        'attempts_remaining': remaining,
        'attempts_allowed': attempts_allowed
    }

@app.route('/')
def index():
    """Landing page - redirect to login"""
    return redirect(url_for('user_login'))

@app.route('/login')
def login_redirect():
    """Redirect /login to the correct user login page"""
    return redirect(url_for('user_login'))

@app.route('/admin_login', methods=['GET', 'POST'])
def admin_login():
    """Handle admin login"""
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        # Hash the password for comparison
        hashed_password = hashlib.sha256(password.encode()).hexdigest()
        
        conn = get_db_connection()
        admin_user = conn.execute('''
            SELECT * FROM Users WHERE UserName = ? AND UserPassword = ? AND IsEligible = 0
        ''', (username, hashed_password)).fetchone()
        
        if admin_user:
            session['username'] = admin_user['UserName']
            session['admin'] = True  # This was missing!
            session['user_id'] = admin_user['UserId']
            return redirect(url_for('admin_dashboard'))
        else:
            conn.close()
            flash('Invalid admin credentials')
            return render_template('admin_login.html')
    
    # GET request - show the admin login page
    return render_template('admin_login.html')

@app.route('/admin')
def admin():
    """Admin login page"""
    return render_template('admin_login.html')

@app.route('/admin/verify', methods=['POST'])
def admin_verify():
    """Verify admin credentials"""
    username = request.form.get('username')
    password = request.form.get('password')
    
    # Hash the provided password for comparison
    hashed_password = hashlib.sha256(password.encode()).hexdigest()
    
    conn = get_db_connection()
    admin = conn.execute('''
        SELECT * FROM Users WHERE UserName = ? AND UserPassword = ? AND IsEligible = 0
    ''', (username, hashed_password)).fetchone()
    conn.close()
    
    if admin:
        # Check for session conflicts
        session_result = manage_user_session(admin['UserId'], is_admin=True)
        
        if session_result['status'] == 'conflict':
            return render_template('session_conflict.html',
                                 session_type='admin',
                                 message=session_result['message'],
                                 user_id=admin['UserId'],
                                 session_id=session_result['session_id'])
        
        session['username'] = admin['UserName']
        session['admin'] = True  # CRITICAL: Set admin flag for authentication
        session['user_id'] = admin['UserId']
        return redirect(url_for('admin_dashboard'))
    else:
        flash('Invalid admin credentials')
        return redirect(url_for('admin_login'))

@app.route('/admin/dashboard')
def admin_dashboard():
    """Admin dashboard with statistics"""
    if not session.get('admin'):
        return redirect(url_for('admin_login'))
    
    conn = get_db_connection()
    
    # IMMEDIATELY clean up any expired sessions when admin dashboard loads
    cleanup_expired_sessions(conn)
    
    # FORCE immediate cleanup of any remaining expired sessions
    force_immediate_cleanup()
    
    # Get statistics (use same criteria as display query for accuracy)
    completed_tests = conn.execute('SELECT COUNT(DISTINCT UserId) FROM UsersTestResults').fetchone()[0]
    active_tests = conn.execute('''
        SELECT COUNT(*) FROM UserTestSessions uts
        JOIN Users u ON uts.UserId = u.UserId
        JOIN TestSettings ts ON uts.TestName = ts.TestName
        WHERE uts.EndTime IS NULL AND uts.IsActive = 1
    ''').fetchone()[0]
    
    # Get all tests with their question counts and details
    all_tests_with_questions = conn.execute('''
        SELECT ts.*, 
               COUNT(ua.id) as TotalQuestions,
               COUNT(CASE WHEN ua.IsActive = 1 THEN 1 END) as ActualQuestions,
               COUNT(CASE WHEN ua.IsActive = 0 THEN 1 END) as DisabledQuestions
        FROM TestSettings ts
        LEFT JOIN UserAnswers ua ON ts.TestName = ua.TestName
        GROUP BY ts.id, ts.TestName, ts.NumberTestQuestions, ts.TimeDuration, ts.CommonPassword, ts.TestPassword, ts.PassingGrade, ts.IsActive
        ORDER BY ts.TestName
    ''').fetchall()
    
    # Get ONLY truly active test sessions (exclude ended ones)
    raw_sessions = conn.execute('''
        SELECT 
            uts.UserId,
            uts.TestName,
            uts.StartTime,
            uts.CurrentPage,
            u.UserName,
            ts.TimeDuration,
            datetime(uts.StartTime, '+' || ts.TimeDuration || ' minutes') as EndTime
        FROM UserTestSessions uts
        JOIN Users u ON uts.UserId = u.UserId
        JOIN TestSettings ts ON uts.TestName = ts.TestName
        WHERE uts.EndTime IS NULL AND uts.IsActive = 1
        ORDER BY uts.StartTime DESC
    ''').fetchall()
    
    # Process sessions with robust status calculation
    active_sessions = []
    for db_session in raw_sessions:
        status_info = is_session_expired(db_session['StartTime'], db_session['TimeDuration'])
        
        # Create session dict with computed status
        session_dict = dict(db_session)
        session_dict['Status'] = 'Expired' if status_info['is_expired'] else 'Active'
        session_dict['ElapsedMinutes'] = round(status_info.get('elapsed_minutes', 0), 1)
        
        active_sessions.append(session_dict)
        
        # If expired, log it for debugging
        if status_info['is_expired']:
            print(f"⚠️ Found expired session: User {db_session['UserId']}, Test: {db_session['TestName']}, Elapsed: {session_dict['ElapsedMinutes']} min")
    
    # Force immediate cleanup of any expired sessions found
    if any(s['Status'] == 'Expired' for s in active_sessions):
        print("🕒 Triggering immediate cleanup of expired sessions...")
        force_immediate_cleanup()
        
        # Refresh the sessions list after cleanup
        raw_sessions = conn.execute('''
            SELECT 
                uts.UserId,
                uts.TestName,
                uts.StartTime,
                uts.CurrentPage,
                u.UserName,
                ts.TimeDuration,
                datetime(uts.StartTime, '+' || ts.TimeDuration || ' minutes') as EndTime
            FROM UserTestSessions uts
            JOIN Users u ON uts.UserId = u.UserId
            JOIN TestSettings ts ON uts.TestName = ts.TestName
            WHERE uts.EndTime IS NULL
            ORDER BY uts.StartTime DESC
        ''').fetchall()
        
        # Reprocess after cleanup
        active_sessions = []
        for db_session in raw_sessions:
            status_info = is_session_expired(db_session['StartTime'], db_session['TimeDuration'])
            session_dict = dict(db_session)
            session_dict['Status'] = 'Expired' if status_info['is_expired'] else 'Active'
            session_dict['ElapsedMinutes'] = round(status_info.get('elapsed_minutes', 0), 1)
            active_sessions.append(session_dict)
    
    # Get recent test results (last 10)
    recent_results = conn.execute('''
        SELECT 
            utr.*,
            u.UserName,
            CASE 
                WHEN utr.PassFail = 'Pass' THEN 'success'
                ELSE 'danger'
            END as StatusClass
        FROM UsersTestResults utr
        JOIN Users u ON utr.UserId = u.UserId
        ORDER BY utr.TestDate DESC
        LIMIT 10
    ''').fetchall()
    
    # Get users for cleanup functionality (excluding admin)
    users = conn.execute('SELECT * FROM Users WHERE UserName != "admin" ORDER BY UserName').fetchall()
    
    # Get recent login sessions for cleanup functionality
    recent_login_sessions = conn.execute('''
        SELECT 
            us.id,
            us.UserId,
            u.UserName,
            us.IPAddress,
            us.UserAgent,
            us.LoginTime,
            us.LogoutTime,
            us.IsActive
        FROM UserSessions us
        LEFT JOIN Users u ON us.UserId = u.UserId
        ORDER BY us.LoginTime DESC
        LIMIT 100
    ''').fetchall()
    
    # Get count of inactive sessions (for cleanup display)
    inactive_sessions_count = conn.execute('SELECT COUNT(*) FROM UserSessions WHERE IsActive = 0').fetchone()[0]
    
    conn.close()
    
    return render_template('admin_dashboard.html',
                         completed_tests=completed_tests,
                         active_tests=active_tests,
                         all_tests_with_questions=all_tests_with_questions,
                         active_sessions=active_sessions,
                         recent_results=recent_results,
                         users=users,
                         recent_login_sessions=recent_login_sessions,
                         inactive_sessions_count=inactive_sessions_count)

@app.route('/admin/activate-test/<int:test_id>')
def activate_test_from_dashboard(test_id):
    """Activate a test from admin dashboard (allows multiple active tests)"""
    if not session.get('admin'):
        return redirect(url_for('admin_login'))
    
    conn = get_db_connection()
    # Just activate the selected test (removed auto-deactivate logic)
    conn.execute('UPDATE TestSettings SET IsActive = 1 WHERE id = ?', (test_id,))
    conn.commit()
    conn.close()
    
    # Also set as current test for admin editing
    set_current_test(test_id)
    flash('Test activated successfully!')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/user-settings')
def user_settings():
    """Admin test settings page"""
    if not session.get('admin'):
        return redirect(url_for('admin_login'))
    
    conn = get_db_connection()
    
    # Get current test settings
    test_settings = get_current_test()
    
    # Get all tests with question counts for dropdown
    all_tests_with_questions = conn.execute('''
        SELECT ts.id, ts.TestName, ts.IsActive,
               COUNT(ua.id) as question_count
        FROM TestSettings ts
        LEFT JOIN UserAnswers ua ON ts.TestName = ua.TestName
        GROUP BY ts.id, ts.TestName, ts.IsActive
        ORDER BY ts.TestName
    ''').fetchall()
    
    # Get test attendance and statistics for current test
    test_attendance_info = None
    if test_settings:
        test_attendance_info = check_test_attendance(test_settings['id'])
    
    # Get all users (excluding admin)
    all_users = conn.execute('SELECT * FROM Users WHERE UserName != "admin" ORDER BY UserName').fetchall()
    
    conn.close()
    
    return render_template('user_settings.html',
                         test_settings=test_settings,
                         all_tests=all_tests_with_questions,
                         users=all_users,  # Fixed: changed from all_users to users
                         test_attendance_info=test_attendance_info)

@app.route('/admin/create-test', methods=['POST'])
def create_test():
    """Create new test set"""
    if not session.get('admin'):
        return redirect(url_for('admin_login'))
    
    test_name = request.form.get('test_name')
    num_questions = int(request.form.get('num_questions'))
    num_questions_for_test = int(request.form.get('num_questions_for_test'))
    time_duration = request.form.get('time_duration')
    common_password = request.form.get('common_password', 'defaultpass')  # Provide default value
    test_password = request.form.get('test_password')
    passing_grade = request.form.get('passing_grade', 70)
    attempts_allowed = request.form.get('attempts_allowed', 3)
    
    # Validate that num_questions_for_test doesn't exceed num_questions
    if num_questions_for_test > num_questions:
        flash(f'Error: Number of Questions for Test ({num_questions_for_test}) cannot exceed total Number of Questions ({num_questions}).')
        return redirect(url_for('user_settings'))
    
    conn = get_db_connection()
    try:
        # First, explicitly check if test name already exists
        existing_test = conn.execute('SELECT id FROM TestSettings WHERE TestName = ?', (test_name,)).fetchone()
        if existing_test:
            flash(f'Test name "{test_name}" already exists! Please choose a different name.')
            conn.close()
            return redirect(url_for('user_settings'))
        
        # If test name is unique, proceed with creation
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO TestSettings (TestName, NumberTestQuestions, NumberQuestionsForTest, TimeDuration, CommonPassword, TestPassword, PassingGrade, AttemptsAllowed)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (test_name, num_questions, num_questions_for_test, time_duration, common_password, test_password, passing_grade, attempts_allowed))
        new_test_id = cursor.lastrowid
        conn.commit()
        
        # Set new test as current selection
        set_current_test(new_test_id)
        flash(f'Test set "{test_name}" created successfully!')
        
    except sqlite3.IntegrityError as e:
        # More specific error handling
        error_msg = str(e).lower()
        if 'unique' in error_msg or 'duplicate' in error_msg:
            flash(f'Test name "{test_name}" already exists! Please choose a different name.')
        elif 'not null' in error_msg:
            flash(f'Missing required field. Please check all fields are filled.')
        else:
            flash(f'Database error: {str(e)}. Please check your input and try again.')
        print(f"IntegrityError in create_test: {e}")  # Debug logging
    except Exception as e:
        flash(f'An error occurred while creating the test: {str(e)}')
        print(f"General error in create_test: {e}")  # Debug logging
    finally:
        conn.close()
    
    return redirect(url_for('user_settings'))

@app.route('/admin/check-test-name', methods=['POST'])
def check_test_name():
    """Check if test name is available"""
    if not session.get('admin'):
        return jsonify({'success': False, 'message': 'Not authorized'})
    
    test_name = request.json.get('test_name', '').strip()
    current_test_id = request.json.get('current_test_id', None)
    
    if not test_name:
        return jsonify({'available': False, 'message': 'Test name cannot be empty'})
    
    conn = get_db_connection()
    
    if current_test_id:
        # Updating existing test - check excluding current test
        existing_test = conn.execute('''
            SELECT id FROM TestSettings 
            WHERE TestName = ? AND id != ?
        ''', (test_name, current_test_id)).fetchone()
    else:
        # Creating new test - check all tests
        existing_test = conn.execute('''
            SELECT id FROM TestSettings WHERE TestName = ?
        ''', (test_name,)).fetchone()
    
    conn.close()
    
    if existing_test:
        return jsonify({'available': False, 'message': f'Test name "{test_name}" already exists'})
    else:
        return jsonify({'available': True, 'message': 'Test name is available'})

@app.route('/admin/update-test-settings', methods=['POST'])
def update_test_settings():
    """Update test settings with attendance-based restrictions"""
    if not session.get('admin'):
        return redirect(url_for('admin_login'))
    
    current_test = get_current_test()
    if not current_test:
        flash('No test selected!')
        return redirect(url_for('user_settings'))
    
    # Check if test has been attended by users
    attendance_info = check_test_attendance(current_test['id'])
    has_attendance = attendance_info['has_attendance']
    
    # Get form data
    test_name = request.form.get('test_name')
    num_questions = int(request.form.get('num_questions'))
    num_questions_for_test = int(request.form.get('num_questions_for_test'))
    time_duration = request.form.get('time_duration')
    common_password = request.form.get('common_password')
    test_password = request.form.get('test_password')
    passing_grade = request.form.get('passing_grade', 70)
    attempts_allowed = request.form.get('attempts_allowed', 3)
    test_enabled = 1 if request.form.get('test_enabled') == 'on' else 0
    
    # Validate that num_questions_for_test doesn't exceed num_questions
    if num_questions_for_test > num_questions:
        flash(f'Error: Number of Questions for Test ({num_questions_for_test}) cannot exceed total Number of Questions ({num_questions}).')
        return redirect(url_for('user_settings'))
    
    conn = get_db_connection()
    
    # Validate restrictions for attended tests
    if has_attendance:
        # For attended tests, only certain fields can be edited
        # Check if restricted fields were changed
        if test_name != current_test['TestName']:
            flash('Error: Test name cannot be changed after users have attended the test.')
            conn.close()
            return redirect(url_for('user_settings'))
        
        if common_password != current_test['CommonPassword']:
            flash('Error: Common password cannot be changed after users have attended the test.')
            conn.close()
            return redirect(url_for('user_settings'))
        
        # Number of questions can only be increased, not decreased
        if num_questions < current_test['NumberTestQuestions']:
            flash(f'Error: Number of questions cannot be decreased from {current_test["NumberTestQuestions"]} to {num_questions} after users have attended the test.')
            conn.close()
            return redirect(url_for('user_settings'))
    
    # Check if the new test name already exists (excluding current test)
    if test_name != current_test['TestName']:
        existing_test = conn.execute('''
            SELECT id FROM TestSettings 
            WHERE TestName = ? AND id != ?
        ''', (test_name, current_test['id'])).fetchone()
        
        if existing_test:
            flash(f'Test name "{test_name}" already exists! Please choose a different name.')
            conn.close()
            return redirect(url_for('user_settings'))
    
    try:
        # Update test settings
        conn.execute('''
            UPDATE TestSettings 
            SET TestName = ?, NumberTestQuestions = ?, NumberQuestionsForTest = ?, TimeDuration = ?, CommonPassword = ?, TestPassword = ?, PassingGrade = ?, AttemptsAllowed = ?, IsActive = ?
            WHERE id = ?
        ''', (test_name, num_questions, num_questions_for_test, time_duration, common_password, test_password, passing_grade, attempts_allowed, test_enabled, current_test['id']))
        conn.commit()
        
        # Create appropriate success message
        status_msg = "enabled" if test_enabled == 1 else "disabled"
        restrictions_msg = " (with attendance restrictions applied)" if has_attendance else ""
        flash(f'Test settings updated successfully{restrictions_msg}! Test is now {status_msg} for users.')
    except sqlite3.IntegrityError:
        flash(f'Test name "{test_name}" already exists! Please choose a different name.')
    
    conn.close()
    return redirect(url_for('user_settings'))

@app.route('/admin/switch-test', methods=['POST'])
def switch_test():
    """Switch current test selection"""
    if not session.get('admin'):
        return redirect(url_for('admin_login'))
    
    test_id = request.form.get('test_id')
    set_current_test(test_id)
    flash('Test switched successfully!')
    return redirect(url_for('user_settings'))

@app.route('/admin/activate-test', methods=['POST'])
def activate_test():
    """Activate test for users (allows multiple active tests)"""
    if not session.get('admin'):
        return redirect(url_for('admin_login'))
    
    test_id = request.form.get('test_id')
    
    conn = get_db_connection()
    # Just activate the selected test (removed auto-deactivate logic)
    conn.execute('UPDATE TestSettings SET IsActive = 1 WHERE id = ?', (test_id,))
    conn.commit()
    conn.close()
    
    flash('Test activated for users!')
    return redirect(url_for('user_settings'))

@app.route('/admin/deactivate-test', methods=['POST'])
def deactivate_test():
    """Deactivate test for users"""
    if not session.get('admin'):
        return redirect(url_for('admin_login'))
    
    test_id = request.form.get('test_id')
    
    conn = get_db_connection()
    conn.execute('UPDATE TestSettings SET IsActive = 0 WHERE id = ?', (test_id,))
    conn.commit()
    conn.close()
    
    flash('Test deactivated for users!')
    return redirect(url_for('user_settings'))

@app.route('/admin/deactivate-test/<int:test_id>')
def deactivate_test_from_dashboard(test_id):
    """Deactivate a test from admin dashboard"""
    if not session.get('admin'):
        return redirect(url_for('admin_login'))
    
    conn = get_db_connection()
    conn.execute('UPDATE TestSettings SET IsActive = 0 WHERE id = ?', (test_id,))
    conn.commit()
    conn.close()
    
    flash('Test deactivated successfully!')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/test-settings')
def test_settings():
    """Admin page for managing test questions"""
    if not session.get('admin'):
        return redirect(url_for('admin_login'))
    
    # Get the currently selected test
    current_test = get_current_test()
    test_settings = None
    questions = []
    test_info_dict = None
    
    if current_test:
        conn = get_db_connection()
        # Fix: Use the ID from current_test row, not the entire row object
        test_settings = conn.execute('SELECT * FROM TestSettings WHERE id = ?', (current_test['id'],)).fetchone()
        
        if test_settings:
            questions = conn.execute('''
                SELECT * FROM UserAnswers 
                WHERE TestName = ? 
                ORDER BY QuestionNumber
            ''', (test_settings['TestName'],)).fetchall()
            
            # Calculate actual active questions count for display - BEFORE closing connection
            active_questions_count = conn.execute('''
                SELECT COUNT(*) as count FROM UserAnswers 
                WHERE TestName = ? AND IsActive = 1
            ''', (test_settings['TestName'],)).fetchone()
            
            # Create enhanced test info with correct count
            test_info_dict = dict(test_settings)
            test_info_dict['NumberTestQuestions'] = active_questions_count['count']
        
        conn.close()
    
    # Use the enhanced test_info_dict or fallback to original test_settings
    return render_template('test_settings.html', 
                         test_info=test_info_dict if test_info_dict else test_settings,
                         test_settings=test_settings,
                         questions=questions)

@app.route('/admin/add-question', methods=['POST'])
def add_question():
    """Add new question"""
    if not session.get('admin'):
        return redirect(url_for('admin_login'))
    
    # Get currently selected test
    test_info = get_current_test()
    if not test_info:
        flash('No test selected!')
        return redirect(url_for('user_settings'))
    
    question = request.form.get('question')
    answer1 = request.form.get('answer1')
    answer2 = request.form.get('answer2')
    answer3 = request.form.get('answer3')
    answer4 = request.form.get('answer4')
    answer5 = request.form.get('answer5', '').strip()  # Optional Answer5
    # Clean empty HTML content from Quill editor
    if answer5:
        import re
        # Remove common empty HTML patterns that Quill might generate
        clean_answer5 = re.sub(r'<p>\s*<br\s*/?>\s*</p>', '', answer5)
        clean_answer5 = re.sub(r'<p>\s*</p>', '', clean_answer5)
        clean_answer5 = re.sub(r'<br\s*/?>', '', clean_answer5)
        clean_answer5 = clean_answer5.strip()
        answer5 = clean_answer5 if clean_answer5 else ''
    correct_answer = request.form.get('correct_answer')
    
    # Handle image upload
    image_filename = None
    if 'question_image' in request.files:
        file = request.files['question_image']
        if file and file.filename != '' and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            # Add timestamp to prevent filename conflicts
            timestamp = str(int(time.time()))
            name, ext = os.path.splitext(filename)
            image_filename = f"{name}_{timestamp}{ext}"
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], image_filename))
    
    conn = get_db_connection()
    # Get next question number
    last_question = conn.execute('''
        SELECT MAX(QuestionNumber) as max_num FROM UserAnswers WHERE TestName = ? AND IsActive = 1 AND IsActive = 1 AND IsActive = 1
    ''', (test_info['TestName'],)).fetchone()
    
    next_question_num = (last_question['max_num'] or 0) + 1
    
    conn.execute('''
        INSERT INTO UserAnswers (TestName, Question, Answer1, Answer2, Answer3, Answer4, Answer5, CorrectAnswer, QuestionNumber, ImageFilename)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (test_info['TestName'], question, answer1, answer2, answer3, answer4, answer5 if answer5 else None, correct_answer, next_question_num, image_filename))
    
    conn.commit()
    conn.close()
    
    flash('Question added successfully!')
    return redirect(url_for('test_settings'))

@app.route('/admin/edit-question/<int:question_id>', methods=['POST'])
def edit_question(question_id):
    """Edit existing question"""
    if not session.get('admin'):
        return redirect(url_for('admin_login'))
    
    question = request.form.get('question')
    answer1 = request.form.get('answer1')
    answer2 = request.form.get('answer2')
    answer3 = request.form.get('answer3')
    answer4 = request.form.get('answer4')
    answer5 = request.form.get('answer5', '').strip()
    correct_answer = request.form.get('correct_answer')
    
    # Check if this is a True/False question type
    true_false_indicator = request.form.get('edit_true_false_indicator')
    
    # If True/False mode is selected, clear Answer3, Answer4, and Answer5
    if true_false_indicator:
        answer3 = ''
        answer4 = ''
        answer5 = ''
    
    conn = get_db_connection()
    
    # Update the question
    conn.execute('''
        UPDATE UserAnswers 
        SET Question = ?, Answer1 = ?, Answer2 = ?, Answer3 = ?, Answer4 = ?, Answer5 = ?, CorrectAnswer = ?
        WHERE id = ?
    ''', (question, answer1, answer2, answer3, answer4, answer5 if answer5 else None, correct_answer, question_id))
    
    conn.commit()
    conn.close()
    
    flash('Question updated successfully!')
    return redirect(url_for('test_settings'))

@app.route('/admin/delete-question/<int:question_id>', methods=['POST'])
def delete_question(question_id):
    """Soft delete question (disable from appearing in tests)"""
    if not session.get('admin'):
        if request.is_json or request.headers.get('Content-Type') == 'application/json':
            return jsonify({'success': False, 'message': 'Admin access required'})
        return redirect(url_for('admin_login'))
    
    try:
        conn = get_db_connection()
        
        # Check if question exists
        question = conn.execute('SELECT * FROM UserAnswers WHERE id = ?', (question_id,)).fetchone()
        if not question:
            conn.close()
            if request.is_json or request.headers.get('Content-Type') == 'application/json':
                return jsonify({'success': False, 'message': 'Question not found'})
            flash('Question not found.')
            return redirect(url_for('test_settings'))
        
        # Soft delete the question by setting IsActive = 0
        conn.execute('UPDATE UserAnswers SET IsActive = 0 WHERE id = ?', (question_id,))
        conn.commit()
        conn.close()
        
        # Return JSON for AJAX requests
        if request.is_json or request.headers.get('Content-Type') == 'application/json':
            return jsonify({'success': True, 'message': 'Question disabled successfully! It will no longer appear in tests.'})
        
        # Traditional form submission
        flash('Question disabled successfully! It will no longer appear in tests.')
        return redirect(url_for('test_settings'))
        
    except Exception as e:
        if request.is_json or request.headers.get('Content-Type') == 'application/json':
            return jsonify({'success': False, 'message': f'Error disabling question: {str(e)}'})
        flash(f'Error disabling question: {str(e)}')
        return redirect(url_for('test_settings'))

@app.route('/admin/enable-question/<int:question_id>', methods=['POST'])
def enable_question(question_id):
    """Re-enable a disabled question"""
    if not session.get('admin'):
        if request.is_json or request.headers.get('Content-Type') == 'application/json':
            return jsonify({'success': False, 'message': 'Admin access required'})
        return redirect(url_for('admin_login'))
    
    try:
        conn = get_db_connection()
        
        # Check if question exists
        question = conn.execute('SELECT * FROM UserAnswers WHERE id = ?', (question_id,)).fetchone()
        if not question:
            conn.close()
            if request.is_json or request.headers.get('Content-Type') == 'application/json':
                return jsonify({'success': False, 'message': 'Question not found'})
            flash('Question not found.')
            return redirect(url_for('test_settings'))
        
        # Re-enable the question by setting IsActive = 1
        conn.execute('UPDATE UserAnswers SET IsActive = 1 WHERE id = ?', (question_id,))
        conn.commit()
        conn.close()
        
        # Return JSON for AJAX requests
        if request.is_json or request.headers.get('Content-Type') == 'application/json':
            return jsonify({'success': True, 'message': 'Question enabled successfully! It will now appear in tests.'})
        
        # Traditional form submission
        flash('Question enabled successfully! It will now appear in tests.')
        return redirect(url_for('test_settings'))
        
    except Exception as e:
        if request.is_json or request.headers.get('Content-Type') == 'application/json':
            return jsonify({'success': False, 'message': f'Error enabling question: {str(e)}'})
        flash(f'Error enabling question: {str(e)}')
        return redirect(url_for('test_settings'))

@app.route('/user_login', methods=['GET', 'POST'])
def user_login():
    """Handle user login"""
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        # Hash the password for comparison
        hashed_password = hashlib.sha256(password.encode()).hexdigest()
        
        conn = get_db_connection()
        user = conn.execute('''
            SELECT * FROM Users WHERE UserName = ? AND UserPassword = ? AND IsEligible = 1
        ''', (username, hashed_password)).fetchone()
        
        if user:
            # Use proper session management
            session_result = manage_user_session(user['UserId'], is_admin=False)
            
            if session_result['status'] == 'conflict':
                return render_template('session_conflict.html',
                                     session_type='user',
                                     message=session_result['message'],
                                     user_id=user['UserId'],
                                     session_id=session_result['session_id'])
            
            # Set additional session data
            session['user_id'] = user['UserId']
            session['username'] = user['UserName']
            session.permanent = True
            
            # Store login session with IP address
            client_ip = get_client_ip()
            user_agent = request.headers.get('User-Agent', '')
            
            conn.execute('''
                UPDATE UserSessions 
                SET IPAddress = ?, UserAgent = ?, LoginTime = CURRENT_TIMESTAMP 
                WHERE SessionId = ?
            ''', (client_ip, user_agent, session_result['session_id']))
            
            conn.commit()
            conn.close()
            
            return redirect(url_for('user_dashboard'))
        else:
            conn.close()
            flash('Invalid credentials. Please try again.')
    
    return render_template('user_login.html')

@app.route('/dashboard')
def user_dashboard():
    """User dashboard showing test history and results"""
    if not session.get('user_id'):
        return redirect(url_for('user_login'))
    
    user_id = session['user_id']
    username = session['username']
    
    conn = get_db_connection()
    
    # FIRST: Check for and auto-submit any expired sessions for this user
    expired_sessions = conn.execute('''
        SELECT uts.*, ts.TimeDuration, ts.PassingGrade, uts.TestResultId
        FROM UserTestSessions uts
        JOIN TestSettings ts ON uts.TestName = ts.TestName
        WHERE uts.UserId = ? AND uts.EndTime IS NULL AND uts.IsActive = 1
    ''', (user_id,)).fetchall()
    
    for session_check in expired_sessions:
        status_info = is_session_expired(session_check['StartTime'], session_check['TimeDuration'])
        if status_info['is_expired']:
            # Auto-submit this expired session immediately
            try:
                test_result_id = session_check['TestResultId']
                test_name = session_check['TestName']
                passing_grade = session_check['PassingGrade']
                
                # Calculate score based on answered questions
                if test_result_id:
                    user_responses = conn.execute('''
                        SELECT ur.SelectedAnswer, ua.CorrectAnswer 
                        FROM UserResponses ur
                        JOIN UserAnswers ua ON ur.QuestionId = ua.id
                        WHERE ur.UserId = ? AND ur.TestResultId = ?
                    ''', (user_id, test_result_id)).fetchall()
                else:
                    user_responses = conn.execute('''
                        SELECT ur.SelectedAnswer, ua.CorrectAnswer
                        FROM UserResponses ur
                        JOIN UserAnswers ua ON ur.QuestionId = ua.id
                        WHERE ur.UserId = ? AND ua.TestName = ?
                    ''', (user_id, test_name)).fetchall()
                
                # Calculate final score
                if user_responses:
                    correct_answers = sum(1 for response in user_responses 
                                        if response['SelectedAnswer'] == response['CorrectAnswer'])
                    total_questions = len(user_responses)
                    score = (correct_answers / total_questions * 100) if total_questions > 0 else 0
                else:
                    score = 0
                
                # Use full test duration for auto-submitted tests
                time_consumed_minutes = session_check['TimeDuration']
                
                # Ensure score is between 0 and 100
                score = min(100, max(0, score))
                pass_fail = 'Pass' if score >= passing_grade else 'Fail'
                
                # Update the existing test result if TestResultId exists, otherwise insert new
                if test_result_id:
                    conn.execute('''
                        UPDATE UsersTestResults 
                        SET PassFail = ?, Score = ?, TestDate = CURRENT_TIMESTAMP, TimeConsumed = ?
                        WHERE id = ?
                    ''', (pass_fail, score, time_consumed_minutes, test_result_id))
                else:
                    # Fallback: insert new result for sessions without TestResultId
                    conn.execute('''
                        INSERT INTO UsersTestResults (UserId, TestName, PassFail, Score, TimeConsumed)
                        VALUES (?, ?, ?, ?, ?)
                    ''', (user_id, test_name, pass_fail, score, time_consumed_minutes))
                
                # Mark session as completed to remove from active sessions
                conn.execute('''
                    UPDATE UserTestSessions 
                    SET IsActive = 0, EndTime = CURRENT_TIMESTAMP
                    WHERE UserId = ? AND TestName = ? AND EndTime IS NULL
                ''', (user_id, test_name))
                
                print(f"✅ Auto-submitted expired test for user {user_id}: {test_name} (Score: {score:.1f}%, {pass_fail})")
                
            except Exception as e:
                print(f"❌ Error auto-submitting expired session in user dashboard: {e}")
    
    conn.commit()
    
    # Get user's test results (only completed tests with scores)
    test_results = conn.execute('''
        SELECT * FROM UsersTestResults 
        WHERE UserId = ? AND Score IS NOT NULL AND PassFail IS NOT NULL
        ORDER BY TestDate DESC
    ''', (user_id,)).fetchall()
    
    # Get all available tests (only active tests, avoid duplicates by showing latest version of each test name)
    available_tests = conn.execute('''
        SELECT * FROM TestSettings 
        WHERE id IN (
            SELECT MAX(id) FROM TestSettings GROUP BY TestName
        )
        AND IsActive = 1
        ORDER BY id DESC
    ''').fetchall()
    
    # Calculate remaining attempts for each test for this user
    tests_with_attempts = []
    for test in available_tests:
        attempt_info = get_user_remaining_attempts(user_id, test['TestName'], test['AttemptsAllowed'] or 3)
        test_dict = dict(test)
        test_dict.update(attempt_info)
        tests_with_attempts.append(test_dict)
    
    available_tests = tests_with_attempts
    
    # Get any TRULY active test sessions for this user (not expired or ended)
    # Use robust Python datetime comparison instead of SQLite datetime
    raw_active_sessions = conn.execute('''
        SELECT uts.*, ts.TimeDuration
        FROM UserTestSessions uts
        JOIN TestSettings ts ON uts.TestName = ts.TestName
        WHERE uts.UserId = ? AND uts.EndTime IS NULL AND uts.IsActive = 1
    ''', (user_id,)).fetchall()
    
    # Filter out expired sessions using robust Python datetime logic
    active_sessions = []
    for session_data in raw_active_sessions:
        status_info = is_session_expired(session_data['StartTime'], session_data['TimeDuration'])
        if not status_info['is_expired']:
            active_sessions.append(session_data)
        else:
            print(f"⚠️ Found expired session in user dashboard that should have been cleaned up: {session_data['TestName']}")
    
    # Calculate detailed statistics
    overall_stats = {
        'total_tests': len(test_results),
        'tests_passed': len([r for r in test_results if r['PassFail'] == 'Pass']),
        'tests_failed': len([r for r in test_results if r['PassFail'] == 'Fail']),
        'avg_score': 0,
        'highest_score': 0,
        'lowest_score': 0,
        'total_time_spent': 0,
        'total_unanswered': 0
    }
    
    # Calculate per-test statistics
    test_stats = {}
    
    if test_results:
        scores = [r['Score'] for r in test_results if r['Score'] is not None]
        if scores:
            overall_stats['avg_score'] = sum(scores) / len(scores)
            overall_stats['highest_score'] = max(scores)
            overall_stats['lowest_score'] = min(scores)
        
        # Calculate total unanswered questions across all completed tests
        total_unanswered = 0
        for result in test_results:
            if result['id']:  # Only count completed tests with valid IDs
                # Get test settings to access NumberQuestionsForTest
                test_settings = conn.execute('''
                    SELECT * FROM TestSettings WHERE TestName = ?
                ''', (result['TestName'],)).fetchone()
                
                # Use NumberQuestionsForTest if available, otherwise fall back to repository count
                try:
                    if test_settings and test_settings['NumberQuestionsForTest'] and test_settings['NumberQuestionsForTest'] > 0:
                        total_questions = test_settings['NumberQuestionsForTest']
                    else:
                        # Fallback to actual repository count
                        total_questions_result = conn.execute('''
                            SELECT COUNT(*) as count FROM UserAnswers 
                            WHERE TestName = ? AND IsActive = 1
                        ''', (result['TestName'],)).fetchone()
                        total_questions = total_questions_result['count'] if total_questions_result else 0
                except (KeyError, IndexError, TypeError):
                    # Column doesn't exist or is None, use repository count
                    total_questions_result = conn.execute('''
                        SELECT COUNT(*) as count FROM UserAnswers 
                        WHERE TestName = ? AND IsActive = 1
                    ''', (result['TestName'],)).fetchone()
                    total_questions = total_questions_result['count'] if total_questions_result else 0
                
                # Get answered questions for this specific test attempt
                answered_questions_result = conn.execute('''
                    SELECT COUNT(*) as count FROM UserResponses 
                    WHERE TestResultId = ? AND SelectedAnswer IS NOT NULL
                ''', (result['id'],)).fetchone()
                
                answered_questions = answered_questions_result['count'] if answered_questions_result else 0
                
                # Calculate unanswered for this test
                unanswered_in_test = max(0, total_questions - answered_questions)
                total_unanswered += unanswered_in_test
        
        overall_stats['total_unanswered'] = total_unanswered
        
        # Build per-test statistics
        for result in test_results:
            test_name = result['TestName']
            if test_name not in test_stats:
                test_stats[test_name] = {
                    'attempts': 0,
                    'passed': 0,
                    'failed': 0,
                    'best_score': 0,
                    'avg_time': 0,
                    'total_time': 0
                }
            
            test_stats[test_name]['attempts'] += 1
            if result['PassFail'] == 'Pass':
                test_stats[test_name]['passed'] += 1
            else:
                test_stats[test_name]['failed'] += 1
            
            # Track best score
            if result['Score'] > test_stats[test_name]['best_score']:
                test_stats[test_name]['best_score'] = result['Score']
            
            # Calculate time spent (if available)  
            if 'TimeConsumed' in result.keys() and result['TimeConsumed']:
                test_stats[test_name]['total_time'] += result['TimeConsumed']
        
        # Calculate average times
        for test_name in test_stats:
            if test_stats[test_name]['attempts'] > 0:
                test_stats[test_name]['avg_time'] = round(
                    test_stats[test_name]['total_time'] / test_stats[test_name]['attempts'], 1
                )
    
    conn.close()
    
    return render_template('user_dashboard.html',
                         username=username,
                         user_id=user_id,
                         test_results=test_results,
                         avg_score=overall_stats['avg_score'],
                         available_tests=available_tests,
                         active_sessions=active_sessions,
                         overall_stats=overall_stats,
                         test_stats=test_stats)

@app.route('/test/results/<int:result_id>')
def view_test_result(result_id):
    """View detailed test result with answers"""
    user_id = session.get('user_id')
    is_admin = session.get('admin')
    
    if not user_id and not is_admin:
        return redirect(url_for('user_login'))
    
    conn = get_db_connection()
    
    # Get the test result - allow admin to view any result, users can only view their own
    if is_admin:
        test_result = conn.execute('''
            SELECT utr.*, u.UserName, u.DOB, u.EmployeeID FROM UsersTestResults utr
            LEFT JOIN Users u ON utr.UserId = u.UserId
            WHERE utr.id = ?
        ''', (result_id,)).fetchone()
    else:
        test_result = conn.execute('''
            SELECT * FROM UsersTestResults 
            WHERE id = ? AND UserId = ?
        ''', (result_id, user_id)).fetchone()
    
    if not test_result:
        flash('Test result not found or access denied')
        if is_admin:
            return redirect(url_for('admin_transactions'))
        else:
            return redirect(url_for('user_dashboard'))
    
    # Get the test session to find which questions were actually presented
    test_session = conn.execute('''
        SELECT QuestionOrder FROM UserTestSessions 
        WHERE TestResultId = ?
        ORDER BY StartTime DESC LIMIT 1
    ''', (result_id,)).fetchone()
    
    if test_session and test_session['QuestionOrder']:
        # Parse the question order to get the list of question IDs that were actually presented
        try:
            presented_question_ids = json.loads(test_session['QuestionOrder'])
            question_ids_str = ','.join(map(str, presented_question_ids))
            
            # Get ONLY the questions that were actually presented during this test attempt
            all_questions = conn.execute(f'''
                SELECT ua.*, ur.SelectedAnswer, ur.IsMarked
                FROM UserAnswers ua
                LEFT JOIN UserResponses ur ON ua.id = ur.QuestionId 
                                           AND ur.TestResultId = ?
                WHERE ua.id IN ({question_ids_str}) AND ua.IsActive = 1
                ORDER BY CASE ua.id {' '.join([f'WHEN {qid} THEN {i}' for i, qid in enumerate(presented_question_ids)])} END
            ''', (result_id,)).fetchall()
        except (json.JSONDecodeError, ValueError):
            # Fallback: get all questions if QuestionOrder is corrupted
            all_questions = conn.execute('''
                SELECT ua.*, ur.SelectedAnswer, ur.IsMarked
                FROM UserAnswers ua
                LEFT JOIN UserResponses ur ON ua.id = ur.QuestionId 
                                           AND ur.TestResultId = ?
                WHERE ua.TestName = ? AND ua.IsActive = 1
                ORDER BY ua.QuestionNumber
            ''', (result_id, test_result['TestName'])).fetchall()
    else:
        # Fallback: get all questions if no test session found
        all_questions = conn.execute('''
            SELECT ua.*, ur.SelectedAnswer, ur.IsMarked
            FROM UserAnswers ua
            LEFT JOIN UserResponses ur ON ua.id = ur.QuestionId 
                                       AND ur.TestResultId = ?
            WHERE ua.TestName = ? AND ua.IsActive = 1
            ORDER BY ua.QuestionNumber
        ''', (result_id, test_result['TestName'])).fetchall()
    
    # Check if we got any responses (for legacy data compatibility)
    answered_questions = sum(1 for q in all_questions if q['SelectedAnswer'])
    
    # Fallback for legacy data
    if answered_questions == 0:
        is_legacy_result = conn.execute('''
            SELECT COUNT(*) as count FROM UserTestSessions 
            WHERE TestResultId = ?
        ''', (result_id,)).fetchone()
        
        if is_legacy_result['count'] == 0:
            print(f"Legacy test result detected for TestResultId {result_id}, using fallback query...")
            all_questions = conn.execute('''
                SELECT ua.*, ur.SelectedAnswer, ur.IsMarked
                FROM UserAnswers ua
                LEFT JOIN UserResponses ur ON ua.id = ur.QuestionId 
                                           AND ur.UserId = ? 
                                           AND ur.TestName = ?
                WHERE ua.TestName = ? AND ua.IsActive = 1
                ORDER BY ua.QuestionNumber
            ''', (test_result['UserId'], test_result['TestName'], test_result['TestName'])).fetchall()
            print(f"Legacy fallback applied - found {sum(1 for q in all_questions if q['SelectedAnswer'])} answered questions")
    
    # Get test settings for additional info
    test_settings = conn.execute('''
        SELECT * FROM TestSettings WHERE TestName = ?
    ''', (test_result['TestName'],)).fetchone()
    
    # Use NumberQuestionsForTest if available, otherwise fall back to repository count
    try:
        # Try to access NumberQuestionsForTest column
        num_questions_for_test = test_settings['NumberQuestionsForTest']
        if num_questions_for_test and num_questions_for_test > 0:
            total_questions = num_questions_for_test
        else:
            # Fallback to actual repository count
            total_questions = len(all_questions)  # Since query already filters by IsActive = 1
    except (KeyError, IndexError, TypeError):
        # Column doesn't exist or is None, use repository count
        total_questions = len(all_questions)  # Since query already filters by IsActive = 1
    
    # Calculate statistics correctly
    correct_answers = sum(1 for q in all_questions 
                         if q['SelectedAnswer'] and q['SelectedAnswer'] == q['CorrectAnswer'])
    answered_questions = sum(1 for q in all_questions if q['SelectedAnswer'])
    incorrect_answers = sum(1 for q in all_questions 
                           if q['SelectedAnswer'] and q['SelectedAnswer'] != q['CorrectAnswer'])
    unanswered_questions = total_questions - answered_questions
    
    conn.close()
    
    return render_template('test_result_detail.html',
                         test_result=test_result,
                         questions=all_questions,
                         total_questions=total_questions,
                         correct_answers=correct_answers,
                         incorrect_answers=incorrect_answers,
                         answered_questions=answered_questions,
                         unanswered_questions=unanswered_questions,
                         test_settings=test_settings,
                         is_admin_view=is_admin)

@app.route('/test/instructions')
def test_instructions():
    """Test instructions page"""
    if not session.get('user_id'):
        return redirect(url_for('user_login'))
    
    test_settings = get_active_test()
    
    # Use NumberQuestionsForTest if available, otherwise fall back to NumberTestQuestions
    if test_settings:
        # Convert to dict for easier manipulation
        test_settings_dict = dict(test_settings)
        
        # If NumberQuestionsForTest is not set or is None, use NumberTestQuestions as fallback
        if not test_settings_dict.get('NumberQuestionsForTest'):
            test_settings_dict['NumberQuestionsForTest'] = test_settings_dict['NumberTestQuestions']
        
        return render_template('test_instructions.html', test_settings=test_settings_dict)
    
    return render_template('test_instructions.html', test_settings=test_settings)

@app.route('/test/start/<int:test_id>')
def start_test(test_id):
    """Start a specific test"""
    if not session.get('user_id'):
        return redirect(url_for('user_login'))
    
    user_id = session['user_id']
    
    conn = get_db_connection()
    test_settings = conn.execute('SELECT * FROM TestSettings WHERE id = ?', (test_id,)).fetchone()
    
    if not test_settings:
        flash('Test not found')
        return redirect(url_for('user_dashboard'))
    
    # Check if test is active/enabled for users
    if not test_settings['IsActive']:
        flash('This test is currently disabled and not available for taking.')
        return redirect(url_for('user_dashboard'))
    
    # Check if test has questions
    question_count = conn.execute('''
        SELECT COUNT(*) as count FROM UserAnswers WHERE TestName = ? AND IsActive = 1
    ''', (test_settings['TestName'],)).fetchone()
    
    if question_count['count'] == 0:
        conn.close()
        return render_template('test_error.html', 
                             error_message="This test has no questions to attend, inform administrator",
                             test_name=test_settings['TestName'])
    
    # Check if user already has an active session for this test
    existing_session = conn.execute('''
        SELECT * FROM UserTestSessions 
        WHERE UserId = ? AND TestName = ? AND IsActive = 1
    ''', (user_id, test_settings['TestName'])).fetchone()
    
    if not existing_session:
        # Create new test result record first (for linking responses)
        cursor = conn.execute('''
            INSERT INTO UsersTestResults (UserId, TestName, PassFail, Score)
            VALUES (?, ?, ?, ?)
        ''', (user_id, test_settings['TestName'], 'In Progress', 0))
        
        # Get the newly created test result ID from the cursor
        test_result_id = cursor.lastrowid
        
        # Generate random question order for this user and test session
        question_order = generate_random_question_order(test_settings['TestName'], user_id, test_result_id)
        question_order_json = json.dumps(question_order)
        
        # Create new test session linked to test result with question order and start time
        start_time = datetime.datetime.now()
        conn.execute('''
            INSERT INTO UserTestSessions (UserId, TestName, CurrentPage, TestResultId, QuestionOrder, StartTime)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (user_id, test_settings['TestName'], 1, test_result_id, question_order_json, start_time))
        conn.commit()
    else:
        # If user has an existing active session, they're returning to the test
        flash('Resuming your active test session...')
        session_start_time = parse_datetime_string(existing_session['StartTime'])
        
        test_duration_minutes = test_settings['TimeDuration'] # Changed from TestDuration to TimeDuration
        elapsed_time = datetime.datetime.now() - session_start_time
        
        if elapsed_time.total_seconds() / 60 > test_duration_minutes:
            # Test has expired, auto-submit it
            flash('Your test time has expired. The test has been submitted automatically.')
            
            # Auto-submit the expired test by calling the function directly
            conn = get_db_connection()
            
            # Calculate score and update test result
            user_responses = conn.execute('''
                SELECT ur.SelectedAnswer, ua.CorrectAnswer 
                FROM UserResponses ur
                JOIN UserAnswers ua ON ur.QuestionId = ua.id
                WHERE ur.UserId = ? AND ur.TestResultId = ?
            ''', (user_id, existing_session['TestResultId'])).fetchall()
            
            correct_answers = sum(1 for response in user_responses if response['SelectedAnswer'] == response['CorrectAnswer'])
            total_questions = len(user_responses) if user_responses else 1
            score = (correct_answers / total_questions) * 100
            
            # Calculate time consumed
            time_consumed = elapsed_time.total_seconds() / 60  # in minutes
            
            # Update the test result
            conn.execute('''
                UPDATE UsersTestResults 
                SET PassFail = ?, Score = ?, TimeConsumed = ?, TestDate = CURRENT_TIMESTAMP
                WHERE id = ?
            ''', ('Failed' if score < test_settings['PassingGrade'] else 'Passed', 
                  score, time_consumed, existing_session['TestResultId']))
            
            # Deactivate the test session
            conn.execute('''
                UPDATE UserTestSessions 
                SET IsActive = 0 
                WHERE UserId = ? AND TestName = ?
            ''', (user_id, test_settings['TestName']))
            
            conn.commit()
            conn.close()
            
            return redirect(url_for('view_test_result', result_id=existing_session['TestResultId']))
        
        # Return to the last page they were on
        current_page = existing_session['CurrentPage'] or 1
        conn.close()
        return redirect(url_for('test_page', page=current_page))
    
    conn.close()
    
    return redirect(url_for('test_page', page=1))

@app.route('/test/verify-password/<int:test_id>')
def verify_test_password_form(test_id):
    """Test password verification form"""
    if not session.get('user_id'):
        return redirect(url_for('user_login'))
    
    conn = get_db_connection()
    test_settings = conn.execute('SELECT * FROM TestSettings WHERE id = ?', (test_id,)).fetchone()
    conn.close()
    
    if not test_settings:
        flash('Test not found')
        return redirect(url_for('user_dashboard'))
    
    # Check if test is active/enabled for users
    if not test_settings['IsActive']:
        flash('This test is currently disabled and not available for taking.')
        return redirect(url_for('user_dashboard'))
    
    # Check if user has remaining attempts for this test
    user_id = session.get('user_id')
    attempt_info = get_user_remaining_attempts(user_id, test_settings['TestName'], test_settings['AttemptsAllowed'] or 3)
    
    if attempt_info['attempts_remaining'] <= 0:
        flash(f'You have exhausted all attempts for this test. You have used {attempt_info["attempts_used"]}/{attempt_info["attempts_allowed"]} attempts.')
        return redirect(url_for('user_dashboard'))
    
    return render_template('test_password_verify.html', test_settings=test_settings,
                            test_id=test_id)

@app.route('/test/verify-password/<int:test_id>', methods=['POST'])
def verify_test_password(test_id):
    """Verify test password and proceed to instructions"""
    if not session.get('user_id'):
        return redirect(url_for('user_login'))
    
    test_password = request.form.get('test_password')
    
    conn = get_db_connection()
    test_settings = conn.execute('SELECT * FROM TestSettings WHERE id = ?', (test_id,)).fetchone()
    conn.close()
    
    if not test_settings:
        flash('Test not found')
        return redirect(url_for('user_dashboard'))
    
    # Check if test is active/enabled for users
    if not test_settings['IsActive']:
        flash('This test is currently disabled and not available for taking.')
        return redirect(url_for('user_dashboard'))
    
    # Check if user has remaining attempts for this test
    user_id = session.get('user_id')
    attempt_info = get_user_remaining_attempts(user_id, test_settings['TestName'], test_settings['AttemptsAllowed'] or 3)
    
    if attempt_info['attempts_remaining'] <= 0:
        flash(f'You have exhausted all attempts for this test. You have used {attempt_info["attempts_used"]}/{attempt_info["attempts_allowed"]} attempts.')
        return redirect(url_for('user_dashboard'))
    
    if test_settings['TestPassword'] and test_password == test_settings['TestPassword']:
        return redirect(url_for('test_instructions_for_test', test_id=test_id))
    else:
        flash('Incorrect test password')
        return redirect(url_for('verify_test_password_form', test_id=test_id))

@app.route('/test/instructions/<int:test_id>')
def test_instructions_for_test(test_id):
    """Test instructions page for specific test"""
    if not session.get('user_id'):
        return redirect(url_for('user_login'))
    
    conn = get_db_connection()
    test_settings = conn.execute('SELECT * FROM TestSettings WHERE id = ?', (test_id,)).fetchone()
    
    if not test_settings:
        conn.close()
        flash('Test not found')
        return redirect(url_for('user_dashboard'))
    
    # Check if test is active/enabled for users
    if not test_settings['IsActive']:
        conn.close()
        flash('This test is currently disabled.')
        return redirect(url_for('user_dashboard'))
    
    # Convert to dict for easier manipulation
    test_settings_dict = dict(test_settings)
    
    # If NumberQuestionsForTest is not set or is None, use NumberTestQuestions as fallback
    if not test_settings_dict.get('NumberQuestionsForTest'):
        test_settings_dict['NumberQuestionsForTest'] = test_settings_dict['NumberTestQuestions']
    
    conn.close()
    
    return render_template('test_instructions.html', 
                         test_settings=test_settings_dict,
                         test_id=test_id)

@app.route('/test/page/<int:page>')
def test_page(page):
    """Display test page with questions"""
    if not session.get('user_id'):
        return redirect(url_for('user_login'))
    
    user_id = session['user_id']
    
    conn = get_db_connection()
    
    # Get the user's active test session to determine which test they're taking
    active_session = conn.execute('''
        SELECT * FROM UserTestSessions 
        WHERE UserId = ? AND IsActive = 1 
        ORDER BY StartTime DESC
        LIMIT 1
    ''', (user_id,)).fetchone()
    
    if not active_session:
        flash('No active test session found. Please start a test first.')
        conn.close()
        return redirect(url_for('user_dashboard'))
    
    # Get the test settings for the current test
    test_settings = conn.execute('''
        SELECT * FROM TestSettings WHERE TestName = ?
    ''', (active_session['TestName'],)).fetchone()
    
    if not test_settings:
        flash('Test configuration not found.')
        conn.close()
        return redirect(url_for('user_dashboard'))
    
    # Update current page in session
    conn.execute('''
        UPDATE UserTestSessions 
        SET CurrentPage = ? 
        WHERE UserId = ? AND TestName = ? AND IsActive = 1
    ''', (page, user_id, active_session['TestName']))
    
    # Get randomized questions for this page
    question_order = []
    if active_session['QuestionOrder']:
        question_order = json.loads(active_session['QuestionOrder'])
    
    if question_order:
        # Use randomized order
        questions = get_questions_in_random_order(test_settings['TestName'], question_order, page)
    else:
        # Fallback to original order (for legacy sessions)
        offset = (page - 1) * 10
        questions = conn.execute('''
            SELECT * FROM UserAnswers 
            WHERE TestName = ? AND IsActive = 1
            ORDER BY QuestionNumber 
            LIMIT 10 OFFSET ?
        ''', (test_settings['TestName'], offset)).fetchall()
    
    # Add global sequential numbering to questions (excluding disabled questions)
    # Get all active questions in order to determine global position
    all_active_questions = conn.execute('''
        SELECT id FROM UserAnswers 
        WHERE TestName = ? AND IsActive = 1 
        ORDER BY QuestionNumber
    ''', (test_settings['TestName'],)).fetchall()
    
    # Create mapping of question ID to global sequential number
    question_id_to_global_number = {}
    for i, q in enumerate(all_active_questions):
        question_id_to_global_number[q['id']] = i + 1
    
    # Add global sequential numbering to current page questions
    questions_with_numbers = []
    for question in questions:
        question_dict = dict(question)
        question_dict['sequential_number'] = question_id_to_global_number.get(question['id'], question['QuestionNumber'])
        questions_with_numbers.append(question_dict)
    questions = questions_with_numbers
    
    # DON'T SORT! Keep randomized order but assign sequential display numbers
    # Assign sequential display numbers based on the randomized order position
    for i, question_dict in enumerate(questions):
        # Calculate the sequential number based on current page and position  
        page_start = (page - 1) * 10  # questions per page
        question_dict['sequential_number'] = page_start + i + 1
    
    # Get user's previous responses for the current test session
    user_responses = {}
    marked_questions = {}
    for question in questions:
        response = conn.execute('''
            SELECT SelectedAnswer, IsMarked FROM UserResponses 
            WHERE TestResultId = ? AND QuestionId = ?
        ''', (active_session['TestResultId'], question['id'])).fetchone()
        
        if response:
            user_responses[question['id']] = response['SelectedAnswer']
            marked_questions[question['id']] = response['IsMarked']
    
    # Use NumberQuestionsForTest if available, otherwise fall back to repository count
    try:
        # Try to access NumberQuestionsForTest column
        num_questions_for_test = test_settings['NumberQuestionsForTest']
        if num_questions_for_test and num_questions_for_test > 0:
            total_questions_count = num_questions_for_test
        else:
            # Fallback to actual repository count
            total_questions_result = conn.execute('''
                SELECT COUNT(*) as count FROM UserAnswers WHERE TestName = ? AND IsActive = 1
            ''', (test_settings['TestName'],)).fetchone()
            total_questions_count = total_questions_result['count']
    except (KeyError, IndexError, TypeError):
        # Column doesn't exist or is None, use repository count
        total_questions_result = conn.execute('''
            SELECT COUNT(*) as count FROM UserAnswers WHERE TestName = ? AND IsActive = 1
        ''', (test_settings['TestName'],)).fetchone()
        total_questions_count = total_questions_result['count']
    
    total_pages = (total_questions_count + 9) // 10  # Ceiling division
    
    # Use the active session for timer
    test_session = active_session
    
    conn.commit()
    conn.close()
    
    # Check if this is an AJAX request (for Exit Test functionality)
    if request.is_json or request.headers.get('Content-Type') == 'application/json':
        return jsonify({
            'success': True,
            'result_id': test_result_id,
            'redirect': f'/test/results/{test_result_id}'
        })
    
    
    # Calculate question offset for sequential numbering
    question_offset = (page - 1) * 10
    
    return render_template('test_page.html', 
                         questions=questions,
                         current_page=page,
                         total_pages=total_pages,
                         total_questions=total_questions_count,
                         test_settings=test_settings,
                           question_offset=question_offset,
                         user_responses=user_responses,
                         marked_questions=marked_questions,
                         test_session=test_session)

@app.route('/test/save-answer', methods=['POST'])
def save_answer():
    """Save user's answer"""
    if not session.get('user_id'):
        return jsonify({'success': False, 'message': 'Not logged in'})
    
    user_id = session['user_id']
    question_id = request.json.get('question_id')
    selected_answer = request.json.get('selected_answer')
    
    conn = get_db_connection()
    
    # Get the user's active test session to determine which test they're taking
    active_session = conn.execute('''
        SELECT TestName, TestResultId FROM UserTestSessions 
        WHERE UserId = ? AND IsActive = 1 
        ORDER BY StartTime DESC
        LIMIT 1
    ''', (user_id,)).fetchone()
    
    if not active_session:
        conn.close()
        return jsonify({'success': False, 'message': 'No active test session'})
    
    # Insert or update user response with TestResultId for proper linking
    conn.execute('''
        INSERT OR REPLACE INTO UserResponses (UserId, TestName, QuestionId, SelectedAnswer, TestResultId)
        VALUES (?, ?, ?, ?, ?)
    ''', (user_id, active_session['TestName'], question_id, selected_answer, active_session['TestResultId']))
    
    conn.commit()
    conn.close()
    
    return jsonify({'success': True})

@app.route('/test/mark-question', methods=['POST'])
def mark_question():
    """Mark/unmark a question"""
    if not session.get('user_id'):
        return jsonify({'success': False, 'message': 'Not logged in'})
    
    user_id = session['user_id']
    question_id = request.json.get('question_id')
    is_marked = request.json.get('is_marked')
    
    conn = get_db_connection()
    
    # Get the user's active test session to determine which test they're taking
    active_session = conn.execute('''
        SELECT TestName, TestResultId FROM UserTestSessions 
        WHERE UserId = ? AND IsActive = 1 
        ORDER BY StartTime DESC
        LIMIT 1
    ''', (user_id,)).fetchone()
    
    if not active_session:
        conn.close()
        return jsonify({'success': False, 'message': 'No active test session'})
    
    # Update mark status - use INSERT OR REPLACE to handle both insert and update
    conn.execute('''
        INSERT OR REPLACE INTO UserResponses (UserId, TestName, QuestionId, SelectedAnswer, IsMarked, TestResultId)
        VALUES (?, ?, ?, 
                COALESCE((SELECT SelectedAnswer FROM UserResponses 
                         WHERE UserId = ? AND TestName = ? AND QuestionId = ? AND TestResultId = ?), NULL), 
                ?, ?)
    ''', (user_id, active_session['TestName'], question_id, 
          user_id, active_session['TestName'], question_id, active_session['TestResultId'], 
          is_marked, active_session['TestResultId']))
    
    conn.commit()
    conn.close()
    
    return jsonify({'success': True})

@app.route('/test/submit', methods=['GET', 'POST'])
def submit_test():
    """Submit the test and calculate results"""
    if not session.get('user_id'):
        return redirect(url_for('user_login'))
    
    # If GET request, redirect to dashboard (shouldn't access this via GET normally)
    if request.method == 'GET':
        flash('Invalid access to test submission. Please start a test properly.')
        return redirect(url_for('user_dashboard'))
    
    user_id = session['user_id']
    
    conn = get_db_connection()
    
    # Get the user's active test session to determine which test they're taking
    active_session = conn.execute('''
        SELECT TestName, TestResultId, StartTime FROM UserTestSessions 
        WHERE UserId = ? AND IsActive = 1 
        ORDER BY StartTime DESC
        LIMIT 1
    ''', (user_id,)).fetchone()
    
    if not active_session:
        flash('No active test session found.')
        conn.close()
        return redirect(url_for('user_dashboard'))
    
    test_name = active_session['TestName']
    test_result_id = active_session['TestResultId']
    start_time = active_session['StartTime']
    
    # Calculate time consumed
    from datetime import datetime
    start_datetime = parse_datetime_string(start_time)
    end_datetime = datetime.now()
    time_consumed_seconds = (end_datetime - start_datetime).total_seconds()
    time_consumed_minutes = round(time_consumed_seconds / 60, 1)
    
    # Get test settings to get passing grade
    test_settings = conn.execute('''
        SELECT * FROM TestSettings WHERE TestName = ?
    ''', (test_name,)).fetchone()
    
    if not test_settings:
        flash('Test settings not found.')
        conn.close()
        return redirect(url_for('user_dashboard'))
    
    passing_grade = test_settings['PassingGrade'] or 70
    
    # Use NumberQuestionsForTest if available, otherwise fall back to repository count
    try:
        # Try to access NumberQuestionsForTest column
        num_questions_for_test = test_settings['NumberQuestionsForTest']
        if num_questions_for_test and num_questions_for_test > 0:
            actual_total_questions = num_questions_for_test
        else:
            # Fallback to actual repository count
            total_questions_result = conn.execute('''
                SELECT COUNT(*) as count FROM UserAnswers 
                WHERE TestName = ? AND IsActive = 1
            ''', (test_name,)).fetchone()
            actual_total_questions = total_questions_result['count']
    except (KeyError, IndexError, TypeError):
        # Column doesn't exist or is None, use repository count
        total_questions_result = conn.execute('''
            SELECT COUNT(*) as count FROM UserAnswers 
            WHERE TestName = ? AND IsActive = 1
        ''', (test_name,)).fetchone()
        actual_total_questions = total_questions_result['count']
    
    # Get user's responses for THIS specific test attempt
    user_responses = conn.execute('''
        SELECT ur.QuestionId, ur.SelectedAnswer, ua.CorrectAnswer
        FROM UserResponses ur
        JOIN UserAnswers ua ON ur.QuestionId = ua.id
        WHERE ur.TestResultId = ?
    ''', (test_result_id,)).fetchall()
    
    # Calculate score correctly
    answered_questions = len(user_responses)
    correct_answers = sum(1 for response in user_responses 
                         if response['SelectedAnswer'] == response['CorrectAnswer'])
    incorrect_answers = answered_questions - correct_answers
    unanswered_questions = actual_total_questions - answered_questions
    
    # Score calculation: only based on answered questions, but capped at 100%
    if answered_questions > 0:
        score = min(100, (correct_answers / actual_total_questions) * 100)
    else:
        score = 0
    
    # Ensure score doesn't exceed 100%
    score = min(100, max(0, score))
    
    pass_fail = 'Pass' if score >= passing_grade else 'Fail'
    
    # Update the test result record with time consumed
    conn.execute('''
        UPDATE UsersTestResults 
        SET PassFail = ?, Score = ?, TestDate = CURRENT_TIMESTAMP, TimeConsumed = ?
        WHERE id = ?
    ''', (pass_fail, score, time_consumed_minutes, test_result_id))
    
    # Mark test session as completed
    conn.execute('''
        UPDATE UserTestSessions 
        SET IsActive = 0, EndTime = CURRENT_TIMESTAMP
        WHERE UserId = ? AND TestName = ? AND IsActive = 1
    ''', (user_id, test_name))
    
    conn.commit()
    conn.close()
    
    # Check if this is an AJAX request (for Exit Test functionality)
    if request.is_json or request.headers.get('Content-Type') == 'application/json':
        return jsonify({
            'success': True,
            'result_id': test_result_id,
            'redirect': f'/test/results/{test_result_id}'
        })
    
    return render_template('test_result.html', 
                         score=score, 
                         pass_fail=pass_fail,
                         correct_answers=correct_answers,
                         incorrect_answers=incorrect_answers,
                         answered_questions=answered_questions,
                         unanswered_questions=unanswered_questions,
                         total_questions=actual_total_questions,
                         test_name=test_name,
                         test_settings=test_settings,
                         passing_grade=passing_grade,
                         time_consumed=time_consumed_minutes,
                         result_id=test_result_id)

@app.route('/api/check-session-status')
def check_session_status():
    """Check if current user's test session has expired"""
    if not session.get('user_id'):
        return jsonify({'expired': True, 'message': 'Not logged in'})
    
    user_id = session['user_id']
    
    conn = get_db_connection()
    
    # Check if user has any active sessions that have expired
    expired_session = conn.execute('''
        SELECT uts.*, ts.TimeDuration 
        FROM UserTestSessions uts
        JOIN TestSettings ts ON uts.TestName = ts.TestName
        WHERE uts.UserId = ? AND uts.IsActive = 1 
        AND datetime(uts.StartTime, '+' || ts.TimeDuration || ' minutes') <= datetime('now')
    ''', (user_id,)).fetchone()
    
    conn.close()
    
    if expired_session:
        return jsonify({'expired': True, 'message': 'Test session has expired'})
    else:
        return jsonify({'expired': False, 'message': 'Session active'})


@app.route('/test/exit-test', methods=['POST'])
def exit_test():
    """Exit test and mark as failed"""
    if not session.get('user_id'):
        return jsonify({'success': False, 'message': 'Not logged in'})
    
    user_id = session['user_id']
    
    conn = get_db_connection()
    
    try:
        # Get active test session
        active_session = conn.execute('''
            SELECT * FROM UserTestSessions 
            WHERE UserId = ? AND IsActive = 1 
            ORDER BY StartTime DESC
            LIMIT 1
        ''', (user_id,)).fetchone()
        
        if not active_session:
            conn.close()
            return jsonify({'success': False, 'message': 'No active test session found'})
        
        test_result_id = active_session['TestResultId']
        
        # If TestResultId is missing, create a new result record
        if not test_result_id:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO UsersTestResults (UserId, TestName, PassFail, Score, TestDate)
                VALUES (?, ?, 'Fail', 0, CURRENT_TIMESTAMP)
            ''', (user_id, active_session['TestName']))
            test_result_id = cursor.lastrowid
            
            # Update the session with the new TestResultId
            conn.execute('''
                UPDATE UserTestSessions 
                SET TestResultId = ?
                WHERE id = ?
            ''', (test_result_id, active_session['id']))
        else:
            # Update existing test result as failed
            conn.execute('''
                UPDATE UsersTestResults 
                SET PassFail = 'Fail', Score = 0, TestDate = CURRENT_TIMESTAMP
                WHERE id = ?
            ''', (test_result_id,))
        
        # End the test session
        conn.execute('''
            UPDATE UserTestSessions 
            SET IsActive = 0, EndTime = CURRENT_TIMESTAMP
            WHERE id = ?
        ''', (active_session['id'],))
        
        conn.commit()
        conn.close()
        
        return jsonify({
            'success': True, 
            'message': 'Test exited successfully', 
            'result_id': test_result_id
        })
        
    except Exception as e:
        conn.rollback()
        conn.close()
        return jsonify({'success': False, 'message': f'Database error: {str(e)}'})

@app.route('/user-guide')
def user_guide():
    """Display user guide page"""
    user_guide_path = os.path.join('templates', 'user_guide.html')
    if os.path.exists(user_guide_path):
        return render_template('user_guide.html')
    else:
        flash('User guide is not available at the moment.')
        return redirect(url_for('user_login'))

@app.route('/validate-session', methods=['POST'])
def validate_user_session():
    """Validate current user session (for automatic logout detection)"""
    try:
        # Get request data
        data = request.get_json() or {}
        check_count = data.get('checkCount', 0)
        client_timestamp = data.get('timestamp', 0)
        
        # Basic session validation
        if not validate_session():
            return jsonify({
                'valid': False, 
                'message': 'Session invalid or expired',
                'reason': 'session_expired'
            })
        
        # Additional check: verify session hasn't been taken over
        user_id = session.get('user_id')
        session_id = session.get('session_id')
        
        if user_id and session_id:
            conn = get_db_connection()
            
            # Check if this specific session is still the only active one for this user
            active_sessions = conn.execute('''
                SELECT SessionId, LoginTime FROM UserSessions 
                WHERE UserId = ? AND IsActive = 1
                ORDER BY LoginTime DESC
            ''', (user_id,)).fetchall()
            
            conn.close()
            
            # If there are multiple active sessions, check if this is the latest one
            if len(active_sessions) > 1:
                latest_session = active_sessions[0]['SessionId']
                if session_id != latest_session:
                    # This session has been superseded by a newer login
                    return jsonify({
                        'valid': False,
                        'message': 'Session taken over by newer login',
                        'reason': 'session_takeover'
                    })
            
            # If there are no active sessions at all, session was manually invalidated
            elif len(active_sessions) == 0:
                return jsonify({
                    'valid': False,
                    'message': 'Session manually invalidated',
                    'reason': 'session_invalidated'
                })
        
        return jsonify({
            'valid': True,
            'checkCount': check_count,
            'timestamp': client_timestamp
        })
        
    except Exception as e:
        # On error, assume session is still valid to avoid false logouts
        return jsonify({
            'valid': True,
            'message': f'Validation error: {str(e)}',
            'error': True
        })

@app.route('/takeover-session', methods=['POST'])
def takeover_session():
    """Take over existing user session"""
    user_id = request.form.get('user_id')
    is_admin = request.form.get('is_admin') == '1'
    
    if not user_id:
        flash('Invalid session takeover request')
        return redirect(url_for('user_login'))
    
    # Take over the session
    takeover_user_session(int(user_id), is_admin)
    
    # Get username for session
    conn = get_db_connection()
    user = conn.execute('SELECT UserName FROM Users WHERE UserId = ?', (user_id,)).fetchone()
    conn.close()
    
    if user:
        session['username'] = user['UserName']
    
    flash('Session taken over successfully. You are now logged in.')
    
    if is_admin:
        return redirect(url_for('admin_dashboard'))
    else:
        return redirect(url_for('user_dashboard'))

@app.route('/logout')
def logout():
    """Logout user and clear all session data"""
    user_id = session.get('user_id')
    session_id = session.get('session_id')
    is_admin = session.get('admin', False)
    
    if session_id:
        # Update database to mark session as inactive and set logout time
        conn = get_db_connection()
        conn.execute('''
            UPDATE UserSessions 
            SET IsActive = 0, LogoutTime = CURRENT_TIMESTAMP 
            WHERE SessionId = ?
        ''', (session_id,))
        
        # Also deactivate any active test sessions for this user
        if user_id:
            conn.execute('''
                UPDATE UserTestSessions 
                SET IsActive = 0 
                WHERE UserId = ? AND IsActive = 1
            ''', (user_id,))
        
        conn.commit()
        conn.close()
    
    # Clear all Flask session data
    session.clear()
    
    # Flash logout message
    flash('You have been logged out successfully.')
    
    # Redirect to appropriate login page
    if is_admin:
        return redirect(url_for('admin_login'))
    else:
        return redirect(url_for('user_login'))

@app.route('/admin/cleanup', methods=['POST'])
def admin_cleanup():
    """Handle admin cleanup operations"""
    if not session.get('admin'):
        return jsonify({'success': False, 'message': 'Unauthorized'})
    
    data = request.get_json()
    action = data.get('action')
    password = data.get('password')
    items = data.get('items', [])
    
    # Verify admin password
    admin_password_hash = hashlib.sha256(password.encode()).hexdigest()
    
    conn = get_db_connection()
    try:
        admin_user = conn.execute('''
            SELECT UserPassword FROM Users 
            WHERE UserName = 'admin' AND IsEligible = 0
        ''').fetchone()
        
        if not admin_user or admin_user['UserPassword'] != admin_password_hash:
            return jsonify({'success': False, 'message': 'Invalid admin password'})
        
        if action == 'test_history':
            # Clear all test history
            conn.execute('DELETE FROM UsersTestResults')
            conn.execute('DELETE FROM UserResponses')
            conn.execute('DELETE FROM UserTestSessions')
            conn.commit()
            return jsonify({'success': True, 'message': 'All test history cleared successfully'})
        
        elif action == 'delete_tests':
            # Delete selected tests
            if not items:
                return jsonify({'success': False, 'message': 'No tests selected'})
            
            test_names = []
            for item in items:
                test_id = item.get('id')
                test_name = item.get('name')
                
                # Delete test and related data
                conn.execute('DELETE FROM UserAnswers WHERE TestName = (SELECT TestName FROM TestSettings WHERE id = ?)', (test_id,))
                conn.execute('DELETE FROM UsersTestResults WHERE TestName = (SELECT TestName FROM TestSettings WHERE id = ?)', (test_id,))
                conn.execute('DELETE FROM UserResponses WHERE TestName = (SELECT TestName FROM TestSettings WHERE id = ?)', (test_id,))
                conn.execute('DELETE FROM UserTestSessions WHERE TestName = (SELECT TestName FROM TestSettings WHERE id = ?)', (test_id,))
                conn.execute('DELETE FROM TestSettings WHERE id = ?', (test_id,))
                
                test_names.append(test_name)
            
            conn.commit()
            return jsonify({'success': True, 'message': f'Successfully deleted {len(test_names)} test(s): {", ".join(test_names)}'})
        
        elif action == 'delete_users':
            # Delete selected users
            if not items:
                return jsonify({'success': False, 'message': 'No users selected'})
            
            user_names = []
            for item in items:
                user_id = item.get('id')
                user_name = item.get('name')
                
                # Don't allow deleting admin user
                if user_name == 'admin':
                    continue
                
                # Delete user and related data
                conn.execute('DELETE FROM UsersTestResults WHERE UserId = ?', (user_id,))
                conn.execute('DELETE FROM UserResponses WHERE UserId = ?', (user_id,))
                conn.execute('DELETE FROM UserTestSessions WHERE UserId = ?', (user_id,))
                conn.execute('DELETE FROM UserSessions WHERE UserId = ?', (user_id,))
                conn.execute('DELETE FROM Users WHERE UserId = ?', (user_id,))
                
                user_names.append(user_name)
            
            conn.commit()
            return jsonify({'success': True, 'message': f'Successfully deleted {len(user_names)} user(s): {", ".join(user_names)}'})
        
        elif action == 'clear_login_sessions':
            # Clear only inactive login sessions (keep active users logged in)
            inactive_sessions_count = conn.execute('SELECT COUNT(*) FROM UserSessions WHERE IsActive = 0').fetchone()[0]
            
            # Delete only inactive login sessions from the database
            conn.execute('DELETE FROM UserSessions WHERE IsActive = 0')
            
            # Keep active sessions so currently logged-in users stay logged in
            
            conn.commit()
            return jsonify({'success': True, 'message': f'Successfully cleared {inactive_sessions_count} inactive login session(s). Active users remain logged in.'})
        
        else:
            return jsonify({'success': False, 'message': 'Invalid cleanup action'})
    
    except Exception as e:
        conn.rollback()
        return jsonify({'success': False, 'message': f'Database error: {str(e)}'})
    finally:
        conn.close()

@app.route('/admin/transactions')
def admin_transactions():
    """Admin page for viewing user transactions and activities"""
    if not session.get('admin'):
        return redirect(url_for('admin_login'))
    
    # Get pagination parameters
    page = request.args.get('page', 1, type=int)
    per_page = 20  # Show 20 entries per page
    
    conn = get_db_connection()
    
    try:
        # Get total count for pagination
        total_test_results = conn.execute('''
            SELECT COUNT(*) as count FROM UsersTestResults
        ''').fetchone()['count']
        
        # Calculate offset for pagination
        offset = (page - 1) * per_page
        
        # Get paginated test results with user information (Last 100, then paginated)
        test_results = conn.execute('''
            SELECT 
                utr.id,
                u.UserName,
                utr.TestName,
                utr.PassFail,
                utr.Score,
                utr.TestDate,
                utr.TimeConsumed,
                COUNT(ur.id) as TotalAnswered
            FROM UsersTestResults utr
            LEFT JOIN Users u ON utr.UserId = u.UserId
            LEFT JOIN UserResponses ur ON utr.id = ur.TestResultId
            GROUP BY utr.id
            ORDER BY utr.TestDate DESC
            LIMIT 100
        ''').fetchall()
        
        # Apply pagination to the limited results
        paginated_results = test_results[offset:offset + per_page]
        
        # Get ONLY truly active test sessions (exclude ended ones)
        raw_sessions = conn.execute('''
            SELECT 
                uts.UserId,
                uts.TestName,
                uts.StartTime,
                uts.CurrentPage,
                u.UserName,
                ts.TimeDuration,
                datetime(uts.StartTime, '+' || ts.TimeDuration || ' minutes') as EndTime
            FROM UserTestSessions uts
            JOIN Users u ON uts.UserId = u.UserId
            JOIN TestSettings ts ON uts.TestName = ts.TestName
            WHERE uts.EndTime IS NULL AND uts.IsActive = 1
            ORDER BY uts.StartTime DESC
        ''').fetchall()
        
        # Process sessions with robust status calculation
        active_sessions = []
        for db_session in raw_sessions:
            status_info = is_session_expired(db_session['StartTime'], db_session['TimeDuration'])
            
            # Create session dict with computed status
            session_dict = dict(db_session)
            session_dict['Status'] = 'Expired' if status_info['is_expired'] else 'Active'
            session_dict['ElapsedMinutes'] = round(status_info.get('elapsed_minutes', 0), 1)
            
            active_sessions.append(session_dict)
            
            # If expired, log it for debugging
            if status_info['is_expired']:
                print(f"⚠️ Found expired session: User {db_session['UserId']}, Test: {db_session['TestName']}, Elapsed: {session_dict['ElapsedMinutes']} min")
        
        # Force immediate cleanup of any expired sessions found
        if any(s['Status'] == 'Expired' for s in active_sessions):
            print("🕒 Triggering immediate cleanup of expired sessions...")
            force_immediate_cleanup()
            
            # Refresh the sessions list after cleanup
            raw_sessions = conn.execute('''
                SELECT 
                    uts.UserId,
                    uts.TestName,
                    uts.StartTime,
                    uts.CurrentPage,
                    u.UserName,
                    ts.TimeDuration,
                    datetime(uts.StartTime, '+' || ts.TimeDuration || ' minutes') as EndTime
                FROM UserTestSessions uts
                JOIN Users u ON uts.UserId = u.UserId
                JOIN TestSettings ts ON uts.TestName = ts.TestName
                WHERE uts.EndTime IS NULL
                ORDER BY uts.StartTime DESC
            ''').fetchall()
            
            # Reprocess after cleanup
            active_sessions = []
            for db_session in raw_sessions:
                status_info = is_session_expired(db_session['StartTime'], db_session['TimeDuration'])
                session_dict = dict(db_session)
                session_dict['Status'] = 'Expired' if status_info['is_expired'] else 'Active'
                session_dict['ElapsedMinutes'] = round(status_info.get('elapsed_minutes', 0), 1)
                active_sessions.append(session_dict)
        
        # Get paginated recent login sessions (Last 100, then paginated)
        login_sessions = conn.execute('''
            SELECT 
                us.UserId,
                u.UserName,
                us.IPAddress,
                us.UserAgent,
                us.LoginTime,
                us.LogoutTime,
                us.IsActive
            FROM UserSessions us
            LEFT JOIN Users u ON us.UserId = u.UserId
            ORDER BY us.LoginTime DESC
            LIMIT 100
        ''').fetchall()
        
        # Get user response statistics (limited to recent 100)
        response_stats = conn.execute('''
            SELECT 
                u.UserName,
                ur.TestName,
                COUNT(*) as TotalResponses,
                SUM(CASE WHEN ur.IsMarked = 1 THEN 1 ELSE 0 END) as MarkedQuestions,
                MAX(utr.TestDate) as LastAttempt
            FROM UserResponses ur
            LEFT JOIN Users u ON ur.UserId = u.UserId
            LEFT JOIN UsersTestResults utr ON ur.TestResultId = utr.id
            GROUP BY ur.UserId, ur.TestName
            ORDER BY LastAttempt DESC
            LIMIT 100
        ''').fetchall()
        
        # Get summary statistics
        stats = {
            'total_test_results': min(total_test_results, 100),  # Cap at 100 for display
            'active_sessions': len(active_sessions),
            'total_users': conn.execute('SELECT COUNT(*) FROM Users WHERE IsEligible = 1').fetchone()[0],
            'total_tests': conn.execute('SELECT COUNT(*) FROM TestSettings').fetchone()[0],
            'total_responses': conn.execute('SELECT COUNT(*) FROM UserResponses').fetchone()[0]
        }
        
        # Calculate pagination info
        total_pages = min((len(test_results) + per_page - 1) // per_page, 5)  # Max 5 pages for 100 records
        
        pagination = {
            'page': page,
            'per_page': per_page,
            'total': min(len(test_results), 100),
            'total_pages': total_pages,
            'has_prev': page > 1,
            'has_next': page < total_pages,
            'prev_num': page - 1 if page > 1 else None,
            'next_num': page + 1 if page < total_pages else None
        }
        
        # Get detailed statistics for each test result
        detailed_results = []
        for result in test_results:
            # Get question counts for this specific test result
            question_stats = conn.execute('''
                SELECT 
                    COUNT(*) as total_questions,
                    SUM(CASE WHEN ur.SelectedAnswer = ua.CorrectAnswer THEN 1 ELSE 0 END) as correct_count,
                    SUM(CASE WHEN ur.SelectedAnswer IS NOT NULL AND ur.SelectedAnswer != ua.CorrectAnswer THEN 1 ELSE 0 END) as incorrect_count,
                    SUM(CASE WHEN ur.SelectedAnswer IS NULL THEN 1 ELSE 0 END) as unanswered_count
                FROM UserAnswers ua
                LEFT JOIN UserResponses ur ON ua.id = ur.QuestionId AND ur.TestResultId = ?
                WHERE ua.TestName = ?
            ''', (result['id'], result['TestName'])).fetchone()
            
            result_dict = dict(result)
            result_dict.update({
                'total_questions': question_stats['total_questions'] or 0,
                'correct_count': question_stats['correct_count'] or 0,
                'incorrect_count': question_stats['incorrect_count'] or 0,
                'unanswered_count': question_stats['unanswered_count'] or question_stats['total_questions'] or 0
            })
            detailed_results.append(result_dict)
        
        return render_template('admin_transactions.html',
                             test_results=paginated_results,
                             active_sessions=active_sessions,
                             login_sessions=login_sessions,
                             response_stats=response_stats,
                             stats=stats,
                             pagination=pagination,
                             detailed_results=detailed_results)
    
    except Exception as e:
        flash(f'Error loading transactions: {str(e)}')
        return redirect(url_for('admin_dashboard'))
    finally:
        conn.close()

@app.route('/manage-users')
def manage_users():
    """Admin page for managing users (excluding admin)"""
    if not session.get('admin'):
        return redirect(url_for('admin_login'))
    
    conn = get_db_connection()
    
    # Get all users (excluding admin)
    users = conn.execute('SELECT * FROM Users WHERE UserName != "admin" ORDER BY UserName').fetchall()
    
    conn.close()
    
    return render_template('manage_users.html', users=users)

def check_session_timeout(user_id):
    """Check if user session has timed out due to inactivity"""
    if not user_id:
        return True
    
    conn = get_db_connection()
    try:
        # Get user's session timeout setting
        user_data = conn.execute('''
            SELECT SessionTimeout FROM Users WHERE UserId = ?
        ''', (user_id,)).fetchone()
        
        if not user_data:
            return True
        
        session_timeout_minutes = user_data['SessionTimeout'] or 15
        
        # Get user's last activity from UserSessions
        last_activity = conn.execute('''
            SELECT LastActivity FROM UserSessions 
            WHERE UserId = ? AND IsActive = 1 
            ORDER BY LastActivity DESC 
            LIMIT 1
        ''', (user_id,)).fetchone()
        
        if last_activity:
            last_activity_time = parse_datetime_string(last_activity['LastActivity'])
            current_time = datetime.datetime.now()
            time_diff = (current_time - last_activity_time).total_seconds() / 60  # in minutes
        
        # Check if session has timed out
        if time_diff > session_timeout_minutes:
            # Mark session as inactive
            conn.execute('''
                UPDATE UserSessions 
                SET IsActive = 0 
                WHERE UserId = ? AND IsActive = 1
            ''', (user_id,))
            conn.commit()
            return True
        
        # Update last activity
        conn.execute('''
            UPDATE UserSessions 
            SET LastActivity = CURRENT_TIMESTAMP 
            WHERE UserId = ? AND IsActive = 1
        ''', (user_id,))
        conn.commit()
        
        return False
        
    except Exception as e:
        print(f"Error checking session timeout: {e}")
        return True
    finally:
        conn.close()

@app.route('/forgot-password')
def forgot_password():
    """Forgot password page for users"""
    return render_template('forgot_password.html', user_type='user')

@app.route('/admin/forgot-password')
def admin_forgot_password():
    """Forgot password page for admin"""
    return render_template('forgot_password.html', user_type='admin')

@app.route('/reset-password', methods=['POST'])
def reset_password():
    """Handle password reset for both users and admin"""
    username = request.form.get('username')
    dob = request.form.get('dob')
    employee_id = request.form.get('employee_id')
    new_password = request.form.get('new_password')
    confirm_password = request.form.get('confirm_password')
    user_type = request.form.get('user_type')
    
    if not all([username, dob, employee_id, new_password, confirm_password]):
        flash('All fields are required')
        return redirect(url_for('forgot_password' if user_type == 'user' else 'admin_forgot_password'))
    
    if new_password != confirm_password:
        flash('Passwords do not match')
        return redirect(url_for('forgot_password' if user_type == 'user' else 'admin_forgot_password'))
    
    conn = get_db_connection()
    
    # Verify user with DOB and EmployeeID
    if user_type == 'admin':
        user = conn.execute('''
            SELECT * FROM Users WHERE UserName = ? AND DOB = ? AND EmployeeID = ? AND IsEligible = 0
        ''', (username, dob, employee_id)).fetchone()
    else:
        user = conn.execute('''
            SELECT * FROM Users WHERE UserName = ? AND DOB = ? AND EmployeeID = ? AND IsEligible = 1
        ''', (username, dob, employee_id)).fetchone()
    
    if not user:
        flash('Invalid username, date of birth, or employee ID')
        conn.close()
        return redirect(url_for('forgot_password' if user_type == 'user' else 'admin_forgot_password'))
    
    # Hash new password
    hashed_password = hashlib.sha256(new_password.encode()).hexdigest()
    
    # Update password
    conn.execute('''
        UPDATE Users SET UserPassword = ? WHERE UserId = ?
    ''', (hashed_password, user['UserId']))
    
    conn.commit()
    conn.close()
    
    flash('Password reset successfully! You can now login with your new password.')
    return redirect(url_for('user_login' if user_type == 'user' else 'admin_login'))

@app.route('/admin/clear-transactions', methods=['POST'])
def clear_login_sessions():
    """Clear login sessions (admin only)"""
    if not session.get('admin'):
        return jsonify({'success': False, 'message': 'Unauthorized'})
    
    admin_password = request.form.get('admin_password')
    
    if not admin_password:
        return jsonify({'success': False, 'message': 'Admin password required'})
    
    # Verify admin password
    hashed_password = hashlib.sha256(admin_password.encode()).hexdigest()
    
    conn = get_db_connection()
    admin_user = conn.execute('''
        SELECT * FROM Users WHERE UserName = 'admin' AND UserPassword = ?
    ''', (hashed_password,)).fetchone()
    
    if not admin_user:
        conn.close()
        return jsonify({'success': False, 'message': 'Invalid admin password'})
    
    try:
        # Clear login sessions
        conn.execute('DELETE FROM UserSessions')
        conn.commit()
        conn.close()
        
        return jsonify({'success': True, 'message': 'Login sessions cleared successfully'})
    except Exception as e:
        conn.close()
        return jsonify({'success': False, 'message': f'Error clearing sessions: {str(e)}'})

def get_client_ip():
    """Get client IP address"""
    if request.environ.get('HTTP_X_FORWARDED_FOR') is None:
        return request.environ['REMOTE_ADDR']
    else:
        return request.environ['HTTP_X_FORWARDED_FOR']

@app.route('/add_user', methods=['POST'])
def add_user():
    """Add a new user"""
    if not session.get('admin'):
        return redirect(url_for('admin'))
    
    username = request.form['username']
    password = request.form['password']
    dob = request.form.get('dob')
    employee_id = request.form.get('employee_id')
    make_admin = request.form.get('make_admin') == '1'
    session_timeout = int(request.form.get('session_timeout', 15))
    
    # Hash the password
    hashed_password = hashlib.sha256(password.encode()).hexdigest()
    
    # Set IsEligible based on make_admin checkbox
    is_eligible = 0 if make_admin else 1
    
    conn = get_db_connection()
    
    try:
        conn.execute('''
            INSERT INTO Users (UserName, UserPassword, IsEligible, DOB, EmployeeID, SessionTimeout)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (username, hashed_password, is_eligible, dob, employee_id, session_timeout))
        conn.commit()
        
        user_type = "admin" if make_admin else "regular user"
        flash(f'User "{username}" added successfully as {user_type}!')
        
    except sqlite3.IntegrityError:
        flash(f'Username "{username}" already exists. Please choose a different username.')
    except Exception as e:
        flash(f'Error adding user: {str(e)}')
    finally:
        conn.close()
    
    return redirect(url_for('user_settings'))

# Add cleanup function for app shutdown
def cleanup_on_shutdown():
    """Clean up all active sessions when the app shuts down"""
    try:
        conn = sqlite3.connect('testingenium.db')
        
        # Mark all active sessions as inactive
        conn.execute('''
            UPDATE UserSessions 
            SET IsActive = 0, LogoutTime = CURRENT_TIMESTAMP 
            WHERE IsActive = 1
        ''')
        
        # Mark all active test sessions as inactive
        conn.execute('''
            UPDATE UserTestSessions 
            SET IsActive = 0 
            WHERE IsActive = 1
        ''')
        
        conn.commit()
        conn.close()
        print("✅ Session cleanup completed on app shutdown")
    except Exception as e:
        print(f"❌ Error during shutdown cleanup: {e}")

# Register cleanup function
atexit.register(cleanup_on_shutdown)

def notify_active_sessions(user_id, event_type, details=None):
    """Notify all active sessions for a user about events"""
    global session_events
    session_events[user_id] = {
        'event': event_type,
        'timestamp': time.time(),
        'details': details or {}
    }
    
def get_session_events(user_id):
    """Get and clear session events for a user"""
    global session_events
    if user_id in session_events:
        event = session_events[user_id]
        del session_events[user_id]  # Clear after reading
        return event
    return None

@app.route('/admin/end-session', methods=['POST'])
def admin_end_session():
    """End an active test session with admin password verification"""
    if not session.get('admin'):
        return jsonify({'success': False, 'message': 'Unauthorized access'})
    
    try:
        data = request.get_json()
        user_id = data.get('user_id')
        test_name = data.get('test_name')
        admin_password = data.get('admin_password')
        
        if not all([user_id, test_name, admin_password]):
            return jsonify({'success': False, 'message': 'Missing required parameters'})
        
        # Verify admin password
        admin_username = session.get('username')
        if not admin_username:
            return jsonify({'success': False, 'message': 'Admin session invalid'})
            
        hashed_password = hashlib.sha256(admin_password.encode()).hexdigest()
        
        conn = get_db_connection()
        
        # Verify admin credentials with debug info
        admin_user = conn.execute('''
            SELECT UserId, UserName FROM Users 
            WHERE UserName = ? AND UserPassword = ? AND IsEligible = 0
        ''', (admin_username, hashed_password)).fetchone()
        
        if not admin_user:
            # Additional debug: check if username exists but password is wrong
            user_exists = conn.execute('''
                SELECT UserName FROM Users WHERE UserName = ? AND IsEligible = 0
            ''', (admin_username,)).fetchone()
            
            conn.close()
            
            if user_exists:
                return jsonify({'success': False, 'message': 'Invalid admin password'})
            else:
                return jsonify({'success': False, 'message': 'Admin user not found'})
        
        print(f"✅ Admin authentication successful for user: {admin_user['UserName']}")
        
        # Check if the test session exists and is active
        active_session = conn.execute('''
            SELECT 
                uts.*, 
                ts.PassingGrade, 
                ts.TimeDuration
            FROM UserTestSessions uts
            JOIN TestSettings ts ON uts.TestName = ts.TestName
            WHERE uts.UserId = ? AND uts.TestName = ? AND uts.EndTime IS NULL
        ''', (user_id, test_name)).fetchone()
        
        if not active_session:
            conn.close()
            return jsonify({'success': False, 'message': 'No active session found for this user and test'})
        
        # Use ROBUST Python datetime calculation instead of unreliable SQLite
        status_info = is_session_expired(active_session['StartTime'], active_session['TimeDuration'])
        session_status = 'Expired' if status_info['is_expired'] else 'Active'
        
        print(f"🔍 Session status check for User {user_id}:")
        print(f"  Start time: {active_session['StartTime']}")
        print(f"  Duration: {active_session['TimeDuration']} min")
        print(f"  Status: {session_status}")
        print(f"  Elapsed: {status_info.get('elapsed_minutes', 0):.1f} min")
        
        # SECURITY: Only allow deletion of expired sessions, not active ones
        if session_status == 'Active':
            conn.close()
            return jsonify({
                'success': False, 
                'message': f'Cannot delete active test session. Session is still active ({status_info.get("elapsed_minutes", 0):.1f} min elapsed of {active_session["TimeDuration"]} min). Sessions can only be deleted after they expire.'
            })
        
        print(f"✅ Admin attempting to delete expired session for user: {user_id}")
        
        # Calculate current score based on answered questions
        user_responses = conn.execute('''
            SELECT ur.SelectedAnswer, ua.CorrectAnswer 
            FROM UserResponses ur
            JOIN UserAnswers ua ON ur.QuestionId = ua.id
            WHERE ur.UserId = ? AND ur.TestResultId = ?
        ''', (user_id, active_session['TestResultId'])).fetchall()
        
        if user_responses:
            correct_answers = sum(1 for response in user_responses if response['SelectedAnswer'] == response['CorrectAnswer'])
            total_questions = len(user_responses)
            score = (correct_answers / total_questions * 100) if total_questions > 0 else 0
        else:
            score = 0
        
        # Calculate time consumed
        start_time = parse_datetime_string(active_session['StartTime'])
        from datetime import datetime
        end_time = datetime.now()
        time_consumed = (end_time - start_time).total_seconds() / 60  # in minutes
        
        # Determine pass/fail status
        pass_fail = 'Pass' if score >= active_session['PassingGrade'] else 'Fail'
        
        # Update the test result
        conn.execute('''
            UPDATE UsersTestResults 
            SET PassFail = ?, Score = ?, TimeConsumed = ?, TestDate = CURRENT_TIMESTAMP
            WHERE id = ?
        ''', (pass_fail, score, time_consumed, active_session['TestResultId']))
        
        # End the test session
        conn.execute('''
            UPDATE UserTestSessions 
            SET EndTime = CURRENT_TIMESTAMP, IsActive = 0
            WHERE UserId = ? AND TestName = ? AND EndTime IS NULL
        ''', (user_id, test_name))
        
        # Log the admin action
        conn.execute('''
            INSERT INTO UserSessions (UserId, SessionId, IsAdmin, LoginTime, LogoutTime, IsActive, BrowserInfo) 
            VALUES (?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 0, ?)
        ''', (admin_user['UserId'], f'ADMIN_ACTION_{int(time.time())}', 1, f'Ended session for user {user_id} in test {test_name}'))
        
        conn.commit()
        conn.close()
        
        return jsonify({
            'success': True, 
            'message': f'Test session ended successfully. User scored {score:.1f}% ({pass_fail})'
        })
        
    except Exception as e:
        return jsonify({'success': False, 'message': f'Error ending session: {str(e)}'})

def cleanup_expired_sessions(conn=None):
    """Immediately clean up all expired test sessions"""
    try:
        if conn is None:
            conn = get_db_connection()
            close_conn = True
        else:
            close_conn = False
        
        # Find all expired sessions that haven't been cleaned up yet
        expired_sessions = conn.execute('''
            SELECT 
                uts.UserId,
                uts.TestName,
                uts.StartTime,
                uts.TestResultId,
                ts.TimeDuration,
                ts.PassingGrade
            FROM UserTestSessions uts
            JOIN TestSettings ts ON uts.TestName = ts.TestName
            WHERE uts.EndTime IS NULL 
            AND datetime('now') > datetime(uts.StartTime, '+' || ts.TimeDuration || ' minutes')
        ''').fetchall()
        
        cleaned_count = 0
        for session in expired_sessions:
            try:
                user_id = session['UserId']
                test_name = session['TestName']
                test_result_id = session['TestResultId']
                passing_grade = session['PassingGrade']
                
                # Calculate score for expired session
                if test_result_id:
                    user_responses = conn.execute('''
                        SELECT ur.SelectedAnswer, ua.CorrectAnswer 
                        FROM UserResponses ur
                        JOIN UserAnswers ua ON ur.QuestionId = ua.id
                        WHERE ur.UserId = ? AND ur.TestResultId = ?
                    ''', (user_id, test_result_id)).fetchall()
                else:
                    user_responses = conn.execute('''
                        SELECT ur.SelectedAnswer, ua.CorrectAnswer
                        FROM UserResponses ur
                        JOIN UserAnswers ua ON ur.QuestionId = ua.id
                        WHERE ur.UserId = ? AND ua.TestName = ?
                    ''', (user_id, test_name)).fetchall()
                
                # Calculate score
                if user_responses:
                    correct_answers = sum(1 for response in user_responses 
                                        if response['SelectedAnswer'] == response['CorrectAnswer'])
                    total_questions = len(user_responses)
                    score = (correct_answers / total_questions * 100) if total_questions > 0 else 0
                else:
                    score = 0
                
                # Use full duration for expired tests
                time_consumed = session['TimeDuration']
                
                # Determine pass/fail status
                pass_fail = 'Pass' if score >= passing_grade else 'Fail'
                
                # Update or insert test result
                if test_result_id:
                    conn.execute('''
                        UPDATE UsersTestResults 
                        SET PassFail = ?, Score = ?, TimeConsumed = ?, TestDate = CURRENT_TIMESTAMP
                        WHERE id = ?
                    ''', (pass_fail, score, time_consumed, test_result_id))
                else:
                    conn.execute('''
                        INSERT INTO UsersTestResults (UserId, TestName, PassFail, Score, TimeConsumed)
                        VALUES (?, ?, ?, ?, ?)
                    ''', (user_id, test_name, pass_fail, score, time_consumed))
                
                # End the test session
                conn.execute('''
                    UPDATE UserTestSessions 
                    SET EndTime = CURRENT_TIMESTAMP, IsActive = 0
                    WHERE UserId = ? AND TestName = ? AND EndTime IS NULL
                ''', (user_id, test_name))
                
                cleaned_count += 1
                
            except Exception as e:
                print(f"Error cleaning up session for user {user_id}: {e}")
                continue
        
        if cleaned_count > 0:
            conn.commit()
            print(f"✅ Cleaned up {cleaned_count} expired test sessions")
        
        if close_conn:
            conn.close()
            
        return cleaned_count
        
    except Exception as e:
        print(f"❌ Error in cleanup_expired_sessions: {e}")
        return 0

def force_immediate_cleanup():
    """Force immediate cleanup of all expired sessions"""
    try:
        conn = get_db_connection()
        
        # Find ALL expired sessions that need immediate cleanup
        expired_sessions = conn.execute('''
            SELECT uts.*, ts.TimeDuration, ts.PassingGrade
            FROM UserTestSessions uts
            JOIN TestSettings ts ON uts.TestName = ts.TestName
            WHERE uts.EndTime IS NULL
            AND datetime('now') > datetime(uts.StartTime, '+' || ts.TimeDuration || ' minutes')
        ''').fetchall()
        
        if len(expired_sessions) == 0:
            print("✅ No expired sessions to clean up")
            conn.close()
            return 0
        
        print(f"🕒 Found {len(expired_sessions)} expired sessions - processing immediately...")
        
        sessions_processed = 0
        for expired_session in expired_sessions:
            try:
                user_id = expired_session['UserId']
                test_name = expired_session['TestName']
                test_result_id = expired_session['TestResultId']
                passing_grade = expired_session['PassingGrade']
                
                # Calculate score for expired session
                if test_result_id:
                    user_responses = conn.execute('''
                        SELECT ur.SelectedAnswer, ua.CorrectAnswer 
                        FROM UserResponses ur
                        JOIN UserAnswers ua ON ur.QuestionId = ua.id
                        WHERE ur.UserId = ? AND ur.TestResultId = ?
                    ''', (user_id, test_result_id)).fetchall()
                else:
                    user_responses = conn.execute('''
                        SELECT ur.SelectedAnswer, ua.CorrectAnswer
                        FROM UserResponses ur
                        JOIN UserAnswers ua ON ur.QuestionId = ua.id
                        WHERE ur.UserId = ? AND ua.TestName = ?
                    ''', (user_id, test_name)).fetchall()
                
                # Calculate score
                if user_responses:
                    correct_answers = sum(1 for response in user_responses 
                                        if response['SelectedAnswer'] == response['CorrectAnswer'])
                    total_questions = len(user_responses)
                    score = (correct_answers / total_questions * 100) if total_questions > 0 else 0
                else:
                    score = 0
                
                # Use full duration for expired tests
                time_consumed = expired_session['TimeDuration']
                
                # Determine pass/fail status
                pass_fail = 'Pass' if score >= passing_grade else 'Fail'
                
                # Update or insert test result
                if test_result_id:
                    conn.execute('''
                        UPDATE UsersTestResults 
                        SET PassFail = ?, Score = ?, TimeConsumed = ?, TestDate = CURRENT_TIMESTAMP
                        WHERE id = ?
                    ''', (pass_fail, score, time_consumed, test_result_id))
                else:
                    conn.execute('''
                        INSERT INTO UsersTestResults (UserId, TestName, PassFail, Score, TimeConsumed)
                        VALUES (?, ?, ?, ?, ?)
                    ''', (user_id, test_name, pass_fail, score, time_consumed))
                
                # End the test session immediately
                conn.execute('''
                    UPDATE UserTestSessions 
                    SET EndTime = CURRENT_TIMESTAMP, IsActive = 0
                    WHERE UserId = ? AND TestName = ? AND EndTime IS NULL
                ''', (user_id, test_name))
                
                print(f"  ✅ Auto-submitted expired test for User {user_id}: {test_name} (Score: {score:.1f}%)")
                sessions_processed += 1
                
            except Exception as e:
                print(f"  ❌ Error processing session for user {user_id}: {e}")
                continue
        
        if sessions_processed > 0:
            conn.commit()
            print(f"✅ Successfully auto-submitted {sessions_processed} expired sessions")
        
        conn.close()
        return sessions_processed
        
    except Exception as e:
        print(f"❌ Error in force_immediate_cleanup: {e}")
        return 0

def is_session_expired(start_time, duration_minutes):
    """Check if a session has expired based on start time and duration"""
    try:
        from datetime import datetime, timedelta
        
        # Parse the start time using our robust parser
        start_datetime = parse_datetime_string(start_time)
        current_datetime = datetime.now()
        
        # Calculate expected end time
        expected_end = start_datetime + timedelta(minutes=duration_minutes)
        
        # Check if current time is past expected end
        is_expired = current_datetime > expected_end
        
        # Calculate elapsed time in minutes
        elapsed_seconds = (current_datetime - start_datetime).total_seconds()
        elapsed_minutes = elapsed_seconds / 60
        
        return {
            'is_expired': is_expired,
            'elapsed_minutes': elapsed_minutes,
            'start_time': start_datetime,
            'current_time': current_datetime,
            'expected_end': expected_end
        }
        
    except Exception as e:
        print(f"Error in is_session_expired: {e}")
        # Default to expired if we can't parse dates properly
        return {'is_expired': True, 'elapsed_minutes': 0}

@app.route('/test/auto-submit', methods=['POST'])
def auto_submit_test():
    """Auto-submit test when timer expires (called from frontend)"""
    if not session.get('user_id'):
        return jsonify({'success': False, 'message': 'Not logged in'})
    
    user_id = session['user_id']
    
    try:
        conn = get_db_connection()
        
        # Get the user's current active test session
        active_session = conn.execute('''
            SELECT uts.*, ts.PassingGrade, ts.TimeDuration
            FROM UserTestSessions uts
            JOIN TestSettings ts ON uts.TestName = ts.TestName
            WHERE uts.UserId = ? AND uts.EndTime IS NULL
            ORDER BY uts.StartTime DESC
            LIMIT 1
        ''', (user_id,)).fetchone()
        
        if not active_session:
            conn.close()
            return jsonify({'success': False, 'message': 'No active test session found'})
        
        # Calculate score based on answered questions
        test_result_id = active_session['TestResultId']
        if test_result_id:
            user_responses = conn.execute('''
                SELECT ur.SelectedAnswer, ua.CorrectAnswer 
                FROM UserResponses ur
                JOIN UserAnswers ua ON ur.QuestionId = ua.id
                WHERE ur.UserId = ? AND ur.TestResultId = ?
            ''', (user_id, test_result_id)).fetchall()
        else:
            user_responses = conn.execute('''
                SELECT ur.SelectedAnswer, ua.CorrectAnswer
                FROM UserResponses ur
                JOIN UserAnswers ua ON ur.QuestionId = ua.id
                WHERE ur.UserId = ? AND ur.TestName = ?
            ''', (user_id, active_session['TestName'])).fetchall()
        
        # Calculate final score
        if user_responses:
            correct_answers = sum(1 for response in user_responses 
                                if response['SelectedAnswer'] == response['CorrectAnswer'])
            total_questions = len(user_responses)
            score = (correct_answers / total_questions * 100) if total_questions > 0 else 0
        else:
            score = 0
        
        # Use full test duration for auto-submitted tests
        time_consumed = active_session['TimeDuration']
        
        # Determine pass/fail
        pass_fail = 'Pass' if score >= active_session['PassingGrade'] else 'Fail'
        
        # Update or create test result
        if test_result_id:
            conn.execute('''
                UPDATE UsersTestResults 
                SET PassFail = ?, Score = ?, TimeConsumed = ?, TestDate = CURRENT_TIMESTAMP
                WHERE id = ?
            ''', (pass_fail, score, time_consumed, test_result_id))
            result_id = test_result_id
        else:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO UsersTestResults (UserId, TestName, PassFail, Score, TimeConsumed)
                VALUES (?, ?, ?, ?, ?)
            ''', (user_id, active_session['TestName'], pass_fail, score, time_consumed))
            result_id = cursor.lastrowid
        
        # Mark session as completed (this removes it from active sessions)
        conn.execute('''
            UPDATE UserTestSessions 
            SET EndTime = CURRENT_TIMESTAMP, IsActive = 0
            WHERE UserId = ? AND TestName = ? AND EndTime IS NULL
        ''', (user_id, active_session['TestName']))
        
        conn.commit()
        conn.close()
        
        print(f"✅ Frontend auto-submitted test for user {user_id}: {active_session['TestName']} (Score: {score:.1f}%)")
        
        return jsonify({
            'success': True, 
            'result_id': result_id,
            'score': score,
            'pass_fail': pass_fail
        })
        
    except Exception as e:
        print(f"❌ Error in frontend auto-submit: {e}")
        return jsonify({'success': False, 'message': 'Auto-submit failed'})

@app.route('/images/<filename>')
def uploaded_file(filename):
    """Serve uploaded images"""
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.route('/admin/delete-question/<int:question_id>', methods=['DELETE'])
def delete_question_permanently(question_id):
    """Permanently delete question from repository"""
    if not session.get('admin'):
        return jsonify({'success': False, 'message': 'Admin access required'})
    
    try:
        conn = get_db_connection()
        
        # Check if question exists
        question = conn.execute('SELECT * FROM UserAnswers WHERE id = ?', (question_id,)).fetchone()
        if not question:
            conn.close()
            return jsonify({'success': False, 'message': 'Question not found'})
        
        # Permanently delete the question from database
        conn.execute('DELETE FROM UserAnswers WHERE id = ?', (question_id,))
        conn.commit()
        conn.close()
        
        return jsonify({'success': True, 'message': 'Question permanently deleted. Consider renumbering questions to fix any gaps.'})
        
    except Exception as e:
        return jsonify({'success': False, 'message': f'Error deleting question: {str(e)}'})

@app.route('/admin/renumber-questions', methods=['POST'])
def renumber_questions():
    """Renumber questions sequentially for the current test only"""
    if not session.get('admin'):
        if request.is_json or request.headers.get('Content-Type') == 'application/json':
            return jsonify({'success': False, 'message': 'Admin access required'})
        return redirect(url_for('admin_login'))
    
    try:
        # Get the currently selected test
        current_test = get_current_test()
        if not current_test:
            message = 'No test selected for renumbering.'
            if request.is_json or request.headers.get('Content-Type') == 'application/json':
                return jsonify({'success': False, 'message': message})
            flash(message)
            return redirect(url_for('test_settings'))
        
        conn = get_db_connection()
        
        # Get test settings for the current test
        test_settings = conn.execute('SELECT * FROM TestSettings WHERE id = ?', (current_test['id'],)).fetchone()
        if not test_settings:
            message = 'Test settings not found for renumbering.'
            if request.is_json or request.headers.get('Content-Type') == 'application/json':
                return jsonify({'success': False, 'message': message})
            flash(message)
            return redirect(url_for('test_settings'))
        
        test_name = test_settings['TestName']
        
        # Get all questions for the current test only, ordered by current QuestionNumber
        questions = conn.execute('''
            SELECT id, QuestionNumber FROM UserAnswers 
            WHERE TestName = ? 
            ORDER BY QuestionNumber
        ''', (test_name,)).fetchall()
        
        renumbered_count = 0
        # Renumber questions sequentially starting from 1
        for i, question in enumerate(questions):
            new_question_number = i + 1
            if question['QuestionNumber'] != new_question_number:
                conn.execute('''
                    UPDATE UserAnswers 
                    SET QuestionNumber = ? 
                    WHERE id = ?
                ''', (new_question_number, question['id']))
                renumbered_count += 1
        
        conn.commit()
        conn.close()
        
        # Return JSON for AJAX requests
        if request.is_json or request.headers.get('Content-Type') == 'application/json':
            return jsonify({
                'success': True, 
                'message': f'Successfully renumbered {renumbered_count} questions in "{test_name}" test.',
                'renumbered_count': renumbered_count,
                'test_name': test_name
            })
        
        # Traditional form submission
        flash(f'Successfully renumbered {renumbered_count} questions in "{test_name}" test.')
        return redirect(url_for('test_settings'))
        
    except Exception as e:
        if request.is_json or request.headers.get('Content-Type') == 'application/json':
            return jsonify({'success': False, 'message': f'Error renumbering questions: {str(e)}'})
        flash(f'Error renumbering questions: {str(e)}')
        return redirect(url_for('test_settings'))

@app.route('/test/get-progress', methods=['POST'])
def get_test_progress():
    """Get current test progress for accurate progress bar updates"""
    try:
        data = request.get_json()
        test_session_id = data.get('test_session_id')
        
        if not test_session_id:
            return jsonify({'success': False, 'message': 'No test session ID provided'})
        
        conn = get_db_connection()
        
        # Get test session info
        session_info = conn.execute('''
            SELECT TestName FROM UserTestSessions WHERE id = ?
        ''', (test_session_id,)).fetchone()
        
        if not session_info:
            conn.close()
            return jsonify({'success': False, 'message': 'Test session not found'})
        
        # Get total answered questions across all pages
        answered_count = conn.execute('''
            SELECT COUNT(DISTINCT QuestionId) as count 
            FROM UserResponses 
            WHERE TestSessionId = ? AND SelectedAnswer IS NOT NULL
        ''', (test_session_id,)).fetchone()['count']
        
        # Get total active questions for this test
        total_count = conn.execute('''
            SELECT COUNT(*) as count 
            FROM UserAnswers 
            WHERE TestName = ? AND IsActive = 1
        ''', (session_info['TestName'],)).fetchone()['count']
        
        conn.close()
        
        percentage = round((answered_count / total_count * 100) if total_count > 0 else 0, 1)
        
        return jsonify({
            'success': True,
            'answered': answered_count,
            'total': total_count,
            'percentage': percentage
        })
        
    except Exception as e:
        return jsonify({'success': False, 'message': f'Error: {str(e)}'})

if __name__ == "__main__":
    init_db()
    
    # Start cleanup task for stale sessions
    cleanup_thread = threading.Thread(target=cleanup_stale_sessions, daemon=True)
    cleanup_thread.start()
    
    print("Cleaning up stale sessions on startup...")
    cleanup_stale_sessions()
    print("Stale sessions cleaned up successfully.")
    
    app.run(debug=True, host='0.0.0.0', port=5067, threaded=True) 