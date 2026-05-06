# 🛠️ Roji Roti (रोजी रोटी) - Advanced Backend API

![Python Version](https://img.shields.io/badge/python-3.10%2B-blue.svg)
![FastAPI](https://img.shields.io/badge/FastAPI-0.100%2B-009688.svg)
![MongoDB](https://img.shields.io/badge/MongoDB_Atlas-Cloud-47A248.svg)
![Authentication](https://img.shields.io/badge/Auth-JWT%20%7C%20OTP-critical)

**Roji Roti** is a localized, high-performance platform built to bridge the gap between blue-collar workers (carpenters, plumbers, electricians, drivers, masons) and employers in India. 

This repository houses the **FastAPI backend**, engineered for high concurrency, real-time data processing, and seamless third-party integrations (Razorpay, SMS/OTP, PDF generation).

---

## ✨ Comprehensive Feature Breakdown

### 👷 For Workers (Employees)
* **Seamless Authentication Flow:** Intelligent login system that detects unregistered numbers and securely redirects them to the signup page. Supports 6-digit OTP and encrypted password login.
* **Geospatial Job Discovery:** Utilizes MongoDB geospatial queries to fetch jobs within a specific radius of the worker's current location.
* **Smart Recommendations:** Algorithmically suggests jobs based on the worker's selected trade category and daily rate expectations.
* **One-Click Applications:** Apply to jobs instantly. The system prevents duplicate applications and tracks states (Pending, Shortlisted, Hired, Rejected).
* **Automated CV Generation:** Dynamically generates professional PDF resumes using `ReportLab`, pulling real-time profile data, ratings, and completed job history.
* **Dashboard Analytics:** Real-time stats showing workers how many employers have viewed or "unlocked" their profile.
* **Availability Toggle:** Workers can easily toggle their "Available for Work" status, instantly updating their visibility in employer search results.

### 🏢 For Employers
* **Dual Entity Registration:** Distinct data models supporting both Individual homeowners and Corporate construction/logistics companies.
* **Job Lifecycle Management:** Post jobs with exact salary brackets, review applicants, update candidate statuses, and automatically close jobs when a candidate is successfully hired.
* **Worker Database Search:** Granular search queries allowing employers to filter the worker database by category, location, and minimum star rating.
* **Contact Unlocking (Premium):** A monetization feature where employers pay to unlock a worker's direct phone number.
* **Automated PDF Receipts:** Automatically generates downloadable transaction receipts immediately after a successful Razorpay payment.
* **Worker Rating System:** Leave a 1 to 5-star rating and written review for workers upon job completion, updating the worker's global average.

### 🛡️ Core System & Security Mechanics
* **Self-Healing Database:** Automatically seeds essential database categories (Trades) upon server startup if the collection is empty.
* **Rate Limiting:** Protects OTP request routes and login endpoints from brute-force and spam attacks using `SlowAPI` (e.g., max 3 OTP requests per minute).
* **Role-Based Access Control (RBAC):** Middleware that strictly ensures Employees cannot access Employer-only endpoints, and vice versa.
* **Password Cryptography:** All passwords and static OTPs are hashed using `Passlib` (Bcrypt) before entering the database.

---

## 🏗️ Architecture & Tech Stack

* **Web Framework:** [FastAPI](https://fastapi.tiangolo.com/) (Asynchronous, highly performant ASGI framework)
* **Database:** [MongoDB Atlas](https://www.mongodb.com/atlas) (Cloud NoSQL)
* **ODM (Object Document Mapper):** [Beanie](https://beanie-odm.dev/) (Motor-based async ODM for Python)
* **Authentication:** JWT (JSON Web Tokens) with standard Bearer flow
* **Payment Gateway:** Razorpay API (Orders & Signature Verification)
* **Document Generation:** ReportLab & BytesIO for in-memory PDF creation
* **Server:** Uvicorn

---

## 🚀 Step-by-Step Setup Guide

### Step 1: System Prerequisites
Ensure you have the following installed on your machine:
* Python 3.10 or higher
* Git
* A [MongoDB Atlas](https://www.mongodb.com/cloud/atlas) account (Free M0 Tier is sufficient)
* A [Razorpay Test Account](https://razorpay.com/) for payment integration

### Step 2: Clone the Repository
```bash
git clone [https://github.com/yourusername/roji-roti-backend.git](https://github.com/yourusername/roji-roti-backend.git)
cd roji-roti-backend
```

### Step 3: Set up the Virtual Environment
It is highly recommended to isolate your Python dependencies.
```bash
# Create the environment
python -m venv venv

# Activate on Windows:
venv\Scripts\activate
# Activate on macOS/Linux:
source venv/bin/activate
```

### Step 4: Install Dependencies
```bash
pip install -r requirements.txt
```

### Step 5: Configure Environment Variables
Create a `.env` file in the root directory. You must fill in these values for the app to start:

```env
# --- DATABASE ---
# Ensure your current IP is whitelisted in MongoDB Atlas Network Access!
MONGODB_URL="mongodb+srv://<db_username>:<db_password>@cluster0.xxxxx.mongodb.net/roji_roti_db?retryWrites=true&w=majority"
DATABASE_NAME="roji_roti_db"

# --- SECURITY ---
# Generate a secure key using: openssl rand -hex 32
SECRET_KEY="your_generated_secure_string_here"
ALGORITHM="HS256"
ACCESS_TOKEN_EXPIRE_MINUTES=1440 # 1 Day expiration

# --- PAYMENTS (Razorpay) ---
# Find these in Razorpay Dashboard -> Settings -> API Keys
RAZORPAY_KEY_ID="rzp_test_your_key_here"
RAZORPAY_KEY_SECRET="your_razorpay_secret_here"
```

### Step 6: Run the Server
Launch the application using Uvicorn. The `--reload` flag ensures the server restarts automatically when you save code changes.
```bash
uvicorn app.main:app --reload
```

---

## 📡 API Testing & Documentation

FastAPI automatically generates interactive documentation based on the OpenAPI standard.

1. **Open the UI:** Navigate to `http://127.0.0.1:8000/docs` in your browser.
2. **Authorize (Login):** * Scroll down to the `/api/v1/auth/login` endpoint.
   * Enter test credentials and hit execute.
   * Copy the `access_token` from the response body.
   * Scroll to the very top of the page, click the green **"Authorize"** button, and paste the token. All locked endpoints are now available to test!

### 📌 Core API Endpoints

| Category | Method | Endpoint | Description |
| :--- | :--- | :--- | :--- |
| **Auth** | `POST` | `/api/v1/auth/login` | Unified login (Handles unregistered routing) |
| **Auth** | `POST` | `/api/v1/auth/request-otp` | Request SMS/Email OTP (Rate Limited) |
| **Worker** | `GET` | `/api/v1/employees/jobs` | Get targeted job feed for worker |
| **Worker** | `GET` | `/api/v1/employees/me/resume` | Download dynamic PDF CV |
| **Employer** | `POST` | `/api/v1/employers/jobs` | Post a new local job |
| **Employer** | `PATCH`| `/api/v1/employers/applications/{id}/status` | Hire, Reject, or Shortlist an applicant |
| **Payments** | `POST` | `/api/v1/payments/create-order` | Initialize Razorpay transaction |
| **Payments** | `GET` | `/api/v1/payments/transactions/{id}/receipt`| Download PDF payment receipt |

---

## 📂 Project Structure

```text
app/
├── api/
│   ├── dependencies.py       # Reusable Depends() (Auth decoding, Current User logic)
│   └── v1/
│       ├── api.py            # Master router aggregating all sub-routers to avoid duplicates
│       └── endpoints/        # Domain-specific logic (auth, jobs, employees, employers, payments)
├── core/
│   ├── config.py             # Pydantic BaseSettings for robust .env validation
│   ├── database.py           # MongoDB connection & Beanie initialization/seeding
│   ├── security.py           # Password hashing & JWT generation functions
│   └── limiter.py            # SlowAPI rate limiting configuration
├── models/                   # Beanie Database Schemas (Job, Employee, Transaction, etc.)
├── schemas/                  # Pydantic validation models for strict Request/Response formatting
├── services/                 # External business logic (Receipt PDFs, Resume PDFs, SMS Mocking)
└── main.py                   # FastAPI application instance, CORS, & Lifespan events
```

---

## 🌍 Deployment Guide (Production)

To deploy this backend to a live server (like Render, Heroku, or AWS):

1. **Remove the `--reload` flag:** Do not use reload in production.
2. **Update CORS:** In `main.py`, change `allow_origins=["*"]` to your actual frontend domain (e.g., `https://rojiroti.com`).
3. **Whitelist IPs:** Ensure your cloud provider's IP address is whitelisted in your MongoDB Atlas settings.
4. **Start Command:** Use Gunicorn with Uvicorn workers for production stability:
   ```bash
   gunicorn app.main:app -w 4 -k uvicorn.workers.UvicornWorker
   ```

---

## 🤝 Future Roadmap
- [ ] **Mapping Integration:** Integrate local Indian mapping APIs (Mappls/Ola Maps) for precise routing and address auto-completion.
- [ ] **WhatsApp Bot:** Implement WhatsApp Business API integration for offline job notifications and basic application commands via text.
- [ ] **In-App Chat:** Build WebSocket endpoints for real-time messaging between employers and shortlisted workers.

---

*Architected and built with ❤️ in Indore, Madhya Pradesh, India.*