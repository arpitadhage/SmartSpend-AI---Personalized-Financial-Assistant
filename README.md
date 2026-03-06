# SmartSpend AI  🧠

**AI-Powered Personal Finance Tracker** 

## Features
- 🧾 **Receipt OCR** — Tesseract OCR (runs locally, no API key needed!)
- 📱 **SMS Parsing** — Extract from any Indian bank SMS
- ⚠️ **Impulse Detection** — Late-night food, large buys, midnight shopping
- ❤️ **Financial Health Score** — 0-100 grade A-F
- 🔮 **AI Spending Forecast** — 3-month trend prediction
- ⚡ **Purchase Simulator** — Impact analysis before buying
- 🔥 **LeetCode Streaks** — 90-day heatmap, 50-day badge milestone
- 👥 **Splitwise** — Share expenses, track balances
- 🏆 **Gamification** — 12 badges, coins, leaderboard

## Quick Start

```bash
# Install system dependency (Ubuntu/Debian)
sudo apt-get install tesseract-ocr

# Install Python packages
pip install flask PyJWT Pillow pytesseract opencv-python-headless numpy

# Run
python app.py
```

Visit: http://localhost:5000



## Tech Stack
- **Backend**: Flask + SQLite (built-in, no extra DB setup)
- **Auth**: PyJWT (no Flask extensions needed)
- **OCR**: Tesseract + OpenCV + Pillow
- **Frontend**: Vanilla JS + Chart.js (no React/Vue needed)

## API Endpoints
| Endpoint | Method | Description |
|----------|--------|-------------|
| /api/register | POST | Register user |
| /api/login | POST | Login |
| /api/dashboard | GET | Full dashboard data |
| /api/expenses | GET | List expenses |
| /api/expenses/add | POST | Add expense (auto impulse detect) |
| /api/expenses/parse-sms | POST | Parse bank SMS |
| /api/expenses/ocr | POST | OCR receipt image |
| /api/expenses/analytics | GET | Month breakdown |
| /api/expenses/health-score | GET | 0-100 score |
| /api/expenses/predict | GET | Next month forecast |
| /api/expenses/simulate | POST | Purchase impact sim |
| /api/expenses/streaks | GET | Streak + heatmap |
| /api/goals/add | POST | Create goal |
| /api/goals/{id}/deposit | POST | Add savings |
| /api/splits/add | POST | Split expense |
| /api/splits/{id}/settle | PUT | Mark settled |
| /api/gamification/badges | GET | All 12 badges |
| /api/gamification/leaderboard | GET | Top 10 users |
