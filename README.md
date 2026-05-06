# DeskFlow 🎫

A lightweight ticket management system built with Python + Flask.

## Tech stack
- **Backend**: Python / Flask
- **Database**: SQLite (via SQLAlchemy)
- **Auth**: Flask-Login + Werkzeug password hashing
- **Frontend**: Jinja2 templates + vanilla CSS

## Quickstart

```bash
# 1. Clone / download the project
cd deskflow

# 2. Create a virtual environment
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Run the app
python app.py
```

Visit **http://localhost:5000** in your browser.

> The first user to register automatically becomes the admin.

## Features
- User registration & login
- Submit tickets with title, description, priority
- Admin dashboard with ticket stats
- Update ticket status (Open → In Progress → Closed)
- Role-based access (users see only their tickets; admins see all)

## Week plan
| Day | Goal |
|-----|------|
| 1 | Get it running locally, explore the code |
| 2 | Add a comments section to tickets |
| 3 | Add ticket search / filter by status |
| 4 | Add user profile page |
| 5 | Polish UI, write README |
| 6 | Deploy to Render.com |
| 7 | Push to GitHub, add to resume |

## Deploying to Render (free)
1. Push this repo to GitHub
2. Go to render.com → New Web Service → connect your repo
3. Build command: `pip install -r requirements.txt`
4. Start command: `python app.py`
5. Done — Render gives you a free `.onrender.com` URL
