import streamlit as st
import psycopg2
import psycopg2.pool
import psycopg2.extras # For RealDictCursor
import json
import os
from datetime import datetime, timedelta, time
import pytz
import pandas as pd
import contextlib
import traceback

# --- Local Imports (Assuming allocate_rooms.py is in the same directory) ---
try:
    from allocate_rooms import run_allocation
except ImportError:
    st.error("Failed to import `run_allocation`. Ensure `allocate_rooms.py` is in the same directory.")
    def run_allocation(db_url, only=None):
        st.warning(f"Dummy `run_allocation` called for {only}. `allocate_rooms.py` might be missing.")
        return False, "Dummy allocation result"

# --- Constants and Configuration ---
PAGE_TITLE = "Weekly Room Allocator"
DATABASE_URL = st.secrets.get("SUPABASE_DB_URI", os.environ.get("SUPABASE_DB_URI"))
OFFICE_TIMEZONE_STR = st.secrets.get("OFFICE_TIMEZONE", os.environ.get("OFFICE_TIMEZONE", "Europe/Amsterdam"))
RESET_PASSWORD = st.secrets.get("ADMIN_RESET_PASSWORD", "trainee")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOMS_FILE = os.path.join(BASE_DIR, 'rooms.json')

OASIS_ROOM_NAME = "Oasis"
VACANT_STATUS = "Vacant"
DAYS_OF_WEEK = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
DAYS_OF_WEEK_MAP = {day: i for i, day in enumerate(DAYS_OF_WEEK)} # Mon:0, Tue:1 etc.
PROJECT_TEAM_DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday"]

try:
    OFFICE_TIMEZONE = pytz.timezone(OFFICE_TIMEZONE_STR)
except pytz.UnknownTimeZoneError:
    st.error(f"Invalid Timezone: '{OFFICE_TIMEZONE_STR}', defaulting to UTC.")
    OFFICE_TIMEZONE = pytz.utc

try:
    with open(ROOMS_FILE, 'r') as f:
        AVAILABLE_ROOMS = json.load(f)
except FileNotFoundError:
    st.error(f"Rooms file not found at {ROOMS_FILE}. Please create it.")
    AVAILABLE_ROOMS = [{"name": "Default Room", "capacity": 4}, {"name": OASIS_ROOM_NAME, "capacity": 15}]
except json.JSONDecodeError:
    st.error(f"Error decoding JSON from {ROOMS_FILE}.")
    AVAILABLE_ROOMS = [{"name": "Default Room", "capacity": 4}, {"name": OASIS_ROOM_NAME, "capacity": 15}]

OASIS_ROOM_DETAILS = next((r for r in AVAILABLE_ROOMS if r["name"] == OASIS_ROOM_NAME), {"capacity": 15})
try:
    OASIS_CAPACITY = int(OASIS_ROOM_DETAILS["capacity"])
except ValueError:
    st.error(f"Invalid capacity format for Oasis room: '{OASIS_ROOM_DETAILS['capacity']}'. Defaulting to 15.")
    OASIS_CAPACITY = 15
ALL_ROOM_NAMES_EXCL_OASIS = [r["name"] for r in AVAILABLE_ROOMS if r["name"] != OASIS_ROOM_NAME]

# --- Database Connection Management ---
@st.cache_resource
def get_db_connection_pool():
    if not DATABASE_URL:
        st.error("DATABASE_URL is not set.")
        return None
    try:
        return psycopg2.pool.SimpleConnectionPool(1, 25, dsn=DATABASE_URL)
    except Exception as e:
        st.error(f"Failed to create database connection pool: {e}")
        return None

@contextlib.contextmanager
def manage_db_connection(pool):
    if pool is None: raise ConnectionError("DB pool not initialized.")
    conn = None
    try:
        conn = pool.getconn()
        yield conn
    finally:
        if conn: pool.putconn(conn)

# --- Helper Functions ---
def get_current_monday(dt_object):
    return dt_object.date() - timedelta(days=dt_object.weekday())

def get_dynamic_submission_window_str(now_in_tz, type="team"):
    """Generates a dynamic string for submission deadlines."""
    current_monday = get_current_monday(now_in_tz)
    
    if type == "team": # Project Team: Wed 09:00 to Thu 16:00
        submission_start_day_idx = DAYS_OF_WEEK_MAP["Wednesday"]
        submission_end_day_idx = DAYS_OF_WEEK_MAP["Thursday"]
        start_time = time(9, 0)
        end_time = time(16, 0)
        period_name = "Project Team"
    elif type == "oasis": # Oasis: Wed 09:00 to Fri 16:00
        submission_start_day_idx = DAYS_OF_WEEK_MAP["Wednesday"]
        submission_end_day_idx = DAYS_OF_WEEK_MAP["Friday"]
        start_time = time(9, 0)
        end_time = time(16, 0)
        period_name = "Oasis"
    else:
        return "Invalid type for submission window."

    # Calculate this week's submission window
    this_week_start_date = current_monday + timedelta(days=submission_start_day_idx)
    this_week_end_date = current_monday + timedelta(days=submission_end_day_idx)
    
    this_week_submission_start_dt = OFFICE_TIMEZONE.localize(datetime.combine(this_week_start_date, start_time))
    this_week_submission_end_dt = OFFICE_TIMEZONE.localize(datetime.combine(this_week_end_date, end_time))

    # Determine if we are past this week's deadline
    if now_in_tz > this_week_submission_end_dt:
        # Move to next week's window
        next_week_start_date = this_week_start_date + timedelta(weeks=1)
        next_week_end_date = this_week_end_date + timedelta(weeks=1)
        display_start_dt = OFFICE_TIMEZONE.localize(datetime.combine(next_week_start_date, start_time))
        display_end_dt = OFFICE_TIMEZONE.localize(datetime.combine(next_week_end_date, end_time))
        week_str = "for next week"
    else:
        # Still within or before this week's window
        display_start_dt = this_week_submission_start_dt
        display_end_dt = this_week_submission_end_dt
        week_str = "for the upcoming week"
        
    start_str = display_start_dt.strftime("%A %d %b %H:%M")
    end_str = display_end_dt.strftime("%A %d %b %H:%M")
    
    return f"Submit {period_name} preferences {week_str}, between **{start_str}** and **{end_str}**."


# --- Database Interaction Functions (largely unchanged, ensure they use manage_db_connection) ---
def fetch_all_from_db(pool, query, params=None):
    with manage_db_connection(pool) as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(query, params)
            return cur.fetchall()

def execute_db_query(pool, query, params=None, commit=False):
    with manage_db_connection(pool) as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            if commit:
                conn.commit()
            if cur.description:
                try: return cur.fetchall()
                except psycopg2.ProgrammingError: return None
            return None

def get_room_grid_data(pool, current_monday_date):
    day_mapping = {current_monday_date + timedelta(days=i): PROJECT_TEAM_DAYS[i] for i in range(len(PROJECT_TEAM_DAYS))}
    grid = {room: {**{"Room": room}, **{day: VACANT_STATUS for day in day_mapping.values()}} for room in ALL_ROOM_NAMES_EXCL_OASIS}
    allocations = fetch_all_from_db(pool, "SELECT team_name, room_name, date FROM weekly_allocations WHERE room_name != %s", (OASIS_ROOM_NAME,))
    contacts_raw = fetch_all_from_db(pool, "SELECT team_name, contact_person FROM weekly_preferences")
    contacts = {row["team_name"]: row["contact_person"] for row in contacts_raw}
    for row in allocations:
        day = day_mapping.get(row["date"])
        if row["room_name"] in grid and day:
            contact = contacts.get(row["team_name"])
            grid[row["room_name"]][day] = f"{row['team_name']} ({contact})" if contact else row['team_name']
    return pd.DataFrame(grid.values())

def get_preferences_data(pool, table_name, db_cols_list):
    query = f"SELECT {', '.join(db_cols_list)} FROM {table_name}"
    try:
        rows = fetch_all_from_db(pool, query)
        return pd.DataFrame(rows, columns=db_cols_list) # Return with raw DB column names
    except Exception as e:
        st.warning(f"Failed to fetch {table_name.replace('_', ' ')}: {e}")
        return pd.DataFrame(columns=db_cols_list)

def insert_team_preference(pool, team, contact, size, days_str):
    if not (3 <= size <= 6): st.error("❌ Team size must be 3-6."); return False
    new_days_set = set(days_str.split(','))
    if new_days_set not in [{"Monday", "Wednesday"}, {"Tuesday", "Thursday"}]:
        st.error("❌ Must select Mon & Wed or Tue & Thu."); return False
    # ... (rest of the logic, ensure it's robust)
    insert_query = """
        INSERT INTO weekly_preferences (team_name, contact_person, team_size, preferred_days, submission_time)
        VALUES (%s, %s, %s, %s, NOW() AT TIME ZONE %s)
        ON CONFLICT (team_name) DO UPDATE SET
        contact_person = EXCLUDED.contact_person, team_size = EXCLUDED.team_size,
        preferred_days = EXCLUDED.preferred_days, submission_time = NOW() AT TIME ZONE %s;
    """
    try:
        execute_db_query(pool, insert_query, (team, contact, size, days_str, OFFICE_TIMEZONE_STR, OFFICE_TIMEZONE_STR), commit=True)
        return True
    except Exception as e: st.error(f"❌ Team preference insert/update failed: {e}"); return False

def reset_table_data(pool, table_name, condition_column=None, condition_value=None, negate_condition=False):
    query = f"DELETE FROM {table_name}"
    params = None
    op_str = ""
    if condition_column and condition_value is not None:
        operator = "!=" if negate_condition else "="
        query += f" WHERE {condition_column} {operator} %s"
        params = (condition_value,)
        op_str = f" (where {condition_column} {operator} {condition_value})"
    try:
        execute_db_query(pool, query, params, commit=True)
        st.success(f"✅ Data from '{table_name}'{op_str} removed.")
        return True
    except Exception as e: st.error(f"❌ Failed to remove data from '{table_name}': {e}"); return False

# --- UI Rendering Functions ---
def display_header_and_info(current_office_time_str):
    st.title(f"📅 {PAGE_TITLE} for TS")
    st.info(f"Current Office Time: **{current_office_time_str}** ({OFFICE_TIMEZONE_STR})")
    st.info(f"""💡 **How This Works:**... Oasis capacity: **{OASIS_CAPACITY}** spots...""") # Keep info brief for example

def admin_allocation_controls(pool):
    # ... (Implementation as before)
    st.subheader("🧠 Allocation Triggers") # Example

def admin_edit_data(pool, data_fetch_func, df_key, update_table_name, column_map_config, current_monday_date=None):
    st.subheader(f"📝 Edit {update_table_name.replace('_', ' ').title()}")
    raw_db_cols = list(column_map_config.keys())
    df = data_fetch_func(pool, update_table_name, raw_db_cols) # Adjusted to pass table_name and raw_cols

    if df is None or df.empty:
        st.info(f"No {update_table_name.replace('_', ' ')} yet to edit."); return

    df_for_editor = df.copy()
    rename_map = {db_col: details['name'] for db_col, details in column_map_config.items() if db_col in df_for_editor.columns}
    df_for_editor.rename(columns=rename_map, inplace=True)
    
    editable_df = st.data_editor(df_for_editor, num_rows="dynamic", use_container_width=True, key=df_key)

    if st.button(f"💾 Save {update_table_name.replace('_', ' ').title()} Changes", key=f"save_{df_key}"):
        # ... (Save logic as before, ensuring it uses db_col for db operations and details['name'] for editable_df row access)
        st.success("Data saved placeholder")


def admin_save_project_allocations(pool, editable_alloc_df, current_monday_date):
    # ... (Implementation as before)
    st.success("Project allocations saved placeholder")

def display_admin_panel(pool, current_office_time, current_monday_date): # Pass current_office_time for dynamic dates
    with st.expander("🔐 Admin Controls"):
        # ... (Password check and other admin controls as before)
        st.success("Admin access granted placeholder")
        # Example call to admin_edit_data
        team_pref_col_config = {
            "team_name": {"name": "Team", "type": str}, "contact_person": {"name": "Contact", "type": str}, 
            "team_size": {"name": "Size", "type": int}, "preferred_days": {"name": "Days", "type": str}, 
            "submission_time": {"name": "Submitted At", "type": datetime}
        } # Ensure this matches your DB
        admin_edit_data(pool, get_preferences_data, "edit_teams", "weekly_preferences", team_pref_col_config)


def display_team_preference_form(pool, now_in_tz):
    st.header("📝 Request Project Room (Teams 3-6)")
    # Use dynamic submission window string
    st.caption(get_dynamic_submission_window_str(now_in_tz, type="team") + 
               f" | Current office time: {now_in_tz.strftime('%A %H:%M')}")
    with st.form("team_form"):
        # ... (form fields as before)
        name = st.text_input("Team Name")
        contact = st.text_input("Contact Person")
        size = st.number_input("Team Size", min_value=3, max_value=6, value=4)
        choice = st.selectbox("Preferred Days", ["Monday and Wednesday", "Tuesday and Thursday"])
        submitted = st.form_submit_button("Submit Team Preference")
        if submitted:
            if not all([name, contact]): st.error("❌ Fill in Team Name & Contact.")
            else:
                day_map = {"Monday and Wednesday": "Monday,Wednesday", "Tuesday and Thursday": "Tuesday,Thursday"}
                if insert_team_preference(pool, name.strip(), contact.strip(), size, day_map[choice]):
                    st.success(f"✅ Preference for '{name}' submitted/updated!"); st.balloons()

def display_oasis_preference_form(pool, now_in_tz):
    st.header("🌿 Reserve Oasis Seat (Individual)")
    # Use dynamic submission window string
    st.caption(get_dynamic_submission_window_str(now_in_tz, type="oasis") +
               f" | Current office time: {now_in_tz.strftime('%A %H:%M')}")
    with st.form("oasis_form"):
        # ... (form fields as before)
        person = st.text_input("Your Name")
        selected_days = st.multiselect("Select Preferred Days (up to 5):", DAYS_OF_WEEK, max_selections=5)
        submitted = st.form_submit_button("Submit Oasis Preference")
        if submitted:
            if not person: st.error("❌ Enter your name.")
            elif not selected_days: st.error("❌ Select at least 1 day.")
            else:
                # ... (submission logic as before)
                st.success(f"Oasis preference for {person} submitted placeholder"); st.balloons()


def display_project_room_allocations(pool, current_monday_date):
    st.header("📌 Project Room Allocations")
    alloc_df = get_room_grid_data(pool, current_monday_date)
    if alloc_df.empty or "Room" not in alloc_df.columns:
        st.write("No project room allocations or malformed data.")
    else:
        st.dataframe(alloc_df.set_index("Room"), use_container_width=True)

def display_add_to_oasis_form(pool, current_monday_date, oasis_capacity_val):
    st.header("🚶 Add Yourself to Oasis (Direct - if space available)")
    # ... (Implementation as before)
    st.write("Direct Oasis add form placeholder.")

def display_oasis_overview_matrix(pool, current_monday_date, oasis_capacity_val):
    st.header("📊 Full Weekly Oasis Overview & Manual Edit")
    # ... (Implementation as in previous correct version, ensuring it's full width)
    st.write("Oasis overview matrix placeholder.")
    # Availability summary below matrix
    st.markdown("---")
    st.subheader("🪑 Oasis Availability Summary (Current)")
    # ... (availability summary logic)
    st.markdown("Example: **Monday**: 10 spots left, **Tuesday**: 8 spots left...")


# --- Main Application ---
def main():
    st.set_page_config(page_title=PAGE_TITLE, layout="wide") # Layout wide is fine, content flow makes it full-width
    
    pool = get_db_connection_pool()
    if pool is None:
        st.error("Database connection failed. App cannot proceed."); return

    now_in_office_tz = datetime.now(OFFICE_TIMEZONE)
    current_monday_date = get_current_monday(now_in_office_tz) # Use now_in_office_tz
    current_office_time_str = now_in_office_tz.strftime('%Y-%m-%d %H:%M:%S')

    display_header_and_info(current_office_time_str)
    display_admin_panel(pool, now_in_office_tz, current_monday_date) # Pass now_in_office_tz
    
    st.divider()

    # Sections will now appear one below the other, full-width
    display_team_preference_form(pool, now_in_office_tz) # Pass now_in_office_tz
    st.divider()
    display_oasis_preference_form(pool, now_in_office_tz) # Pass now_in_office_tz
    st.divider()
    display_add_to_oasis_form(pool, current_monday_date, OASIS_CAPACITY)
    st.divider()
    display_project_room_allocations(pool, current_monday_date)
    st.divider()
    display_oasis_overview_matrix(pool, current_monday_date, OASIS_CAPACITY)

if __name__ == "__main__":
    main()