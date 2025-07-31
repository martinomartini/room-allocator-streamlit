"""
Database utilities and connection management for the Room Allocator application.
"""
import streamlit as st
import psycopg2
import psycopg2.pool
import pandas as pd
from psycopg2.extras import RealDictCursor
from datetime import datetime, date, timedelta
from typing import Optional, Dict, List, Tuple, Any
import pytz

from config import config


class DatabaseManager:
    """Manages database connections and common operations."""
    
    def __init__(self):
        self._pool = None
    
    @st.cache_resource
    def get_connection_pool(_self):
        """Get database connection pool with caching."""
        if not config.DATABASE_URL:
            st.error("Database URL is not configured. Please set SUPABASE_DB_URI.")
            return None
        
        try:
            return psycopg2.pool.SimpleConnectionPool(
                config.DB_POOL_MIN_CONN, 
                config.DB_POOL_MAX_CONN, 
                dsn=config.DATABASE_URL
            )
        except Exception as e:
            st.error(f"Failed to create database connection pool: {e}")
            return None
    
    def get_connection(self):
        """Get a connection from the pool."""
        if not self._pool:
            self._pool = self.get_connection_pool()
        
        if self._pool:
            return self._pool.getconn()
        return None
    
    def return_connection(self, conn):
        """Return a connection to the pool."""
        if self._pool and conn:
            self._pool.putconn(conn)
    
    def execute_query(self, query: str, params: Tuple = None, fetch_one: bool = False, fetch_all: bool = True) -> Any:
        """Execute a query with proper connection management."""
        conn = self.get_connection()
        if not conn:
            return None
        
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(query, params)
                
                if fetch_one:
                    return cur.fetchone()
                elif fetch_all:
                    return cur.fetchall()
                else:
                    conn.commit()
                    return cur.rowcount
                    
        except Exception as e:
            conn.rollback()
            st.error(f"Database query failed: {e}")
            return None
        finally:
            self.return_connection(conn)
    
    def execute_transaction(self, queries: List[Tuple[str, Tuple]]) -> bool:
        """Execute multiple queries in a transaction."""
        conn = self.get_connection()
        if not conn:
            return False
        
        try:
            with conn.cursor() as cur:
                for query, params in queries:
                    cur.execute(query, params)
                conn.commit()
                return True
        except Exception as e:
            conn.rollback()
            st.error(f"Transaction failed: {e}")
            return False
        finally:
            self.return_connection(conn)


class AdminSettingsManager:
    """Manages admin settings stored in the database."""
    
    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager
        self._initialize_table()
    
    def _initialize_table(self):
        """Create admin_settings table if it doesn't exist."""
        query = """
            CREATE TABLE IF NOT EXISTS admin_settings (
                id SERIAL PRIMARY KEY,
                setting_key VARCHAR(255) UNIQUE NOT NULL,
                setting_value TEXT,
                updated_at TIMESTAMP DEFAULT NOW()
            )
        """
        self.db.execute_query(query, fetch_all=False)
    
    def get_setting(self, key: str, default_value: str = "") -> str:
        """Get an admin setting from database."""
        query = "SELECT setting_value FROM admin_settings WHERE setting_key = %s"
        result = self.db.execute_query(query, (key,), fetch_one=True)
        return result['setting_value'] if result else default_value
    
    def set_setting(self, key: str, value: str) -> bool:
        """Set an admin setting in database."""
        query = """
            INSERT INTO admin_settings (setting_key, setting_value, updated_at) 
            VALUES (%s, %s, NOW())
            ON CONFLICT (setting_key) 
            DO UPDATE SET setting_value = EXCLUDED.setting_value, updated_at = NOW()
        """
        result = self.db.execute_query(query, (key, value), fetch_all=False)
        return result is not None
    
    @st.cache_data(ttl=config.ADMIN_SETTINGS_CACHE_TTL)
    def load_all_settings(_self) -> Dict[str, str]:
        """Load all admin settings from database with caching."""
        settings = {}
        for key, default_value in config.DEFAULT_SETTINGS.items():
            settings[key] = _self.get_setting(key, default_value)
        return settings


class RoomManager:
    """Manages room-related database operations."""
    
    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager
    
    def get_room_grid(self, display_monday: date) -> pd.DataFrame:
        """Get the room allocation grid for a specific week."""
        import json
        import os
        
        # Calculate day mapping
        day_mapping = {
            display_monday + timedelta(days=0): "Monday",
            display_monday + timedelta(days=1): "Tuesday", 
            display_monday + timedelta(days=2): "Wednesday",
            display_monday + timedelta(days=3): "Thursday"
        }
        day_labels = list(day_mapping.values())
        
        # Load room configuration
        try:
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            rooms_file = os.path.join(base_dir, config.ROOMS_FILE)
            with open(rooms_file) as f:
                all_rooms = [r["name"] for r in json.load(f) if r["name"] != "Oasis"]
        except (FileNotFoundError, json.JSONDecodeError):
            st.error(f"Error: Could not load valid data from {config.ROOMS_FILE}.")
            return pd.DataFrame()
        
        # Initialize grid
        grid = {room: {**{"Room": room}, **{day: "Vacant" for day in day_labels}} for room in all_rooms}
        
        # Get allocations from database
        query = """
            SELECT wa.team_name, wa.room_name, wa.date, wp.contact_person
            FROM weekly_allocations wa
            LEFT JOIN weekly_preferences wp ON wa.team_name = wp.team_name
            WHERE wa.room_name != 'Oasis' AND wa.date >= %s AND wa.date <= %s
        """
        start_date = display_monday
        end_date = display_monday + timedelta(days=3)
        
        allocations = self.db.execute_query(query, (start_date, end_date))
        
        if allocations:
            for row in allocations:
                team = row["team_name"]
                room = row["room_name"]
                date_val = row["date"]
                contact = row["contact_person"]
                
                day = day_mapping.get(date_val)
                if room not in grid or not day:
                    continue
                
                display_text = f"{team} ({contact})" if contact else team
                grid[room][day] = display_text
        
        return pd.DataFrame(grid.values())
    
    def get_preferences(self) -> pd.DataFrame:
        """Get team preferences from database."""
        query = """
            SELECT team_name, contact_person, team_size, preferred_days, submission_time 
            FROM weekly_preferences 
            ORDER BY submission_time DESC
        """
        rows = self.db.execute_query(query)
        
        if rows:
            return pd.DataFrame([dict(row) for row in rows], 
                              columns=["Team", "Contact", "Size", "Days", "Submitted At"])
        return pd.DataFrame()
    
    def get_oasis_preferences(self) -> pd.DataFrame:
        """Get Oasis preferences from database."""
        query = """
            SELECT person_name, preferred_day_1, preferred_day_2, preferred_day_3, 
                   preferred_day_4, preferred_day_5, submission_time 
            FROM oasis_preferences 
            ORDER BY submission_time DESC
        """
        rows = self.db.execute_query(query)
        
        if rows:
            return pd.DataFrame([dict(row) for row in rows],
                              columns=["Person", "Day 1", "Day 2", "Day 3", "Day 4", "Day 5", "Submitted At"])
        return pd.DataFrame()


class PreferenceManager:
    """Manages preference submission and validation."""
    
    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager
    
    def insert_team_preference(self, team: str, contact: str, size: int, days: str) -> bool:
        """Insert team preference with validation."""
        # Validation
        if not team or not contact:
            st.error("❌ Team Name and Contact Person are required.")
            return False
        
        if not config.MIN_TEAM_SIZE <= size <= config.MAX_TEAM_SIZE:
            st.error(f"❌ Team size must be between {config.MIN_TEAM_SIZE} and {config.MAX_TEAM_SIZE}.")
            return False
        
        # Check if team already submitted
        check_query = "SELECT 1 FROM weekly_preferences WHERE team_name = %s"
        existing = self.db.execute_query(check_query, (team,), fetch_one=True)
        
        if existing:
            st.error(f"❌ Team '{team}' has already submitted a preference. Contact admin to change.")
            return False
        
        # Validate day selection
        new_days_set = set(days.split(','))
        if new_days_set not in config.VALID_DAY_PAIRS:
            st.error("❌ Invalid day selection. Must select Monday & Wednesday or Tuesday & Thursday.")
            return False
        
        # Insert preference
        insert_query = """
            INSERT INTO weekly_preferences (team_name, contact_person, team_size, preferred_days, submission_time) 
            VALUES (%s, %s, %s, %s, NOW() AT TIME ZONE 'UTC')
        """
        success = self.db.execute_query(insert_query, (team, contact, size, days), fetch_all=False)
        return success is not None
    
    def insert_oasis_preference(self, person: str, selected_days: List[str]) -> bool:
        """Insert Oasis preference with validation."""
        if not person:
            st.error("❌ Please enter your name.")
            return False
        
        if not config.MIN_OASIS_DAYS <= len(selected_days) <= config.MAX_OASIS_DAYS:
            st.error(f"❌ Select between {config.MIN_OASIS_DAYS} and {config.MAX_OASIS_DAYS} preferred days.")
            return False
        
        # Check if person already submitted
        check_query = "SELECT 1 FROM oasis_preferences WHERE person_name = %s"
        existing = self.db.execute_query(check_query, (person,), fetch_one=True)
        
        if existing:
            st.error("❌ You've already submitted. Contact admin to change your selection.")
            return False
        
        # Prepare days data
        padded_days = selected_days + [None] * (5 - len(selected_days))
        
        # Insert preference
        insert_query = """
            INSERT INTO oasis_preferences 
            (person_name, preferred_day_1, preferred_day_2, preferred_day_3, preferred_day_4, preferred_day_5, submission_time) 
            VALUES (%s, %s, %s, %s, %s, %s, NOW() AT TIME ZONE 'UTC')
        """
        success = self.db.execute_query(insert_query, (person.strip(), *padded_days), fetch_all=False)
        return success is not None


# Global database manager instances (will be initialized when needed)
_db_manager = None
_admin_settings_manager = None
_room_manager = None
_preference_manager = None


def get_db_manager():
    """Get or create the database manager instance."""
    global _db_manager
    if _db_manager is None:
        _db_manager = DatabaseManager()
    return _db_manager


def get_admin_settings_manager():
    """Get or create the admin settings manager instance."""
    global _admin_settings_manager
    if _admin_settings_manager is None:
        _admin_settings_manager = AdminSettingsManager(get_db_manager())
    return _admin_settings_manager


def get_room_manager():
    """Get or create the room manager instance."""
    global _room_manager
    if _room_manager is None:
        _room_manager = RoomManager(get_db_manager())
    return _room_manager


def get_preference_manager():
    """Get or create the preference manager instance."""
    global _preference_manager
    if _preference_manager is None:
        _preference_manager = PreferenceManager(get_db_manager())
    return _preference_manager


# For backward compatibility
db_manager = property(lambda self: get_db_manager())
admin_settings_manager = property(lambda self: get_admin_settings_manager())
room_manager = property(lambda self: get_room_manager())
preference_manager = property(lambda self: get_preference_manager())