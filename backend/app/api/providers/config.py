"""
AI Provider Configuration
Manages API keys and model configurations securely via environment variables
"""
import os
from typing import Dict, List

class AIConfig:
    """Centralized AI configuration management"""
    
    # API Provider Configuration
    API_KEY = os.getenv("OPENROUTER_API_KEY", "")
    PROVIDER = os.getenv("AI_PROVIDER", "openrouter")
    
    # Default Model
    MODEL = os.getenv("DEFAULT_MODEL", "openai/gpt-oss-120b:free")
    
    # Available Models
    AVAILABLE_MODELS: List[str] = [
        "openai/gpt-oss-120b:free",
        "meta-llama/llama-3.3-70b-instruct:free",
        "google/gemma-4-31b-it:free",
        "qwen/qwen3-next-80b-a3b-instruct:free",
        "minimax/minimax-m2.5:free",
        "nvidia/nemotron-3-super-120b-a12b:free",
        "openai/gpt-oss-20b:free",
    ]
    
    # API Endpoints
    ENDPOINTS: Dict[str, str] = {
        "chat": "https://openrouter.ai/api/v1/chat/completions",
        "models": "https://openrouter.ai/api/v1/models",
    }
    
    # HTTP Headers for OpenRouter
    HEADERS: Dict[str, str] = {
        "HTTP-Referer": os.getenv("HTTP_REFERER", ""),
        "X-Title": os.getenv("X_TITLE", "AutoFill AI System"),
        "Authorization": f"Bearer {API_KEY}",
    }
    
    # Request Configuration
    TIMEOUT = int(os.getenv("AI_REQUEST_TIMEOUT", "30"))
    MAX_RETRIES = int(os.getenv("AI_MAX_RETRIES", "3"))
    
    @classmethod
    def validate(cls) -> bool:
        """Validate that required configuration is present"""
        if not cls.API_KEY:
            raise ValueError("OPENROUTER_API_KEY environment variable is not set")
        return True


class ModelConfig:
    """Model-specific configurations"""
    
    # Thinking model (more powerful, slower)
    THINKING_MODEL = os.getenv("THINKING_MODEL", "openai/gpt-4o")
    
    # Suggestions model (faster, lighter)
    SUGGESTIONS_MODEL = os.getenv("SUGGESTIONS_MODEL", "openai/gpt-4o-mini")
    
    # Temperature settings
    TEMPERATURES = {
        "thinking": 0.7,      # More creative for analysis
        "suggestions": 0.5,   # Balanced for suggestions
        "creative": 0.9,      # Creative writing
        "precise": 0.2,       # Precise/deterministic
    }
    
    # Token limits
    MAX_TOKENS = {
        "thinking": 2000,
        "suggestions": 1500,
        "default": 1000,
    }
