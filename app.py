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
OASIS_CAPACITY = OASIS_ROOM_DETAILS["capacity"]
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
    
    grouped = df.groupby("Day")["Person"].apply(lambda x: ", ".join(sorted(set(x))))
    grouped = grouped.reindex(DAYS_OF_WEEK, fill_value=VACANT_STATUS).reset_index()
    return grouped.rename(columns={"Day": "Weekday", "Person": "People"})


def get_preferences_data(pool, table_name, columns):
    query = f"SELECT {', '.join(columns)} FROM {table_name}"
    try:
        rows = fetch_all_from_db(pool, query)
        return pd.DataFrame(rows, columns=[col.split(' ')[-1].title() for col in columns]) # Basic title casing for display
    except Exception as e:
        st.warning(f"Failed to fetch {table_name.replace('_', ' ')}: {e}")
        return pd.DataFrame(columns=[col.title() for col in columns])


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
    existing_prefs = fetch_all_from_db(pool, existing_query, (team,))
    voted_days = set(d for row in existing_prefs for d in row['preferred_days'].split(',')) if existing_prefs else set()

    if len(voted_days) >= 2 or len(voted_days.union(new_days_set)) > 2:
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

def reset_table_data(pool, table_name, condition_column=None, condition_value=None):
    query = f"DELETE FROM {table_name}"
    params = None
    if condition_column and condition_value:
        query += f" WHERE {condition_column} = %s"
        params = (condition_value,)
    try:
        execute_db_query(pool, query, params, commit=True)
        st.success(f"✅ Data from '{table_name}' (partially) removed.")
        return True
    except Exception as e:
        st.error(f"❌ Failed to remove data from {table_name}: {e}")
        return False


# --- UI Rendering Functions ---
def display_header_and_info(office_timezone_str, current_office_time):
    st.title(f"📅 {PAGE_TITLE} for TS")
    st.info(f"Current Office Time: **{current_office_time.strftime('%Y-%m-%d %H:%M:%S')}** ({office_timezone_str})")
    
    # Combined How-To and Oasis Info
    st.info("""
    💡 **How This Works:**

    - 🧑‍🤝‍🧑 **Project Teams (3-6 people):**
        - Can select **either Monday & Wednesday** or **Tuesday & Thursday**.
        - Friday is flexible (no formal allocation via this tool for project teams).
        - There are 6 rooms for 4 persons and 1 room for 6 persons (as per `rooms.json`).
        - Submit preferences from **Wednesday 09:00** until **Thursday 16:00**.
        - Allocations shared on **Thursday at 16:00**.
    - 🌿 **Oasis Users (Individual Bookings):**
        - Can choose **up to 5 preferred weekdays**.
        - Allocation is random and fair, aiming for at least one preferred day.
        - Oasis capacity: **""" + str(OASIS_CAPACITY) + """** spots.
        - Submit preferences from **Wednesday 09:00** until **Friday 16:00**.
        - Allocation done on **Friday at 16:00**.
    - ❗ **Important:** You may generally submit preferences **once**. For changes, contact an admin. (Team preference form now supports updates).
    - ✅ Allocations are refreshed **weekly** by an admin.

    ---

    ### 🌿 Oasis: How to Join

    1.  **✅ Reserve Oasis Seat (Recommended before Friday 16:00)**
        ➤ Submit your **preferred days** (up to 5).
        ➤ Allocation is done **automatically and fairly** at **Friday 16:00**.
        ➤ Everyone gets **at least one** of their preferred days, depending on availability.

    2.  **⚠️ Add Yourself to Oasis Allocation (Use if you missed the deadline)**
        ➤ Use this **only if you missed the Friday 16:00 deadline** or forgot to submit preferences.
        ➤ You will be added **immediately** to the selected days **if there’s space left**.
        ➤ This option does **not guarantee fairness** and bypasses the regular preference-based allocation.
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

def admin_edit_data(pool, data_fetch_func, df_key, update_table_name, column_map, current_monday=None, delete_condition_col=None, delete_condition_val=None):
    st.subheader(f"📝 Edit {update_table_name.replace('_', ' ').title()}")
    df = data_fetch_func(pool) if not current_monday else data_fetch_func(pool, current_monday)
    
    if not df.empty:
        editable_df = st.data_editor(df, num_rows="dynamic", use_container_width=True, key=df_key)
        if st.button(f"💾 Save {update_table_name.replace('_', ' ').title()} Changes", key=f"save_{df_key}"):
            try:
                with manage_db_connection(pool) as conn:
                    with conn.cursor() as cur:
                        # Clear relevant old data
                        delete_query = f"DELETE FROM {update_table_name}"
                        params = []
                        if delete_condition_col and delete_condition_val:
                            delete_query += f" WHERE {delete_condition_col} = %s"
                            params.append(delete_condition_val)
                        cur.execute(delete_query, tuple(params) if params else None)

                        # Insert new/updated data
                        placeholders = ', '.join(['%s'] * len(column_map))
                        insert_cols = ', '.join(column_map.keys())
                        insert_query = f"INSERT INTO {update_table_name} ({insert_cols}) VALUES ({placeholders})"
                        
                        for _, row in editable_df.iterrows():
                            values = []
                            for db_col, df_col_info in column_map.items():
                                df_col_name = df_col_info['name']
                                val_type = df_col_info.get('type', str)
                                
                                if df_col_name == 'date_from_day_and_room': # Special handling for project allocations
                                    room_val = str(row["Room"]) if pd.notnull(row["Room"]) else None
                                    for day_idx, day_name in enumerate(PROJECT_TEAM_DAYS):
                                        cell_value = row.get(day_name, "")
                                        if cell_value and cell_value != VACANT_STATUS:
                                            team_info = str(cell_value).split("(")[0].strip()
                                            if team_info and room_val:
                                                date_obj = current_monday + timedelta(days=day_idx)
                                                # This needs to be structured to insert one row per allocation
                                                # The current generic admin_edit_data might be too simple for this specific case.
                                                # For now, this part is more complex and might require specific logic outside.
                                                # Let's assume for other tables it's simpler.
                                                # For project allocations, specific save logic is better.
                                                pass # Placeholder for complex case
                                    # This loop means we can't directly use the generic row insertion below for project_allocations
                                    # This specific edit needs its own save logic.
                                    st.warning("Project room allocation editing save logic is complex and needs specific implementation outside generic editor.")
                                    return # Exit if it's the complex case for now
                                else:
                                    val = row[df_col_name]
                                    if pd.isnull(val): values.append(None)
                                    elif val_type == int: values.append(int(val))
                                    elif val_type == datetime: values.append(val) # Assumes it's already datetime
                                    else: values.append(str(val))
                            
                            if not df_col_name == 'date_from_day_and_room': # Avoid re-inserting for the complex case handled above
                                cur.execute(insert_query, tuple(values))
                        conn.commit()
                st.success(f"✅ {update_table_name.replace('_', ' ').title()} updated.")
                st.rerun()
            except Exception as e:
                st.error(f"❌ Failed to save {update_table_name.replace('_', ' ').title()}: {e}")
    else:
        st.info(f"No {update_table_name.replace('_', ' ')} yet to edit.")


def admin_save_project_allocations(pool, editable_alloc_df, current_monday):
    try:
        with manage_db_connection(pool) as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM weekly_allocations WHERE room_name != %s", (OASIS_ROOM_NAME,))
                for _, row in editable_alloc_df.iterrows():
                    room_val = str(row["Room"]) if pd.notnull(row["Room"]) else None
                    if not room_val: continue

                    for day_idx, day_name in enumerate(PROJECT_TEAM_DAYS):
                        cell_value = row.get(day_name, "")
                        if cell_value and cell_value != VACANT_STATUS:
                            team_info = str(cell_value).split("(")[0].strip() # Extract team name
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

        # Project Room Allocations Editing
        st.subheader("📌 Edit Project Room Allocations")
        alloc_df = get_room_grid_data(pool, current_monday)
        if not alloc_df.empty:
            editable_alloc = st.data_editor(alloc_df, num_rows="dynamic", use_container_width=True, key="edit_allocations")
            if st.button("💾 Save Project Room Allocation Changes", key="save_project_alloc_changes"):
                admin_save_project_allocations(pool, editable_alloc, current_monday)
        else:
            st.info("No project room allocations yet to edit.")

        # Data Resets
        st.subheader("🧹 Reset Data")
        col_reset1, col_reset2 = st.columns(2)
        with col_reset1:
            if st.button("🗑️ Remove Project Room Allocations", key="reset_proj_alloc"):
                reset_table_data(pool, "weekly_allocations", "room_name", OASIS_ROOM_NAME, negate_condition=True) # Custom logic needed for not equals
            if st.button("🧽 Remove Project Room Preferences", key="reset_proj_pref"):
                reset_table_data(pool, "weekly_preferences")
        with col_reset2:
            if st.button("🗑️ Remove Oasis Allocations", key="reset_oasis_alloc"):
                reset_table_data(pool, "weekly_allocations", "room_name", OASIS_ROOM_NAME)
            if st.button("🧽 Remove Oasis Preferences", key="reset_oasis_pref"):
                reset_table_data(pool, "oasis_preferences")
        
        # Team Preferences Editing
        team_pref_cols = {"team_name": {"name": "Team"}, "contact_person": {"name": "Contact"}, "team_size": {"name": "Size", "type": int}, "preferred_days": {"name": "Days"}, "submission_time": {"name": "Submitted At", "type": datetime}}
        admin_edit_data(pool, lambda p: get_preferences_data(p, "weekly_preferences", list(team_pref_cols.keys())), "edit_teams", "weekly_preferences", team_pref_cols)
        
        # Oasis Preferences Editing
        oasis_pref_cols = {"person_name": {"name": "Person"}, "preferred_day_1": {"name": "Day 1"}, "preferred_day_2": {"name": "Day 2"}, "preferred_day_3": {"name": "Day 3"}, "preferred_day_4": {"name": "Day 4"}, "preferred_day_5": {"name": "Day 5"}, "submission_time": {"name": "Submitted At", "type": datetime}}
        admin_edit_data(pool, lambda p: get_preferences_data(p, "oasis_preferences", list(oasis_pref_cols.keys())), "edit_oasis", "oasis_preferences", oasis_pref_cols)


def display_team_preference_form(pool):
    st.header("📝 Request Project Room (Teams 3-6)")
    st.caption("Submit between Wednesday 09:00 - Thursday 16:00 for the upcoming week.") # Example, make dynamic if needed
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
                    # st.rerun() # Consider if rerun is desired here

def display_oasis_preference_form(pool):
    st.header("🌿 Reserve Oasis Seat (Individual)")
    st.caption("Submit between Wednesday 09:00 - Friday 16:00 for the upcoming week.") # Example
    with st.form("oasis_form"):
        person = st.text_input("Your Name")
        selected_days = st.multiselect("Select Your Preferred Days (up to 5):", DAYS_OF_WEEK, max_selections=5)
        
        submitted = st.form_submit_button("Submit Oasis Preference")
        if submitted:
            if not person: st.error("❌ Please enter your name.")
            elif not selected_days: st.error("❌ Select at least 1 preferred day.")
            else:
                # Check for duplicate
                existing_check = fetch_all_from_db(pool, "SELECT 1 FROM oasis_preferences WHERE person_name = %s", (person.strip(),))
                if existing_check:
                    st.error("❌ You've already submitted. Contact admin to change or use the edit feature if available.")
                else:
                    padded_days = selected_days + [None] * (5 - len(selected_days))
                    insert_query = """
                        INSERT INTO oasis_preferences (person_name, preferred_day_1, preferred_day_2, preferred_day_3, preferred_day_4, preferred_day_5, submission_time)
                        VALUES (%s, %s, %s, %s, %s, %s, NOW() AT TIME ZONE %s)
                    """
                    try:
                        execute_db_query(pool, insert_query, (person.strip(), *padded_days, OFFICE_TIMEZONE_STR), commit=True)
                        st.success(f"✅ Oasis preference for '{person}' submitted!")
                        # st.rerun()
                    except Exception as e:
                        st.error(f"❌ Failed to save Oasis preference: {e}")


def display_project_room_allocations(pool, current_monday):
    st.header("📌 Project Room Allocations")
    alloc_df = get_room_grid_data(pool, current_monday)
    if alloc_df.empty:
        st.write("No project room allocations yet for this week.")
    else:
        st.dataframe(alloc_df.set_index("Room"), use_container_width=True)


def display_add_to_oasis_form(pool, current_monday, oasis_capacity):
    st.header("🚶 Add Yourself to Oasis (Direct - if space available)")
    st.caption("Use this if you missed the preference deadline. Availability is not guaranteed.")
    with st.form("oasis_add_form"):
        user_name = st.text_input("Your Name")
        selected_days = st.multiselect("Select day(s) to add yourself:", DAYS_OF_WEEK)
        
        submitted = st.form_submit_button("➕ Add Me to Oasis Schedule")
        if submitted:
            if not user_name.strip(): st.error("❌ Please enter your name.")
            elif not selected_days: st.error("❌ Select at least one day.")
            else:
                name_clean = user_name.strip().title()
                try:
                    with manage_db_connection(pool) as conn:
                        with conn.cursor() as cur:
                            # Remove existing entries for this user for selected days to avoid duplicates if re-adding
                            # This makes it an "overwrite for selected days" operation
                            for day_str in selected_days:
                                date_obj = current_monday + timedelta(days=DAYS_OF_WEEK.index(day_str))
                                cur.execute(
                                    "DELETE FROM weekly_allocations WHERE room_name = %s AND team_name = %s AND date = %s",
                                    (OASIS_ROOM_NAME, name_clean, date_obj)
                                )
                            
                            all_successful = True
                            for day_str in selected_days:
                                date_obj = current_monday + timedelta(days=DAYS_OF_WEEK.index(day_str))
                                cur.execute(
                                    "SELECT COUNT(*) FROM weekly_allocations WHERE room_name = %s AND date = %s",
                                    (OASIS_ROOM_NAME, date_obj)
                                )
                                count = cur.fetchone()[0]
                                if count >= oasis_capacity:
                                    st.warning(f"⚠️ Oasis is full on {day_str}. '{name_clean}' not added for this day.")
                                    all_successful = False
                                else:
                                    cur.execute(
                                        "INSERT INTO weekly_allocations (team_name, room_name, date) VALUES (%s, %s, %s)",
                                        (name_clean, OASIS_ROOM_NAME, date_obj)
                                    )
                            conn.commit()
                            if all_successful and selected_days: st.success(f"✅ '{name_clean}' added to Oasis for selected day(s)!")
                            elif selected_days : st.info("Partial success. See warnings above.")
                            st.rerun()
                except Exception as e:
                    st.error(f"❌ Error adding to Oasis: {e}")


def display_oasis_overview_matrix(pool, current_monday, oasis_capacity):
    st.header("📊 Full Weekly Oasis Overview & Manual Edit")
    
    # Niek's special handling: to be always included or managed separately
    NIEK_USER = "Niek" # Example of a user with special status

    try:
        with manage_db_connection(pool) as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT team_name, date FROM weekly_allocations WHERE room_name = %s", (OASIS_ROOM_NAME,))
                rows = cur.fetchall()

        df_alloc = pd.DataFrame(rows, columns=["Name", "Date"]) if rows else pd.DataFrame(columns=["Name", "Date"])
        if not df_alloc.empty:
            df_alloc["Date"] = pd.to_datetime(df_alloc["Date"]).dt.date
        
        # Ensure Niek is in the index, and other unique names
        unique_names = sorted(list(set(df_alloc["Name"].tolist() + [NIEK_USER])))
        
        day_dates = [current_monday + timedelta(days=i) for i in range(len(DAYS_OF_WEEK))]
        matrix_df = pd.DataFrame(False, index=unique_names, columns=DAYS_OF_WEEK)

        for _, row in df_alloc.iterrows():
            if row["Date"] in day_dates:
                day_name = DAYS_OF_WEEK[day_dates.index(row["Date"])]
                if row["Name"] in matrix_df.index:
                     matrix_df.at[row["Name"], day_name] = True
        
        # Niek's special status: always marked as true (can be overridden by editor if not disabled)
        if NIEK_USER in matrix_df.index:
            matrix_df.loc[NIEK_USER, :] = True 

        # --- Display availability ---
        st.subheader("🪑 Oasis Availability Summary")
        cols_avail = st.columns(len(DAYS_OF_WEEK))
        for idx, day_name in enumerate(DAYS_OF_WEEK):
            current_date = day_dates[idx]
            signed_up_count = df_alloc[df_alloc["Date"] == current_date].shape[0]
            spots_left = max(0, oasis_capacity - signed_up_count)
            cols_avail[idx].metric(label=day_name, value=f"{spots_left} spots")

        # --- Display editable matrix ---
        st.subheader("✍️ Edit Oasis Roster")
        # Disable editing for Niek's row as an example of special handling
        disabled_cols_for_niek = {day: True for day in DAYS_OF_WEEK} if NIEK_USER in matrix_df.index else {}
        
        edited_matrix = st.data_editor(
            matrix_df,
            use_container_width=True,
            disabled=[NIEK_USER] if NIEK_USER in matrix_df.index else [], # Disable entire row for Niek
            key="oasis_matrix_editor"
        )

        if st.button("💾 Save Oasis Matrix Changes", key="save_oasis_matrix"):
            try:
                with manage_db_connection(pool) as conn:
                    with conn.cursor() as cur:
                        # Clear existing Oasis allocations for the week, except Niek's if he's managed specially
                        cur.execute("DELETE FROM weekly_allocations WHERE room_name = %s AND team_name != %s", (OASIS_ROOM_NAME, NIEK_USER))
                        # Or, if Niek is also managed by this matrix:
                        # cur.execute("DELETE FROM weekly_allocations WHERE room_name = %s", (OASIS_ROOM_NAME,))

                        # Re-insert Niek's fixed schedule if he's managed outside the editable part
                        if NIEK_USER in edited_matrix.index and edited_matrix.loc[NIEK_USER].all(): # Assuming Niek is always all days if present
                             for day_idx, day_name in enumerate(DAYS_OF_WEEK):
                                if edited_matrix.at[NIEK_USER, day_name]: # Check if Niek is marked for this day
                                    date_obj = current_monday + timedelta(days=day_idx)
                                    cur.execute(
                                        "INSERT INTO weekly_allocations (team_name, room_name, date) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                                        (NIEK_USER, OASIS_ROOM_NAME, date_obj)
                                    )
                        
                        # Insert based on the edited matrix for other users
                        # Keep track of daily counts to respect capacity
                        daily_counts = {day: df_alloc[(df_alloc['Date'] == day_dates[DAYS_OF_WEEK.index(day)]) & (df_alloc['Name'] == NIEK_USER)].shape[0] for day in DAYS_OF_WEEK}


                        for person_name, attendance_series in edited_matrix.iterrows():
                            if person_name == NIEK_USER: continue # Skip Niek if handled separately or always true

                            for day_name, is_attending in attendance_series.items():
                                if is_attending:
                                    day_idx = DAYS_OF_WEEK.index(day_name)
                                    date_obj = current_monday + timedelta(days=day_idx)
                                    
                                    if daily_counts[day_name] < oasis_capacity:
                                        cur.execute(
                                            "INSERT INTO weekly_allocations (team_name, room_name, date) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                                            (person_name, OASIS_ROOM_NAME, date_obj)
                                        )
                                        daily_counts[day_name] += 1
                                    else:
                                        st.warning(f"⚠️ Oasis full on {day_name}. '{person_name}' could not be added.")
                        conn.commit()
                st.success("✅ Oasis matrix saved.")
                st.rerun()
            except Exception as e:
                st.error(f"❌ Failed to save Oasis matrix: {e}")

    except Exception as e:
        st.error(f"❌ Error loading Oasis overview matrix: {e}")


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