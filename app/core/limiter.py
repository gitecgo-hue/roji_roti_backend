from slowapi import Limiter
from slowapi.util import get_remote_address

# Initialize the limiter here in neutral territory
limiter = Limiter(key_func=get_remote_address)