# 🛠️ Roji Roti (रोजी रोटी) - Backend API

![Python Version](https://img.shields.io/badge/python-3.10%2B-blue.svg)
![FastAPI](https://img.shields.io/badge/FastAPI-0.100%2B-009688.svg)
![MongoDB](https://img.shields.io/badge/MongoDB_Atlas-Cloud-47A248.svg)
![License](https://img.shields.io/badge/license-MIT-green.svg)

**Roji Roti** is a localized, high-performance platform built to bridge the gap between blue-collar workers (carpenters, plumbers, electricians, drivers, masons) and employers in India. 

This repository houses the **FastAPI backend**, engineered for high concurrency, real-time data processing, and seamless third-party integrations (Razorpay, SMS/OTP, PDF generation).

---

## ✨ Core Features

### 👷 For Workers (Employees)
* **Seamless Authentication:** Intelligent login flow that automatically redirects unregistered users to the signup page. Supports both OTP and password-based login.
* **Smart Job Discovery:** Geolocation-based job feeds and personalized recommendations based on the worker's trade category (e.g., automatically showing Carpenter jobs to Carpenters).
* **One-Click Applications:** Apply to jobs instantly. The system tracks application states (Pending, Shortlisted, Hired, Rejected).
* **Automated CV Generation:** Generates and serves professional PDF resumes dynamically using ReportLab based on the worker's profile data.
* **Dashboard Analytics:** Real-time stats showing workers how many employers have viewed or "unlocked" their profile.

### 🏢 For Employers
* **Dual Entity Registration:** Support for both Individual homeowners and Corporate companies.
* **Job Lifecycle Management:** Post jobs, review applicants, update candidate statuses, and automatically close jobs when a candidate is hired.
* **Worker Database Search:** Browse available workers by category and location. 
* **Contact Unlocking & Subscriptions:** Premium feature to unlock worker contact details, powered by a Razorpay subscription model.
* **Automated PDF Receipts:** Generates downloadable transaction receipts immediately after successful Razorpay payments.

### ⚙️ System Mechanics
* **Self-Healing Database:** Automatically seeds essential database categories (Trades) upon server startup if the collection is empty.
* **Automated Notifications:** Triggers in-app alerts when application statuses change.
* **Rate Limiting:** Protects OTP and Login routes from brute-force attacks using SlowAPI.

---

## 🏗️ Architecture & Tech Stack

* **Web Framework:** [FastAPI](https://fastapi.tiangolo.com/) (Asynchronous, highly performant)
* **Database:** [MongoDB Atlas](https://www.mongodb.com/atlas) (Cloud NoSQL)
* **ODM (Object Document Mapper):** [Beanie](https://beanie-odm.dev/) (Motor-based async ODM)
* **Authentication:** JWT (JSON Web Tokens) with custom middleware
* **Payment Gateway:** Razorpay API
* **Document Generation:** ReportLab (Python PDF library)
* **Server:** Uvicorn (ASGI)

---

## 📡 Quick API Reference

The API is fully documented via Swagger UI (`/docs`). Here are a few key routes:

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `POST` | `/api/v1/auth/login` | Unified login (Seamless worker routing) |
| `POST` | `/api/v1/auth/request-otp` | Request SMS/Email OTP |
| `GET` | `/api/v1/employees/jobs` | Get targeted job feed for worker |
| `POST` | `/api/v1/employers/jobs` | Post a new job (Employer) |
| `PATCH`| `/api/v1/employers/applications/{id}/status` | Hire/Reject an applicant |
| `POST` | `/api/v1/payments/create-order` | Initialize Razorpay transaction |
| `GET` | `/api/v1/payments/transactions/{id}/receipt`| Download PDF payment receipt |

---

## 🚀 Getting Started

### 1. Prerequisites
* Python 3.10+
* A [MongoDB Atlas](https://www.mongodb.com/cloud/atlas) account (Free Tier works perfectly)
* Razorpay Test API Keys

### 2. Environment Variables
Create a `.env` file in the root of your project:

```env
# Database (Ensure your IP is whitelisted in Atlas)
MONGODB_URL="mongodb+srv://<username>:<password>@cluster0.xxxxx.mongodb.net/roji_roti_db?retryWrites=true&w=majority"
DATABASE_NAME="roji_roti_db"

# Security
SECRET_KEY="generate_a_random_secure_string_here"
ALGORITHM="HS256"
ACCESS_TOKEN_EXPIRE_MINUTES=1440

# Third-Party Integrations
RAZORPAY_KEY_ID="rzp_test_yourkeyhere"
RAZORPAY_KEY_SECRET="your_razorpay_secret"