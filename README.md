# 🏔️ Trip OS: High-Availability Travel & Logistics System

> *A cloud-native, asynchronous backend engine built to orchestrate high-stakes group expeditions.*
> **Currently powering: Kedarnath 2026.**

[![FastAPI](https://img.shields.io/badge/FastAPI-005571?style=for-the-badge&logo=fastapi)](https://fastapi.tiangolo.com/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-316192?style=for-the-badge&logo=postgresql)](https://www.postgresql.org/)
[![Render](https://img.shields.io/badge/Render-46E3B7?style=for-the-badge&logo=render)](https://render.com/)
[![Sentry](https://img.shields.io/badge/Sentry-362D59?style=for-the-badge&logo=sentry)](https://sentry.io/)
[![Google Gemini](https://img.shields.io/badge/Google%20Gemini-8E75B2?style=for-the-badge&logo=googlegemini)](https://aistudio.google.com/)
[![Python](https://img.shields.io/badge/Python-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![Docker](https://img.shields.io/badge/Docker-2496ED?style=for-the-badge&logo=docker&logoColor=white)](https://www.docker.com/)
[![Supabase](https://img.shields.io/badge/Supabase-3ECF8E?style=for-the-badge&logo=supabase&logoColor=white)](https://supabase.com/)

---

## 📋 Table of Contents

- [Overview](#-overview)
- [System Architecture](#-system-architecture)
- [Core Modules](#-core-modules)
- [Tech Stack](#-tech-stack)
- [Environment Setup](#-environment-setup)
- [Database Schema](#-database-schema)
- [Deployment](#-deployment)
- [Author](#-author)

---

## 🌐 Overview

**Trip OS** is not a travel app. It's a **logistics command center** built to handle the operational complexity of coordinating a group expedition to one of India's most demanding pilgrimage routes — Kedarnath.

Every feature is engineered for real-world conditions: unreliable connectivity, dynamic group expenses, emergency safety protocols, and the cognitive overhead of managing 10+ people in altitude terrain.

**What it does:**
- 💸 Real-time group expense tracking & settlement with zero manual reconciliation
- 🎒 Interactive packing lists with per-member accountability states
- 🆘 One-command SOS emergency broadcast to all squad members with geolocation
- 🤖 Daily 7:00 AM automated briefings (weather, plan, reminders) via Telegram
- 📸 AI-powered image classification and caption generation via Gemini Vision
- 📊 Admin-only HTML dashboards and CSV export for full financial audit trails

---

## 🏗️ System Architecture

```mermaid
graph TD
    User((👤 Squad Member)) -->|Sends message / photo| TG[Telegram API]
    TG -->|Webhook POST| RL{🛡️ SlowAPI\nRate Limiter}
    RL -->|Validated Request| FW{🔐 Secret Token\nVerification}
    FW -->|Authorized| FA[⚡ FastAPI Server]

    subgraph RENDER ["🐳 Render — Docker Container"]
        FA -->|Route dispatch| H[Bot Handlers]
        H -->|Business rules| CL[Core Logic Layer]
        H -->|SQLAlchemy 2.0 Async| DB[AsyncPG Driver]
        SCHED[⏰ APScheduler] -->|7:00 AM IST Cron| TG
        SCHED --> CL
    end

    subgraph CLOUD ["☁️ External Cloud Services"]
        H -->|Vision inference| GEM[✨ Google Gemini 1.5 Flash]
        DB -->|Async writes/reads| SUP[(🗄️ Supabase\nPostgreSQL)]
        FA -->|Exceptions & traces| SEN[🔭 Sentry.io]
    end

    subgraph INFRA ["🔧 Dev Toolchain"]
        MIG[Alembic Migrations] --> SUP
        ENV[.env Secrets] --> FA
    end

    style FA fill:#009485,color:#fff,stroke:#009485
    style SUP fill:#3ecf8e,color:#000,stroke:#3ecf8e
    style SEN fill:#362D59,color:#fff,stroke:#362D59
    style TG fill:#24A1DE,color:#fff,stroke:#24A1DE
    style GEM fill:#8E75B2,color:#fff,stroke:#8E75B2
    style RENDER fill:#1a1a2e,color:#fff,stroke:#46E3B7,stroke-width:2px
    style CLOUD fill:#0f3460,color:#fff,stroke:#e94560,stroke-width:2px
    style INFRA fill:#16213e,color:#fff,stroke:#0f3460,stroke-width:2px
```

### 🛰️ Architectural Highlights

| Principle | Implementation |
|---|---|
| **Stateless Execution** | Containerized FastAPI node holds zero state — all persistence delegated to Supabase; Telegram acts as file CDN for receipts/photos |
| **Async Concurrency** | SQLAlchemy 2.0 async engine with `NullPool` — prevents I/O blocking and connection exhaustion on serverless DB tiers |
| **Schema Safety** | Database schema strictly version-controlled via **Alembic** migrations — zero manual schema edits in production |
| **Observability** | Sentry SDK intercepts unhandled exceptions, logs SQL query failures with full stack traces in real-time |
| **Security** | `/webhook` endpoint hardened with SlowAPI rate-limiting + cryptographic `X-Telegram-Bot-Api-Secret-Token` validation |
| **Reliability** | APScheduler survives container restarts; idempotent cron design prevents duplicate briefings |

---

## 🚀 Core Modules

### 💰 Finance — Multi-Tenant Expense Ledger

The finance module operates as a **multi-tenant ledger** — every transaction is attributed to a member, tagged with a category, and stored immutably. Real-time settlement computation determines who owes whom without any manual calculation.

- **Expense logging** via Telegram command (`/addexpense amount description @paid_by`)
- **Real-time settlement** — computed on-demand using a debt-simplification algorithm
- **HTML dashboard** — formatted, shareable summary rendered on command
- **Admin CSV export** — full audit trail available to admins only, rate-limited

### 🗺️ Logistics — Field Operations

The logistics module handles the operational reality of a trek: gear accountability and emergency response.

- **Interactive packing lists** — per-member item states (`packed` / `unpacked` / `not applicable`) toggled via inline Telegram keyboards
- **Geolocation tracking** — members share live location; system acknowledges receipt
- **SOS Emergency Broadcast** — single command triggers IST-timestamped emergency alert to all registered squad members with coordinates

### ⏰ Automation — Daily Squad Briefings

`APScheduler` fires a non-blocking async cron job at **7:00 AM IST** daily.

- Pulls day's agenda, weather context, and pending action items
- Composes and dispatches a formatted Telegram message to the group
- Fully idempotent — safe to restart container mid-day without double-sends

### 🤖 Vision AI — Gemini Flash Integration

Zero-shot image classification powered by **Google Gemini 1.5 Flash**.

- Members forward photos (gear, locations, food) to the bot
- Gemini returns a classification label + auto-generated creative caption
- Result posted back to the group in real-time

---

## 🛠️ Tech Stack

| Layer | Technology | Purpose |
|---|---|---|
| **API Framework** | FastAPI (async) | Webhook server, route handling |
| **Bot Interface** | python-telegram-bot | Telegram Bot API wrapper |
| **ORM** | SQLAlchemy 2.0 (async) | Database access layer |
| **DB Driver** | asyncpg + NullPool | Non-blocking PostgreSQL connection |
| **Database** | Supabase (PostgreSQL) | Persistent cloud storage |
| **Migrations** | Alembic | Schema version control |
| **Scheduler** | APScheduler | Cron automation |
| **AI Vision** | Google Gemini 1.5 Flash | Image classification & copy generation |
| **Observability** | Sentry SDK | Error tracking & alerting |
| **Rate Limiting** | SlowAPI | DDoS protection |
| **Containerization** | Docker | Reproducible build & deployment |
| **Hosting** | Render | Cloud container deployment |

---

## ⚙️ Environment Setup

Clone the repository and configure the following environment variables in a `.env` file:

```ini
# ── Core ──────────────────────────────────────────────
TELEGRAM_BOT_TOKEN="your_botfather_token"
DATABASE_URL="postgresql+asyncpg://user:password@host:6543/postgres"

# ── Security & Observability ──────────────────────────
WEBHOOK_URL="https://your-render-url.onrender.com"
WEBHOOK_SECRET="your_cryptographic_secret_string"
SENTRY_DSN="your_sentry_project_dsn"

# ── Optional AI ───────────────────────────────────────
GEMINI_API_KEY="your_google_ai_key"
```

### Running Locally

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Apply database migrations
alembic upgrade head

# 3. Start the server
uvicorn main:app --reload --port 8000
```

### Running with Docker

```bash
# Build the image
docker build -t trip-os .

# Run the container
docker run --env-file .env -p 8000:8000 trip-os
```

---

## 🗄️ Database Schema

Schema is managed via **Alembic migrations**. Never edit the database schema manually in production.

```bash
# Create a new migration
alembic revision --autogenerate -m "description_of_change"

# Apply all pending migrations
alembic upgrade head

# Rollback one migration
alembic downgrade -1
```

---

## 🚢 Deployment (Render)

1. Push code to GitHub
2. Connect repository to **Render** → select **Docker** as runtime
3. Set all environment variables in the Render dashboard (never commit `.env`)
4. Render auto-deploys on every `main` branch push
5. Register the Telegram webhook:

```bash
curl -X POST "https://api.telegram.org/bot<TOKEN>/setWebhook" \
  -d "url=https://your-render-url.onrender.com/webhook" \
  -d "secret_token=your_cryptographic_secret_string"
```

---

## 👨‍💻 Author

**Aditya** — Python Automation & AI engineer
