import random
import string

def generate_referral_code(length: int = 8) -> str:
    """Generates a random uppercase alphanumeric referral code."""
    characters = string.ascii_uppercase + string.digits
    return ''.join(random.choice(characters) for _ in range(length))