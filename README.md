# Lunara Bot – Modular AI-Powered Crypto Trading Assistant

Lunara Bot is a secure, modular, and intelligent crypto trading assistant for Binance Spot markets, powered by Python and Telegram Bot API. It features robust admin controls, subscription management, encrypted API key storage, and multi-AI support (Gemini, Mistral).

---

## 🚀 Key Features
- **Telegram Bot Interface:** Trade insights, admin commands, and user management
- **Encrypted API Key Storage:** Securely store user API keys using Fernet encryption
- **Admin & Subscription Management:** Activate users, manage tiers, and control access
- **Multi-AI Integration:** Gemini and Mistral AI for trade supervision and signals
- **Automated Trading:** Rule-driven signals, risk controls, and real-time execution
- **SQLite Database:** Local, reliable user and trade management
- **Error Handling:** Robust error and exception management for reliability

---

## 🛡️ Security & Setup
- All sensitive keys are encrypted and never exposed in logs
- Admin commands (`/activate`, `/setapi`) for secure user management
- Store your bot token, API keys, and secrets in `.env` or environment variables
- Only admins can activate premium features and manage subscriptions

---

## 🛠️ Installation
1. Clone the repo:
   ```bash
   git clone https://github.com/Drknessheo/lunara-bot.git
   cd lunara-bot
   ```
2. Setup virtual environment:
   ```bash
   python -m venv venv
   venv\Scripts\activate  # On Windows
   source venv/bin/activate  # On Linux/Mac
   ```
3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
4. Add your credentials to `.env` or `config.py`:
   - `BOT_TOKEN`, `BINANCE_API_KEY`, `BINANCE_SECRET_KEY`, `ADMIN_USER_ID`, `TELEGRAM_CREATOR_ID`, `BINANCE_ENCRYPTION_KEY`, etc.

---

## 🧪 Running the Bot
```bash
python main.py
```

---

## 📂 Folder Structure
```
lunara-bot/
├── main.py               # Entry point for the bot
├── modules/              # Core modules (db_access, slip_manager, etc.)
├── config.py             # Configuration and environment variables
├── requirements.txt      # Python dependencies
└── README.md             # Project documentation
```

---

## 📖 Main Commands
- `/help` – Show usage and features
- `/status` – Show wallet and trade status
- `/activate` – Admin: Activate user and manage subscription
- `/setapi` – Securely store user API keys (admin only)
- `/import` – Import trades manually
- `/about` – Learn more about Lunara

---

## ⚖️ License
This project is licensed under the MIT License. See [`LICENSE`](LICENSE) for details.

---

## 🙌 Author
**Shamim Reza Saikat**
Telegram: [@Drknessheo](https://t.me/drknessheo)
Email: [s_r_saikat@yahoo.com](mailto:s_r_saikat@yahoo.com)
ORCID: [0009-0008-3119-166X](https://orcid.org/0009-0008-3119-166X)

---

## 🌠 Vision
Lunara is your AI-powered crypto trading companion, harmonizing intention, signal, and market flow for disciplined, secure, and scalable trading.

---

## Webhook Retry System

Failed promotion webhooks are automatically enqueued and retried with exponential backoff. This helps ensure promotions are delivered reliably even when receivers are temporarily unavailable.

Admin commands:
- `/retry_queue` — list pending retries
- `/retry_dispatch <index>` — manually retry one
- `/retry_flush confirm` — clear the queue
- `/retry_stats` — show retry metrics

Redis keys used:
- `promotion_webhook_retry` — pending items
- `promotion_webhook_failed` — permanently failed
- `promotion_log` — successful dispatches

Usage:
Send `/retry_stats` in any admin-approved thread or DM to get a quick pulse on retry health.

### Redis Metrics (promotion_webhook_stats)

Stored in Redis hash `promotion_webhook_stats`:

- `pending`: Number of items currently in the retry queue
- `failed`: Total number of failed dispatches moved to failed list
- `total_sent`: Total successful dispatches (via retry)
- `last_failed_ts`: ISO timestamp of the most recent failure

View manually:
```bash
redis-cli HGETALL promotion_webhook_stats
```

Or use `/retry_stats` to view in bot output.

---

## Binance timeout & retry behavior

Short summary: network calls to Binance are configured with a 10s timeout and
use a 3-attempt exponential backoff for read timeouts and connection errors.
On repeated failures the code returns empty results and logs a clear message to
help tracing (see `docs/binance-timeout.md` for full details).
