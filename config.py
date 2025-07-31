"""
Centralized configuration for the Room Allocator application.
All configurable values should be defined here.
"""
import os
import streamlit as st
from datetime import date
import pytz


class Config:
    """Application configuration class."""
    
    def __init__(self):
        self._database_url = None
        self._admin_password = None
        self._office_timezone_str = None
    
    # Database Configuration
    @property
    def DATABASE_URL(self):
        if self._database_url is None:
            self._database_url = st.secrets.get("SUPABASE_DB_URI", os.environ.get("SUPABASE_DB_URI"))
        return self._database_url
    
    DB_POOL_MIN_CONN = 1
    DB_POOL_MAX_CONN = 25
    
    # Security Configuration
    @property
    def ADMIN_PASSWORD(self):
        if self._admin_password is None:
            self._admin_password = st.secrets.get("ADMIN_PASSWORD", os.environ.get("ADMIN_PASSWORD", "boom123"))
        return self._admin_password
    
    # Timezone Configuration
    @property
    def OFFICE_TIMEZONE_STR(self):
        if self._office_timezone_str is None:
            self._office_timezone_str = st.secrets.get("OFFICE_TIMEZONE", os.environ.get("OFFICE_TIMEZONE", "Europe/Amsterdam"))
        return self._office_timezone_str
    
    # Application Configuration
    PAGE_TITLE = "Weekly Room Allocator - TS"
    PAGE_LAYOUT = "wide"
    
    # Room Configuration
    ROOMS_FILE = "rooms.json"
    OASIS_DEFAULT_CAPACITY = 12
    
    # Team Size Constraints
    MIN_TEAM_SIZE = 3
    MAX_TEAM_SIZE = 6
    
    # Oasis Preferences Constraints
    MIN_OASIS_DAYS = 1
    MAX_OASIS_DAYS = 5
    
    # Static Date Configuration (can be moved to database in future)
    STATIC_PROJECT_MONDAY = date(2024, 5, 27)
    STATIC_OASIS_MONDAY = date(2024, 5, 27)
    
    # Cache Settings
    ADMIN_SETTINGS_CACHE_TTL = 60  # seconds
    
    # Validation Patterns
    VALID_DAY_PAIRS = [
        {"Monday", "Wednesday"},
        {"Tuesday", "Thursday"}
    ]
    
    WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
    
    # Default Display Texts
    DEFAULT_SETTINGS = {
        'submission_week_of_text': '3 June',
        'submission_start_text': 'Wednesday 5 June 09:00',
        'submission_end_text': 'Thursday 6 June 16:00',
        'oasis_end_text': 'Friday 7 June 16:00',
        'project_allocations_display_markdown_content': 'Displaying project rooms for the week of 27 May 2024.',
        'oasis_allocations_display_markdown_content': 'Displaying Oasis for the week of 27 May 2024.'
    }
    
    def get_office_timezone(self):
        """Get the office timezone object with error handling."""
        try:
            return pytz.timezone(self.OFFICE_TIMEZONE_STR)
        except pytz.UnknownTimeZoneError:
            st.error(f"Invalid Timezone: '{self.OFFICE_TIMEZONE_STR}', defaulting to UTC.")
            return pytz.utc
    
    def validate_config(self):
        """Validate critical configuration values."""
        errors = []
        
        if not self.DATABASE_URL:
            errors.append("Database URL is not configured. Please set SUPABASE_DB_URI.")
        
        if not self.ADMIN_PASSWORD:
            errors.append("Admin password is not configured.")
        
        # Validate timezone
        try:
            pytz.timezone(self.OFFICE_TIMEZONE_STR)
        except pytz.UnknownTimeZoneError:
            errors.append(f"Invalid timezone: {self.OFFICE_TIMEZONE_STR}")
        
        return errors


# Create a global config instance
config = Config()