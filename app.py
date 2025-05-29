import streamlit as st
import psycopg2
import psycopg2.pool
import psycopg2.extras # For RealDictCursor
import json
import os
from datetime import datetime, timedelta
import pytz
import pandas as pd
import contextlib
import traceback

# --- Local Imports (Assuming allocate_rooms.py is in the same directory) ---
# Ensure this file exists and is correctly implemented
try:
    from allocate_rooms import run_allocation
except ImportError:
    st.error("Failed to import `run_allocation`. Ensure `allocate_rooms.py` is in the same directory.")
    # Provide a dummy function if it's missing, so the app can partially load
    def run_allocation(db_url, only=None):
        st.warning(f"Dummy `run_allocation` called for {only}. `allocate_rooms.py` might be missing.")
        return False, "Dummy allocation result"

# --- Constants and Configuration ---
PAGE_TITLE = "Weekly Room Allocator"
DATABASE_URL = st.secrets.get("SUPABASE_DB_URI", os.environ.get("SUPABASE_DB_URI"))
OFFICE_TIMEZONE_STR = st.secrets.get("OFFICE_TIMEZONE", os.environ.get("OFFICE_TIMEZONE", "Europe/Amsterdam")) # Defaulting to a common European timezone
RESET_PASSWORD = st.secrets.get("ADMIN_RESET_PASSWORD", "trainee") # It's better to get passwords from secrets

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOMS_FILE = os.path.join(BASE_DIR, 'rooms.json')

OASIS_ROOM_NAME = "Oasis"
VACANT_STATUS = "Vacant"
DAYS_OF_WEEK = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
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
# Ensure OASIS_CAPACITY is an integer
try:
    OASIS_CAPACITY = int(OASIS_ROOM_DETAILS["capacity"])
except ValueError:
    st.error(f"Invalid capacity format for Oasis room in rooms.json: '{OASIS_ROOM_DETAILS['capacity']}'. Defaulting to 15.")
    OASIS_CAPACITY = 15

ALL_ROOM_NAMES_EXCL_OASIS = [r["name"] for r in AVAILABLE_ROOMS if r["name"] != OASIS_ROOM_NAME]


# --- Database Connection Management ---
@st.cache_resource
def get_db_connection_pool():
    """Initializes and returns a simple connection pool."""
    if not DATABASE_URL:
        st.error("DATABASE_URL is not set. Please configure it in secrets or environment variables.")
        return None
    try:
        return psycopg2.pool.SimpleConnectionPool(1, 25, dsn=DATABASE_URL)
    except Exception as e:
        st.error(f"Failed to create database connection pool: {e}")
        return None

@contextlib.contextmanager
def manage_db_connection(pool):
    """Context manager for acquiring and releasing a database connection."""
    if pool is None:
        raise ConnectionError("Database connection pool is not initialized.")
    conn = None
    try:
        conn = pool.getconn()
        yield conn
    finally:
        if conn:
            pool.putconn(conn)

# --- Helper Functions ---
def get_current_monday(timezone):
    """Returns the date of the Monday of the current week in the given timezone."""
    today = datetime.now(timezone).date()
    return today - timedelta(days=today.weekday())

# --- Database Interaction Functions ---
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
            # For SELECT queries that might return a single value or row count
            if cur.description:
                try:
                    return cur.fetchall() # Or fetchone() if appropriate
                except psycopg2.ProgrammingError: # No results to fetch
                    return None
            return None


def get_room_grid_data(pool, current_monday):
    day_mapping = {current_monday + timedelta(days=i): PROJECT_TEAM_DAYS[i] for i in range(len(PROJECT_TEAM_DAYS))}
    day_labels = list(day_mapping.values())

    grid = {room: {**{"Room": room}, **{day: VACANT_STATUS for day in day_labels}} for room in ALL_ROOM_NAMES_EXCL_OASIS}

    allocations_query = "SELECT team_name, room_name, date FROM weekly_allocations WHERE room_name != %s"
    allocations = fetch_all_from_db(pool, allocations_query, (OASIS_ROOM_NAME,))

    contacts_query = "SELECT team_name, contact_person FROM weekly_preferences"
    contacts_raw = fetch_all_from_db(pool, contacts_query)
    contacts = {row["team_name"]: row["contact_person"] for row in contacts_raw}

    for row in allocations:
        team, room, date_obj = row["team_name"], row["room_name"], row["date"]
        day = day_mapping.get(date_obj)
        if room not in grid or not day:
            continue
        contact = contacts.get(team)
        grid[room][day] = f"{team} ({contact})" if contact else team
    return pd.DataFrame(grid.values())


def get_oasis_grid_data(pool):
    query = "SELECT team_name, date FROM weekly_allocations WHERE room_name = %s"
    data = fetch_all_from_db(pool, query, (OASIS_ROOM_NAME,))
    if not data: return pd.DataFrame()

    df = pd.DataFrame(data, columns=["Person", "Date"])
    df["Date"] = pd.to_datetime(df["Date"])
    df["Day"] = df["Date"].dt.strftime('%A')
    
    # SYNTAX FIX APPLIED HERE
    grouped = df.groupby("Day")["Person"].apply(lambda x: ", ".join(sorted(set(str(p) for p in x)))) # Ensure person names are strings for sorting
    grouped = grouped.reindex(DAYS_OF_WEEK, fill_value=VACANT_STATUS).reset_index()
    return grouped.rename(columns={"Day": "Weekday", "Person": "People"})


def get_preferences_data(pool, table_name, columns):
    query = f"SELECT {', '.join(columns)} FROM {table_name}"
    try:
        rows = fetch_all_from_db(pool, query)
        # Ensure column names for DataFrame are simple strings
        df_cols = [col.split('.')[-1].split(' ')[-1].title() for col in columns]
        return pd.DataFrame(rows, columns=df_cols) 
    except Exception as e:
        st.warning(f"Failed to fetch {table_name.replace('_', ' ')}: {e}")
        return pd.DataFrame(columns=[col.split('.')[-1].split(' ')[-1].title() for col in columns])


def insert_team_preference(pool, team, contact, size, days_str):
    if not (3 <= size <= 6):
        st.error("❌ Team size must be between 3 and 6.")
        return False
    
    new_days_set = set(days_str.split(','))
    valid_day_pairs = [{"Monday", "Wednesday"}, {"Tuesday", "Thursday"}]
    if new_days_set not in valid_day_pairs:
        st.error("❌ Must select Monday & Wednesday or Tuesday & Thursday.")
        return False

    # Check existing preferences to prevent exceeding 2 days
    existing_query = "SELECT preferred_days FROM weekly_preferences WHERE team_name = %s"
    existing_prefs_rows = fetch_all_from_db(pool, existing_query, (team,)) # Will be list of dicts
    voted_days = set()
    if existing_prefs_rows:
        for row_dict in existing_prefs_rows:
            if row_dict and 'preferred_days' in row_dict and row_dict['preferred_days']:
                 voted_days.update(row_dict['preferred_days'].split(','))
            
    if len(voted_days) >= 2 and not new_days_set.issubset(voted_days) : # Allow re-submitting same days
         if len(voted_days.union(new_days_set)) > 2:
            st.error(f"❌ Max 2 days allowed per team. You have already voted for: {', '.join(voted_days) if voted_days else 'None'}.")
            return False

    insert_query = """
        INSERT INTO weekly_preferences (team_name, contact_person, team_size, preferred_days, submission_time)
        VALUES (%s, %s, %s, %s, NOW() AT TIME ZONE %s)
        ON CONFLICT (team_name) DO UPDATE SET
        contact_person = EXCLUDED.contact_person,
        team_size = EXCLUDED.team_size,
        preferred_days = EXCLUDED.preferred_days,
        submission_time = NOW() AT TIME ZONE %s;
    """
    try:
        execute_db_query(pool, insert_query, (team, contact, size, days_str, OFFICE_TIMEZONE_STR, OFFICE_TIMEZONE_STR), commit=True)
        return True
    except Exception as e:
        st.error(f"❌ Preference insert/update failed: {e}")
        return False

def reset_table_data(pool, table_name, condition_column=None, condition_value=None, negate_condition=False):
    query = f"DELETE FROM {table_name}"
    params = None
    if condition_column and condition_value is not None: # Ensure condition_value can be False or 0
        operator = "!=" if negate_condition else "="
        query += f" WHERE {condition_column} {operator} %s"
        params = (condition_value,)
    try:
        execute_db_query(pool, query, params, commit=True)
        st.success(f"✅ Data from '{table_name}' (where {condition_column} {operator} {condition_value if params else ''}) removed.")
        return True
    except Exception as e:
        st.error(f"❌ Failed to remove data from {table_name}: {e}")
        return False


# --- UI Rendering Functions ---
def display_header_and_info(office_timezone_str, current_office_time):
    st.title(f"📅 {PAGE_TITLE} for TS")
    st.info(f"Current Office Time: **{current_office_time.strftime('%Y-%m-%d %H:%M:%S')}** ({office_timezone_str})")
    
    st.info(f"""
    💡 **How This Works:**

    - 🧑‍🤝‍🧑 **Project Teams (3-6 people):**
        - Can select **either Monday & Wednesday** or **Tuesday & Thursday**.
        - Friday is flexible (no formal allocation via this tool for project teams).
        - Submit preferences from **Wednesday 09:00** until **Thursday 16:00**.
        - Allocations shared on **Thursday at 16:00**.
    - 🌿 **Oasis Users (Individual Bookings):**
        - Can choose **up to 5 preferred weekdays**.
        - Allocation is random and fair, aiming for at least one preferred day.
        - Oasis capacity: **{OASIS_CAPACITY}** spots.
        - Submit preferences from **Wednesday 09:00** until **Friday 16:00**.
        - Allocation done on **Friday at 16:00**.
    - ❗ **Important:** Team preferences can be updated. Oasis preferences are one-time (contact admin for changes).
    - ✅ Allocations are refreshed **weekly** by an admin.

    ---

    ### 🌿 Oasis: How to Join

    1.  **✅ Reserve Oasis Seat (Recommended before Friday 16:00)**
        ➤ Submit your **preferred days** (up to 5).
        ➤ Allocation is done **automatically and fairly** at **Friday 16:00**.

    2.  **⚠️ Add Yourself to Oasis Allocation (Use if you missed the deadline)**
        ➤ Use this **only if you missed the Friday 16:00 deadline** or forgot to submit preferences.
        ➤ You will be added **immediately** to selected days **if space is left**. This bypasses fairness.
    """)

def admin_allocation_controls(pool):
    st.subheader("🧠 Allocation Triggers")
    col1, col2 = st.columns(2)
    with col1:
        if st.button("🚀 Run Project Room Allocation", key="run_project_alloc"):
            success, _ = run_allocation(DATABASE_URL, only="project")
            st.success("✅ Project room allocation completed.") if success else st.error("❌ Project room allocation failed.")
    with col2:
        if st.button("🎲 Run Oasis Allocation", key="run_oasis_alloc"):
            success, _ = run_allocation(DATABASE_URL, only="oasis")
            st.success("✅ Oasis allocation completed.") if success else st.error("❌ Oasis allocation failed.")

def admin_edit_data(pool, data_fetch_func, df_key, update_table_name, column_map_config, current_monday=None, delete_condition_col=None, delete_condition_val=None):
    st.subheader(f"📝 Edit {update_table_name.replace('_', ' ').title()}")
    
    # Use a consistent way to get DataFrame column names for data_editor
    df_display_cols = [details['name'] for details in column_map_config.values()]
    
    # Fetch data using the raw DB column names expected by get_preferences_data
    raw_db_cols = list(column_map_config.keys())
    df = data_fetch_func(pool, raw_db_cols) if not current_monday else data_fetch_func(pool, raw_db_cols, current_monday) # Adjust if current_monday is used

    if df is None or df.empty:
        st.info(f"No {update_table_name.replace('_', ' ')} yet to edit.")
        return

    # Ensure DataFrame has columns as per display names for st.data_editor
    # This mapping needs to be robust if get_preferences_data changes column names
    # For now, assume get_preferences_data returns df with cols matching raw_db_cols that then get titled
    # Let's standardise: get_preferences_data returns with raw_db_cols, we rename for display
    df_for_editor = df.copy()
    rename_map = {raw_col: details['name'] for raw_col, details in column_map_config.items()}
    df_for_editor.rename(columns=rename_map, inplace=True)
    
    editable_df = st.data_editor(df_for_editor, num_rows="dynamic", use_container_width=True, key=df_key)

    if st.button(f"💾 Save {update_table_name.replace('_', ' ').title()} Changes", key=f"save_{df_key}"):
        try:
            with manage_db_connection(pool) as conn:
                with conn.cursor() as cur:
                    delete_query = f"DELETE FROM {update_table_name}"
                    del_params = []
                    if delete_condition_col and delete_condition_val is not None:
                        delete_query += f" WHERE {delete_condition_col} = %s"
                        del_params.append(delete_condition_val)
                    cur.execute(delete_query, tuple(del_params) if del_params else None)

                    insert_db_cols = list(column_map_config.keys())
                    placeholders = ', '.join(['%s'] * len(insert_db_cols))
                    insert_query = f"INSERT INTO {update_table_name} ({', '.join(insert_db_cols)}) VALUES ({placeholders})"
                    
                    for _, row in editable_df.iterrows():
                        values = []
                        for db_col, details in column_map_config.items():
                            df_col_name = details['name'] # Name used in data_editor
                            val = row.get(df_col_name)
                            val_type = details.get('type', str)
                            
                            if pd.isnull(val): values.append(None)
                            elif val_type == int: values.append(int(val))
                            elif val_type == datetime : values.append(val if isinstance(val, datetime) else pd.to_datetime(val).to_pydatetime())
                            elif val_type == bool: values.append(bool(val))
                            else: values.append(str(val))
                        cur.execute(insert_query, tuple(values))
                conn.commit()
            st.success(f"✅ {update_table_name.replace('_', ' ').title()} updated.")
            st.rerun()
        except Exception as e:
            st.error(f"❌ Failed to save {update_table_name.replace('_', ' ').title()}: {e}")


def admin_save_project_allocations(pool, editable_alloc_df, current_monday):
    try:
        with manage_db_connection(pool) as conn:
            with conn.cursor() as cur:
                # Clear only non-Oasis allocations
                cur.execute("DELETE FROM weekly_allocations WHERE room_name != %s", (OASIS_ROOM_NAME,))
                
                for _, row in editable_alloc_df.iterrows():
                    room_val = str(row["Room"]) if pd.notnull(row["Room"]) else None
                    if not room_val: continue

                    for day_idx, day_name in enumerate(PROJECT_TEAM_DAYS): # Only Mon-Thu for project rooms
                        cell_value = row.get(day_name, "")
                        if cell_value and str(cell_value).strip() != VACANT_STATUS:
                            team_info = str(cell_value).split("(")[0].strip() 
                            if team_info:
                                date_obj = current_monday + timedelta(days=day_idx)
                                cur.execute(
                                    "INSERT INTO weekly_allocations (team_name, room_name, date) VALUES (%s, %s, %s)",
                                    (team_info, room_val, date_obj)
                                )
                conn.commit()
        st.success("✅ Manual project room allocations updated.")
        st.rerun()
    except Exception as e:
        st.error(f"❌ Failed to save project room allocations: {e}")


def display_admin_panel(pool, office_timezone, current_monday):
    with st.expander("🔐 Admin Controls"):
        pwd = st.text_input("Enter admin password:", type="password", key="admin_pwd")
        if not pwd: return
        if pwd != RESET_PASSWORD:
            st.error("❌ Incorrect password.")
            return
        
        st.success("✅ Access granted.")
        admin_allocation_controls(pool)

        st.subheader("📌 Edit Project Room Allocations")
        alloc_df_grid = get_room_grid_data(pool, current_monday) # This returns df with "Room" and day names
        if not alloc_df_grid.empty:
            # Make "Room" the index for better display if desired, or keep as column
            editable_alloc = st.data_editor(alloc_df_grid.set_index("Room") if "Room" in alloc_df_grid else alloc_df_grid, 
                                            num_rows="dynamic", use_container_width=True, key="edit_allocations")
            if st.button("💾 Save Project Room Allocation Changes", key="save_project_alloc_changes"):
                admin_save_project_allocations(pool, editable_alloc.reset_index(), current_monday) # Pass DataFrame with "Room" as column
        else:
            st.info("No project room allocations yet to edit.")

        st.subheader("🧹 Reset Data")
        col_reset1, col_reset2 = st.columns(2)
        with col_reset1:
            if st.button("🗑️ Remove Project Room Allocations (Non-Oasis)", key="reset_proj_alloc"):
                reset_table_data(pool, "weekly_allocations", "room_name", OASIS_ROOM_NAME, negate_condition=True)
            if st.button("🧽 Remove All Project Room Preferences", key="reset_proj_pref"):
                reset_table_data(pool, "weekly_preferences")
        with col_reset2:
            if st.button("🗑️ Remove Oasis Allocations", key="reset_oasis_alloc"):
                reset_table_data(pool, "weekly_allocations", "room_name", OASIS_ROOM_NAME)
            if st.button("🧽 Remove All Oasis Preferences", key="reset_oasis_pref"):
                reset_table_data(pool, "oasis_preferences")
        
        team_pref_col_config = {
            "team_name": {"name": "Team", "type": str}, 
            "contact_person": {"name": "Contact", "type": str}, 
            "team_size": {"name": "Size", "type": int}, 
            "preferred_days": {"name": "Days", "type": str}, 
            "submission_time": {"name": "Submitted At", "type": datetime}
        }
        admin_edit_data(pool, lambda p, cols: get_preferences_data(p, "weekly_preferences", cols), 
                        "edit_teams", "weekly_preferences", team_pref_col_config)
        
        oasis_pref_col_config = {
            "person_name": {"name": "Person", "type": str}, 
            "preferred_day_1": {"name": "Day 1", "type": str}, 
            "preferred_day_2": {"name": "Day 2", "type": str}, 
            "preferred_day_3": {"name": "Day 3", "type": str}, 
            "preferred_day_4": {"name": "Day 4", "type": str}, 
            "preferred_day_5": {"name": "Day 5", "type": str}, 
            "submission_time": {"name": "Submitted At", "type": datetime}
        }
        admin_edit_data(pool, lambda p, cols: get_preferences_data(p, "oasis_preferences", cols), 
                        "edit_oasis", "oasis_preferences", oasis_pref_col_config)


def display_team_preference_form(pool):
    st.header("📝 Request Project Room (Teams 3-6)")
    st.caption(f"Submit between Wednesday 09:00 - Thursday 16:00 for the upcoming week. Current office time: {datetime.now(OFFICE_TIMEZONE).strftime('%A %H:%M')}")
    with st.form("team_form"):
        name = st.text_input("Team Name")
        contact = st.text_input("Contact Person")
        size = st.number_input("Team Size", min_value=3, max_value=6, value=4)
        choice = st.selectbox("Preferred Days", ["Monday and Wednesday", "Tuesday and Thursday"])
        
        submitted = st.form_submit_button("Submit Team Preference")
        if submitted:
            if not all([name, contact]):
                st.error("❌ Please fill in Team Name and Contact Person.")
            else:
                day_map = {"Monday and Wednesday": "Monday,Wednesday", "Tuesday and Thursday": "Tuesday,Thursday"}
                if insert_team_preference(pool, name.strip(), contact.strip(), size, day_map[choice]):
                    st.success(f"✅ Preference for '{name}' submitted/updated successfully!")
                    st.balloons()
                    # Consider st.rerun() if immediate reflection is needed elsewhere

def display_oasis_preference_form(pool):
    st.header("🌿 Reserve Oasis Seat (Individual)")
    st.caption(f"Submit between Wednesday 09:00 - Friday 16:00 for the upcoming week. Current office time: {datetime.now(OFFICE_TIMEZONE).strftime('%A %H:%M')}")
    with st.form("oasis_form"):
        person = st.text_input("Your Name")
        selected_days = st.multiselect("Select Your Preferred Days (up to 5):", DAYS_OF_WEEK, max_selections=5)
        
        submitted = st.form_submit_button("Submit Oasis Preference")
        if submitted:
            if not person: st.error("❌ Please enter your name.")
            elif not selected_days: st.error("❌ Select at least 1 preferred day.")
            else:
                person_stripped = person.strip()
                existing_check = fetch_all_from_db(pool, "SELECT 1 FROM oasis_preferences WHERE person_name = %s", (person_stripped,))
                if existing_check:
                    st.error(f"❌ '{person_stripped}' has already submitted. Contact admin to change.")
                else:
                    padded_days = selected_days + [None] * (5 - len(selected_days))
                    insert_query = """
                        INSERT INTO oasis_preferences (person_name, preferred_day_1, preferred_day_2, preferred_day_3, preferred_day_4, preferred_day_5, submission_time)
                        VALUES (%s, %s, %s, %s, %s, %s, NOW() AT TIME ZONE %s)
                    """
                    try:
                        execute_db_query(pool, insert_query, (person_stripped, *padded_days, OFFICE_TIMEZONE_STR), commit=True)
                        st.success(f"✅ Oasis preference for '{person_stripped}' submitted!")
                        st.balloons()
                    except Exception as e:
                        st.error(f"❌ Failed to save Oasis preference: {e}")


def display_project_room_allocations(pool, current_monday):
    st.header("📌 Project Room Allocations")
    alloc_df = get_room_grid_data(pool, current_monday)
    if alloc_df.empty or "Room" not in alloc_df.columns: # Check if "Room" column exists
        st.write("No project room allocations yet for this week, or data is malformed.")
    else:
        st.dataframe(alloc_df.set_index("Room"), use_container_width=True)


def display_add_to_oasis_form(pool, current_monday, oasis_capacity_val):
    st.header("🚶 Add Yourself to Oasis (Direct - if space available)")
    st.caption("Use this if you missed the preference deadline. Availability is not guaranteed.")
    with st.form("oasis_add_form"):
        user_name = st.text_input("Your Name")
        selected_days_add = st.multiselect("Select day(s) to add yourself:", DAYS_OF_WEEK, key="add_oasis_days")
        
        submitted = st.form_submit_button("➕ Add Me to Oasis Schedule")
        if submitted:
            if not user_name.strip(): st.error("❌ Please enter your name.")
            elif not selected_days_add: st.error("❌ Select at least one day.")
            else:
                name_clean = user_name.strip().title()
                try:
                    with manage_db_connection(pool) as conn:
                        with conn.cursor() as cur:
                            all_successful = True
                            days_added_count = 0
                            for day_str in selected_days_add:
                                date_obj = current_monday + timedelta(days=DAYS_OF_WEEK.index(day_str))
                                # Check if already added for this day to prevent duplicates from this form
                                cur.execute(
                                    "SELECT 1 FROM weekly_allocations WHERE room_name = %s AND team_name = %s AND date = %s",
                                    (OASIS_ROOM_NAME, name_clean, date_obj)
                                )
                                if cur.fetchone():
                                    st.info(f"'{name_clean}' is already scheduled for Oasis on {day_str}.")
                                    continue

                                cur.execute(
                                    "SELECT COUNT(*) FROM weekly_allocations WHERE room_name = %s AND date = %s",
                                    (OASIS_ROOM_NAME, date_obj)
                                )
                                count = cur.fetchone()[0]
                                if count >= oasis_capacity_val:
                                    st.warning(f"⚠️ Oasis is full on {day_str}. '{name_clean}' not added for this day.")
                                    all_successful = False
                                else:
                                    cur.execute(
                                        "INSERT INTO weekly_allocations (team_name, room_name, date) VALUES (%s, %s, %s)",
                                        (name_clean, OASIS_ROOM_NAME, date_obj)
                                    )
                                    days_added_count +=1
                            conn.commit()
                            if days_added_count > 0 : st.success(f"✅ '{name_clean}' processed for selected day(s) in Oasis!")
                            if not all_successful and selected_days_add : st.info("Partial success due to capacity. See warnings above.")
                            elif not selected_days_add and not user_name.strip() : pass # No action if no days/name
                            else: st.info("Processing complete for Oasis direct add.")
                            st.rerun()
                except Exception as e:
                    st.error(f"❌ Error adding to Oasis: {e}")


def display_oasis_overview_matrix(pool, current_monday, oasis_capacity_val):
    st.header("📊 Full Weekly Oasis Overview & Manual Edit")
    
    NIEK_USER = "Niek" 

    try:
        with manage_db_connection(pool) as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT team_name, date FROM weekly_allocations WHERE room_name = %s", (OASIS_ROOM_NAME,))
                rows = cur.fetchall()

        df_alloc = pd.DataFrame(rows, columns=["Name", "Date"]) if rows else pd.DataFrame(columns=["Name", "Date"])
        
        # Ensure all names are strings before processing
        if not df_alloc.empty:
            df_alloc["Name"] = df_alloc["Name"].astype(str)
            df_alloc["Date"] = pd.to_datetime(df_alloc["Date"]).dt.date
        
        name_list_for_set = [str(n) for n in df_alloc["Name"].tolist()] + [str(NIEK_USER)]
        unique_names = sorted(list(set(name_list_for_set)))
        
        day_dates = [current_monday + timedelta(days=i) for i in range(len(DAYS_OF_WEEK))]
        matrix_df = pd.DataFrame(False, index=unique_names, columns=DAYS_OF_WEEK)

        for _, row in df_alloc.iterrows():
            if row["Date"] in day_dates: # row["Date"] is datetime.date
                day_name_idx = day_dates.index(row["Date"])
                day_name = DAYS_OF_WEEK[day_name_idx]
                # row["Name"] is already string here
                if row["Name"] in matrix_df.index: # matrix_df.index are strings
                     matrix_df.at[row["Name"], day_name] = True
        
        if NIEK_USER in matrix_df.index: # NIEK_USER is string
            matrix_df.loc[NIEK_USER, :] = True 

        st.subheader("🪑 Oasis Availability Summary")
        cols_avail = st.columns(len(DAYS_OF_WEEK))
        for idx, day_name in enumerate(DAYS_OF_WEEK):
            current_date_for_summary = day_dates[idx]
            # Filter df_alloc for current day; Name column is string
            signed_up_count = df_alloc[df_alloc["Date"] == current_date_for_summary].shape[0]
            spots_left = max(0, oasis_capacity_val - signed_up_count)
            cols_avail[idx].metric(label=day_name, value=f"{spots_left} spots")

        st.subheader("✍️ Edit Oasis Roster")
        edited_matrix = st.data_editor(
            matrix_df,
            use_container_width=True,
            disabled=[NIEK_USER] if NIEK_USER in matrix_df.index else [],
            key="oasis_matrix_editor"
        )

        if st.button("💾 Save Oasis Matrix Changes", key="save_oasis_matrix"):
            try:
                with manage_db_connection(pool) as conn_save:
                    with conn_save.cursor() as cur_save:
                        # Clear existing Oasis allocations for the week, except Niek's if he's managed specially
                        cur_save.execute("DELETE FROM weekly_allocations WHERE room_name = %s AND team_name != %s", (OASIS_ROOM_NAME, str(NIEK_USER)))
                        # Or if Niek is also managed by this matrix (and team_name!=NIEK_USER is removed):
                        # cur_save.execute("DELETE FROM weekly_allocations WHERE room_name = %s", (OASIS_ROOM_NAME,))

                        if str(NIEK_USER) in edited_matrix.index: # NIEK_USER is string
                             for day_idx_niek, day_name_niek in enumerate(DAYS_OF_WEEK):
                                if edited_matrix.at[str(NIEK_USER), day_name_niek]: 
                                    date_obj_niek = current_monday + timedelta(days=day_idx_niek)
                                    cur_save.execute(
                                        "INSERT INTO weekly_allocations (team_name, room_name, date) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                                        (str(NIEK_USER), OASIS_ROOM_NAME, date_obj_niek)
                                    )
                        
                        # Calculate initial daily_counts including Niek if he was just re-added
                        daily_counts = {}
                        for day_idx_count, day_name_count in enumerate(DAYS_OF_WEEK):
                            date_obj_count = current_monday + timedelta(days=day_idx_count)
                            cur_save.execute("SELECT COUNT(*) FROM weekly_allocations WHERE room_name = %s AND date = %s", (OASIS_ROOM_NAME, date_obj_count))
                            current_db_count = cur_save.fetchone()[0]
                            daily_counts[day_name_count] = current_db_count
                        
                        for person_name_iter, attendance_series in edited_matrix.iterrows():
                            person_name_str = str(person_name_iter) # Ensure person_name is string
                            if person_name_str == str(NIEK_USER): continue 

                            for day_name_save, is_attending_save in attendance_series.items():
                                if is_attending_save:
                                    day_idx_save = DAYS_OF_WEEK.index(day_name_save)
                                    date_obj_save = current_monday + timedelta(days=day_idx_save)
                                    
                                    if daily_counts[day_name_save] < oasis_capacity_val:
                                        # Check if this specific user for this day already exists from Niek's special insert
                                        cur_save.execute("SELECT 1 FROM weekly_allocations WHERE team_name = %s AND room_name = %s AND date = %s",
                                                         (person_name_str, OASIS_ROOM_NAME, date_obj_save))
                                        if not cur_save.fetchone(): # Only insert if not already there
                                            cur_save.execute(
                                                "INSERT INTO weekly_allocations (team_name, room_name, date) VALUES (%s, %s, %s)",
                                                (person_name_str, OASIS_ROOM_NAME, date_obj_save)
                                            )
                                            daily_counts[day_name_save] += 1
                                    else:
                                        # Check if this person is already in DB for this day (e.g. was Niek and matrix kept him)
                                        cur_save.execute("SELECT 1 FROM weekly_allocations WHERE team_name = %s AND room_name = %s AND date = %s",
                                                         (person_name_str, OASIS_ROOM_NAME, date_obj_save))
                                        if not cur_save.fetchone():
                                            st.warning(f"⚠️ Oasis full on {day_name_save}. '{person_name_str}' could not be added.")
                        conn_save.commit()
                st.success("✅ Oasis matrix saved.")
                st.rerun()
            except Exception as e_save:
                st.error(f"❌ Failed to save Oasis matrix: {e_save}")

    except Exception as e_load:
        st.error(f"❌ Error loading Oasis overview matrix: {e_load}")
        st.text(traceback.format_exc())


# --- Main Application ---
def main():
    st.set_page_config(page_title=PAGE_TITLE, layout="wide")
    
    pool = get_db_connection_pool()
    if pool is None:
        st.error("Database connection could not be established. Application cannot proceed.")
        return

    current_office_time = datetime.now(OFFICE_TIMEZONE)
    current_monday_date = get_current_monday(OFFICE_TIMEZONE)

    display_header_and_info(OFFICE_TIMEZONE_STR, current_office_time)
    display_admin_panel(pool, OFFICE_TIMEZONE, current_monday_date)
    
    st.divider()
    col1_forms, col2_allocs = st.columns(2) 

    with col1_forms:
        display_team_preference_form(pool)
        st.divider()
        display_oasis_preference_form(pool)
        st.divider()
        display_add_to_oasis_form(pool, current_monday_date, OASIS_CAPACITY)

    with col2_allocs: 
        display_project_room_allocations(pool, current_monday_date)
        st.divider()
        display_oasis_overview_matrix(pool, current_monday_date, OASIS_CAPACITY)


if __name__ == "__main__":
    main()