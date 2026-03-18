from pydantic_settings import BaseSettings
from typing import Optional
import os

class Settings(BaseSettings):
    # Application settings
    APP_NAME: str = "Spinr API"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False
    
    # Database settings
    SUPABASE_URL: str
    SUPABASE_SERVICE_ROLE_KEY: str
    USE_SUPABASE: bool = True  # Supabase is now the default database
    
    # Firebase settings
    FIREBASE_SERVICE_ACCOUNT_JSON: Optional[str] = None
    
    # Security settings
    JWT_SECRET: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    
    # CORS settings
    ALLOWED_ORIGINS: str = "http://localhost:3000,http://localhost:3001"
    
    # Rate limiting
    RATE_LIMIT: str = "10/minute"
    
    # File storage
    STORAGE_BUCKET: str = "driver-documents"
    
    # Environment
    ENV: str = "development"
    
    class Config:
        # Look for .env in backend directory, or current directory
        env_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env')
        env_file_encoding = 'utf-8'
        
        # Map environment variables to field names
        fields = {
            'JWT_SECRET': 'secret_key',
            'ENV': 'debug'
        }

    @property
    def SECRET_KEY(self) -> str:
        return self.JWT_SECRET
        
    @property
    def debug(self) -> bool:
        return self.ENV.lower() == 'development'

settings = Settings()
