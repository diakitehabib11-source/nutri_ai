import os
from dotenv import load_dotenv

def load_env_vars():
    load_dotenv()
    return {
        "HEYGEN_API_KEY": os.getenv("HEYGEN_API_KEY")
    }
