from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, date
import os
import smtplib
from email.mime.text import MIMEText

app = Flask(__name__)
app.config['SECRET_KEY'] = 'dev-secret-key-change-in-production'
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///deskflow.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Email config — set these in Render environment variables
MAIL_USER = os.environ.get('MAIL_USER', '')
MAIL_PASS = os.environ.get('MAIL_PASS', '')

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

# ─── Secondary SPOC association table ─────────────────────────────────────────
ticket_spocs = db.Table('ticket_spocs',
    db.Column('ticket_id', db.Integer, db.ForeignKey('ticket.id')),
    db.Column('user_id',   db.Integer, db.ForeignKey('user.id'))
)

# ─── Models ────────────────────────────────────────────────────────────────────

class Team(db.Model):
    id      = db.Column(db.Integer, primary_key=True)
    name    = db.Column(db.String(100), unique=True, nullable=False)
    members = db.relationship('User', backref='team', lazy=True)

class Category(db.Model):
    id      = db.Column(db.Integer, primary_key=True)
    name    = db.Column(db.String(100), unique=True, nullable=False)
    tickets = db.relationship('Ticket', backref='category', lazy=True)

class User(UserMixin, db.Model):
    id       = db.Column(db.Integer, primary_key=True)
    name     = db.Column(db.String(100), nullable=False)
    email    = db.Column(db.String(150), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)
    team_id  = db.Column(db.Integer, db.ForeignKey('team.id'), nullable=True)
    tickets_created  = db.relationship('Ticket', foreign_keys='Ticket.user_id', backref='author', lazy=True)
    tickets_assigned = db.relationship('Ticket', foreign_keys='Ticket.assigned_to', backref='assignee', lazy=True)
    comments         = db.relationship('Comment', backref='commenter', lazy=True)

class Ticket(db.Model):
    id          = db.Column(db.Integer, primary_key=True)
    title       = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=False)
    priority    = db.Column(db.String(20), default='medium')
    status      = db.Column(db.String(20), default='open')
    start_date  = db.Column(db.Date, nullable=True)
    due_date    = db.Column(db.Date, nullable=True)
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at  = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    user_id     = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    assigned_to = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    category_id = db.Column(db.Integer, db.ForeignKey('category.id'), nullable=True)
    reference_url = db.Column(db.String(500), nullable=True)
    spocs       = db.relationship('User', secondary=ticket_spocs, backref='spoc_tickets', lazy=True)
    comments    = db.relationship('Comment', backref='ticket', lazy=True, cascade='all, delete-orphan')

    @property
    def is_overdue(self):
        if self.due_date and self.status != 'closed':
            return date.today() > self.due_date
        return False

    @property
    def days_until_due(self):
        if self.due_date:
            return (self.due_date - date.today()).days
        return None


    # ─── Weight config (tune these values as needed) ──────────────────────────
    PRIORITY_WEIGHT  = {'high': 40, 'medium': 20, 'low': 10}
    TIMELINE_WEIGHT  = 50   # max points for timeline urgency
    CATEGORY_WEIGHT  = 10   # base points for having a category

    @property
    def weight_score(self):
        score = 0
        # Priority contribution
        score += self.PRIORITY_WEIGHT.get(self.priority, 0)
        # Category contribution
        if self.category_id:
            score += self.CATEGORY_WEIGHT
        # Timeline urgency — highest weight
        d = self.days_until_due
        if d is not None:
            if d < 0:       score += self.TIMELINE_WEIGHT        # overdue = max urgency
            elif d == 0:    score += self.TIMELINE_WEIGHT        # due today
            elif d <= 2:    score += int(self.TIMELINE_WEIGHT * 0.85)
            elif d <= 5:    score += int(self.TIMELINE_WEIGHT * 0.65)
            elif d <= 10:   score += int(self.TIMELINE_WEIGHT * 0.40)
            elif d <= 20:   score += int(self.TIMELINE_WEIGHT * 0.20)
            else:           score += int(self.TIMELINE_WEIGHT * 0.05)
        return score

    def can_edit(self, user):
        return user.is_admin or self.user_id == user.id

class Comment(db.Model):
    id         = db.Column(db.Integer, primary_key=True)
    body       = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    ticket_id  = db.Column(db.Integer, db.ForeignKey('ticket.id'), nullable=False)
    user_id    = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# ─── Email helper ──────────────────────────────────────────────────────────────

def send_email(to, subject, body):
    if not MAIL_USER or not MAIL_PASS:
        return
    try:
        msg = MIMEText(body, 'html')
        msg['Subject'] = subject
        msg['From']    = MAIL_USER
        msg['To']      = to
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as s:
            s.login(MAIL_USER, MAIL_PASS)
            s.send_message(msg)
    except Exception as e:
        print(f'Email error: {e}')

def notify_assignment(ticket):
    if not ticket.assignee:
        return
    send_email(
        to      = ticket.assignee.email,
        subject = f'[DeskFlow] Ticket assigned to you: #{ticket.id} {ticket.title}',
        body    = f'''
        <p>Hi {ticket.assignee.name},</p>
        <p>A ticket has been assigned to you:</p>
        <p><b>#{ticket.id} — {ticket.title}</b></p>
        <p>Priority: {ticket.priority} | Status: {ticket.status}</p>
        <p>By: {ticket.author.name}</p>
        <p><a href="https://deskflow-rmwt.onrender.com/ticket/{ticket.id}">View ticket →</a></p>
        '''
    )

def notify_status_change(ticket, old_status):
    recipients = set()
    if ticket.author:    recipients.add(ticket.author.email)
    if ticket.assignee:  recipients.add(ticket.assignee.email)
    for spoc in ticket.spocs: recipients.add(spoc.email)
    for email in recipients:
        send_email(
            to      = email,
            subject = f'[DeskFlow] Ticket #{ticket.id} updated: {old_status} → {ticket.status}',
            body    = f'''
            <p>Ticket <b>#{ticket.id} — {ticket.title}</b> status changed from
            <b>{old_status}</b> to <b>{ticket.status}</b>.</p>
            <p><a href="https://deskflow-rmwt.onrender.com/ticket/{ticket.id}">View ticket →</a></p>
            '''
        )

# ─── Auth ──────────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return redirect(url_for('dashboard') if current_user.is_authenticated else 'login')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        name     = request.form['name'].strip()
        email    = request.form['email'].strip().lower()
        password = request.form['password']
        if not email.endswith('@unacademy.com'):
            flash('Only @unacademy.com email addresses are allowed.', 'error')
            return redirect(url_for('register'))
        if User.query.filter_by(email=email).first():
            flash('Email already registered.', 'error')
            return redirect(url_for('register'))
        user = User(
            name=name, email=email,
            password=generate_password_hash(password),
            is_admin=(User.query.count() == 0)
        )
        db.session.add(user)
        db.session.commit()
        login_user(user)
        flash(f'Welcome, {name}! {"You are the admin." if user.is_admin else ""}', 'success')
        return redirect(url_for('dashboard'))
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email    = request.form['email'].strip().lower()
        password = request.form['password']
        user     = User.query.filter_by(email=email).first()
        if user and check_password_hash(user.password, password):
            login_user(user)
            return redirect(url_for('dashboard'))
        flash('Invalid email or password.', 'error')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

# ─── Dashboard (Kanban) ────────────────────────────────────────────────────────

@app.route('/dashboard')
@login_required
def dashboard():
    # Search & filter params
    q          = request.args.get('q', '').strip()
    f_status   = request.args.get('status', '')
    f_priority = request.args.get('priority', '')
    f_category = request.args.get('category', '')
    f_team     = request.args.get('team', '')

    if current_user.is_admin:
        query = Ticket.query
    else:
        query = Ticket.query.filter(
            (Ticket.user_id == current_user.id) |
            (Ticket.assigned_to == current_user.id) |
            (Ticket.spocs.any(User.id == current_user.id))
        )

    if q:
        query = query.filter(Ticket.title.ilike(f'%{q}%') | Ticket.description.ilike(f'%{q}%'))
    if f_status:
        query = query.filter(Ticket.status == f_status)
    if f_priority:
        query = query.filter(Ticket.priority == f_priority)
    if f_category:
        query = query.filter(Ticket.category_id == int(f_category))
    if f_team:
        query = query.join(User, Ticket.assigned_to == User.id).filter(User.team_id == int(f_team))

    all_tickets = query.order_by(Ticket.created_at.desc()).all()

    stats = {
        'open':        sum(1 for t in all_tickets if t.status == 'open'),
        'in_progress': sum(1 for t in all_tickets if t.status == 'in_progress'),
        'closed':      sum(1 for t in all_tickets if t.status == 'closed'),
        'overdue':     sum(1 for t in all_tickets if t.is_overdue),
    }

    categories = Category.query.order_by(Category.name).all()
    teams      = Team.query.order_by(Team.name).all()

    return render_template('dashboard.html',
        open_tickets      =[t for t in all_tickets if t.status == 'open'],
        inprogress_tickets=[t for t in all_tickets if t.status == 'in_progress'],
        closed_tickets    =[t for t in all_tickets if t.status == 'closed'],
        stats=stats, categories=categories, teams=teams,
        q=q, f_status=f_status, f_priority=f_priority,
        f_category=f_category, f_team=f_team
    )

@app.route('/ticket/update_status', methods=['POST'])
@login_required
def update_status():
    data       = request.get_json()
    ticket     = Ticket.query.get_or_404(data['ticket_id'])
    old_status = ticket.status
    ticket.status     = data['status']
    ticket.updated_at = datetime.utcnow()
    db.session.commit()
    if old_status != ticket.status:
        notify_status_change(ticket, old_status)
    return jsonify({'success': True})

# ─── Tickets ───────────────────────────────────────────────────────────────────

@app.route('/ticket/new', methods=['GET', 'POST'])
@login_required
def new_ticket():
    users      = User.query.order_by(User.name).all()
    categories = Category.query.order_by(Category.name).all()
    if request.method == 'POST':
        assigned  = request.form.get('assigned_to')
        cat       = request.form.get('category_id')
        start     = request.form.get('start_date')
        due       = request.form.get('due_date')
        spoc_ids  = request.form.getlist('spocs')
        ticket    = Ticket(
            title       = request.form['title'].strip(),
            description = request.form['description'].strip(),
            priority    = request.form['priority'],
            user_id     = current_user.id,
            assigned_to = int(assigned) if assigned else None,
            category_id = int(cat)      if cat      else None,
            start_date  = datetime.strptime(start, '%Y-%m-%d').date() if start else None,
            due_date      = datetime.strptime(due,   '%Y-%m-%d').date() if due   else None,
            reference_url = request.form.get('reference_url','').strip() or None,
        )
        ticket.spocs = [User.query.get(int(i)) for i in spoc_ids if i]
        db.session.add(ticket)
        db.session.commit()
        notify_assignment(ticket)
        flash('Ticket submitted!', 'success')
        return redirect(url_for('dashboard'))
    return render_template('new_ticket.html', users=users, categories=categories)

@app.route('/ticket/<int:ticket_id>')
@login_required
def view_ticket(ticket_id):
    ticket     = Ticket.query.get_or_404(ticket_id)
    users      = User.query.order_by(User.name).all()
    categories = Category.query.order_by(Category.name).all()
    spoc_ids   = [u.id for u in ticket.spocs]
    is_spoc    = current_user.id in spoc_ids
    can_edit   = ticket.can_edit(current_user)
    if not current_user.is_admin and ticket.user_id != current_user.id \
       and ticket.assigned_to != current_user.id and not is_spoc:
        flash('Access denied.', 'error')
        return redirect(url_for('dashboard'))
    return render_template('view_ticket.html', ticket=ticket, users=users,
                           categories=categories, spoc_ids=spoc_ids, can_edit=can_edit)

@app.route('/ticket/<int:ticket_id>/update', methods=['POST'])
@login_required
def update_ticket(ticket_id):
    ticket = Ticket.query.get_or_404(ticket_id)
    if not ticket.can_edit(current_user):
        flash('Only the ticket creator or admin can edit this ticket.', 'error')
        return redirect(url_for('view_ticket', ticket_id=ticket_id))
    old_status = ticket.status
    old_assign = ticket.assigned_to
    assigned   = request.form.get('assigned_to')
    cat        = request.form.get('category_id')
    start      = request.form.get('start_date')
    due        = request.form.get('due_date')
    spoc_ids   = request.form.getlist('spocs')
    ticket.status      = request.form['status']
    ticket.priority    = request.form['priority']
    ticket.assigned_to = int(assigned) if assigned else None
    ticket.category_id = int(cat)      if cat      else None
    ticket.start_date  = datetime.strptime(start, '%Y-%m-%d').date() if start else None
    ticket.reference_url = request.form.get('reference_url','').strip() or None
    ticket.due_date    = datetime.strptime(due,   '%Y-%m-%d').date() if due   else None
    ticket.spocs       = [User.query.get(int(i)) for i in spoc_ids if i]
    ticket.updated_at  = datetime.utcnow()
    db.session.commit()
    if old_status != ticket.status:
        notify_status_change(ticket, old_status)
    if old_assign != ticket.assigned_to:
        notify_assignment(ticket)
    flash('Ticket updated.', 'success')
    return redirect(url_for('view_ticket', ticket_id=ticket_id))

@app.route('/ticket/<int:ticket_id>/comment', methods=['POST'])
@login_required
def add_comment(ticket_id):
    ticket = Ticket.query.get_or_404(ticket_id)
    body   = request.form.get('body', '').strip()
    if body:
        comment = Comment(body=body, ticket_id=ticket_id, user_id=current_user.id)
        db.session.add(comment)
        db.session.commit()
    return redirect(url_for('view_ticket', ticket_id=ticket_id) + '#comments')

# ─── Timeline ──────────────────────────────────────────────────────────────────

@app.route('/timeline')
@login_required
def timeline():
    if current_user.is_admin:
        tickets = Ticket.query.filter(Ticket.due_date != None).order_by(Ticket.due_date).all()
    else:
        tickets = Ticket.query.filter(
            (Ticket.user_id == current_user.id) | (Ticket.assigned_to == current_user.id),
            Ticket.due_date != None
        ).order_by(Ticket.due_date).all()
    return render_template('timeline.html', tickets=tickets)

@app.route('/ticket/<int:ticket_id>/quick_status', methods=['POST'])
@login_required
def quick_status(ticket_id):
    ticket     = Ticket.query.get_or_404(ticket_id)
    old_status = ticket.status
    ticket.status     = request.form.get('status', ticket.status)
    ticket.updated_at = datetime.utcnow()
    db.session.commit()
    if old_status != ticket.status:
        notify_status_change(ticket, old_status)
    flash(f'Status updated to {ticket.status.replace("_", " ")}.', 'success')
    return redirect(url_for('view_ticket', ticket_id=ticket_id))

@app.route('/archive')
@login_required
def archive():
    q          = request.args.get('q', '').strip()
    f_category = request.args.get('category', '')
    f_team     = request.args.get('team', '')
    from_date  = request.args.get('from_date', '')
    to_date    = request.args.get('to_date', '')

    if current_user.is_admin:
        query = Ticket.query.filter(Ticket.status == 'closed')
    else:
        query = Ticket.query.filter(
            Ticket.status == 'closed',
            (Ticket.user_id == current_user.id) |
            (Ticket.assigned_to == current_user.id) |
            (Ticket.spocs.any(User.id == current_user.id))
        )
    if q:
        query = query.filter(Ticket.title.ilike(f'%{q}%') | Ticket.description.ilike(f'%{q}%'))
    if f_category:
        query = query.filter(Ticket.category_id == int(f_category))
    if f_team:
        query = query.join(User, Ticket.assigned_to == User.id).filter(User.team_id == int(f_team))
    if from_date:
        query = query.filter(Ticket.updated_at >= datetime.strptime(from_date, '%Y-%m-%d'))
    if to_date:
        query = query.filter(Ticket.updated_at <= datetime.strptime(to_date, '%Y-%m-%d'))

    tickets    = query.order_by(Ticket.updated_at.desc()).all()
    categories = Category.query.order_by(Category.name).all()
    teams      = Team.query.order_by(Team.name).all()
    return render_template('archive.html',
        tickets=tickets, categories=categories, teams=teams,
        q=q, f_category=f_category, f_team=f_team,
        from_date=from_date, to_date=to_date
    )
@app.route('/active')
@login_required
def active():
    q          = request.args.get('q', '').strip()
    f_status   = request.args.get('status', '')
    f_priority = request.args.get('priority', '')
    f_category = request.args.get('category', '')
    f_team     = request.args.get('team', '')
    sort       = request.args.get('sort', 'score')

    if current_user.is_admin:
        query = Ticket.query.filter(Ticket.status != 'closed')
    else:
        query = Ticket.query.filter(
            Ticket.status != 'closed',
            (Ticket.user_id == current_user.id) |
            (Ticket.assigned_to == current_user.id) |
            (Ticket.spocs.any(User.id == current_user.id))
        )

    if q:
        query = query.filter(Ticket.title.ilike(f'%{q}%') | Ticket.description.ilike(f'%{q}%'))
    if f_status:
        query = query.filter(Ticket.status == f_status)
    if f_priority:
        query = query.filter(Ticket.priority == f_priority)
    if f_category:
        query = query.filter(Ticket.category_id == int(f_category))
    if f_team:
        query = query.join(User, Ticket.assigned_to == User.id).filter(User.team_id == int(f_team))

    tickets    = query.all()
    categories = Category.query.order_by(Category.name).all()
    teams      = Team.query.order_by(Team.name).all()

    if sort == 'score':
        tickets = sorted(tickets, key=lambda t: t.weight_score, reverse=True)
    elif sort == 'due':
        tickets = sorted(tickets, key=lambda t: (t.due_date is None, t.due_date or date.today()))
    else:
        tickets = sorted(tickets, key=lambda t: t.created_at, reverse=True)

    return render_template('active.html',
        tickets=tickets, categories=categories, teams=teams,
        q=q, f_status=f_status, f_priority=f_priority,
        f_category=f_category, f_team=f_team, sort=sort
    )

@app.route('/profile')
@login_required
def profile():
    created  = Ticket.query.filter_by(user_id=current_user.id).order_by(Ticket.created_at.desc()).all()
    assigned = Ticket.query.filter_by(assigned_to=current_user.id).order_by(Ticket.created_at.desc()).all()
    spoc_on  = current_user.spoc_tickets
    stats = {
        'created':      len(created),
        'assigned':     len(assigned),
        'open':         sum(1 for t in assigned if t.status == 'open'),
        'in_progress':  sum(1 for t in assigned if t.status == 'in_progress'),
        'closed':       sum(1 for t in assigned if t.status == 'closed'),
        'overdue':      sum(1 for t in assigned if t.is_overdue),
        'total_weight': sum(t.weight_score for t in assigned if t.status != 'closed'),
    }
    return render_template('profile.html',
        created=created, assigned=assigned, spoc_on=spoc_on, stats=stats
    )

@app.route('/profile/update', methods=['POST'])
@login_required
def update_profile():
    name = request.form.get('name', '').strip()
    if name:
        current_user.name = name
        db.session.commit()
        flash('Profile updated.', 'success')
    return redirect(url_for('profile'))
# ─── Admin Panel ───────────────────────────────────────────────────────────────

@app.route('/admin')
@login_required
def admin_panel():
    if not current_user.is_admin:
        flash('Admin only.', 'error')
        return redirect(url_for('dashboard'))
    return render_template('admin.html',
        categories = Category.query.order_by(Category.name).all(),
        teams      = Team.query.order_by(Team.name).all(),
        users      = User.query.order_by(User.name).all(),
    )

@app.route('/admin/category/new', methods=['POST'])
@login_required
def new_category():
    if not current_user.is_admin: return redirect(url_for('dashboard'))
    name = request.form['name'].strip()
    if name and not Category.query.filter_by(name=name).first():
        db.session.add(Category(name=name))
        db.session.commit()
        flash(f'Category "{name}" created.', 'success')
    return redirect(url_for('admin_panel'))

@app.route('/admin/category/<int:cat_id>/delete', methods=['POST'])
@login_required
def delete_category(cat_id):
    if not current_user.is_admin: return redirect(url_for('dashboard'))
    cat = Category.query.get_or_404(cat_id)
    db.session.delete(cat)
    db.session.commit()
    flash('Category deleted.', 'success')
    return redirect(url_for('admin_panel'))

@app.route('/admin/user/<int:user_id>/team', methods=['POST'])
@login_required
def assign_team(user_id):
    if not current_user.is_admin: return redirect(url_for('dashboard'))
    user         = User.query.get_or_404(user_id)
    team_id      = request.form.get('team_id')
    user.team_id = int(team_id) if team_id else None
    db.session.commit()
    flash(f'{user.name} assigned to team.', 'success')
    return redirect(url_for('admin_panel'))

# ─── Init ──────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        for tname in ['Business Ops', 'Academic Operations', 'Growth']:
            if not Team.query.filter_by(name=tname).first():
                db.session.add(Team(name=tname))
        db.session.commit()
    app.run(host='0.0.0.0', port=10000, debug=False)