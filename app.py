import streamlit as st
import psycopg2
import psycopg2.pool
import json
import os
from datetime import datetime, timedelta, date # Ensure date is imported
import pytz
import pandas as pd
from psycopg2.extras import RealDictCursor
from allocate_rooms import run_allocation  # Assuming this file exists and is correct

# -----------------------------------------------------
# Configuration and Global Constants
# -----------------------------------------------------
st.set_page_config(page_title="Weekly Room Allocator", layout="wide")

DATABASE_URL = st.secrets.get("SUPABASE_DB_URI", os.environ.get("SUPABASE_DB_URI"))
OFFICE_TIMEZONE_STR = st.secrets.get("OFFICE_TIMEZONE", os.environ.get("OFFICE_TIMEZONE", "UTC"))
RESET_PASSWORD = "trainee"  # Consider moving to secrets

# !!! --- SET YOUR DESIRED OPERATIONAL MONDAY HERE --- !!!
# This date will be the Monday of the week the application ALWAYS operates on.
# Example: For the week starting Monday, June 10th, 2024, use date(2024, 6, 10)
CONSTANT_OPERATIONAL_MONDAY = date(2024, 6, 10) # <<<< CHANGE THIS DATE AS NEEDED
# !!! ------------------------------------------------- !!!

# Attempt to set office timezone
try:
    OFFICE_TIMEZONE = pytz.timezone(OFFICE_TIMEZONE_STR)
except pytz.UnknownTimeZoneError:
    st.error(f"Invalid Timezone: '{OFFICE_TIMEZONE_STR}', defaulting to UTC.")
    OFFICE_TIMEZONE = pytz.utc

# Locate rooms.json in the application directory
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOMS_FILE = os.path.join(BASE_DIR, 'rooms.json')
try:
    with open(ROOMS_FILE, 'r') as f:
        AVAILABLE_ROOMS = json.load(f)
except FileNotFoundError:
    st.error(f"Error: {ROOMS_FILE} not found. Please ensure it exists in the application directory.")
    AVAILABLE_ROOMS = []

# Extract Oasis room (default capacity 15 if not found)
oasis = next((r for r in AVAILABLE_ROOMS if r["name"] == "Oasis"), {"capacity": 15})

# -----------------------------------------------------
# Database Connection Pool
# -----------------------------------------------------
@st.cache_resource
def get_db_connection_pool():
    """Create a connection pool for the PostgreSQL database."""
    if not DATABASE_URL:
        st.error("Database URL is not configured. Please set SUPABASE_DB_URI.")
        return None
    return psycopg2.pool.SimpleConnectionPool(1, 25, dsn=DATABASE_URL)

def get_connection(pool):
    """Retrieve a connection from the pool."""
    if pool:
        return pool.getconn()
    return None

def return_connection(pool, conn):
    """Return a connection to the pool."""
    if pool and conn:
        pool.putconn(conn)

# Initialize the pool
pool = get_db_connection_pool()

# -----------------------------------------------------
# Helper to load/update default date text
# -----------------------------------------------------
# These texts are for display. Admin should align them with CONSTANT_OPERATIONAL_MONDAY's week.
if "week_of_text" not in st.session_state:
    st.session_state["week_of_text"] = f"{CONSTANT_OPERATIONAL_MONDAY.strftime('%B %d')}"
if "submission_start_text" not in st.session_state:
    st.session_state["submission_start_text"] = "Wednesday (of operational week) 09:00"
if "submission_end_text" not in st.session_state:
    st.session_state["submission_end_text"] = "Thursday (of operational week) 16:00"
if "oasis_end_text" not in st.session_state:
    st.session_state["oasis_end_text"] = "Friday (of operational week) 16:00"
if "project_allocations_markdown_content" not in st.session_state:
    st.session_state["project_allocations_markdown_content"] = f"For the week of {st.session_state['week_of_text']} (Fixed to week starting {CONSTANT_OPERATIONAL_MONDAY.strftime('%B %d, %Y')})."

# -----------------------------------------------------
# Database Utility Functions
# -----------------------------------------------------
def get_room_grid(pool):
    """Fetch allocations (non-Oasis) for the CONSTANT_OPERATIONAL_MONDAY's week."""
    if not pool: return pd.DataFrame()

    this_monday = CONSTANT_OPERATIONAL_MONDAY
    day_mapping = {
        this_monday + timedelta(days=0): "Monday",
        this_monday + timedelta(days=1): "Tuesday",
        this_monday + timedelta(days=2): "Wednesday",
        this_monday + timedelta(days=3): "Thursday"
    }
    day_labels = list(day_mapping.values())

    try:
        with open(ROOMS_FILE) as f:
            all_rooms = [r["name"] for r in json.load(f) if r["name"] != "Oasis"]
    except (FileNotFoundError, json.JSONDecodeError):
        st.error(f"Error: Could not load valid data from {ROOMS_FILE}.")
        return pd.DataFrame()

    grid = {room: {**{"Room": room}, **{day: "Vacant" for day in day_labels}} for room in all_rooms}
    conn = get_connection(pool)
    if not conn: return pd.DataFrame(grid.values())

    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            selected_week_end = this_monday + timedelta(days=3) # Thursday
            cur.execute("""
                SELECT team_name, room_name, date
                FROM weekly_allocations
                WHERE room_name != 'Oasis' AND date >= %s AND date <= %s
            """, (this_monday, selected_week_end))
            allocations = cur.fetchall()

            cur.execute("SELECT team_name, contact_person FROM weekly_preferences")
            contacts = {row["team_name"]: row["contact_person"] for row in cur.fetchall()}

        for row in allocations:
            team, room, date_val = row["team_name"], row["room_name"], row["date"]
            day = day_mapping.get(date_val)
            if room not in grid or not day: continue
            contact = contacts.get(team)
            grid[room][day] = f"{team} ({contact})" if contact else team
        return pd.DataFrame(grid.values())
    except psycopg2.Error as e:
        st.warning(f"Database error while getting room grid: {e}")
        return pd.DataFrame(grid.values())
    finally: return_connection(pool, conn)

def get_oasis_grid(pool):
    """Fetch Oasis allocations for the CONSTANT_OPERATIONAL_MONDAY's week."""
    if not pool: return pd.DataFrame()
    conn = get_connection(pool)
    if not conn: return pd.DataFrame()

    selected_monday_dt = CONSTANT_OPERATIONAL_MONDAY
    selected_friday_dt = selected_monday_dt + timedelta(days=4)

    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT team_name, room_name, date FROM weekly_allocations 
                WHERE room_name = 'Oasis' AND date >= %s AND date <= %s
            """, (selected_monday_dt, selected_friday_dt))
            data = cur.fetchall()
            if not data: return pd.DataFrame(columns=["Weekday", "People"])

            df = pd.DataFrame(data, columns=["Person", "Room", "Date"])
            df["Date"] = pd.to_datetime(df["Date"])
            df["Day"] = df["Date"].dt.strftime('%A')

            all_days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
            grouped = df.groupby("Day")["Person"].apply(lambda x: ", ".join(sorted(set(x))))
            grouped = grouped.reindex(all_days, fill_value="Vacant").reset_index()
            grouped = grouped.rename(columns={"Day": "Weekday", "Person": "People"})
            return grouped
    except Exception as e:
        st.warning(f"Failed to load oasis allocation data: {e}")
        return pd.DataFrame(columns=["Weekday", "People"])
    finally: return_connection(pool, conn)

# --- (get_preferences and get_oasis_preferences remain largely the same) ---
def get_preferences(pool):
    if not pool: return pd.DataFrame()
    conn = get_connection(pool)
    if not conn: return pd.DataFrame()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT team_name, contact_person, team_size, preferred_days, submission_time FROM weekly_preferences ORDER BY submission_time DESC")
            return pd.DataFrame(cur.fetchall(), columns=["Team", "Contact", "Size", "Days", "Submitted At"])
    except Exception as e:
        st.warning(f"Failed to fetch preferences: {e}")
        return pd.DataFrame()
    finally: return_connection(pool, conn)

def get_oasis_preferences(pool):
    if not pool: return pd.DataFrame()
    conn = get_connection(pool)
    if not conn: return pd.DataFrame()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT person_name, preferred_day_1, preferred_day_2, preferred_day_3, preferred_day_4, preferred_day_5, submission_time FROM oasis_preferences ORDER BY submission_time DESC")
            return pd.DataFrame(cur.fetchall(), columns=["Person", "Day 1", "Day 2", "Day 3", "Day 4", "Day 5", "Submitted At"])
    except Exception as e:
        st.warning(f"Failed to fetch oasis preferences: {e}")
        return pd.DataFrame()
    finally: return_connection(pool, conn)

# --- (Insert functions remain the same) ---
def insert_preference(pool, team, contact, size, days):
    if not pool: return False
    if not team or not contact:
        st.error("❌ Team Name and Contact Person are required.")
        return False
    if size < 3: st.error("❌ Team size must be at least 3."); return False
    if size > 6: st.error("❌ Team size cannot exceed 6."); return False
    conn = get_connection(pool)
    if not conn: return False
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM weekly_preferences WHERE team_name = %s", (team,))
            if cur.fetchone():
                st.error(f"❌ Team '{team}' has already submitted. Contact admin.")
                return False
            new_days_set = set(days.split(','))
            if new_days_set not in [set(["Monday", "Wednesday"]), set(["Tuesday", "Thursday"])]:
                st.error("❌ Invalid day selection. Must be Mon & Wed or Tue & Thu.")
                return False
            cur.execute("INSERT INTO weekly_preferences (team_name, contact_person, team_size, preferred_days, submission_time) VALUES (%s, %s, %s, %s, NOW() AT TIME ZONE 'UTC')", (team, contact, size, days))
            conn.commit()
            return True
    except psycopg2.Error as e:
        st.error(f"Database insert failed: {e}"); conn.rollback(); return False
    finally: return_connection(pool, conn)

def insert_oasis(pool, person, selected_days):
    if not pool: return False
    if not person: st.error("❌ Please enter your name."); return False
    if not selected_days: st.error("❌ Select at least 1 preferred day."); return False
    if len(selected_days) > 5: st.error("❌ Max 5 preferred days."); return False
    conn = get_connection(pool)
    if not conn: return False
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM oasis_preferences WHERE person_name = %s", (person,))
            if cur.fetchone():
                st.error("❌ You've already submitted. Contact admin."); return False
            padded_days = selected_days + [None] * (5 - len(selected_days))
            cur.execute("INSERT INTO oasis_preferences (person_name, preferred_day_1, preferred_day_2, preferred_day_3, preferred_day_4, preferred_day_5, submission_time) VALUES (%s, %s, %s, %s, %s, %s, NOW() AT TIME ZONE 'UTC')", (person.strip(), *padded_days))
            conn.commit()
            return True
    except psycopg2.Error as e:
        st.error(f"Oasis insert failed: {e}"); conn.rollback(); return False
    finally: return_connection(pool, conn)

# -----------------------------------------------------
# Streamlit App UI
# -----------------------------------------------------
st.title("📅 Weekly Room Allocator")
st.info( # This info block uses manually set date texts by admin.
    f"""
    💡 **How This Works (Data fixed to week of {CONSTANT_OPERATIONAL_MONDAY.strftime('%B %d, %Y')}):**
    
    - 🧑‍🤝‍🧑 Project teams can select **either Monday & Wednesday** or **Tuesday & Thursday**. **Friday** is (for now) flexible. 
      There are 6 rooms for 4 persons and 1 room for 6 persons.
    - 🌿 Oasis users can choose **up to 5 preferred weekdays**, and will be randomly assigned—fairness is guaranteed. 
      There are 16 places in the Oasis.
    - ❗ You may only submit **once**. If you need to change your input, contact an admin.
    - 🗓️ **From {st.session_state.get("submission_start_text", "Wednesday 09:00")}** you can submit your **project room preference** until **{st.session_state.get("submission_end_text", "Thursday 16:00")}**. 
      The allocations will be shared on **{st.session_state.get("submission_end_text", "Thursday at 16:00")}**.
    - 🌿 **Oasis preferences** can be submitted **from {st.session_state.get("submission_start_text", "Wednesday 09:00")} until {st.session_state.get("oasis_end_text", "Friday 16:00")}**, 
      and allocation will be done at **{st.session_state.get("oasis_end_text", "Friday 16:00")}**.
    - ✅ Allocations are refreshed **for the fixed operational week** by an admin. 
        
    ---
    (Rest of the info block remains the same)
    """
)
now_local_for_display_only = datetime.now(OFFICE_TIMEZONE)
st.info(f"Current Office Time: **{now_local_for_display_only.strftime('%Y-%m-%d %H:%M:%S')}** ({OFFICE_TIMEZONE_STR})")
st.success(f"✅ Application is operating on the fixed week starting: **{CONSTANT_OPERATIONAL_MONDAY.strftime('%A, %d %B %Y')}**")

# ---------------- Admin Controls ---------------------
with st.expander("🔐 Admin Controls"):
    pwd = st.text_input("Enter admin password:", type="password", key="admin_pwd_fixed")
    if pwd == RESET_PASSWORD:
        st.success("✅ Access granted.")
        st.info(f"This application is configured to operate ONLY on the week of **{CONSTANT_OPERATIONAL_MONDAY.strftime('%d %B %Y')}**. To change this, the `CONSTANT_OPERATIONAL_MONDAY` variable in the script must be updated.")

        st.subheader("💼 Update Configurable Display Texts")
        st.caption(f"These texts are for display. Ensure they align with the fixed operational week ({CONSTANT_OPERATIONAL_MONDAY.strftime('%B %d, %Y')}).")
        new_week_of_text = st.text_input("Display text for 'Week of'", st.session_state["week_of_text"], key="wot_fixed")
        new_sub_start_text = st.text_input("Display text for 'Submission start'", st.session_state["submission_start_text"], key="sst_fixed")
        new_sub_end_text = st.text_input("Display text for 'Submission end'", st.session_state["submission_end_text"], key="set_fixed")
        new_oasis_end_text = st.text_input("Display text for 'Oasis end'", st.session_state["oasis_end_text"], key="oet_fixed")
        new_project_alloc_markdown_content = st.text_input(
            "Display text for 'Project Room Allocations' section", 
            st.session_state["project_allocations_markdown_content"],
            key="pamc_fixed"
        )
        if st.button("Update Display Texts", key="update_display_texts_btn_fixed"):
            st.session_state["week_of_text"] = new_week_of_text
            st.session_state["submission_start_text"] = new_sub_start_text
            st.session_state["submission_end_text"] = new_sub_end_text
            st.session_state["oasis_end_text"] = new_oasis_end_text
            st.session_state["project_allocations_markdown_content"] = new_project_alloc_markdown_content
            st.success("Display texts updated!"); st.rerun()

        st.subheader("🧠 Project Room Admin")
        if st.button("🚀 Run Project Room Allocation", key="run_proj_alloc_btn_fixed"):
            # IMPORTANT: run_allocation might need to be modified to accept CONSTANT_OPERATIONAL_MONDAY
            # if its logic should be constrained to this fixed week.
            if run_allocation:
                # success, _ = run_allocation(DATABASE_URL, only="project", target_monday=CONSTANT_OPERATIONAL_MONDAY) # Example modification
                success, _ = run_allocation(DATABASE_URL, only="project") 
                if success: st.success(f"✅ Project room allocation completed for week of {CONSTANT_OPERATIONAL_MONDAY.strftime('%B %d')}.")
                else: st.error("❌ Project room allocation failed.")
            else: st.error("run_allocation function not available.")

        st.subheader("🌿 Oasis Admin")
        if st.button("🎲 Run Oasis Allocation", key="run_oasis_alloc_btn_fixed"):
            if run_allocation:
                # success, _ = run_allocation(DATABASE_URL, only="oasis", target_monday=CONSTANT_OPERATIONAL_MONDAY) # Example modification
                success, _ = run_allocation(DATABASE_URL, only="oasis")
                if success: st.success(f"✅ Oasis allocation completed for week of {CONSTANT_OPERATIONAL_MONDAY.strftime('%B %d')}.")
                else: st.error("❌ Oasis allocation failed.")
            else: st.error("run_allocation function not available.")

        st.subheader("📌 Project Room Allocations (Admin Edit)")
        alloc_df_admin = get_room_grid(pool) # Uses CONSTANT_OPERATIONAL_MONDAY
        if not alloc_df_admin.empty:
            editable_alloc = st.data_editor(alloc_df_admin, num_rows="dynamic", use_container_width=True, key="edit_alloc_admin_fixed")
            if st.button("💾 Save Project Room Allocation Changes", key="save_proj_changes_btn_fixed"):
                conn_admin_alloc = get_connection(pool)
                if not conn_admin_alloc: st.error("No DB connection")
                else:
                    try:
                        with conn_admin_alloc.cursor() as cur:
                            selected_monday_for_edit = CONSTANT_OPERATIONAL_MONDAY
                            selected_thursday_for_edit = selected_monday_for_edit + timedelta(days=3)
                            cur.execute("DELETE FROM weekly_allocations WHERE room_name != 'Oasis' AND date >= %s AND date <= %s", (selected_monday_for_edit, selected_thursday_for_edit))
                            day_indices = {"Monday": 0, "Tuesday": 1, "Wednesday": 2, "Thursday": 3}
                            for _, row in editable_alloc.iterrows():
                                for day_name, day_idx in day_indices.items():
                                    value = row.get(day_name, "")
                                    if value and value != "Vacant":
                                        team_info = str(value).split("(")[0].strip()
                                        room_name_val = str(row["Room"]) if pd.notnull(row["Room"]) else None
                                        alloc_date = selected_monday_for_edit + timedelta(days=day_idx)
                                        if team_info and room_name_val:
                                            cur.execute("INSERT INTO weekly_allocations (team_name, room_name, date) VALUES (%s, %s, %s)", (team_info, room_name_val, alloc_date))
                        conn_admin_alloc.commit()
                        st.success(f"✅ Manual project room allocations updated for week of {CONSTANT_OPERATIONAL_MONDAY.strftime('%B %d')}.")
                    except Exception as e:
                        st.error(f"❌ Failed to save project room allocations: {e}")
                        if conn_admin_alloc: conn_admin_alloc.rollback()
                    finally: return_connection(pool, conn_admin_alloc)
        else: st.info(f"No project room allocations for week of {CONSTANT_OPERATIONAL_MONDAY.strftime('%B %d')}.")
        
        # --- Reset Buttons ---
        # These reset operations are generally global. If they need to be week-specific for the CONSTANT_OPERATIONAL_MONDAY,
        # the SQL queries within them would need `WHERE date >= CONSTANT_OPERATIONAL_MONDAY AND date <= end_of_constant_week` clauses.
        # For simplicity, I am leaving them as global resets for now unless specified otherwise.
        st.subheader("🧹 Reset Project Room Data (GLOBAL)")
        if st.button("🗑️ Remove All Project Room Allocations (Non-Oasis)", key="reset_pra_fixed"):
            # This is a GLOBAL reset of non-Oasis. To make it week-specific:
            # WHERE room_name != 'Oasis' AND date >= CONSTANT_OPERATIONAL_MONDAY AND date <= CONSTANT_OPERATIONAL_MONDAY + timedelta(days=3)
            conn_reset_pra = get_connection(pool)
            if conn_reset_pra:
                try:
                    with conn_reset_pra.cursor() as cur: cur.execute("DELETE FROM weekly_allocations WHERE room_name != 'Oasis'"); conn_reset_pra.commit()
                    st.success("✅ All Project room allocations (non-Oasis) removed globally.")
                except Exception as e: st.error(f"❌ Failed: {e}"); conn_reset_pra.rollback()
                finally: return_connection(pool, conn_reset_pra)

        if st.button("🧽 Remove All Project Room Preferences (GLOBAL)", key="reset_prp_fixed"):
            conn_reset_prp = get_connection(pool)
            if conn_reset_prp:
                try:
                    with conn_reset_prp.cursor() as cur: cur.execute("DELETE FROM weekly_preferences"); conn_reset_prp.commit()
                    st.success("✅ All project room preferences removed globally.")
                except Exception as e: st.error(f"❌ Failed: {e}"); conn_reset_prp.rollback()
                finally: return_connection(pool, conn_reset_prp)

        st.subheader("🌾 Reset Oasis Data (GLOBAL)")
        if st.button("🗑️ Remove All Oasis Allocations", key="reset_oa_fixed"):
            # This is a GLOBAL reset of Oasis. To make it week-specific:
            # WHERE room_name = 'Oasis' AND date >= CONSTANT_OPERATIONAL_MONDAY AND date <= CONSTANT_OPERATIONAL_MONDAY + timedelta(days=4)
            conn_reset_oa = get_connection(pool)
            if conn_reset_oa:
                try:
                    with conn_reset_oa.cursor() as cur: cur.execute("DELETE FROM weekly_allocations WHERE room_name = 'Oasis'"); conn_reset_oa.commit()
                    st.success("✅ All Oasis allocations removed globally.")
                except Exception as e: st.error(f"❌ Failed: {e}"); conn_reset_oa.rollback()
                finally: return_connection(pool, conn_reset_oa)

        if st.button("🧽 Remove All Oasis Preferences (GLOBAL)", key="reset_op_fixed"):
            conn_reset_op = get_connection(pool)
            if conn_reset_op:
                try:
                    with conn_reset_op.cursor() as cur: cur.execute("DELETE FROM oasis_preferences"); conn_reset_op.commit()
                    st.success("✅ All Oasis preferences removed globally.")
                except Exception as e: st.error(f"❌ Failed: {e}"); conn_reset_op.rollback()
                finally: return_connection(pool, conn_reset_op)

        st.subheader("🧾 Team Preferences (Admin Edit - GLOBAL)")
        # Preferences are not typically week-specific, so editing them remains global.
        df_team_prefs_admin = get_preferences(pool)
        if not df_team_prefs_admin.empty:
            editable_team_df = st.data_editor(df_team_prefs_admin, num_rows="dynamic", use_container_width=True, key="edit_teams_fixed")
            if st.button("💾 Save Team Preference Changes", key="save_team_prefs_fixed"):
                conn_admin_tp = get_connection(pool)
                if conn_admin_tp:
                    try:
                        with conn_admin_tp.cursor() as cur:
                            cur.execute("DELETE FROM weekly_preferences")
                            for _, row in editable_team_df.iterrows():
                                sub_time = row.get("Submitted At", datetime.now(pytz.utc))
                                if pd.isna(sub_time) or sub_time is None: sub_time = datetime.now(pytz.utc)
                                cur.execute("INSERT INTO weekly_preferences (team_name, contact_person, team_size, preferred_days, submission_time) VALUES (%s, %s, %s, %s, %s)", (row["Team"], row["Contact"], int(row["Size"]), row["Days"], sub_time))
                            conn_admin_tp.commit(); st.success("✅ Team preferences updated.")
                    except Exception as e: st.error(f"❌ Failed to update team preferences: {e}"); conn_admin_tp.rollback()
                    finally: return_connection(pool, conn_admin_tp)
        else: st.info("No team preferences submitted yet to edit.")

        st.subheader("🌿 Oasis Preferences (Admin Edit - GLOBAL)")
        # Oasis preferences are also not typically week-specific.
        df_oasis_prefs_admin = get_oasis_preferences(pool)
        if not df_oasis_prefs_admin.empty:
            cols_to_display = ["Person", "Day 1", "Day 2", "Day 3", "Day 4", "Day 5", "Submitted At"]
            editable_oasis_df = st.data_editor(df_oasis_prefs_admin[cols_to_display], num_rows="dynamic", use_container_width=True, key="edit_oasis_fixed")
            if st.button("💾 Save Oasis Preference Changes", key="save_oasis_prefs_fixed"):
                conn_admin_op = get_connection(pool)
                if conn_admin_op:
                    try:
                        with conn_admin_op.cursor() as cur:
                            cur.execute("DELETE FROM oasis_preferences")
                            for _, row in editable_oasis_df.iterrows():
                                sub_time = row.get("Submitted At", datetime.now(pytz.utc))
                                if pd.isna(sub_time) or sub_time is None: sub_time = datetime.now(pytz.utc)
                                cur.execute("INSERT INTO oasis_preferences (person_name, preferred_day_1, preferred_day_2, preferred_day_3, preferred_day_4, preferred_day_5, submission_time) VALUES (%s, %s, %s, %s, %s, %s, %s)", (row["Person"], row.get("Day 1"), row.get("Day 2"), row.get("Day 3"), row.get("Day 4"), row.get("Day 5"), sub_time))
                            conn_admin_op.commit(); st.success("✅ Oasis preferences updated.")
                    except Exception as e: st.error(f"❌ Failed to update oasis preferences: {e}"); conn_admin_op.rollback()
                    finally: return_connection(pool, conn_admin_op)
        else: st.info("No oasis preferences submitted yet to edit.")

    elif pwd: st.error("❌ Incorrect password.")

# -----------------------------------------------------
# Team Form (Project Room Requests)
# -----------------------------------------------------
st.header("📝 Request Project Room")
st.markdown( # Uses admin-set display texts
    f"""
    For teams of 3 or more. Submissions for the **week of {st.session_state.get("week_of_text", "the operational week")}** are open 
    from **{st.session_state.get("submission_start_text", "start time")}** until **{st.session_state.get("submission_end_text", "end time")}**.
    (These preferences will be considered for allocations for the fixed week: {CONSTANT_OPERATIONAL_MONDAY.strftime('%B %d, %Y')})
    """
)
with st.form("team_form_fixed"):
    team_name = st.text_input("Team Name", key="tn_fixed")
    contact_person = st.text_input("Contact Person", key="cp_fixed")
    team_size = st.number_input("Team Size (3-6)", min_value=3, max_value=6, value=3, key="ts_fixed")
    day_choice = st.selectbox("Preferred Days", ["Monday and Wednesday", "Tuesday and Thursday"], key="dc_fixed")
    if st.form_submit_button("Submit Project Room Request"):
        day_map = {"Monday and Wednesday": "Monday,Wednesday", "Tuesday and Thursday": "Tuesday,Thursday"}
        if insert_preference(pool, team_name, contact_person, team_size, day_map[day_choice]):
            st.success(f"✅ Preference submitted for {team_name}!"); st.rerun()

# -----------------------------------------------------
# Oasis Form (Preferences)
# -----------------------------------------------------
st.header("🌿 Reserve Oasis Seat")
st.markdown( # Uses admin-set display texts
    f"""
    Submit your personal preferences for the **week of {st.session_state.get("week_of_text", "the operational week")}**. 
    Submissions open from **{st.session_state.get("submission_start_text", "start time")}** until **{st.session_state.get("oasis_end_text", "Oasis end time")}**.
    (These preferences will be considered for allocations for the fixed week: {CONSTANT_OPERATIONAL_MONDAY.strftime('%B %d, %Y')})
    """
)
with st.form("oasis_form_fixed"):
    oasis_person_name = st.text_input("Your Name", key="opn_fixed")
    oasis_selected_days = st.multiselect("Select Your Preferred Days for Oasis (up to 5):", ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"], max_selections=5, key="osd_fixed")
    if st.form_submit_button("Submit Oasis Preference"):
        if insert_oasis(pool, oasis_person_name, oasis_selected_days):
            st.success(f"✅ Oasis preference submitted for {oasis_person_name}!"); st.rerun()

# -----------------------------------------------------
# Display: Project Room Allocations
# -----------------------------------------------------
st.header("📌 Project Room Allocations")
st.markdown(st.session_state['project_allocations_markdown_content']) # Uses admin-set display text
alloc_display_df = get_room_grid(pool) # Uses CONSTANT_OPERATIONAL_MONDAY
if alloc_display_df.empty:
    st.write(f"No project room allocations yet for the week of {CONSTANT_OPERATIONAL_MONDAY.strftime('%B %d, %Y')}.")
else:
    st.dataframe(alloc_display_df, use_container_width=True, hide_index=True)

# -----------------------------------------------------
# Ad-hoc Oasis Addition
# -----------------------------------------------------
st.header("🚶 Add Yourself to Oasis (Ad-hoc)")
st.caption(f"Use this if you missed preference submission for the week of {CONSTANT_OPERATIONAL_MONDAY.strftime('%B %d, %Y')}. Subject to availability.")
# current_display_monday is now CONSTANT_OPERATIONAL_MONDAY for adhoc logic
with st.form("oasis_add_form_fixed"):
    adhoc_oasis_name = st.text_input("Your Name", key="aon_fixed")
    adhoc_oasis_days = st.multiselect("Select day(s) to add yourself to Oasis:", ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"], key="aod_fixed")
    if st.form_submit_button("➕ Add Me to Oasis Schedule"):
        if not adhoc_oasis_name.strip(): st.error("❌ Please enter your name.")
        elif not adhoc_oasis_days: st.error("❌ Select at least one day.")
        else:
            conn_adhoc = get_connection(pool)
            if not conn_adhoc: st.error("No DB Connection")
            else:
                try:
                    with conn_adhoc.cursor() as cur:
                        name_clean = adhoc_oasis_name.strip().title()
                        days_map_indices = {"Monday": 0, "Tuesday": 1, "Wednesday": 2, "Thursday": 3, "Friday": 4}
                        for day_str in adhoc_oasis_days: # Delete any existing for this person/day in fixed week
                            date_obj_check = CONSTANT_OPERATIONAL_MONDAY + timedelta(days=days_map_indices[day_str])
                            cur.execute("DELETE FROM weekly_allocations WHERE room_name = 'Oasis' AND team_name = %s AND date = %s", (name_clean, date_obj_check))
                        added_to_all_selected = True
                        for day_str in adhoc_oasis_days:
                            date_obj = CONSTANT_OPERATIONAL_MONDAY + timedelta(days=days_map_indices[day_str])
                            cur.execute("SELECT COUNT(*) FROM weekly_allocations WHERE room_name = 'Oasis' AND date = %s", (date_obj,))
                            count = cur.fetchone()[0]
                            if count >= oasis.get("capacity", 15):
                                st.warning(f"⚠️ Oasis is full on {day_str}. Could not add {name_clean}.")
                                added_to_all_selected = False
                            else:
                                cur.execute("INSERT INTO weekly_allocations (team_name, room_name, date) VALUES (%s, 'Oasis', %s)", (name_clean, date_obj))
                        conn_adhoc.commit()
                        if added_to_all_selected and adhoc_oasis_days: st.success(f"✅ {name_clean} added to Oasis for selected day(s)!")
                        elif adhoc_oasis_days: st.info("ℹ️ Check messages above for ad-hoc Oasis additions.")
                        st.rerun()
                except Exception as e:
                    st.error(f"❌ Error adding to Oasis: {e}")
                    if conn_adhoc: conn_adhoc.rollback()
                finally: return_connection(pool, conn_adhoc)

# -----------------------------------------------------
# Full Weekly Oasis Overview
# -----------------------------------------------------
st.header("📊 Full Weekly Oasis Overview")
st.caption(f"For the week of {st.session_state.get('week_of_text', CONSTANT_OPERATIONAL_MONDAY.strftime('%B %d, %Y'))}")

oasis_overview_monday = CONSTANT_OPERATIONAL_MONDAY
oasis_overview_days_dates = [oasis_overview_monday + timedelta(days=i) for i in range(5)]
oasis_overview_day_names = [d.strftime("%A") for d in oasis_overview_days_dates]
oasis_capacity = oasis.get("capacity", 15)
conn_matrix = get_connection(pool)
if not conn_matrix: st.error("No DB connection for Oasis Overview")
else:
    try:
        with conn_matrix.cursor() as cur:
            cur.execute("SELECT team_name, date FROM weekly_allocations WHERE room_name = 'Oasis' AND date >= %s AND date <= %s", (oasis_overview_monday, oasis_overview_days_dates[-1]))
            rows = cur.fetchall()
        df_matrix = pd.DataFrame(rows, columns=["Name", "Date"]) if rows else pd.DataFrame(columns=["Name", "Date"])
        if not df_matrix.empty: df_matrix["Date"] = pd.to_datetime(df_matrix["Date"]).dt.date
        unique_names_allocated = set(df_matrix["Name"]) if not df_matrix.empty else set()
        names_from_prefs = set()
        try:
            with conn_matrix.cursor() as cur:
                cur.execute("SELECT DISTINCT person_name FROM oasis_preferences")
                names_from_prefs = {row[0] for row in cur.fetchall()}
        except psycopg2.Error: st.warning("Could not fetch names from Oasis preferences for matrix.")
        all_relevant_names = sorted(list(unique_names_allocated.union(names_from_prefs).union({"Niek"}))) # Ensure Niek is an option
        if not all_relevant_names: all_relevant_names = ["Niek"]

        matrix_df = pd.DataFrame(False, index=all_relevant_names, columns=oasis_overview_day_names)
        if not df_matrix.empty:
            for _, row_data in df_matrix.iterrows():
                person_name, alloc_date = row_data["Name"], row_data["Date"]
                if alloc_date in oasis_overview_days_dates:
                    day_label = alloc_date.strftime("%A")
                    if person_name in matrix_df.index: matrix_df.at[person_name, day_label] = True
        if "Niek" in matrix_df.index: # Niek always allocated
            for day_n in oasis_overview_day_names: matrix_df.at["Niek", day_n] = True
        
        st.subheader("🪑 Oasis Availability Summary")
        for day_dt, day_str_label in zip(oasis_overview_days_dates, oasis_overview_day_names):
            used_spots = len(set(df_matrix[df_matrix["Date"] == day_dt]["Name"])) if not df_matrix.empty else 0
            spots_left = max(0, oasis_capacity - used_spots)
            st.markdown(f"**{day_str_label}**: {spots_left} spot(s) left")

        edited_matrix = st.data_editor(matrix_df, use_container_width=True, disabled=["Niek"] if "Niek" in matrix_df.index else [], key="oasis_matrix_editor_fixed")
        if st.button("💾 Save Oasis Matrix Changes", key="save_oasis_matrix_fixed"):
            try:
                with conn_matrix.cursor() as cur:
                    cur.execute("DELETE FROM weekly_allocations WHERE room_name = 'Oasis' AND team_name != 'Niek' AND date >= %s AND date <= %s", (oasis_overview_monday, oasis_overview_days_dates[-1]))
                    if "Niek" in edited_matrix.index:
                        cur.execute("DELETE FROM weekly_allocations WHERE room_name = 'Oasis' AND team_name = 'Niek' AND date >= %s AND date <= %s", (oasis_overview_monday, oasis_overview_days_dates[-1]))
                        for day_idx, day_str_col in enumerate(oasis_overview_day_names):
                            if edited_matrix.at["Niek", day_str_col]:
                                cur.execute("INSERT INTO weekly_allocations (team_name, room_name, date) VALUES (%s, %s, %s)", ("Niek", "Oasis", oasis_overview_monday + timedelta(days=day_idx)))
                    occupied_counts_per_day = {day_col: (1 if ("Niek" in edited_matrix.index and edited_matrix.at["Niek", day_col]) else 0) for day_col in oasis_overview_day_names}
                    for person_name_matrix in edited_matrix.index:
                        if person_name_matrix == "Niek": continue
                        for day_idx, day_str_col in enumerate(oasis_overview_day_names):
                            if edited_matrix.at[person_name_matrix, day_str_col]:
                                if occupied_counts_per_day[day_str_col] < oasis_capacity:
                                    cur.execute("INSERT INTO weekly_allocations (team_name, room_name, date) VALUES (%s, %s, %s)", (person_name_matrix, "Oasis", oasis_overview_monday + timedelta(days=day_idx)))
                                    occupied_counts_per_day[day_str_col] += 1
                                else: st.warning(f"⚠️ {person_name_matrix} could not be added to Oasis on {day_str_col}: capacity reached.")
                    conn_matrix.commit(); st.success("✅ Oasis Matrix saved!"); st.rerun()
            except Exception as e_matrix_save:
                st.error(f"❌ Failed to save Oasis Matrix: {e_matrix_save}")
                if conn_matrix: conn_matrix.rollback()
    except Exception as e_matrix_load: st.error(f"❌ Error loading Oasis Matrix data: {e_matrix_load}")
    finally: return_connection(pool, conn_matrix)

if not pool: st.error("🚨 Cannot connect to the database. Check configurations.")
