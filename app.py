"""
SmartSpend AI v3 - Complete Flask Application
Uses: Flask + sqlite3 (built-in) + PyJWT + Pillow + pytesseract
No SQLAlchemy or Flask-extensions needed.
"""
import os, sqlite3, json, re, base64, hashlib, secrets, datetime
from functools import wraps
from flask import Flask, request, jsonify, render_template, g
import jwt as pyjwt
from PIL import Image
import pytesseract, io, cv2, numpy as np

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'smartspend-secret-key-2025')
DB_PATH = os.path.join(os.path.dirname(__file__), 'smartspend.db')

# ─────────────────────────────────────────────────────────────
#  DATABASE
# ─────────────────────────────────────────────────────────────
def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA journal_mode=WAL")
        g.db.execute("PRAGMA foreign_keys=ON")
    return g.db

@app.teardown_appcontext
def close_db(e=None):
    db = g.pop('db', None)
    if db: db.close()

def init_db():
    with app.app_context():
        db = sqlite3.connect(DB_PATH)
        db.row_factory = sqlite3.Row
        db.executescript('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            total_points INTEGER DEFAULT 0,
            coins INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS goals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id),
            goal_name TEXT NOT NULL,
            goal_amount REAL NOT NULL,
            current_savings REAL DEFAULT 0,
            monthly_saving REAL DEFAULT 0,
            category TEXT DEFAULT 'other',
            target_date TEXT,
            is_completed INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS expenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id),
            amount REAL NOT NULL,
            category TEXT DEFAULT 'other',
            merchant TEXT,
            description TEXT,
            payment_mode TEXT DEFAULT 'upi',
            transaction_id TEXT,
            entry_type TEXT DEFAULT 'manual',
            is_impulse INTEGER DEFAULT 0,
            impulse_reason TEXT,
            expense_date TEXT DEFAULT (datetime('now')),
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS splits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            expense_id INTEGER REFERENCES expenses(id),
            user_id INTEGER NOT NULL REFERENCES users(id),
            ower_name TEXT NOT NULL,
            ower_email TEXT,
            amount_owed REAL NOT NULL,
            is_settled INTEGER DEFAULT 0,
            settled_at TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS daily_streaks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id),
            streak_date TEXT NOT NULL,
            daily_spend REAL DEFAULT 0,
            threshold REAL DEFAULT 200,
            under_threshold INTEGER DEFAULT 0,
            UNIQUE(user_id, streak_date)
        );
        CREATE TABLE IF NOT EXISTS badges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id),
            badge_key TEXT NOT NULL,
            badge_name TEXT NOT NULL,
            icon TEXT NOT NULL,
            points INTEGER DEFAULT 0,
            awarded_at TEXT DEFAULT (datetime('now')),
            UNIQUE(user_id, badge_key)
        );
        ''')
        db.commit()
        db.close()

# ─────────────────────────────────────────────────────────────
#  AUTH HELPERS
# ─────────────────────────────────────────────────────────────
def hash_password(pw): return hashlib.sha256(pw.encode()).hexdigest()
def check_password(pw, hashed): return hash_password(pw) == hashed

def make_token(user_id):
    payload = {'sub': str(user_id), 'exp': datetime.datetime.utcnow() + datetime.timedelta(days=7)}
    return pyjwt.encode(payload, app.config['SECRET_KEY'], algorithm='HS256')

def jwt_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.headers.get('Authorization', '')
        token = auth.replace('Bearer ', '').strip()
        if not token:
            return jsonify({'error': 'No token'}), 401
        try:
            data = pyjwt.decode(token, app.config['SECRET_KEY'], algorithms=['HS256'])
            g.user_id = int(data['sub'])
        except Exception as e:
            return jsonify({'error': 'Invalid token'}), 401
        return f(*args, **kwargs)
    return decorated

def uid(): return g.user_id

# ─────────────────────────────────────────────────────────────
#  OCR UTILITIES (Real Tesseract OCR)
# ─────────────────────────────────────────────────────────────
CAT_KW = {
    'food': ['swiggy','zomato','uber eats','food','restaurant','dominos','pizza','kfc','mcdonald','cafe','blinkit','zepto','dunzo'],
    'transport': ['uber','ola','rapido','metro','irctc','indigo','air','bus','petrol','fuel','rapido'],
    'shopping': ['amazon','flipkart','myntra','ajio','nykaa','meesho','shop','mall','store','reliance'],
    'entertainment': ['netflix','spotify','hotstar','prime','bookmyshow','pvr','inox','game','youtube'],
    'health': ['pharmacy','apollo','medplus','hospital','clinic','doctor','lab','1mg','netmeds'],
    'utilities': ['electricity','water','gas','broadband','airtel','jio','vi','bsnl','recharge'],
    'groceries': ['bigbasket','grofer','dmart','reliance fresh','grocery','supermarket','vegetables'],
    'travel': ['hotel','airbnb','oyo','makemytrip','goibibo','cleartrip','booking'],
}
IMPULSE_MERCHANTS = ['swiggy','zomato','amazon','flipkart','myntra','ajio','nykaa','meesho','blinkit','zepto','dominos','kfc','pizza']

def guess_category(text):
    t = text.lower()
    for cat, kws in CAT_KW.items():
        if any(k in t for k in kws): return cat
    return 'other'

def detect_impulse(amount, category, merchant, dt_str):
    reasons = []
    try:
        dt = datetime.datetime.fromisoformat(dt_str)
        hour = dt.hour
    except:
        hour = 12
    m = (merchant or '').lower()
    is_night = hour >= 23 or hour < 5
    if is_night and category == 'food':
        reasons.append(f'Late night food order at {hour}:00')
    if amount >= 3000 and category in ['food','shopping','entertainment']:
        reasons.append(f'Large {category} purchase ₹{amount:,.0f}')
    for kw in IMPULSE_MERCHANTS:
        if kw in m:
            if is_night: reasons.append(f'Late night from {merchant}')
            elif amount >= 500: reasons.append(f'Impulse buy from {merchant}')
            break
    if category == 'shopping' and 0 <= hour <= 3:
        reasons.append('Midnight shopping')
    return {'is_impulse': len(reasons)>0, 'reason': reasons[0] if reasons else None}

def ocr_from_base64(b64_str):
    """Real OCR using pytesseract"""
    try:
        img_bytes = base64.b64decode(b64_str)
        nparr = np.frombuffer(img_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        # Preprocess: grayscale, upscale, threshold
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        gray = cv2.resize(gray, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
        _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        pil_img = Image.fromarray(thresh)
        text = pytesseract.image_to_string(pil_img, config='--psm 6')
        return parse_text(text)
    except Exception as e:
        return {'parsed': False, 'error': str(e), 'amount': None, 'merchant': None, 'category': 'other'}

def parse_text(text):
    """Parse extracted text for amount, merchant, txn id"""
    amount = None
    merchant = None
    tx_id = None
    payment_mode = 'other'
    # Amount patterns
    for pat in [
        r'(?:Rs\.?|INR|₹)\s*([\d,]+\.?\d*)',
        r'(?:amount|total|paid|debit)[:\s]+(?:Rs\.?|INR|₹)?\s*([\d,]+\.?\d*)',
        r'([\d,]+\.?\d*)\s*(?:Rs\.?|INR|₹)',
    ]:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            try:
                amount = float(m.group(1).replace(',',''))
                if amount > 0: break
            except: pass
    # Merchant
    for pat in [
        r'(?:to|at|from|merchant)[:\s]+([A-Za-z][A-Za-z0-9\s&\'-]{2,30})',
        r'(?:paid to|sent to|transferred to)[:\s]+([A-Za-z][A-Za-z0-9\s&\'-]{2,30})',
    ]:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            merchant = m.group(1).strip().title()[:40]
            break
    # Transaction ID
    m = re.search(r'(?:txn|ref|utr|rrn|transaction)[:\s#]*([A-Z0-9]{8,25})', text, re.IGNORECASE)
    if m: tx_id = m.group(1)
    # Payment mode
    if re.search(r'upi|gpay|phonepe|paytm|bhim', text, re.IGNORECASE): payment_mode='upi'
    elif re.search(r'credit\s*card|debit\s*card|visa|mastercard', text, re.IGNORECASE): payment_mode='card'
    elif re.search(r'\bcash\b', text, re.IGNORECASE): payment_mode='cash'

    cat = guess_category(f'{merchant or ""} {text[:200]}')
    return {'parsed': amount is not None, 'amount': amount, 'merchant': merchant,
            'category': cat, 'transaction_id': tx_id, 'payment_mode': payment_mode, 'raw_text': text[:300]}

def parse_sms(sms):
    return parse_text(sms)

# ─────────────────────────────────────────────────────────────
#  GAMIFICATION HELPERS
# ─────────────────────────────────────────────────────────────
ALL_BADGES = [
    ('starter',       '🌱', 'First Steps',        'Add your first expense',           5,   1),
    ('goal_setter',   '🎯', 'Goal Setter',         'Create your first savings goal',   10,  2),
    ('streak_7',      '🔥', 'Week Warrior',        '7-day spending streak',            15,  3),
    ('streak_30',     '🌊', 'Month Master',        '30-day spending streak',           50,  4),
    ('streak_50',     '🏆', 'Streak Legend',       '50-day spending streak',           100, 5),
    ('saver_1k',      '💰', 'Saver',               'Save ₹1,000 in any goal',          10,  6),
    ('saver_10k',     '💎', 'Diamond Saver',       'Save ₹10,000 in any goal',         30,  7),
    ('goal_done',     '🏅', 'Goal Crusher',        'Complete a savings goal',          50,  8),
    ('no_impulse_7',  '🧘', 'Mindful Spender',     '7 days with no impulse spending',  20,  9),
    ('budget_master', '👑', 'Budget Master',       '1 full month under budget',        40,  10),
    ('splitter',      '🤝', 'Fair Splitter',       'Track a shared expense',           5,   11),
    ('ocr_user',      '📷', 'Receipt Scanner',     'Use OCR to add expense',           5,   12),
]

def award_badge(db, user_id, badge_key):
    badge = next((b for b in ALL_BADGES if b[0]==badge_key), None)
    if not badge: return 0
    try:
        db.execute('INSERT OR IGNORE INTO badges(user_id,badge_key,badge_name,icon,points) VALUES(?,?,?,?,?)',
                   (user_id, badge_key, badge[2], badge[1], badge[4]))
        rows = db.execute('SELECT changes()').fetchone()[0]
        if rows > 0:
            db.execute('UPDATE users SET total_points=total_points+?, coins=coins+? WHERE id=?', (badge[4], badge[4]//2, user_id))
            db.commit()
            return badge[4]
    except: pass
    return 0

def add_points(db, user_id, pts, coins=0):
    db.execute('UPDATE users SET total_points=total_points+?, coins=coins+? WHERE id=?', (pts, coins or pts//2, user_id))
    db.commit()

def update_streak(db, user_id, expense_date_str, amount):
    try:
        date_part = expense_date_str[:10]
        row = db.execute('SELECT * FROM daily_streaks WHERE user_id=? AND streak_date=?', (user_id, date_part)).fetchone()
        if row:
            new_spend = row['daily_spend'] + amount
            under = 1 if new_spend <= row['threshold'] else 0
            db.execute('UPDATE daily_streaks SET daily_spend=?, under_threshold=? WHERE id=?', (new_spend, under, row['id']))
        else:
            under = 1 if amount <= 200 else 0
            db.execute('INSERT INTO daily_streaks(user_id,streak_date,daily_spend,threshold,under_threshold) VALUES(?,?,?,200,?)',
                       (user_id, date_part, amount, under))
        db.commit()
    except Exception as e:
        print('Streak error:', e)

def calc_streak(db, user_id):
    rows = db.execute('SELECT streak_date, under_threshold FROM daily_streaks WHERE user_id=? ORDER BY streak_date DESC', (user_id,)).fetchall()
    current = best = max_run = 0
    today = datetime.date.today().isoformat()
    for i, r in enumerate(rows):
        if r['under_threshold']:
            if i == 0 or rows[i-1]['streak_date'] == (datetime.date.fromisoformat(r['streak_date']) + datetime.timedelta(days=1)).isoformat():
                current += 1
            max_run += 1
            best = max(best, max_run)
        else:
            if i == 0: break
            max_run = 0
    return current, best

# ─────────────────────────────────────────────────────────────
#  PAGE ROUTES
# ─────────────────────────────────────────────────────────────
@app.route('/') 
def index(): return render_template('index.html')
@app.route('/login')
def login_page(): return render_template('login.html')
@app.route('/register')
def register_page(): return render_template('register.html')
@app.route('/dashboard')
def dashboard_page(): return render_template('dashboard.html')
@app.route('/expenses')
def expenses_page(): return render_template('expenses.html')
@app.route('/goals')
def goals_page(): return render_template('goals.html')
@app.route('/splits')
def splits_page(): return render_template('splits.html')
@app.route('/gamification')
def gamification_page(): return render_template('gamification.html')
@app.route('/predictor')
def predictor_page(): return render_template('predictor.html')

# ─────────────────────────────────────────────────────────────
#  AUTH API
# ─────────────────────────────────────────────────────────────
@app.route('/api/register', methods=['POST'])
def register():
    d = request.get_json()
    name = (d.get('name') or '').strip()
    email = (d.get('email') or '').strip().lower()
    pw = d.get('password','')
    if not name or not email or len(pw) < 6:
        return jsonify({'error': 'Name, email and password (6+ chars) required'}), 400
    db = get_db()
    if db.execute('SELECT id FROM users WHERE email=?', (email,)).fetchone():
        return jsonify({'error': 'Email already registered'}), 400
    cur = db.execute('INSERT INTO users(name,email,password_hash) VALUES(?,?,?)', (name, email, hash_password(pw)))
    db.commit()
    user_id = cur.lastrowid
    award_badge(db, user_id, 'starter')
    token = make_token(user_id)
    return jsonify({'token': token, 'user': {'id': user_id, 'name': name, 'email': email}}), 201

@app.route('/api/login', methods=['POST'])
def login():
    d = request.get_json()
    email = (d.get('email') or '').strip().lower()
    pw = d.get('password','')
    db = get_db()
    u = db.execute('SELECT * FROM users WHERE email=?', (email,)).fetchone()
    if not u or not check_password(pw, u['password_hash']):
        return jsonify({'error': 'Invalid email or password'}), 401
    token = make_token(u['id'])
    return jsonify({'token': token, 'user': {'id': u['id'], 'name': u['name'], 'email': u['email'], 'total_points': u['total_points'], 'coins': u['coins']}})

@app.route('/api/me', methods=['GET'])
@jwt_required
def me():
    db = get_db()
    u = db.execute('SELECT id,name,email,total_points,coins,created_at FROM users WHERE id=?', (uid(),)).fetchone()
    return jsonify(dict(u))

# ─────────────────────────────────────────────────────────────
#  DASHBOARD API
# ─────────────────────────────────────────────────────────────
@app.route('/api/dashboard', methods=['GET'])
@jwt_required
def dashboard():
    db = get_db()
    u = db.execute('SELECT * FROM users WHERE id=?', (uid(),)).fetchone()
    now = datetime.datetime.now()
    m, y = now.month, now.year

    # Month expenses
    exps = db.execute('''SELECT * FROM expenses WHERE user_id=? 
        AND strftime('%m',expense_date)=? AND strftime('%Y',expense_date)=?''',
        (uid(), f'{m:02d}', str(y))).fetchall()
    month_total = sum(e['amount'] for e in exps)
    impulse_total = sum(e['amount'] for e in exps if e['is_impulse'])
    impulse_pct = round(impulse_total/month_total*100, 1) if month_total else 0

    # By category
    by_cat = {}
    for e in exps:
        by_cat[e['category']] = by_cat.get(e['category'], 0) + e['amount']
    # By day
    by_day = {}
    for e in exps:
        d = e['expense_date'][:10]
        by_day[d] = by_day.get(d, 0) + e['amount']

    # Goals
    goals = db.execute('SELECT * FROM goals WHERE user_id=? AND is_completed=0', (uid(),)).fetchall()
    active_goals = []
    for g_ in goals:
        pct = round(g_['current_savings']/g_['goal_amount']*100, 1) if g_['goal_amount'] else 0
        ms = g_['monthly_saving'] or 1
        remaining = max(0, g_['goal_amount'] - g_['current_savings'])
        months_left = round(remaining/ms, 1) if ms else 999
        active_goals.append({**dict(g_), 'progress_pct': min(pct,100), 'months_left': months_left})
    completed = db.execute('SELECT COUNT(*) as c FROM goals WHERE user_id=? AND is_completed=1', (uid(),)).fetchone()['c']

    # Streak
    cur_streak, best_streak = calc_streak(db, uid())
    streak_data = db.execute('SELECT * FROM daily_streaks WHERE user_id=? ORDER BY streak_date DESC LIMIT 90', (uid(),)).fetchall()
    heatmap = [{'date': r['streak_date'], 'spend': r['daily_spend'], 'green': bool(r['under_threshold'])} for r in reversed(streak_data)]

    # Health score
    score = 100
    score -= min(int(impulse_pct * 0.4), 30)
    score += min(len(goals) * 5, 15)
    score += min(cur_streak, 15)
    score += min(u['total_points'] // 20, 10)
    score = max(0, min(100, score))
    grade = 'A' if score>=80 else 'B' if score>=65 else 'C' if score>=50 else 'D'

    # Predict next month
    monthly = []
    for i in range(3):
        pm = (now.month - i - 1) % 12 + 1
        py = now.year if now.month - i > 0 else now.year - 1
        t = db.execute('''SELECT COALESCE(SUM(amount),0) as s FROM expenses WHERE user_id=?
            AND strftime('%m',expense_date)=? AND strftime('%Y',expense_date)=?''',
            (uid(), f'{pm:02d}', str(py))).fetchone()['s']
        monthly.append(t)
    predicted = round(sum(monthly)/len([x for x in monthly if x > 0]) if any(monthly) else 0, 0)

    # Badges
    badges = db.execute('SELECT * FROM badges WHERE user_id=? ORDER BY awarded_at DESC', (uid(),)).fetchall()

    # Recent expenses
    recent = db.execute('SELECT * FROM expenses WHERE user_id=? ORDER BY expense_date DESC LIMIT 8', (uid(),)).fetchall()

    return jsonify({
        'user': {'name': u['name'], 'email': u['email'], 'total_points': u['total_points'], 'coins': u['coins']},
        'stats': {
            'month_total': round(month_total, 2),
            'impulse_pct': impulse_pct,
            'active_goals': len(goals),
            'completed_goals': completed,
            'cur_streak': cur_streak,
            'best_streak': best_streak,
            'points': u['total_points'],
            'coins': u['coins'],
        },
        'health': {'score': score, 'grade': grade, 'label': ['Poor','Needs Work','Average','Good','Excellent'][['F','D','C','B','A'].index(grade) if grade in ['F','D','C','B','A'] else 2], 'impulse_pct': impulse_pct},
        'predicted': predicted,
        'by_category': {k: round(v,2) for k,v in sorted(by_cat.items(), key=lambda x:-x[1])},
        'by_day': {k: round(v,2) for k,v in sorted(by_day.items())},
        'active_goals': active_goals,
        'badges': [dict(b) for b in badges],
        'recent_expenses': [dict(e) for e in recent],
        'heatmap': heatmap,
        'streak': {'current': cur_streak, 'best': best_streak},
    })

# ─────────────────────────────────────────────────────────────
#  EXPENSES API
# ─────────────────────────────────────────────────────────────
@app.route('/api/expenses', methods=['GET'])
@jwt_required
def list_expenses():
    db = get_db()
    page = int(request.args.get('page',1))
    per = int(request.args.get('per_page',15))
    cat = request.args.get('category','')
    month = request.args.get('month','')
    year = request.args.get('year','')
    where = 'WHERE user_id=?'; params = [uid()]
    if cat: where += ' AND category=?'; params.append(cat)
    if month: where += f" AND strftime('%m',expense_date)=?"; params.append(f'{int(month):02d}')
    if year: where += f" AND strftime('%Y',expense_date)=?"; params.append(year)
    total = db.execute(f'SELECT COUNT(*) as c FROM expenses {where}', params).fetchone()['c']
    rows = db.execute(f'SELECT * FROM expenses {where} ORDER BY expense_date DESC LIMIT ? OFFSET ?',
                      params+[per,(page-1)*per]).fetchall()
    return jsonify({'expenses': [dict(r) for r in rows], 'total': total, 'pages': (total+per-1)//per, 'page': page})

@app.route('/api/expenses/add', methods=['POST'])
@jwt_required
def add_expense():
    d = request.get_json()
    amount = float(d.get('amount',0))
    if amount <= 0: return jsonify({'error': 'amount must be positive'}), 400
    category = d.get('category','other')
    merchant = d.get('merchant','')
    payment_mode = d.get('payment_mode','upi')
    tx_id = d.get('transaction_id','')
    entry_type = d.get('entry_type','manual')
    dt = d.get('expense_date') or datetime.datetime.now().isoformat()
    desc = d.get('description','')
    imp = detect_impulse(amount, category, merchant, dt)
    db = get_db()
    cur = db.execute('''INSERT INTO expenses(user_id,amount,category,merchant,description,payment_mode,transaction_id,entry_type,is_impulse,impulse_reason,expense_date)
        VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
        (uid(), amount, category, merchant, desc, payment_mode, tx_id, entry_type, int(imp['is_impulse']), imp['reason'], dt))
    db.commit()
    exp_id = cur.lastrowid
    update_streak(db, uid(), dt, amount)
    add_points(db, uid(), 2, 1)
    # Badge checks
    total_exps = db.execute('SELECT COUNT(*) as c FROM expenses WHERE user_id=?', (uid(),)).fetchone()['c']
    if total_exps == 1: award_badge(db, uid(), 'starter')
    if entry_type == 'ocr': award_badge(db, uid(), 'ocr_user')
    if entry_type == 'split': award_badge(db, uid(), 'splitter')
    streak, _ = calc_streak(db, uid())
    if streak >= 7: award_badge(db, uid(), 'streak_7')
    if streak >= 30: award_badge(db, uid(), 'streak_30')
    if streak >= 50: award_badge(db, uid(), 'streak_50')
    return jsonify({'message': 'Expense added!', 'id': exp_id, 'impulse': imp}), 201

@app.route('/api/expenses/<int:eid>', methods=['DELETE'])
@jwt_required
def delete_expense(eid):
    db = get_db()
    db.execute('DELETE FROM expenses WHERE id=? AND user_id=?', (eid, uid()))
    db.commit()
    return jsonify({'message': 'Deleted'})

@app.route('/api/expenses/parse-sms', methods=['POST'])
@jwt_required
def parse_sms_api():
    d = request.get_json()
    text = d.get('sms_text','')
    if not text: return jsonify({'error': 'sms_text required'}), 400
    return jsonify(parse_sms(text))

@app.route('/api/expenses/ocr', methods=['POST'])
@jwt_required
def ocr_expense():
    d = request.get_json()
    b64 = d.get('image_base64','')
    text = d.get('text','')
    if b64:
        result = ocr_from_base64(b64)
    elif text:
        result = parse_text(text)
    else:
        return jsonify({'error': 'Provide image_base64 or text'}), 400
    return jsonify(result)

@app.route('/api/expenses/analytics', methods=['GET'])
@jwt_required
def analytics():
    db = get_db()
    now = datetime.datetime.now()
    m = int(request.args.get('month', now.month))
    y = int(request.args.get('year', now.year))
    rows = db.execute('''SELECT * FROM expenses WHERE user_id=?
        AND strftime('%m',expense_date)=? AND strftime('%Y',expense_date)=?''',
        (uid(), f'{m:02d}', str(y))).fetchall()
    total = sum(r['amount'] for r in rows)
    imp_total = sum(r['amount'] for r in rows if r['is_impulse'])
    by_cat = {}
    by_day = {}
    for r in rows:
        by_cat[r['category']] = by_cat.get(r['category'],0)+r['amount']
        d = r['expense_date'][:10]
        by_day[d] = by_day.get(d,0)+r['amount']
    return jsonify({
        'total': round(total,2), 'impulse_total': round(imp_total,2),
        'impulse_pct': round(imp_total/total*100,1) if total else 0,
        'count': len(rows),
        'by_category': {k:round(v,2) for k,v in sorted(by_cat.items(),key=lambda x:-x[1])},
        'by_day': {k:round(v,2) for k,v in sorted(by_day.items())},
    })

@app.route('/api/expenses/health-score', methods=['GET'])
@jwt_required
def health_score():
    db = get_db()
    u = db.execute('SELECT * FROM users WHERE id=?', (uid(),)).fetchone()
    now = datetime.datetime.now()
    exps = db.execute('''SELECT * FROM expenses WHERE user_id=?
        AND expense_date >= date('now','-90 days')''', (uid(),)).fetchall()
    total = sum(e['amount'] for e in exps) or 1
    imp = sum(e['amount'] for e in exps if e['is_impulse'])
    imp_pct = round(imp/total*100,1)
    goals = db.execute('SELECT COUNT(*) as c FROM goals WHERE user_id=? AND is_completed=0',(uid(),)).fetchone()['c']
    cur_streak, _ = calc_streak(db, uid())
    score = 100 - min(int(imp_pct*0.4),30) + min(goals*5,15) + min(cur_streak,15) + min(u['total_points']//20,10)
    score = max(0, min(100, score))
    grade = 'A' if score>=80 else 'B' if score>=65 else 'C' if score>=50 else 'D' if score>=35 else 'F'
    labels = {'A':'Excellent','B':'Good','C':'Average','D':'Needs Work','F':'Poor'}
    return jsonify({'score':score,'grade':grade,'label':labels[grade],'impulse_pct':imp_pct,'streak_days':cur_streak,'active_goals':goals,'points':u['total_points']})

@app.route('/api/expenses/predict', methods=['GET'])
@jwt_required
def predict_spending():
    db = get_db()
    now = datetime.datetime.now()
    monthly = []
    for i in range(3):
        pm = (now.month - i - 1) % 12 + 1
        py = now.year - (1 if now.month - i <= 0 else 0)
        t = db.execute('''SELECT COALESCE(SUM(amount),0) as s FROM expenses WHERE user_id=?
            AND strftime('%m',expense_date)=? AND strftime('%Y',expense_date)=?''',
            (uid(), f'{pm:02d}', str(py))).fetchone()['s']
        monthly.append(float(t))
    valid = [x for x in monthly if x > 0]
    predicted = round(sum(valid)/len(valid),2) if valid else 0
    trend = round(monthly[0]-monthly[-1],2) if len(monthly)>1 else 0
    return jsonify({'predicted': predicted, 'trend': trend, 'last_3_months': list(reversed(monthly))})

@app.route('/api/expenses/simulate', methods=['POST'])
@jwt_required
def simulate_purchase():
    db = get_db()
    d = request.get_json()
    amount = float(d.get('amount',0))
    item = d.get('item_name','Item')
    if amount<=0: return jsonify({'error':'amount required'}),400
    now = datetime.datetime.now()
    month_spend = db.execute('''SELECT COALESCE(SUM(amount),0) as s FROM expenses WHERE user_id=?
        AND strftime('%m',expense_date)=? AND strftime('%Y',expense_date)=?''',
        (uid(),f'{now.month:02d}',str(now.year))).fetchone()['s']
    goals = db.execute('SELECT * FROM goals WHERE user_id=? AND is_completed=0',(uid(),)).fetchall()
    total_savings = sum(g['current_savings'] for g in goals)
    sav_impact = round(amount/total_savings*100,1) if total_savings>0 else 0
    mon_impact = round(amount/(month_spend+amount)*100,1) if (month_spend+amount)>0 else 0
    risk = 'HIGH' if sav_impact>20 or mon_impact>25 else 'MEDIUM' if sav_impact>10 or mon_impact>15 else 'LOW'
    delays = []
    for g in goals[:3]:
        ms = g['monthly_saving'] or 1
        delays.append({'goal':g['goal_name'],'delay_months':round(amount/ms,1)})
    rec = '⚠️ Consider waiting — HIGH budget risk!' if risk=='HIGH' else '💛 Proceed with caution.' if risk=='MEDIUM' else '✅ Safe to buy!'
    return jsonify({'item':item,'amount':amount,'savings_impact':sav_impact,'monthly_impact':mon_impact,'budget_risk':risk,'goal_delays':delays,'recommendation':rec})

@app.route('/api/expenses/streaks', methods=['GET'])
@jwt_required
def streak_info():
    db = get_db()
    cur, best = calc_streak(db, uid())
    import datetime as dt_mod
    today = dt_mod.date.today()
    week_start = today - dt_mod.timedelta(days=today.weekday())
    rows_90 = db.execute('SELECT * FROM daily_streaks WHERE user_id=? ORDER BY streak_date DESC LIMIT 90',(uid(),)).fetchall()
    weekly = [r for r in rows_90 if r['streak_date'] >= week_start.isoformat()]
    monthly = [r for r in rows_90 if r['streak_date'][:7] == today.isoformat()[:7]]
    heatmap = [{'date':r['streak_date'],'spend':r['daily_spend'],'green':bool(r['under_threshold'])} for r in reversed(rows_90)]
    return jsonify({
        'current': cur, 'best': best,
        'weekly_green': sum(1 for r in weekly if r['under_threshold']),
        'weekly_total': len(weekly),
        'monthly_green': sum(1 for r in monthly if r['under_threshold']),
        'monthly_total': len(monthly),
        'milestone_50': cur >= 50,
        'heatmap': heatmap,
    })

# ─────────────────────────────────────────────────────────────
#  GOALS API
# ─────────────────────────────────────────────────────────────
@app.route('/api/goals', methods=['GET'])
@jwt_required
def list_goals():
    db = get_db()
    rows = db.execute('SELECT * FROM goals WHERE user_id=? ORDER BY created_at DESC',(uid(),)).fetchall()
    result = []
    for g in rows:
        pct = round(g['current_savings']/g['goal_amount']*100,1) if g['goal_amount'] else 0
        ms = g['monthly_saving'] or 1
        remaining = max(0, g['goal_amount'] - g['current_savings'])
        result.append({**dict(g),'progress_pct':min(pct,100),'months_left':round(remaining/ms,1)})
    return jsonify({'goals':result})

@app.route('/api/goals/add', methods=['POST'])
@jwt_required
def add_goal():
    d = request.get_json()
    name = d.get('goal_name','').strip()
    amount = float(d.get('goal_amount',0))
    monthly = float(d.get('monthly_saving',0))
    if not name or amount<=0: return jsonify({'error':'name and amount required'}),400
    db = get_db()
    cur = db.execute('INSERT INTO goals(user_id,goal_name,goal_amount,monthly_saving,category,target_date) VALUES(?,?,?,?,?,?)',
                     (uid(),name,amount,monthly,d.get('category','other'),d.get('target_date','')))
    db.commit()
    add_points(db,uid(),10,5)
    award_badge(db,uid(),'goal_setter')
    return jsonify({'message':'Goal created!','id':cur.lastrowid}),201

@app.route('/api/goals/<int:gid>/deposit', methods=['POST'])
@jwt_required
def deposit_goal(gid):
    d = request.get_json()
    amount = float(d.get('amount',0))
    if amount<=0: return jsonify({'error':'amount required'}),400
    db = get_db()
    g = db.execute('SELECT * FROM goals WHERE id=? AND user_id=?',(gid,uid())).fetchone()
    if not g: return jsonify({'error':'Goal not found'}),404
    new_savings = g['current_savings']+amount
    completed = new_savings >= g['goal_amount']
    db.execute('UPDATE goals SET current_savings=?, is_completed=? WHERE id=?',(new_savings,int(completed),gid))
    db.commit()
    add_points(db,uid(),5,2)
    if completed: award_badge(db,uid(),'goal_done')
    if new_savings >= 1000: award_badge(db,uid(),'saver_1k')
    if new_savings >= 10000: award_badge(db,uid(),'saver_10k')
    return jsonify({'message':'Deposit added!','new_savings':new_savings,'completed':completed})

@app.route('/api/goals/<int:gid>/delete', methods=['DELETE'])
@jwt_required
def delete_goal(gid):
    db = get_db()
    db.execute('DELETE FROM goals WHERE id=? AND user_id=?',(gid,uid()))
    db.commit()
    return jsonify({'message':'Deleted'})

@app.route('/api/goals/predict', methods=['POST'])
@jwt_required
def predict_goal():
    d = request.get_json()
    goal_amount = float(d.get('goal_amount',0))
    monthly = float(d.get('monthly_saving',0))
    current = float(d.get('current_savings',0))
    if goal_amount<=0 or monthly<=0: return jsonify({'error':'goal_amount and monthly_saving required'}),400
    remaining = max(0, goal_amount-current)
    months_needed = round(remaining/monthly,1)
    improved = round(remaining/(monthly*1.1),1)
    return jsonify({'months_needed':months_needed,'improved_months':improved,'months_saved':round(months_needed-improved,1),'tip':'Increase monthly saving by 10% to reach goal faster!'})

# ─────────────────────────────────────────────────────────────
#  SPLITS API
# ─────────────────────────────────────────────────────────────
@app.route('/api/splits/add', methods=['POST'])
@jwt_required
def add_split():
    d = request.get_json()
    total = float(d.get('total_amount',0))
    merchant = d.get('merchant','')
    category = d.get('category','food')
    participants = d.get('participants',[])
    if total<=0 or not participants: return jsonify({'error':'total_amount and participants required'}),400
    db = get_db()
    cur = db.execute('INSERT INTO expenses(user_id,amount,category,merchant,entry_type,expense_date) VALUES(?,?,?,?,?,?)',
                     (uid(),total,category,merchant,'split',datetime.datetime.now().isoformat()))
    db.commit()
    exp_id = cur.lastrowid
    for p in participants:
        db.execute('INSERT INTO splits(expense_id,user_id,ower_name,ower_email,amount_owed) VALUES(?,?,?,?,?)',
                   (exp_id,uid(),p.get('name',''),p.get('email',''),float(p.get('amount',0))))
    db.commit()
    award_badge(db,uid(),'splitter')
    total_owed = sum(float(p.get('amount',0)) for p in participants)
    return jsonify({'message':'Split created!','expense_id':exp_id,'total_owed':total_owed}),201

@app.route('/api/splits', methods=['GET'])
@jwt_required
def list_splits():
    db = get_db()
    rows = db.execute('''SELECT s.*, e.merchant, e.amount as total_amount, e.category
        FROM splits s JOIN expenses e ON s.expense_id=e.id
        WHERE s.user_id=? ORDER BY s.created_at DESC''',(uid(),)).fetchall()
    total_pending = sum(r['amount_owed'] for r in rows if not r['is_settled'])
    balances = {}
    for r in rows:
        n = r['ower_name']
        if n not in balances: balances[n]={'total_owed':0,'settled':0,'pending':0}
        balances[n]['total_owed']+=r['amount_owed']
        if r['is_settled']: balances[n]['settled']+=r['amount_owed']
        else: balances[n]['pending']+=r['amount_owed']
    return jsonify({'splits':[dict(r) for r in rows],'total_pending':round(total_pending,2),'balances':[{'name':k,**v} for k,v in balances.items()]})

@app.route('/api/splits/<int:sid>/settle', methods=['PUT'])
@jwt_required
def settle_split(sid):
    db = get_db()
    db.execute('UPDATE splits SET is_settled=1, settled_at=? WHERE id=? AND user_id=?',
               (datetime.datetime.now().isoformat(),sid,uid()))
    db.commit()
    return jsonify({'message':'Settled!'})

# ─────────────────────────────────────────────────────────────
#  GAMIFICATION API
# ─────────────────────────────────────────────────────────────
@app.route('/api/gamification/badges', methods=['GET'])
@jwt_required
def get_badges():
    db = get_db()
    u = db.execute('SELECT total_points,coins FROM users WHERE id=?',(uid(),)).fetchone()
    earned = db.execute('SELECT badge_key FROM badges WHERE user_id=?',(uid(),)).fetchall()
    earned_keys = {r['badge_key'] for r in earned}
    all_b = []
    for b in ALL_BADGES:
        all_b.append({'key':b[0],'icon':b[1],'name':b[2],'description':b[3],'points':b[4],'order':b[5],'earned':b[0] in earned_keys})
    earned_list = db.execute('SELECT * FROM badges WHERE user_id=? ORDER BY awarded_at DESC',(uid(),)).fetchall()
    return jsonify({'all_badges':all_b,'earned':[dict(r) for r in earned_list],'total_points':u['total_points'],'coins':u['coins']})

@app.route('/api/gamification/leaderboard', methods=['GET'])
def leaderboard():
    db = get_db()
    rows = db.execute('SELECT id,name,total_points,coins FROM users ORDER BY total_points DESC LIMIT 10').fetchall()
    result = [{'rank':i+1,'name':r['name'],'total_points':r['total_points'],'coins':r['coins']} for i,r in enumerate(rows)]
    return jsonify({'leaderboard':result})

# ─────────────────────────────────────────────────────────────
#  RUN
# ─────────────────────────────────────────────────────────────
if __name__ == '__main__':
    init_db()
    print('✅ SmartSpend AI v3 starting...')
    print('📊 Dashboard: http://localhost:5000')
    app.run(debug=True, host='0.0.0.0', port=5000)

