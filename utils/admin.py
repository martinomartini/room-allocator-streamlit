"""
Admin-related utilities for the Room Allocator application.
"""
import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional
import pytz

from config import config
from utils.database import DatabaseManager, AdminSettingsManager


class AdminOperations:
    """Handles admin operations like data management and allocation running."""
    
    def __init__(self, db_manager: DatabaseManager, admin_settings: AdminSettingsManager):
        self.db = db_manager
        self.admin_settings = admin_settings
    
    def create_archive_tables(self) -> bool:
        """Create archive tables for data backup."""
        queries = [
            ("""
                CREATE TABLE IF NOT EXISTS weekly_preferences_archive (
                    archive_id SERIAL PRIMARY KEY,
                    original_id INTEGER,
                    team_name VARCHAR(255),
                    contact_person VARCHAR(255),
                    team_size INTEGER,
                    preferred_days VARCHAR(100),
                    submission_time TIMESTAMP,
                    deleted_at TIMESTAMP DEFAULT NOW(),
                    deleted_by VARCHAR(255),
                    deletion_reason TEXT
                )
            """, ()),
            ("""
                CREATE TABLE IF NOT EXISTS oasis_preferences_archive (
                    archive_id SERIAL PRIMARY KEY,
                    original_id INTEGER,
                    person_name VARCHAR(255),
                    preferred_day_1 VARCHAR(20),
                    preferred_day_2 VARCHAR(20),
                    preferred_day_3 VARCHAR(20),
                    preferred_day_4 VARCHAR(20),
                    preferred_day_5 VARCHAR(20),
                    submission_time TIMESTAMP,
                    deleted_at TIMESTAMP DEFAULT NOW(),
                    deleted_by VARCHAR(255),
                    deletion_reason TEXT
                )
            """, ()),
            ("""
                CREATE TABLE IF NOT EXISTS weekly_allocations_archive (
                    archive_id SERIAL PRIMARY KEY,
                    original_id INTEGER,
                    team_name VARCHAR(255),
                    room_name VARCHAR(255),
                    date DATE,
                    allocated_at TIMESTAMP,
                    confirmed BOOLEAN DEFAULT FALSE,
                    confirmed_at TIMESTAMP,
                    deleted_at TIMESTAMP DEFAULT NOW(),
                    deleted_by VARCHAR(255),
                    deletion_reason TEXT
                )
            """, ())
        ]
        
        return self.db.execute_transaction(queries)
    
    def backup_weekly_preferences(self, deleted_by: str = "admin", deletion_reason: str = "Manual deletion") -> bool:
        """Backup weekly preferences before deletion."""
        query = """
            INSERT INTO weekly_preferences_archive 
            (team_name, contact_person, team_size, preferred_days, submission_time, deleted_by, deletion_reason)
            SELECT team_name, contact_person, team_size, preferred_days, submission_time, %s, %s
            FROM weekly_preferences
        """
        return self.db.execute_query(query, (deleted_by, deletion_reason), fetch_all=False) is not None
    
    def backup_oasis_preferences(self, deleted_by: str = "admin", deletion_reason: str = "Manual deletion") -> bool:
        """Backup oasis preferences before deletion."""
        query = """
            INSERT INTO oasis_preferences_archive 
            (person_name, preferred_day_1, preferred_day_2, preferred_day_3, preferred_day_4, preferred_day_5, 
             submission_time, deleted_by, deletion_reason)
            SELECT person_name, preferred_day_1, preferred_day_2, preferred_day_3, preferred_day_4, preferred_day_5,
                   submission_time, %s, %s
            FROM oasis_preferences
        """
        return self.db.execute_query(query, (deleted_by, deletion_reason), fetch_all=False) is not None
    
    def clear_project_allocations(self, monday_date) -> bool:
        """Clear project room allocations for a specific week."""
        query = "DELETE FROM weekly_allocations WHERE room_name != 'Oasis' AND date >= %s AND date <= %s"
        return self.db.execute_query(query, (monday_date, monday_date + timedelta(days=6)), fetch_all=False) is not None
    
    def clear_oasis_allocations(self, monday_date) -> bool:
        """Clear Oasis allocations for a specific week."""
        query = "DELETE FROM weekly_allocations WHERE room_name = 'Oasis' AND date >= %s AND date <= %s"
        return self.db.execute_query(query, (monday_date, monday_date + timedelta(days=6)), fetch_all=False) is not None
    
    def clear_all_weekly_preferences(self) -> bool:
        """Clear all weekly preferences."""
        query = "DELETE FROM weekly_preferences"
        return self.db.execute_query(query, fetch_all=False) is not None
    
    def clear_all_oasis_preferences(self) -> bool:
        """Clear all oasis preferences."""
        query = "DELETE FROM oasis_preferences"
        return self.db.execute_query(query, fetch_all=False) is not None
    
    def update_display_settings(self, settings: Dict[str, str]) -> int:
        """Update multiple display settings in batch."""
        success_count = 0
        for key, value in settings.items():
            if self.admin_settings.set_setting(key, value):
                success_count += 1
        return success_count
    
    def get_oasis_matrix_data(self, monday_date) -> Tuple[pd.DataFrame, Dict]:
        """Get data for the Oasis allocation matrix."""
        import json
        import os
        
        # Get Oasis capacity
        try:
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            rooms_file = os.path.join(base_dir, config.ROOMS_FILE)
            with open(rooms_file) as f:
                rooms_data = json.load(f)
                oasis_config = next((r for r in rooms_data if r["name"] == "Oasis"), {"capacity": config.OASIS_DEFAULT_CAPACITY})
        except:
            oasis_config = {"capacity": config.OASIS_DEFAULT_CAPACITY}
        
        # Calculate dates and day names
        days_dates = [monday_date + timedelta(days=i) for i in range(5)]
        day_names = [d.strftime("%A") for d in days_dates]
        
        # Get current allocations
        query = """
            SELECT team_name, date FROM weekly_allocations 
            WHERE room_name = 'Oasis' AND date >= %s AND date <= %s
        """
        allocations = self.db.execute_query(query, (monday_date, days_dates[-1]))
        
        # Get names from preferences
        prefs_query = "SELECT DISTINCT person_name FROM oasis_preferences"
        preference_names = self.db.execute_query(prefs_query)
        
        # Combine all relevant names
        allocated_names = set()
        if allocations:
            allocated_names = {row["team_name"] for row in allocations}
        
        pref_names = set()
        if preference_names:
            pref_names = {row["person_name"] for row in preference_names}
        
        all_names = sorted(list(allocated_names.union(pref_names).union({"Niek"})))
        if not all_names:
            all_names = ["Niek"]
        
        # Create initial matrix
        matrix_df = pd.DataFrame(False, index=all_names, columns=day_names)
        
        # Fill in current allocations
        if allocations:
            allocation_df = pd.DataFrame([dict(row) for row in allocations])
            allocation_df["Date"] = pd.to_datetime(allocation_df["Date"]).dt.date
            
            for _, row in allocation_df.iterrows():
                person_name = row["Name"]
                alloc_date = row["Date"]
                if alloc_date in days_dates and person_name in matrix_df.index:
                    matrix_df.at[person_name, alloc_date.strftime("%A")] = True
        
        # Ensure Niek is always allocated
        if "Niek" in matrix_df.index:
            for day_name in day_names:
                matrix_df.at["Niek", day_name] = True
        
        return matrix_df, {"capacity": oasis_config["capacity"], "dates": days_dates, "day_names": day_names}


def render_admin_controls(admin_ops: AdminOperations, admin_settings: AdminSettingsManager) -> None:
    """Render the admin controls section."""
    with st.expander("🔐 Admin Controls"):
        pwd = st.text_input("Enter admin password:", type="password", key="admin_pwd_main")
        
        if pwd == config.ADMIN_PASSWORD:
            st.success("✅ Access granted.")
            
            # Display text management
            render_display_text_management(admin_settings)
            
            # Room allocation controls
            render_allocation_controls()
            
            # Data management controls
            render_data_management_controls(admin_ops)
            
        elif pwd:
            st.error("❌ Incorrect password.")


def render_display_text_management(admin_settings: AdminSettingsManager) -> None:
    """Render display text management form."""
    st.subheader("💼 Update All Display Texts (Stored in Database)")
    st.markdown("**Note:** These texts are stored in database and will persist permanently across all refreshes and sessions.")
    
    # Refresh settings
    if st.button("🔄 Refresh Settings from Database", key="refresh_settings"):
        st.cache_data.clear()
        st.success("Settings refreshed from database!")
        st.rerun()
    
    # Load current settings
    current_settings = admin_settings.load_all_settings()
    
    # Settings form
    with st.form("admin_display_texts_form"):
        st.markdown("### Display Text Configuration")
        
        new_settings = {}
        new_settings['submission_week_of_text'] = st.text_input(
            "Text for 'Submissions for the week of ...' (e.g., '9 June')", 
            current_settings['submission_week_of_text']
        )
        new_settings['submission_start_text'] = st.text_input(
            "Display text for 'Submission start'", 
            current_settings['submission_start_text']
        )
        new_settings['submission_end_text'] = st.text_input(
            "Display text for 'Submission end'", 
            current_settings['submission_end_text']
        )
        new_settings['oasis_end_text'] = st.text_input(
            "Display text for 'Oasis end'", 
            current_settings['oasis_end_text']
        )
        new_settings['project_allocations_display_markdown_content'] = st.text_area(
            "Header text for 'Project Room Allocations' section", 
            current_settings['project_allocations_display_markdown_content'],
            height=100
        )
        new_settings['oasis_allocations_display_markdown_content'] = st.text_area(
            "Header text for 'Oasis Allocations' section", 
            current_settings['oasis_allocations_display_markdown_content'],
            height=100
        )
        
        if st.form_submit_button("💾 Save All Display Texts to Database"):
            admin_ops = AdminOperations(admin_settings.db, admin_settings)
            success_count = admin_ops.update_display_settings(new_settings)
            
            if success_count == len(new_settings):
                st.success("✅ All display texts saved to database and will persist permanently!")
                st.cache_data.clear()
                st.rerun()
            else:
                st.error(f"❌ Only {success_count}/{len(new_settings)} settings saved successfully.")


def render_allocation_controls() -> None:
    """Render allocation control buttons."""
    st.subheader("🧠 Project Room Admin")
    if st.button("🚀 Run Project Room Allocation", key="btn_run_proj_alloc"):
        try:
            from allocate_rooms import run_allocation
            success, _ = run_allocation(config.DATABASE_URL, only="project", base_monday_date=config.STATIC_PROJECT_MONDAY)
            if success:
                st.success("✅ Project room allocation completed.")
                st.rerun()
            else:
                st.error("❌ Project room allocation failed.")
        except ImportError:
            st.error("❌ Allocation function not available.")

    st.subheader("🌿 Oasis Admin")
    if st.button("🎲 Run Oasis Allocation", key="btn_run_oasis_alloc"):
        try:
            from allocate_rooms import run_allocation
            success, _ = run_allocation(config.DATABASE_URL, only="oasis", base_monday_date=config.STATIC_OASIS_MONDAY)
            if success:
                st.success("✅ Oasis allocation completed.")
                st.rerun()
            else:
                st.error("❌ Oasis allocation failed.")
        except ImportError:
            st.error("❌ Allocation function not available.")


def render_data_management_controls(admin_ops: AdminOperations) -> None:
    """Render data management controls."""
    st.subheader("🧹 Data Management")
    
    # Project room data management
    render_project_data_controls(admin_ops)
    
    # Oasis data management
    render_oasis_data_controls(admin_ops)


def render_project_data_controls(admin_ops: AdminOperations) -> None:
    """Render project room data management controls."""
    if st.button("🗑️ Remove Project Allocations for Current Week", key="btn_reset_proj_alloc_week"):
        if admin_ops.clear_project_allocations(config.STATIC_PROJECT_MONDAY):
            st.success("✅ Project room allocations removed.")
            st.rerun()
        else:
            st.error("❌ Failed to reset project allocations.")

    # Global project preferences deletion with confirmation
    if "show_proj_prefs_confirm" not in st.session_state:
        st.session_state.show_proj_prefs_confirm = False
        
    if not st.session_state.show_proj_prefs_confirm:
        if st.button("🧽 Remove All Project Room Preferences (Global Action)", key="btn_reset_all_proj_prefs"):
            st.session_state.show_proj_prefs_confirm = True
            st.rerun()
    else:
        st.warning("⚠️ This will permanently delete ALL project room preferences!")
        
        col1, col2 = st.columns(2)
        with col1:
            if st.button("✅ Yes, Delete All Preferences", key="btn_confirm_delete_proj_prefs"):
                backup_success = admin_ops.backup_weekly_preferences("admin", "Manual deletion via admin panel")
                if admin_ops.clear_all_weekly_preferences():
                    if backup_success:
                        st.success("✅ All project room preferences removed and backed up to archive.")
                    else:
                        st.success("✅ All project room preferences removed. (Backup may have failed)")
                    st.session_state.show_proj_prefs_confirm = False
                    st.rerun()
                else:
                    st.error("❌ Failed to delete preferences.")
        
        with col2:
            if st.button("❌ Cancel", key="btn_cancel_delete_proj_prefs"):
                st.session_state.show_proj_prefs_confirm = False
                st.rerun()


def render_oasis_data_controls(admin_ops: AdminOperations) -> None:
    """Render Oasis data management controls."""
    if st.button("🗑️ Remove Oasis Allocations for Current Week", key="btn_reset_oasis_alloc_week"):
        if admin_ops.clear_oasis_allocations(config.STATIC_OASIS_MONDAY):
            st.success("✅ Oasis allocations removed.")
            st.rerun()
        else:
            st.error("❌ Failed to reset Oasis allocations.")

    # Global Oasis preferences deletion with confirmation
    if "show_oasis_prefs_confirm" not in st.session_state:
        st.session_state.show_oasis_prefs_confirm = False
        
    if not st.session_state.show_oasis_prefs_confirm:
        if st.button("🧽 Remove All Oasis Preferences (Global Action)", key="btn_reset_all_oasis_prefs"):
            st.session_state.show_oasis_prefs_confirm = True
            st.rerun()
    else:
        st.warning("⚠️ This will permanently delete ALL Oasis preferences!")
        
        col1, col2 = st.columns(2)
        with col1:
            if st.button("✅ Yes, Delete All Preferences", key="btn_confirm_delete_oasis_prefs"):
                backup_success = admin_ops.backup_oasis_preferences("admin", "Manual deletion via admin panel")
                if admin_ops.clear_all_oasis_preferences():
                    if backup_success:
                        st.success("✅ All Oasis preferences removed and backed up to archive.")
                    else:
                        st.success("✅ All Oasis preferences removed. (Backup may have failed)")
                    st.session_state.show_oasis_prefs_confirm = False
                    st.rerun()
                else:
                    st.error("❌ Failed to delete preferences.")
        
        with col2:
            if st.button("❌ Cancel", key="btn_cancel_delete_oasis_prefs"):
                st.session_state.show_oasis_prefs_confirm = False
                st.rerun()