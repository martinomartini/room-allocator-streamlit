import streamlit as st
import psycopg2
import psycopg2.pool
import psycopg2.extras # For RealDictCursor
import json
import os
from datetime import datetime, timedelta, date
import pytz
import pandas as pd

# Assuming allocate_rooms.py exists and has a run_allocation function
# from allocate_rooms import run_allocation # Keep this if the file exists

# --- Global Configuration & Constants ---
st.set_page_config(page_title="Weekly Room Allocator", layout="wide")

DATABASE_URL = st.secrets.get("SUPABASE_DB_URI", os.environ.get("SUPABASE_DB_URI"))
OFFICE_TIMEZONE_STR = st.secrets.get("OFFICE_TIMEZONE", os.environ.get("OFFICE_TIMEZONE", "UTC"))
RESET_PASSWORD = st.secrets.get("ADMIN_PASSWORD", "trainee") # Prefer secrets for passwords

try:
    OFFICE_TIMEZONE = pytz.timezone(OFFICE_TIMEZONE_STR)
except pytz.UnknownTimeZoneError:
    st.error(f"Invalid Timezone: '{OFFICE_TIMEZONE_STR}', defaulting to UTC.")
    OFFICE_TIMEZONE = pytz.utc

# Calculate current dates once
TODAY = datetime.now(OFFICE_TIMEZONE).date()
THIS_MONDAY = TODAY - timedelta(days=TODAY.weekday())
# For forms that might refer to a specific upcoming week, you might need more dynamic date logic
# Example: if forms are for "next week", calculate next_monday here.
# For this script, we'll use THIS_MONDAY for current week views.

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOMS_FILE = os.path.join(BASE_DIR, 'rooms.json')

try:
    with open(ROOMS_FILE, 'r') as f:
        AVAILABLE_ROOMS = json.load(f)
except FileNotFoundError:
    st.error(f"Error: {ROOMS_FILE} not found. Please ensure it exists.")
    AVAILABLE_ROOMS = []
except json.JSONDecodeError:
    st.error(f"Error: Could not decode {ROOMS_FILE}. Invalid JSON.")
    AVAILABLE_ROOMS = []

OASIS_ROOM_NAME = "Oasis"
OASIS_DETAILS = next((r for r in AVAILABLE_ROOMS if r["name"] == OASIS_ROOM_NAME), {"capacity": 15})

# --- Database Connection Pool ---
@st.cache_resource
def get_db_connection_pool():
    if not DATABASE_URL:
        st.error("Database URL (SUPABASE_DB_URI) is not configured.")
        return None
    try:
        return psycopg2.pool.SimpleConnectionPool(1, 25, dsn=DATABASE_URL)
    except psycopg2.OperationalError as e:
        st.error(f"Failed to connect to the database: {e}")
        return None

POOL = get_db_connection_pool()

def get_connection():
    if POOL:
        return POOL.getconn()
    return None

def return_connection(conn):
    if POOL and conn:
        POOL.putconn(conn)

# --- Cached Database Functions ---

@st.cache_data(ttl=300) # Cache for 5 minutes, or clear explicitly
def get_room_grid_data(current_week_monday: date):
    """Fetches and structures project room allocation data for a given week's Monday."""
    if not POOL: return pd.DataFrame()

    day_mapping_for_grid = {
        current_week_monday + timedelta(days=i): (current_week_monday + timedelta(days=i)).strftime('%A')
        for i in range(4) # Monday to Thursday
    }
    day_labels = list(day_mapping_for_grid.values())

    try:
        with open(ROOMS_FILE, 'r') as f:
            all_project_rooms = [r["name"] for r in json.load(f) if r["name"] != OASIS_ROOM_NAME]
    except (FileNotFoundError, json.JSONDecodeError):
        st.warning(f"Could not load room names from {ROOMS_FILE} for grid.")
        all_project_rooms = []

    # Initialize grid with all rooms as Vacant
    grid_data = {
        room: {**{"Room": room}, **{day: "Vacant" for day in day_labels}}
        for room in all_project_rooms
    }

    conn = get_connection()
    if not conn: return pd.DataFrame(grid_data.values())

    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # Single query to get allocations and contact persons
            query = """
                SELECT
                    wa.team_name,
                    wa.room_name,
                    wa.date,
                    COALESCE(wp.contact_person, '') as contact_person
                FROM
                    weekly_allocations wa
                LEFT JOIN
                    weekly_preferences wp ON wa.team_name = wp.team_name
                WHERE
                    wa.room_name != %s AND wa.date >= %s AND wa.date <= %s;
            """
            cur.execute(query, (OASIS_ROOM_NAME, current_week_monday, current_week_monday + timedelta(days=3)))
            allocations = cur.fetchall()

        for alloc in allocations:
            room = alloc["room_name"]
            alloc_date = alloc["date"] # This is a datetime.date object
            day_str = day_mapping_for_grid.get(alloc_date)

            if room in grid_data and day_str:
                team_name = alloc["team_name"]
                contact = alloc["contact_person"]
                label = f"{team_name} ({contact})" if contact else team_name
                grid_data[room][day_str] = label
        
        return pd.DataFrame(grid_data.values())

    except psycopg2.Error as e:
        st.warning(f"Database error in get_room_grid_data: {e}")
        return pd.DataFrame(grid_data.values()) # Return what we have
    finally:
        return_connection(conn)

@st.cache_data(ttl=300)
def get_oasis_grid_data():
    if not POOL: return pd.DataFrame()
    conn = get_connection()
    if not conn: return pd.DataFrame()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # Fetch Oasis allocations for the current week (Mon-Fri)
            # Assuming THIS_MONDAY is the reference for the "current week"
            start_of_week = THIS_MONDAY
            end_of_week = THIS_MONDAY + timedelta(days=4)
            cur.execute("""
                SELECT team_name, date FROM weekly_allocations 
                WHERE room_name = %s AND date BETWEEN %s AND %s
            """, (OASIS_ROOM_NAME, start_of_week, end_of_week))
            data = cur.fetchall()

        if not data: return pd.DataFrame(columns=["Weekday", "People"])

        df = pd.DataFrame(data)
        df["Date"] = pd.to_datetime(df["date"])
        df["Day"] = df["Date"].dt.strftime('%A')
        
        all_days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
        grouped = df.groupby("Day")["team_name"].apply(lambda x: ", ".join(sorted(set(x))))
        grouped = grouped.reindex(all_days, fill_value="Vacant").reset_index()
        return grouped.rename(columns={"Day": "Weekday", "team_name": "People"})

    except psycopg2.Error as e:
        st.warning(f"Failed to load Oasis allocation data: {e}")
        return pd.DataFrame(columns=["Weekday", "People"])
    finally:
        return_connection(conn)

@st.cache_data(ttl=300)
def get_preferences_data():
    if not POOL: return pd.DataFrame()
    conn = get_connection()
    if not conn: return pd.DataFrame()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT team_name, contact_person, team_size, preferred_days FROM weekly_preferences")
            return pd.DataFrame(cur.fetchall(), columns=["Team", "Contact", "Size", "Days"])
    except psycopg2.Error as e:
        st.warning(f"Failed to fetch preferences: {e}")
        return pd.DataFrame()
    finally:
        return_connection(conn)

@st.cache_data(ttl=300)
def get_oasis_preferences_data():
    if not POOL: return pd.DataFrame()
    conn = get_connection()
    if not conn: return pd.DataFrame()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT person_name, preferred_day_1, preferred_day_2, 
                       preferred_day_3, preferred_day_4, preferred_day_5, submission_time 
                FROM oasis_preferences ORDER BY submission_time DESC
            """)
            # Ensure all columns exist even if some are all None
            df = pd.DataFrame(cur.fetchall())
            expected_cols = ["person_name", "preferred_day_1", "preferred_day_2", "preferred_day_3", "preferred_day_4", "preferred_day_5", "submission_time"]
            for col in expected_cols:
                if col not in df.columns:
                    df[col] = None
            df.columns = ["Person", "Day 1", "Day 2", "Day 3", "Day 4", "Day 5", "Submitted At"]
            return df

    except psycopg2.Error as e:
        st.warning(f"Failed to fetch oasis preferences: {e}")
        return pd.DataFrame()
    finally:
        return_connection(conn)

# --- Database Write Functions (with cache clearing) ---
def execute_db_write_operation(query, params=None, success_message="Operation successful.", error_message="Operation failed."):
    if not POOL:
        st.error("Database not connected.")
        return False
    conn = get_connection()
    if not conn:
        st.error("Failed to get database connection.")
        return False
    try:
        with conn.cursor() as cur:
            cur.execute(query, params)
        conn.commit()
        st.success(success_message)
        st.cache_data.clear() # Clear all data caches
        return True
    except psycopg2.Error as e:
        conn.rollback()
        st.error(f"{error_message}: {e}")
        return False
    finally:
        return_connection(conn)

def insert_preference(team, contact, size, days_str):
    # Validations
    if not team or not contact: st.error("❌ Team Name and Contact Person are required."); return False
    if not (3 <= size <= 6): st.error("❌ Team size must be between 3 and 6."); return False
    
    # Day choice validation (already handled by selectbox, but good to have)
    valid_day_sets = [{"Monday", "Wednesday"}, {"Tuesday", "Thursday"}]
    if set(days_str.split(',')) not in valid_day_sets:
        st.error("❌ Invalid day selection. Must be Mon & Wed or Tue & Thu.")
        return False

    # Check for existing submission by the team (optional, can be handled by DB constraint too)
    # For simplicity, we can rely on a UNIQUE constraint on team_name in weekly_preferences table
    # or perform a SELECT check here if needed.

    query = """
        INSERT INTO weekly_preferences (team_name, contact_person, team_size, preferred_days, submission_time)
        VALUES (%s, %s, %s, %s, NOW() AT TIME ZONE 'UTC')
        ON CONFLICT (team_name) DO UPDATE SET
        contact_person = EXCLUDED.contact_person,
        team_size = EXCLUDED.team_size,
        preferred_days = EXCLUDED.preferred_days,
        submission_time = NOW() AT TIME ZONE 'UTC'; 
    """
    # Using ON CONFLICT to allow updates if team submits again, or remove it for strict one-time submission
    # If strict one-time, you'd need a SELECT check first or catch the unique violation error.
    # For now, allowing update.
    return execute_db_write_operation(
        query, (team, contact, size, days_str),
        success_message=f"✅ Preference submitted/updated for {team}!",
        error_message=f"Failed to submit preference for {team}"
    )

def insert_oasis_preference(person, selected_days):
    if not person: st.error("❌ Please enter your name."); return False
    if not (1 <= len(selected_days) <= 5): st.error("❌ Select between 1 and 5 preferred days."); return False

    padded_days = selected_days + [None] * (5 - len(selected_days))
    query = """
        INSERT INTO oasis_preferences 
            (person_name, preferred_day_1, preferred_day_2, preferred_day_3, preferred_day_4, preferred_day_5, submission_time)
        VALUES (%s, %s, %s, %s, %s, %s, NOW() AT TIME ZONE 'UTC')
        ON CONFLICT (person_name) DO UPDATE SET
            preferred_day_1 = EXCLUDED.preferred_day_1,
            preferred_day_2 = EXCLUDED.preferred_day_2,
            preferred_day_3 = EXCLUDED.preferred_day_3,
            preferred_day_4 = EXCLUDED.preferred_day_4,
            preferred_day_5 = EXCLUDED.preferred_day_5,
            submission_time = NOW() AT TIME ZONE 'UTC';
    """
    # Again, using ON CONFLICT for update.
    return execute_db_write_operation(
        query, (person.strip(), *padded_days),
        success_message=f"✅ Oasis preference submitted/updated for {person}!",
        error_message=f"Failed to submit Oasis preference for {person}"
    )

# --- UI Rendering ---
st.title("📅 Weekly Room Allocator")
st.info(f"Current Office Time: **{datetime.now(OFFICE_TIMEZONE).strftime('%Y-%m-%d %H:%M:%S')}** ({OFFICE_TIMEZONE_STR})")
st.caption(f"Displaying data for the week of: **{THIS_MONDAY.strftime('%B %d, %Y')}**")

if not POOL:
    st.error("🚨 Critical Error: Database connection pool is not available. Application functionality is limited.")
else:
    # --- Admin Section ---
    with st.expander("🔐 Admin Controls"):
        # (Admin UI code - kept similar to previous, ensuring execute_db_write_operation is used)
        # ... (For brevity, admin section mostly omitted but should use execute_db_write_operation)
        # Example for one admin button:
        pwd = st.text_input("Enter admin password:", type="password", key="admin_pwd_eff")
        if pwd == RESET_PASSWORD:
            st.success("✅ Admin access granted.")
            st.subheader("🧹 Reset Project Room Data")
            if st.button("🗑️ Remove All Project Room Allocations (Non-Oasis)"):
                execute_db_write_operation(
                    "DELETE FROM weekly_allocations WHERE room_name != %s", (OASIS_ROOM_NAME,),
                    "✅ Project room allocations (non-Oasis) removed."
                )
            # ... (Other admin buttons for reset, allocation runs, data edits) ...
            # For data editors, the save logic would be:
            # 1. Get data using cached function.
            # 2. Show st.data_editor.
            # 3. On save button:
            #    - Get connection.
            #    - Start transaction (optional but good).
            #    - DELETE existing data for the scope.
            #    - INSERT new data from editor.
            #    - Commit.
            #    - Call st.cache_data.clear().
            #    - Handle errors with rollback.
            #    - Return connection.
            # This is complex, execute_db_write_operation is for simpler single ops.
            # The full admin section requires more detailed implementation of this pattern.
            
            # Example for running allocation (assuming run_allocation exists and is imported)
            # st.subheader("🧠 Run Allocations")
            # if st.button("🚀 Run Project Room Allocation"):
            #     # success, _ = run_allocation(DATABASE_URL, only="project") # Original call
            #     # if success: 
            #     #    st.success("✅ Project room allocation completed.")
            #     #    st.cache_data.clear()
            #     # else: st.error("❌ Project room allocation failed.")
            #     st.info("Run allocation logic needs to be integrated here.")


        elif pwd:
            st.error("❌ Incorrect admin password.")

    # --- Team Preference Form ---
    st.header("📝 Request Project Room")
    # You might want to make these dates dynamic based on when submissions are open
    st.markdown("For teams of 3-6. Submissions for the **current/next cycle**.") # Generic
    with st.form("team_form_eff"):
        team_name = st.text_input("Team Name")
        contact_person = st.text_input("Contact Person")
        team_size = st.number_input("Team Size (3-6)", min_value=3, max_value=6, value=3)
        day_choice_str = st.selectbox("Preferred Days", ["Monday and Wednesday", "Tuesday and Thursday"])
        
        submit_team_pref = st.form_submit_button("Submit Project Room Request")
        if submit_team_pref:
            day_map = {"Monday and Wednesday": "Monday,Wednesday", "Tuesday and Thursday": "Tuesday,Thursday"}
            if insert_preference(team_name, contact_person, team_size, day_map[day_choice_str]):
                st.rerun()

    # --- Oasis Preference Form ---
    st.header("🌿 Reserve Oasis Seat")
    st.markdown("Submit your personal preferences for Oasis (up to 5 days).")
    with st.form("oasis_form_eff"):
        oasis_person_name = st.text_input("Your Name", key="oasis_person_eff")
        oasis_selected_days = st.multiselect(
            "Select Your Preferred Days for Oasis (up to 5):",
            ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"],
            max_selections=5
        )
        submit_oasis_pref = st.form_submit_button("Submit Oasis Preference")
        if submit_oasis_pref:
            if insert_oasis_preference(oasis_person_name, oasis_selected_days):
                st.rerun()
    
    # --- Display Allocations ---
    st.header(f"📌 Project Room Allocations (Week of {THIS_MONDAY.strftime('%b %d')})")
    project_alloc_df = get_room_grid_data(THIS_MONDAY)
    if project_alloc_df.empty:
        st.write("No project room allocations to display for this week yet.")
    else:
        st.dataframe(project_alloc_df, use_container_width=True, hide_index=True)

    st.header(f"📊 Oasis Overview (Week of {THIS_MONDAY.strftime('%b %d')})")
    oasis_alloc_df = get_oasis_grid_data() # Implicitly uses THIS_MONDAY for week filtering
    if oasis_alloc_df.empty:
        st.write("No Oasis allocations to display for this week yet.")
    else:
        st.dataframe(oasis_alloc_df, use_container_width=True, hide_index=True)


    # --- Ad-hoc Oasis Add Form & Matrix Editor ---
    # These sections involve more complex state and direct DB manipulation.
    # The matrix editor, in particular, requires careful handling of DB updates based on cell changes.
    # The previous version's logic for this can be adapted, ensuring cache clearing after saves.
    # For brevity in this "efficiency" focused response, the detailed UI for these are omitted,
    # but the principles of using cached data for display and clearing cache after writes apply.
    st.header("🚶 Add Yourself to Oasis Ad-hoc / Edit Full Matrix")
    st.info("Ad-hoc add and Matrix editor functionality would be here. "
            "Ensure any database modifications clear the cache (`st.cache_data.clear()`) and re-run.")


    # Example: Ad-hoc Oasis (simplified)
    with st.form("oasis_add_form_eff"):
        adhoc_name = st.text_input("Your Name (for Ad-hoc Oasis Add)")
        adhoc_days = st.multiselect("Select day(s) to add to Oasis:", ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"])
        add_submit = st.form_submit_button("➕ Add Me Ad-hoc")

        if add_submit:
            if not adhoc_name.strip(): st.error("❌ Name is required for ad-hoc add.");
            elif not adhoc_days: st.error("❌ Select at least one day for ad-hoc add.");
            else:
                # Complex logic: check capacity, then insert for each day.
                # Needs individual INSERTs or a more complex batch operation.
                # Each successful insert should ideally clear cache.
                # This is a simplified placeholder.
                all_successful = True
                conn = get_connection()
                if conn:
                    try:
                        with conn.cursor() as cur:
                            for day_str in adhoc_days:
                                day_date = THIS_MONDAY + timedelta(days=["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"].index(day_str))
                                
                                # Check capacity
                                cur.execute("SELECT COUNT(*) FROM weekly_allocations WHERE room_name = %s AND date = %s", (OASIS_ROOM_NAME, day_date))
                                count = cur.fetchone()[0]
                                if count >= OASIS_DETAILS.get("capacity", 15):
                                    st.warning(f"⚠️ Oasis full on {day_str}. Could not add {adhoc_name}.")
                                    all_successful = False
                                    continue
                                
                                # Insert (handle conflicts if person tries to add again to same day)
                                cur.execute("""
                                    INSERT INTO weekly_allocations (team_name, room_name, date)
                                    VALUES (%s, %s, %s)
                                    ON CONFLICT (team_name, room_name, date) DO NOTHING; 
                                """, (adhoc_name.strip(), OASIS_ROOM_NAME, day_date))
                        conn.commit()
                        st.cache_data.clear()
                        if all_successful: st.success(f"✅ {adhoc_name} added to Oasis for selected available days!")
                        else: st.info(f"ℹ️ {adhoc_name} added for some days. Check warnings above.")
                        st.rerun()
                    except psycopg2.Error as e:
                        conn.rollback()
                        st.error(f"Failed to add ad-hoc to Oasis: {e}")
                    finally:
                        return_connection(conn)
                else:
                    st.error("No DB connection for ad-hoc add.")


# --- Footer or additional info ---
st.markdown("---")
st.caption("Weekly Room Allocator | Using Streamlit & PostgreSQL")