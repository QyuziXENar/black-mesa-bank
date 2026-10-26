import os
import random
from datetime import datetime
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, flash
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'black-mesa-secret-key-2026')

# Render PostgreSQL URL fix + Local SQLite fallback
database_url = os.environ.get('DATABASE_URL')
if database_url and database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = database_url or 'sqlite:///black_mesa_bank.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# ----------------- DATABASE MODELS ----------------- #
class User(db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    pin_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default='customer') # 'admin' or 'customer'
    account_number = db.Column(db.String(15), unique=True, nullable=False)
    balance = db.Column(db.Float, default=0.0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    transactions = db.relationship('Transaction', backref='user', lazy=True, cascade="all, delete-orphan")

    def check_pin(self, pin):
        return check_password_hash(self.pin_hash, pin)

class Transaction(db.Model):
    __tablename__ = 'transactions'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    transaction_type = db.Column(db.String(20), nullable=False) # 'DEPOSIT', 'WITHDRAWAL', 'INITIAL'
    amount = db.Column(db.Float, nullable=False)
    balance_after = db.Column(db.Float, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    description = db.Column(db.String(200))

# ----------------- CONVERTED BANK LOGIC ----------------- #
class BankAccountService:
    @staticmethod
    def deposit(user, amount, pin):
        if not user.check_pin(pin):
            return False, "Security check failed: Incorrect PIN entered."
        if amount <= 0:
            return False, "Deposit amount must be greater than zero."
            
        user.balance += amount
        tx = Transaction(
            user_id=user.id,
            transaction_type='DEPOSIT',
            amount=amount,
            balance_after=user.balance,
            description='Online Cash Deposit'
        )
        db.session.add(tx)
        db.session.commit()
        return True, f"Successfully deposited ${amount:,.2f}. Current balance: ${user.balance:,.2f}"

    @staticmethod
    def withdraw(user, amount, pin):
        if not user.check_pin(pin):
            return False, "Security check failed: Incorrect PIN entered."
        if amount <= 0:
            return False, "Withdrawal amount must be greater than zero."
        if amount > user.balance:
            return False, f"Insufficient funds! Available balance: ${user.balance:,.2f}"

        user.balance -= amount
        tx = Transaction(
            user_id=user.id,
            transaction_type='WITHDRAWAL',
            amount=amount,
            balance_after=user.balance,
            description='Online Cash Withdrawal'
        )
        db.session.add(tx)
        db.session.commit()
        return True, f"Successfully withdrew ${amount:,.2f}. Current balance: ${user.balance:,.2f}"

# ----------------- AUTH DECORATORS ----------------- #
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('Please log in to access your bank portal.', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if session.get('role') != 'admin':
            flash('Admin authorization required.', 'danger')
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return decorated_function

def generate_account_number():
    while True:
        acc = f"BM-{random.randint(1000, 9999)}-{random.randint(1000, 9999)}"
        if not User.query.filter_by(account_number=acc).first():
            return acc

# ----------------- ROUTES ----------------- #
@app.route('/')
def index():
    if 'user_id' in session:
        return redirect(url_for('admin_panel' if session.get('role') == 'admin' else 'dashboard'))
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        pin = request.form.get('pin', '').strip()
        
        user = User.query.filter_by(username=username).first()
        if user and user.check_pin(pin):
            session.clear()
            session['user_id'] = user.id
            session['username'] = user.username
            session['role'] = user.role
            flash(f'Authentication successful! Welcome, {user.username}.', 'success')
            return redirect(url_for('admin_panel' if user.role == 'admin' else 'dashboard'))
        else:
            flash('Invalid username or PIN code. Please verify credentials.', 'danger')

    return render_template('login.html')

@app.route('/dashboard')
@login_required
def dashboard():
    user = db.session.get(User, session['user_id'])
    if user.role == 'admin':
        return redirect(url_for('admin_panel'))
    
    recent_transactions = Transaction.query.filter_by(user_id=user.id)\
        .order_by(Transaction.timestamp.desc()).limit(15).all()
        
    return render_template('dashboard.html', user=user, transactions=recent_transactions)

@app.route('/transaction', methods=['POST'])
@login_required
def perform_transaction():
    user = db.session.get(User, session['user_id'])
    trans_type = request.form.get('type')
    confirm_pin = request.form.get('confirm_pin', '').strip()
    
    try:
        amount = float(request.form.get('amount', 0))
    except ValueError:
        flash('Invalid transaction amount entered.', 'danger')
        return redirect(url_for('dashboard'))

    if trans_type == 'deposit':
        success, msg = BankAccountService.deposit(user, amount, confirm_pin)
        flash(msg, 'success' if success else 'danger')
    elif trans_type == 'withdraw':
        success, msg = BankAccountService.withdraw(user, amount, confirm_pin)
        flash(msg, 'success' if success else 'danger')

    return redirect(url_for('dashboard'))

@app.route('/change-pin', methods=['GET', 'POST'])
@login_required
def change_pin():
    user = db.session.get(User, session['user_id'])
    if request.method == 'POST':
        current_pin = request.form.get('current_pin', '').strip()
        new_pin = request.form.get('new_pin', '').strip()
        confirm_new_pin = request.form.get('confirm_new_pin', '').strip()

        if not user.check_pin(current_pin):
            flash('Current PIN is incorrect.', 'danger')
        elif len(new_pin) < 4:
            flash('New PIN must be at least 4 digits.', 'warning')
        elif new_pin != confirm_new_pin:
            flash('New PIN and Confirmation PIN do not match.', 'warning')
        else:
            user.pin_hash = generate_password_hash(new_pin)
            db.session.commit()
            flash('Security Update: Your PIN was successfully changed!', 'success')
            return redirect(url_for('dashboard'))

    return render_template('change_pin.html', user=user)

# CRUD: Read Admin Dashboard
@app.route('/admin')
@login_required
@admin_required
def admin_panel():
    customers = User.query.filter_by(role='customer').order_by(User.created_at.desc()).all()
    all_transactions = Transaction.query.order_by(Transaction.timestamp.desc()).limit(20).all()
    total_deposits = db.session.query(db.func.sum(User.balance)).filter(User.role == 'customer').scalar() or 0.0
    return render_template('admin.html', customers=customers, transactions=all_transactions, total_deposits=total_deposits)

# CRUD: Create Customer
@app.route('/admin/create-customer', methods=['POST'])
@login_required
@admin_required
def create_customer():
    username = request.form.get('username', '').strip()
    default_pin = request.form.get('default_pin', '').strip()
    try:
        initial_balance = float(request.form.get('initial_balance', 0))
    except ValueError:
        initial_balance = 0.0

    if not username or not default_pin:
        flash('Username and default PIN are required.', 'warning')
        return redirect(url_for('admin_panel'))

    if User.query.filter_by(username=username).first():
        flash(f'Account creation failed: User "{username}" already exists.', 'danger')
        return redirect(url_for('admin_panel'))

    account_no = generate_account_number()
    new_user = User(
        username=username,
        pin_hash=generate_password_hash(default_pin),
        role='customer',
        account_number=account_no,
        balance=initial_balance
    )
    db.session.add(new_user)
    db.session.commit()

    if initial_balance > 0:
        init_tx = Transaction(
            user_id=new_user.id,
            transaction_type='INITIAL',
            amount=initial_balance,
            balance_after=initial_balance,
            description='Initial Account Opening Balance'
        )
        db.session.add(init_tx)
        db.session.commit()

    flash(f'New customer registered: {username} | Account: {account_no} | Default PIN: {default_pin}', 'success')
    return redirect(url_for('admin_panel'))

# CRUD: Delete Customer Account
@app.route('/admin/delete-customer/<int:user_id>', methods=['POST'])
@login_required
@admin_required
def delete_customer(user_id):
    customer = db.session.get(User, user_id)
    if not customer:
        flash('Action aborted: Account record not found.', 'danger')
        return redirect(url_for('admin_panel'))
    if customer.role == 'admin':
        flash('Security Violation: Root administrator accounts cannot be deleted.', 'danger')
        return redirect(url_for('admin_panel'))

    username = customer.username
    db.session.delete(customer)
    db.session.commit()
    flash(f'Record purged: Account for "{username}" has been permanently closed/deleted.', 'warning')
    return redirect(url_for('admin_panel'))

@app.route('/logout')
def logout():
    session.clear()
    flash('You have been securely signed out.', 'info')
    return redirect(url_for('login'))

# ----------------- AUTO-INITIALIZATION ----------------- #
with app.app_context():
    db.create_all()
    # Pre-seed Admin account if not existing
    if not User.query.filter_by(username='admin').first():
        admin = User(
            username='admin',
            pin_hash=generate_password_hash('admin123'),
            role='admin',
            account_number='BM-0000-0001',
            balance=0.0
        )
        db.session.add(admin)
        
        # Pre-seed your Customer account
        sample_customer = User(
            username='franz',
            pin_hash=generate_password_hash('1234'),
            role='customer',
            account_number='BM-7788-9900',
            balance=1500.0
        )
        db.session.add(sample_customer)
        db.session.commit()
        
        init_tx = Transaction(
            user_id=sample_customer.id,
            transaction_type='INITIAL',
            amount=1500.0,
            balance_after=1500.0,
            description='Opening Balance Deposit'
        )
        db.session.add(init_tx)
        db.session.commit()

if __name__ == '__main__':
    app.run(debug=True, port=5000)