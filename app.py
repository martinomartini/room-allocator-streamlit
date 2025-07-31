"""
Streamlined and refactored main application for Room Allocator.
This version uses centralized configuration and modular utilities.
"""
import streamlit as st
from datetime import datetime
import pandas as pd

# Import our refactored modules
from config import config
from utils.database import (
    get_db_manager, get_admin_settings_manager, get_room_manager, get_preference_manager
)
from utils.admin import AdminOperations, render_admin_controls
from utils.validation import validate_admin_password

# Configure Streamlit page
st.set_page_config(page_title=config.PAGE_TITLE, layout=config.PAGE_LAYOUT)

# Validate configuration
config_errors = config.validate_config()
if config_errors:
    for error in config_errors:
        st.error(error)
    st.stop()

# Initialize session state
if "project_rooms_display_monday" not in st.session_state:
    st.session_state.project_rooms_display_monday = config.STATIC_PROJECT_MONDAY
if "oasis_display_monday" not in st.session_state:
    st.session_state.oasis_display_monday = config.STATIC_OASIS_MONDAY

# Initialize managers
db_manager = get_db_manager()
admin_settings_manager = get_admin_settings_manager()
room_manager = get_room_manager()
preference_manager = get_preference_manager()

# Initialize admin operations
admin_ops = AdminOperations(db_manager, admin_settings_manager)
admin_ops.create_archive_tables()

# Load admin settings
admin_settings = admin_settings_manager.load_all_settings()

# -----------------------------------------------------
# Main UI
# -----------------------------------------------------
st.title("📅 Weekly Room Allocator")

# Quick access to analytics
col1, col2 = st.columns([3, 1])
with col2:
    if st.button("📊 View Analytics Dashboard", type="secondary"):
        st.switch_page("pages/3_Historical_Analytics.py")

# Display current time
now_local = datetime.now(config.get_office_timezone())
st.info(f"Current Office Time: **{now_local.strftime('%Y-%m-%d %H:%M:%S')}** ({config.OFFICE_TIMEZONE_STR})")

# Admin Controls
render_admin_controls(admin_ops, admin_settings_manager)

# -----------------------------------------------------
# Team Form (Project Room Requests)
# -----------------------------------------------------
st.header("📝 Request Project Room")
st.markdown(
    f"""
    For teams of 3 or more. Submissions for the **week of {admin_settings['submission_week_of_text']}** are open 
    from **{admin_settings['submission_start_text']}** until **{admin_settings['submission_end_text']}**.
    """
)

with st.form("team_form_main"):
    team_name = st.text_input("Team Name", key="tf_team_name")
    contact_person = st.text_input("Contact Person", key="tf_contact_person")
    team_size = st.number_input(
        f"Team Size ({config.MIN_TEAM_SIZE}-{config.MAX_TEAM_SIZE})", 
        min_value=config.MIN_TEAM_SIZE, 
        max_value=config.MAX_TEAM_SIZE, 
        value=config.MIN_TEAM_SIZE, 
        key="tf_team_size"
    )
    day_choice = st.selectbox(
        "Preferred Days", 
        ["Monday and Wednesday", "Tuesday and Thursday"], 
        key="tf_day_choice"
    )
    submit_team_pref = st.form_submit_button("Submit Project Room Request")

    if submit_team_pref:
        day_map = {
            "Monday and Wednesday": "Monday,Wednesday",
            "Tuesday and Thursday": "Tuesday,Thursday"
        }
        if preference_manager.insert_team_preference(team_name, contact_person, team_size, day_map[day_choice]):
            st.success(f"✅ Preference submitted for {team_name}!")
            st.rerun()

# -----------------------------------------------------
# Oasis Form (Preferences)
# -----------------------------------------------------
st.header("🌿 Reserve Oasis Seat")
st.markdown(
    f"""
    Submit your personal preferences for the **week of {admin_settings['submission_week_of_text']}**. 
    Submissions open from **{admin_settings['submission_start_text']}** until **{admin_settings['oasis_end_text']}**.
    """
)

with st.form("oasis_form_main"):
    oasis_person_name = st.text_input("Your Name", key="of_oasis_person")
    oasis_selected_days = st.multiselect(
        f"Select Your Preferred Days for Oasis (up to {config.MAX_OASIS_DAYS}):",
        config.WEEKDAYS,
        max_selections=config.MAX_OASIS_DAYS,
        key="of_oasis_days"
    )
    submit_oasis_pref = st.form_submit_button("Submit Oasis Preference")

    if submit_oasis_pref:
        if preference_manager.insert_oasis_preference(oasis_person_name, oasis_selected_days):
            st.success(f"✅ Oasis preference submitted for {oasis_person_name}!")
            st.rerun()

# -----------------------------------------------------
# Display: Project Room Allocations
# -----------------------------------------------------
st.header("📌 Project Room Allocations")
st.markdown(admin_settings['project_allocations_display_markdown_content'])

alloc_display_df = room_manager.get_room_grid(st.session_state.project_rooms_display_monday)
if alloc_display_df.empty:
    st.write("No project room allocations yet.")
else:
    st.dataframe(alloc_display_df, use_container_width=True, hide_index=True)

# -----------------------------------------------------
# Ad-hoc Oasis Addition
# -----------------------------------------------------
st.header("🚶 Add Yourself to Oasis (Ad-hoc)")
st.caption("Use this if you missed preference submission. Subject to availability.")

with st.form("oasis_add_form_main"):
    adhoc_oasis_name = st.text_input("Your Name", key="af_adhoc_name")
    adhoc_oasis_days = st.multiselect(
        "Select day(s):",
        config.WEEKDAYS,
        key="af_adhoc_days"
    )
    add_adhoc_submit = st.form_submit_button("➕ Add Me to Oasis Schedule")

if add_adhoc_submit:
    if not adhoc_oasis_name.strip():
        st.error("❌ Please enter your name.")
    elif not adhoc_oasis_days:
        st.error("❌ Select at least one day.")
    else:
        # This section needs the ad-hoc logic - keeping the existing implementation for now
        # but with cleaner error handling
        import json
        import os
        from datetime import timedelta
        
        try:
            # Load Oasis configuration
            base_dir = os.path.dirname(os.path.abspath(__file__))
            rooms_file = os.path.join(base_dir, config.ROOMS_FILE)
            with open(rooms_file) as f:
                rooms_data = json.load(f)
                oasis_config = next((r for r in rooms_data if r["name"] == "Oasis"), {"capacity": config.OASIS_DEFAULT_CAPACITY})
            
            conn = db_manager.get_connection()
            if not conn:
                st.error("❌ Database connection failed.")
            else:
                try:
                    with conn.cursor() as cur:
                        name_clean = adhoc_oasis_name.strip().title()
                        days_map_indices = {"Monday": 0, "Tuesday": 1, "Wednesday": 2, "Thursday": 3, "Friday": 4}
                        current_monday = st.session_state.oasis_display_monday
                        
                        # Check if confirmed column exists
                        has_confirmed_column = False
                        try:
                            cur.execute("SELECT confirmed FROM weekly_allocations LIMIT 1")
                            has_confirmed_column = True
                        except:
                            conn.rollback()
                            has_confirmed_column = False
                        
                        # Remove existing entries for this person on selected days
                        for day_str in adhoc_oasis_days:
                            date_obj = current_monday + timedelta(days=days_map_indices[day_str])
                            cur.execute("DELETE FROM weekly_allocations WHERE room_name = 'Oasis' AND team_name = %s AND date = %s", (name_clean, date_obj))
                        
                        added_to_all_selected = True
                        for day_str in adhoc_oasis_days:
                            date_obj = current_monday + timedelta(days=days_map_indices[day_str])
                            cur.execute("SELECT COUNT(*) FROM weekly_allocations WHERE room_name = 'Oasis' AND date = %s", (date_obj,))
                            count = cur.fetchone()[0]
                            
                            if count >= oasis_config.get("capacity", config.OASIS_DEFAULT_CAPACITY):
                                st.warning(f"⚠️ Oasis is full on {day_str}. Could not add {name_clean}.")
                                added_to_all_selected = False
                            else:
                                if has_confirmed_column:
                                    cur.execute("INSERT INTO weekly_allocations (team_name, room_name, date, confirmed) VALUES (%s, 'Oasis', %s, %s)", (name_clean, date_obj, False))
                                else:
                                    cur.execute("INSERT INTO weekly_allocations (team_name, room_name, date) VALUES (%s, 'Oasis', %s)", (name_clean, date_obj))
                        
                        conn.commit()
                        if added_to_all_selected and adhoc_oasis_days:
                            st.success(f"✅ {name_clean} added to Oasis for selected day(s)!")
                        elif adhoc_oasis_days:
                            st.info("ℹ️ Check messages above for details on your ad-hoc Oasis additions.")
                        st.rerun()
                        
                except Exception as e:
                    st.error(f"❌ Error adding to Oasis: {e}")
                    if conn:
                        try:
                            conn.rollback()
                        except:
                            pass
                finally:
                    db_manager.return_connection(conn)
                    
        except Exception as e:
            st.error(f"❌ Configuration error: {e}")

# -----------------------------------------------------
# Full Weekly Oasis Overview
# -----------------------------------------------------
st.header("📊 Full Weekly Oasis Overview")
st.markdown(admin_settings['oasis_allocations_display_markdown_content'])

# Get Oasis matrix data
matrix_df, matrix_info = admin_ops.get_oasis_matrix_data(st.session_state.oasis_display_monday)

# Display availability summary
st.subheader("🪑 Oasis Availability Summary")
conn = db_manager.get_connection()
if conn:
    try:
        with conn.cursor() as cur:
            for day_dt, day_str in zip(matrix_info["dates"], matrix_info["day_names"]):
                cur.execute("SELECT COUNT(*) FROM weekly_allocations WHERE room_name = 'Oasis' AND date = %s", (day_dt,))
                used_spots = cur.fetchone()[0]
                spots_left = max(0, matrix_info["capacity"] - used_spots)
                st.markdown(f"**{day_str}**: {spots_left} spot(s) left")
    finally:
        db_manager.return_connection(conn)

# Display and edit matrix
edited_matrix = st.data_editor(
    matrix_df,
    use_container_width=True,
    disabled=["Niek"] if "Niek" in matrix_df.index else [],
    key="oasis_matrix_editor_main"
)

if st.button("💾 Save Oasis Matrix Changes", key="btn_save_oasis_matrix_changes"):
    # This section needs the matrix saving logic - keeping existing implementation
    # but with better error handling
    conn = db_manager.get_connection()
    if not conn:
        st.error("❌ Database connection failed.")
    else:
        try:
            with conn.cursor() as cur:
                # Check if confirmed column exists
                cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'weekly_allocations' AND column_name = 'confirmed'")
                has_confirmed_col = cur.fetchone() is not None
                
                monday_date = st.session_state.oasis_display_monday
                end_date = matrix_info["dates"][-1]
                
                # Delete existing Oasis allocations for this week
                cur.execute("DELETE FROM weekly_allocations WHERE room_name = 'Oasis' AND team_name != 'Niek' AND date >= %s AND date <= %s", (monday_date, end_date))
                
                if "Niek" in edited_matrix.index:
                    cur.execute("DELETE FROM weekly_allocations WHERE room_name = 'Oasis' AND team_name = 'Niek' AND date >= %s AND date <= %s", (monday_date, end_date))
                    for day_idx, day_col_name in enumerate(matrix_info["day_names"]):
                        if edited_matrix.at["Niek", day_col_name]:
                            date_obj = monday_date + timedelta(days=day_idx)
                            if has_confirmed_col:
                                cur.execute("INSERT INTO weekly_allocations (team_name, room_name, date, confirmed, confirmed_at) VALUES (%s, %s, %s, %s, NOW())", ("Niek", "Oasis", date_obj, True))
                            else:
                                cur.execute("INSERT INTO weekly_allocations (team_name, room_name, date) VALUES (%s, %s, %s)", ("Niek", "Oasis", date_obj))
                
                # Track occupied counts per day
                occupied_counts_per_day = {day_col: 0 for day_col in matrix_info["day_names"]}
                if "Niek" in edited_matrix.index:
                    for day_col_name in matrix_info["day_names"]:
                        if edited_matrix.at["Niek", day_col_name]:
                            occupied_counts_per_day[day_col_name] += 1
                
                # Add other people
                for person_name in edited_matrix.index:
                    if person_name == "Niek":
                        continue
                    for day_idx, day_col_name in enumerate(matrix_info["day_names"]):
                        if edited_matrix.at[person_name, day_col_name]:
                            if occupied_counts_per_day[day_col_name] < matrix_info["capacity"]:
                                date_obj = monday_date + timedelta(days=day_idx)
                                if has_confirmed_col:
                                    cur.execute("INSERT INTO weekly_allocations (team_name, room_name, date, confirmed, confirmed_at) VALUES (%s, %s, %s, %s, NOW())", (person_name, "Oasis", date_obj, True))
                                else:
                                    cur.execute("INSERT INTO weekly_allocations (team_name, room_name, date) VALUES (%s, %s, %s)", (person_name, "Oasis", date_obj))
                                occupied_counts_per_day[day_col_name] += 1
                            else:
                                st.warning(f"⚠️ {person_name} could not be added to Oasis on {day_col_name}: capacity reached.")
                
                conn.commit()
                if has_confirmed_col:
                    st.success("✅ Oasis Matrix saved successfully! All entries marked as confirmed.")
                else:
                    st.success("✅ Oasis Matrix saved successfully!")
                    st.info("💡 To enable attendance confirmation tracking, please run the SQL commands in backup_tables.sql on your database.")
                st.rerun()
                
        except Exception as e:
            st.error(f"❌ Failed to save Oasis Matrix: {e}")
            if conn:
                conn.rollback()
        finally:
            db_manager.return_connection(conn)

# Final connectivity check
if not db_manager.get_connection_pool():
    st.error("🚨 Cannot connect to the database. Please check configurations or contact an admin.")