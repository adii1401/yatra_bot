---
title: Trip OS Master Node
emoji: 🏔️
colorFrom: blue
colorTo: indigo
sdk: docker
app_port: 7860
pinned: false
---

# 🏔️ Trip OS: High-Availability Travel & Logistics System

A cloud-native, asynchronous backend engine built to manage high-stakes group expeditions (Kedarnath 2026). Featuring real-time expense reconciliation, safety protocols, and Generative AI computer vision for social media automation.

[![FastAPI](https://img.shields.io/badge/FastAPI-005571?style=for-the-badge&logo=fastapi)](https://fastapi.tiangolo.com/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-316192?style=for-the-badge&logo=postgresql)](https://www.postgresql.org/)
[![Docker](https://img.shields.io/badge/Docker-2496ED?style=for-the-badge&logo=docker)](https://www.docker.com/)
[![Google Gemini](https://img.shields.io/badge/Google%20Gemini-8E75B2?style=for-the-badge&logo=googlegemini)](https://aistudio.google.com/)

---

## 🏗️ System Architecture

The following diagram illustrates the high-level architecture and data flow between the Telegram Bot API, the containerized FastAPI engine, and the persistent cloud layers.

```mermaid
graph TD
    User((Squad Member)) -->|Sends message/photo| Telegram[Telegram API]
    Telegram -->|Webhook| FastAPI[FastAPI Server]

    subgraph "Hugging Face Space (Docker Container)"
        FastAPI -->|Routes request| Handlers[Bot Handlers]
        Handlers -->|Logistics & Vault| CoreLogic[Core Logic]
        Handlers -->|SQLAlchemy| DB_Driver[AsyncPG Driver]
    end

    subgraph "External Cloud Services"
        Handlers -->|Sends photo| Gemini[Google Gemini 1.5 Flash]
        Gemini -->|Returns Captions| Handlers
        DB_Driver -->|Saves data securely| Supabase[(Supabase PostgreSQL Database)]
    end

    style FastAPI fill:#009485,color:#fff,stroke:#333,stroke-width:2px
    style Supabase fill:#3ecf8e,color:#000,stroke:#333,stroke-width:2px
    style Gemini fill:#8e75b2,color:#fff,stroke:#333,stroke-width:2px
    style Telegram fill:#24A1DE,color:#fff,stroke:#333,stroke-width:2px
```

---

## 🛰️ Architectural Highlights

- **Stateless Execution:** The containerized FastAPI node remains stateless, delegating all persistence to Supabase.
- **Async Concurrency:** Built with SQLAlchemy 2.0 async engine to prevent I/O blocking during high-traffic expense logging.
- **Fault Tolerance:** Implements `pool_pre_ping` to ensure database connection resilience in low-bandwidth mountain environments.

---

## 🚀 Core Modules

| Module | Technical Implementation |
|---|---|
| **Finance** | Multi-tenant ledger with real-time settlement logic and an HTML Dashboard |
| **Logistics** | Geolocation tracking and SOS emergency broadcast system |
| **Vision AI** | Zero-shot image classification and creative copy generation using Gemini 1.5 Flash |

---

## 🛠️ Environment Setup

To run this project, configure the following environment variables:

```ini
TELEGRAM_BOT_TOKEN="your_botfather_token"
DATABASE_URL="postgresql+asyncpg://user:password@host:5432/postgres"
GEMINI_API_KEY="your_google_ai_key"
WEBHOOK_URL="your_deployment_url"
```

---

## 👨‍💻 Author

**Aditya** — [github.com/adii1401](https://github.com/adii1401)