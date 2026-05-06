TRANSLATIONS = {
    "en": {
        "job_posted": "Job posted successfully!",
        "insufficient_funds": "Insufficient contact credits.",
        "otp_sent": "OTP sent successfully.",
        "user_not_found": "User not registered."
    },
    "hi": {
        "job_posted": "नौकरी सफलतापूर्वक पोस्ट की गई!",
        "insufficient_funds": "संपर्क क्रेडिट अपर्याप्त हैं।",
        "otp_sent": "ओटीपी सफलतापूर्वक भेजा गया।",
        "user_not_found": "उपयोगकर्ता पंजीकृत नहीं है।"
    }
}

def get_text(key: str, lang: str = "en") -> str:
    """Helper to fetch translated text based on language code."""
    return TRANSLATIONS.get(lang, TRANSLATIONS["en"]).get(key, key)