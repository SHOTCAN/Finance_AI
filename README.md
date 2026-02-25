# 🏦 Personal Finance AI Assistant

AI-powered personal finance manager via Telegram bot.

## Features
- 💰 Track income & expenses via chat
- 📸 OCR receipt scanning (Google Vision)
- 🧠 AI financial advisor (Groq LLM)
- 📊 Daily/weekly/monthly reports
- 📈 Cashflow forecasting & anomaly detection
- 🔐 Multi-user with admin OTP approval
- 📋 Google Sheets export

## Tech Stack
- **Backend**: FastAPI + PostgreSQL
- **AI**: Groq API (5-key rotation)
- **OCR**: Google Cloud Vision
- **Deploy**: Railway
- **Interface**: Telegram Bot

## Setup
1. Copy `.env.example` to `.env` and fill in your values
2. `pip install -r requirements.txt`
3. Deploy to Railway with PostgreSQL add-on
4. Set webhook: `POST /webhook/setup`

## Security
- All secrets in `.env` (never committed)
- JWT authentication with refresh tokens
- OTP-based user registration (admin approval)
- Row-level data isolation per user
- Audit logging on all critical actions
