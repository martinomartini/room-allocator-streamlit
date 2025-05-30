import streamlit as st
import psycopg2
import psycopg2.pool
import json
import os
from datetime import datetime, timedelta
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
# Database Utility Functions
# -----------------------------------------------------
def get_room_grid(pool):
    """Fetch current week's allocations (non-Oasis) from the database and build a grid (DataFrame)."""
    if not pool:
        return pd.DataFrame()

    today = datetime.now(OFFICE_TIMEZONE).date()
    this_monday = today - timedelta(days=today.weekday())
    day_mapping = {
        this_monday + timedelta(days=0): "Monday",
        this_monday + timedelta(days=1): "Tuesday",
        this_monday + timedelta(days=2): "Wednesday",
        this_monday + timedelta(days=3): "Thursday"
    }
    day_labels = list(day_mapping.values())

    # Fetch all room names from rooms.json (excluding Oasis)
    try:
        with open(ROOMS_FILE) as f:
            all_rooms = [r["name"] for r in json.load(f) if r["name"] != "Oasis"]
    except (FileNotFoundError, json.JSONDecodeError):
        st.error(f"Error: Could not load valid data from {ROOMS_FILE}.")
        return pd.DataFrame()

    # Initialize every room as "Vacant" for each day
    grid = {
        room: {**{"Room": room}, **{day: "Vacant" for day in day_labels}}
        for room in all_rooms
    }

    conn = get_connection(pool)
    if not conn:
        return pd.DataFrame(grid.values())  # Return empty grid if no connection

    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # Fetch project room allocations
            cur.execute("""
                SELECT team_name, room_name, date
                FROM weekly_allocations
                WHERE room_name != 'Oasis'
            """)
            allocations = cur.fetchall()

            # Fetch team contact info
            cur.execute("""
                SELECT team_name, contact_person
                FROM weekly_preferences
            """)
            contacts = {row["team_name"]: row["contact_person"] for row in cur.fetchall()}

        # Fill the grid with allocated data
        for row in allocations:
            team = row["team_name"]
            room = row["room_name"]
            date = row["date"]
            day = day_mapping.get(date)
            if room not in grid or not day:
                continue
            contact = contacts.get(team)
            label = f"{team} ({contact})" if contact else team
            grid[room][day] = label

        return pd.DataFrame(grid.values())

    except psycopg2.Error as e:
        st.warning(f"Database error while getting room grid: {e}")
        return pd.DataFrame(grid.values())
    finally:
        return_connection(pool, conn)

def get_oasis_grid(pool):
    """Fetch Oasis allocations for the current week."""
    if not pool:
        return pd.DataFrame()

    conn = get_connection(pool)
    if not conn:
        return pd.DataFrame()

    try:
        with conn.cursor() as cur:
            cur.execute("SELECT team_name, room_name, date FROM weekly_allocations WHERE room_name = 'Oasis'")
            data = cur.fetchall()
            if not data:
                return pd.DataFrame()
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
        return pd.DataFrame()
    finally:
        return_connection(pool, conn)

def get_preferences(pool):
    """Fetch project room preferences."""
    if not pool:
        return pd.DataFrame()

    conn = get_connection(pool)
    if not conn:
        return pd.DataFrame()

    try:
        with conn.cursor() as cur:
            cur.execute("SELECT team_name, contact_person, team_size, preferred_days FROM weekly_preferences")
            rows = cur.fetchall()
            return pd.DataFrame(rows, columns=["Team", "Contact", "Size", "Days"])
    except Exception as e:
        st.warning(f"Failed to fetch preferences: {e}")
        return pd.DataFrame()
    finally:
        return_connection(pool, conn)

def get_oasis_preferences(pool):
    """Fetch Oasis preferences."""
    if not pool:
        return pd.DataFrame()

    conn = get_connection(pool)
    if not conn:
        return pd.DataFrame()

    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT person_name, preferred_day_1, preferred_day_2,
                       preferred_day_3, preferred_day_4, preferred_day_5, submission_time
                FROM oasis_preferences
            """)
            rows = cur.fetchall()
            return pd.DataFrame(rows, columns=["Person", "Day 1", "Day 2", "Day 3", "Day 4", "Day 5", "Submitted At"])
    except Exception as e:
        st.warning(f"Failed to fetch oasis preferences: {e}")
        return pd.DataFrame()
    finally:
        return_connection(pool, conn)

# -----------------------------------------------------
# Insert / Update Functions
# -----------------------------------------------------
def insert_preference(pool, team, contact, size, days):
    """Insert a new team preference into weekly_preferences."""
    if not pool:
        return False
    if not team or not contact:
        st.error("❌ Team Name and Contact Person are required.")
        return False
    if size < 3:
        st.error("❌ Team size must be at least 3.")
        return False
    if size > 6:
        st.error("❌ Team size cannot exceed 6.")
        return False

    conn = get_connection(pool)
    if not conn:
        return False

    try:
        with conn.cursor() as cur:
            # Check if team already submitted
            cur.execute("SELECT 1 FROM weekly_preferences WHERE team_name = %s", (team,))
            if cur.fetchone():
                st.error(f"❌ Team '{team}' has already submitted a preference. Contact admin to change.")
                return False

            # Validate day choices (Monday & Wednesday or Tuesday & Thursday)
            new_days_set = set(days.split(','))
            valid_pairs = [set(["Monday", "Wednesday"]), set(["Tuesday", "Thursday"])]
            if new_days_set not in valid_pairs:
                st.error("❌ Invalid day selection. Must select Monday & Wednesday or Tuesday & Thursday.")
                return False

            cur.execute(
                """
                INSERT INTO weekly_preferences (team_name, contact_person, team_size, preferred_days, submission_time)
                VALUES (%s, %s, %s, %s, NOW() AT TIME ZONE 'UTC')
                """,
                (team, contact, size, days)
            )
            conn.commit()
            return True
    except psycopg2.Error as e:
        st.error(f"Database insert failed: {e}")
        conn.rollback()
        return False
    finally:
        return_connection(pool, conn)

def insert_oasis(pool, person, selected_days):
    """Insert a new Oasis preference for a single person."""
    if not pool:
        return False
    if not person:
        st.error("❌ Please enter your name.")
        return False
    if not selected_days or len(selected_days) == 0:
        st.error("❌ Select at least 1 preferred day.")
        return False
    if len(selected_days) > 5:
        st.error("❌ You can select a maximum of 5 preferred days.")
        return False

    conn = get_connection(pool)
    if not conn:
        return False

    try:
        with conn.cursor() as cur:
            # Check if already submitted
            cur.execute("SELECT 1 FROM oasis_preferences WHERE person_name = %s", (person,))
            if cur.fetchone():
                st.error("❌ You've already submitted. Contact admin to change your selection.")
                return False

            # Pad to 5 days with NULLs if needed
            padded_days = selected_days + [None] * (5 - len(selected_days))
            cur.execute(
                """
                INSERT INTO oasis_preferences (
                    person_name,
                    preferred_day_1,
                    preferred_day_2,
                    preferred_day_3,
                    preferred_day_4,
                    preferred_day_5,
                    submission_time
                )
                VALUES (%s, %s, %s, %s, %s, %s, NOW() AT TIME ZONE 'UTC')
                """,
                (person.strip(), *padded_days)
            )
            conn.commit()
            return True
    except psycopg2.Error as e:
        st.error(f"Oasis insert failed: {e}")
        conn.rollback()
        return False
    finally:
        return_connection(pool, conn)

# -----------------------------------------------------
# Helper to load/update default date text
# -----------------------------------------------------
if "week_of_text" not in st.session_state:
    st.session_state["week_of_text"] = "9 June"
if "submission_start_text" not in st.session_state:
    st.session_state["submission_start_text"] = "Wednesday 4 June 09:00"
if "submission_end_text" not in st.session_state:
    st.session_state["submission_end_text"] = "Thursday 5 June 16:00"
if "oasis_end_text" not in st.session_state:
    st.session_state["oasis_end_text"] = "Friday 6 June 16:00"

# -----------------------------------------------------
# Streamlit App UI
# -----------------------------------------------------
st.title("📅 Weekly Room Allocator")

st.info(
    """
    💡 **How This Works:**
    
    - 🧑‍🤝‍🧑 Project teams can select **either Monday & Wednesday** or **Tuesday & Thursday**. **Friday** is (for now) flexible. 
      There are 6 rooms for 4 persons and 1 room for 6 persons.
    - 🌿 Oasis users can choose **up to 5 preferred weekdays**, and will be randomly assigned—fairness is guaranteed. 
      There are 16 places in the Oasis.
    - ❗ You may only submit **once**. If you need to change your input, contact an admin.
    - 🗓️ **From Wednesday 09:00** you can submit your **project room preference** until **Thursday 16:00**. 
      The allocations will be shared on **Thursday at 16:00**.
    - 🌿 **Oasis preferences** can be submitted **from Wednesday 09:00 until Friday 16:00**, 
      and allocation will be done at **Friday 16:00**.
    - ✅ Allocations are refreshed **weekly** by an admin. 
        
    ---
    
    ### 🌿 Oasis: How to Join
    
    1. **✅ Reserve Oasis Seat (recommended)**  
       ➤ Submit your **preferred days** (up to 5).  
       ➤ Allocation is done **automatically and fairly** at **Friday 16:00**.  
       ➤ Everyone gets **at least one** of their preferred days, depending on availability.

    2. **⚠️ Add Yourself to Oasis Allocation (only if you forgot)**  
       ➤ Use this **only if you missed the deadline** or forgot to submit your preferences.  
       ➤ You will be added **immediately** to the selected days **if there’s space left**.  
       ➤ This option does **not guarantee fairness** and bypasses the regular process.

    ℹ️ Always use **"Reserve Oasis Seat"** before Friday 16:00 to ensure fair participation.  
    Only use **"Add Yourself"** if you forgot to register.
    """
)

now_local = datetime.now(OFFICE_TIMEZONE)
st.info(f"Current Office Time: **{now_local.strftime('%Y-%m-%d %H:%M:%S')}** ({OFFICE_TIMEZONE_STR})")

# ---------------- Admin Controls ---------------------
with st.expander("🔐 Admin Controls"):
    pwd = st.text_input("Enter admin password:", type="password", key="admin_pwd")

    if pwd == RESET_PASSWORD:
        st.success("✅ Access granted.")

        # Button to update date references in markdown
        st.subheader("💼 Update Markdown Dates")
        new_week_of_text = st.text_input("Week of (e.g., '9 June')", st.session_state["week_of_text"])
        new_sub_start_text = st.text_input("Submission start (e.g., 'Wednesday 4 June 09:00')", st.session_state["submission_start_text"])
        new_sub_end_text = st.text_input("Submission end (e.g., 'Thursday 5 June 16:00')", st.session_state["submission_end_text"])
        new_oasis_end_text = st.text_input("Oasis end (e.g., 'Friday 6 June 16:00')", st.session_state["oasis_end_text"])
        if st.button("Update Date Text"):
            st.session_state["week_of_text"] = new_week_of_text
            st.session_state["submission_start_text"] = new_sub_start_text
            st.session_state["submission_end_text"] = new_sub_end_text
            st.session_state["oasis_end_text"] = new_oasis_end_text
            st.success("Markdown date references updated!")

        # 1) Project Room Admin
        st.subheader("🧠 Project Room Admin")
        if st.button("🚀 Run Project Room Allocation"):
            if run_allocation:
                success, _ = run_allocation(DATABASE_URL, only="project")
                if success:
                    st.success("✅ Project room allocation completed.")
                else:
                    st.error("❌ Project room allocation failed.")
            else:
                st.error("run_allocation function not available.")

        # 2) Oasis Admin
        st.subheader("🌿 Oasis Admin")
        if st.button("🎲 Run Oasis Allocation"):
            if run_allocation:
                success, _ = run_allocation(DATABASE_URL, only="oasis")
                if success:
                    st.success("✅ Oasis allocation completed.")
                else:
                    st.error("❌ Oasis allocation failed.")
            else:
                st.error("run_allocation function not available.")

        # 3) Project Room Allocations (Admin Edit)
        st.subheader("📌 Project Room Allocations (Admin Edit)")
        try:
            alloc_df_admin = get_room_grid(pool)
            if not alloc_df_admin.empty:
                editable_alloc = st.data_editor(alloc_df_admin, num_rows="dynamic", use_container_width=True, key="edit_allocations")
                if st.button("💾 Save Project Room Allocation Changes"):
                    conn_admin_alloc = get_connection(pool)
                    if not conn_admin_alloc:
                        st.error("No DB connection")
                    else:
                        try:
                            with conn_admin_alloc.cursor() as cur:
                                # Clear existing allocations for non-Oasis rooms
                                cur.execute("DELETE FROM weekly_allocations WHERE room_name != 'Oasis'")

                                # Reinsert based on the edited DataFrame
                                today_admin = datetime.now(OFFICE_TIMEZONE).date()
                                this_monday_admin = today_admin - timedelta(days=today_admin.weekday())
                                day_indices = {"Monday": 0, "Tuesday": 1, "Wednesday": 2, "Thursday": 3}

                                for _, row in editable_alloc.iterrows():
                                    for day_name, day_idx in day_indices.items():
                                        value = row.get(day_name, "")
                                        if value and value != "Vacant":
                                            team_info = str(value).split("(")[0].strip()
                                            room_name = str(row["Room"]) if pd.notnull(row["Room"]) else None
                                            alloc_date = this_monday_admin + timedelta(days=day_idx)
                                            if team_info and room_name:
                                                cur.execute(
                                                    """
                                                    INSERT INTO weekly_allocations (team_name, room_name, date)
                                                    VALUES (%s, %s, %s)
                                                    """,
                                                    (team_info, room_name, alloc_date)
                                                )
                            conn_admin_alloc.commit()
                            st.success("✅ Manual project room allocations updated.")
                        except Exception as e:
                            st.error(f"❌ Failed to save project room allocations: {e}")
                            if conn_admin_alloc:
                                conn_admin_alloc.rollback()
                        finally:
                            return_connection(pool, conn_admin_alloc)
            else:
                st.info("No project room allocations yet to edit.")
        except Exception as e:
            st.warning(f"Failed to load project room allocation data for admin: {e}")

        # 4) Reset Project Room Data
        st.subheader("🧹 Reset Project Room Data")
        if st.button("🗑️ Remove All Project Room Allocations (Non-Oasis)"):
            conn_reset_pra = get_connection(pool)
            if conn_reset_pra:
                try:
                    with conn_reset_pra.cursor() as cur:
                        cur.execute("DELETE FROM weekly_allocations WHERE room_name != 'Oasis'")
                        conn_reset_pra.commit()
                        st.success("✅ Project room allocations (non-Oasis) removed.")
                except Exception as e:
                    st.error(f"❌ Failed: {e}")
                    conn_reset_pra.rollback()
                finally:
                    return_connection(pool, conn_reset_pra)

        if st.button("🧽 Remove All Project Room Preferences"):
            conn_reset_prp = get_connection(pool)
            if conn_reset_prp:
                try:
                    with conn_reset_prp.cursor() as cur:
                        cur.execute("DELETE FROM weekly_preferences")
                        conn_reset_prp.commit()
                        st.success("✅ All project room preferences removed.")
                except Exception as e:
                    st.error(f"❌ Failed: {e}")
                    conn_reset_prp.rollback()
                finally:
                    return_connection(pool, conn_reset_prp)

        # 5) Reset Oasis Data
        st.subheader("🌾 Reset Oasis Data")
        if st.button("🗑️ Remove All Oasis Allocations"):
            conn_reset_oa = get_connection(pool)
            if conn_reset_oa:
                try:
                    with conn_reset_oa.cursor() as cur:
                        cur.execute("DELETE FROM weekly_allocations WHERE room_name = 'Oasis'")
                        conn_reset_oa.commit()
                        st.success("✅ All Oasis allocations removed.")
                except Exception as e:
                    st.error(f"❌ Failed: {e}")
                    conn_reset_oa.rollback()
                finally:
                    return_connection(pool, conn_reset_oa)

        if st.button("🧽 Remove All Oasis Preferences"):
            conn_reset_op = get_connection(pool)
            if conn_reset_op:
                try:
                    with conn_reset_op.cursor() as cur:
                        cur.execute("DELETE FROM oasis_preferences")
                        conn_reset_op.commit()
                        st.success("✅ All Oasis preferences removed.")
                except Exception as e:
                    st.error(f"❌ Failed: {e}")
                    conn_reset_op.rollback()
                finally:
                    return_connection(pool, conn_reset_op)

        # 6) Team Preferences (Admin Edit)
        st.subheader("🧾 Team Preferences (Admin Edit)")
        df_team_prefs_admin = get_preferences(pool)
        if not df_team_prefs_admin.empty:
            editable_team_df = st.data_editor(df_team_prefs_admin, num_rows="dynamic", use_container_width=True, key="edit_teams")
            if st.button("💾 Save Team Preference Changes"):
                conn_admin_tp = get_connection(pool)
                if conn_admin_tp:
                    try:
                        with conn_admin_tp.cursor() as cur:
                            # Clear before re-inserting
                            cur.execute("DELETE FROM weekly_preferences")
                            for _, row in editable_team_df.iterrows():
                                cur.execute(
                                    """
                                    INSERT INTO weekly_preferences (team_name, contact_person, team_size, preferred_days, submission_time)
                                    VALUES (%s, %s, %s, %s, NOW() AT TIME ZONE 'UTC')
                                    """,
                                    (row["Team"], row["Contact"], int(row["Size"]), row["Days"])
                                )
                            conn_admin_tp.commit()
                            st.success("✅ Team preferences updated.")
                    except Exception as e:
                        st.error(f"❌ Failed to update team preferences: {e}")
                        conn_admin_tp.rollback()
                    finally:
                        return_connection(pool, conn_admin_tp)
        else:
            st.info("No team preferences submitted yet to edit.")

        # 7) Oasis Preferences (Admin Edit)
        st.subheader("🌿 Oasis Preferences (Admin Edit)")
        df_oasis_prefs_admin = get_oasis_preferences(pool)
        if not df_oasis_prefs_admin.empty:
            cols_to_display = ["Person", "Day 1", "Day 2", "Day 3", "Day 4", "Day 5", "Submitted At"]
            editable_oasis_df = st.data_editor(df_oasis_prefs_admin[cols_to_display], num_rows="dynamic", use_container_width=True, key="edit_oasis")
            if st.button("💾 Save Oasis Preference Changes"):
                conn_admin_op = get_connection(pool)
                if conn_admin_op:
                    try:
                        with conn_admin_op.cursor() as cur:
                            cur.execute("DELETE FROM oasis_preferences")
                            for _, row in editable_oasis_df.iterrows():
                                sub_time = row.get("Submitted At", datetime.now(pytz.utc))
                                if pd.isna(sub_time) or sub_time is None:
                                    sub_time = datetime.now(pytz.utc)
                                cur.execute(
                                    """
                                    INSERT INTO oasis_preferences (
                                        person_name, preferred_day_1, preferred_day_2,
                                        preferred_day_3, preferred_day_4, preferred_day_5, submission_time
                                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                                    """,
                                    (
                                        row["Person"], row.get("Day 1"), row.get("Day 2"),
                                        row.get("Day 3"), row.get("Day 4"), row.get("Day 5"),
                                        sub_time
                                    )
                                )
                            conn_admin_op.commit()
                            st.success("✅ Oasis preferences updated.")
                    except Exception as e:
                        st.error(f"❌ Failed to update oasis preferences: {e}")
                        conn_admin_op.rollback()
                    finally:
                        return_connection(pool, conn_admin_op)
        else:
            st.info("No oasis preferences submitted yet to edit.")

    elif pwd:
        # If a password was entered but doesn't match
        st.error("❌ Incorrect password.")

# -----------------------------------------------------
# Team Form (Project Room Requests)
# -----------------------------------------------------
st.header("📝 Request Project Room")

st.markdown(
    f"""
    For teams of 3 or more. Submissions for the **week of {st.session_state["week_of_text"]}** are open 
    from **{st.session_state["submission_start_text"]}** until **{st.session_state["submission_end_text"]}**.
    """
)

with st.form("team_form"):
    team_name = st.text_input("Team Name")
    contact_person = st.text_input("Contact Person")
    team_size = st.number_input("Team Size (3-6)", min_value=3, max_value=6, value=3)
    day_choice = st.selectbox("Preferred Days", ["Monday and Wednesday", "Tuesday and Thursday"])
    submit_team_pref = st.form_submit_button("Submit Project Room Request")

    if submit_team_pref:
        day_map = {
            "Monday and Wednesday": "Monday,Wednesday",
            "Tuesday and Thursday": "Tuesday,Thursday"
        }
        if insert_preference(pool, team_name, contact_person, team_size, day_map[day_choice]):
            st.success(f"✅ Preference submitted for {team_name}!")
            st.rerun()

# -----------------------------------------------------
# Oasis Form (Preferences)
# -----------------------------------------------------
st.header("🌿 Reserve Oasis Seat")

st.markdown(
    f"""
    Submit your personal preferences for the **week of {st.session_state["week_of_text"]}**. 
    Submissions open from **{st.session_state["submission_start_text"]}** until **{st.session_state["oasis_end_text"]}**.
    """
)

with st.form("oasis_form"):
    oasis_person_name = st.text_input("Your Name", key="oasis_person")
    oasis_selected_days = st.multiselect(
        "Select Your Preferred Days for Oasis (up to 5):",
        ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"],
        max_selections=5
    )
    submit_oasis_pref = st.form_submit_button("Submit Oasis Preference")

    if submit_oasis_pref:
        if insert_oasis(pool, oasis_person_name, oasis_selected_days):
            st.success(f"✅ Oasis preference submitted for {oasis_person_name}!")
            st.rerun()

# -----------------------------------------------------
# Display: Project Room Allocations
# -----------------------------------------------------
st.header("📌 Project Room Allocations")
alloc_display_df = get_room_grid(pool)
if alloc_display_df.empty:
    st.write("No project room allocations yet for the current week.")
else:
    st.dataframe(alloc_display_df, use_container_width=True, hide_index=True)

# -----------------------------------------------------
# Ad-hoc Oasis Addition
# -----------------------------------------------------
st.header("🚶 Add Yourself to Oasis (Ad-hoc)")
st.caption("Use this if you missed the preference submission. Subject to availability.")

current_display_monday = datetime.now(OFFICE_TIMEZONE).date() - timedelta(days=datetime.now(OFFICE_TIMEZONE).weekday())

with st.form("oasis_add_form"):
    adhoc_oasis_name = st.text_input("Your Name", key="adhoc_name")
    adhoc_oasis_days = st.multiselect(
        "Select day(s) to add yourself to Oasis:",
        ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"],
        key="adhoc_days"
    )
    add_adhoc_submit = st.form_submit_button("➕ Add Me to Oasis Schedule")

    if add_adhoc_submit:
        if not adhoc_oasis_name.strip():
            st.error("❌ Please enter your name.")
        elif not adhoc_oasis_days:
            st.error("❌ Select at least one day.")
        else:
            conn_adhoc = get_connection(pool)
            if not conn_adhoc:
                st.error("No DB Connection")
            else:
                try:
                    with conn_adhoc.cursor() as cur:
                        name_clean = adhoc_oasis_name.strip().title()
                        days_map_indices = {
                            "Monday": 0, "Tuesday": 1,
                            "Wednesday": 2, "Thursday": 3, "Friday": 4
                        }
                        # Remove any existing entries for this week
                        for day_str in adhoc_oasis_days:
                            date_obj_check = current_display_monday + timedelta(days=days_map_indices[day_str])
                            cur.execute(
                                """
                                DELETE FROM weekly_allocations
                                WHERE room_name = 'Oasis' AND team_name = %s AND date = %s
                                """,
                                (name_clean, date_obj_check)
                            )

                        # Add the person if space is available
                        added_to_all_selected = True
                        for day_str in adhoc_oasis_days:
                            date_obj = current_display_monday + timedelta(days=days_map_indices[day_str])
                            cur.execute(
                                "SELECT COUNT(*) FROM weekly_allocations WHERE room_name = 'Oasis' AND date = %s",
                                (date_obj,)
                            )
                            count = cur.fetchone()[0]
                            if count >= oasis.get("capacity", 15):
                                st.warning(f"⚠️ Oasis is full on {day_str}. Could not add {name_clean}.")
                                added_to_all_selected = False
                            else:
                                cur.execute(
                                    """
                                    INSERT INTO weekly_allocations (team_name, room_name, date)
                                    VALUES (%s, 'Oasis', %s)
                                    """,
                                    (name_clean, date_obj)
                                )
                        conn_adhoc.commit()
                        if added_to_all_selected and adhoc_oasis_days:
                            st.success(f"✅ {name_clean} added to Oasis for selected day(s)!")
                        elif adhoc_oasis_days:
                            st.info("ℹ️ Check messages above for details on your ad-hoc Oasis additions.")
                        st.rerun()
                except Exception as e:
                    st.error(f"❌ Error adding to Oasis: {e}")
                    if conn_adhoc:
                        conn_adhoc.rollback()
                finally:
                    return_connection(pool, conn_adhoc)

# -----------------------------------------------------
# Full Weekly Oasis Overview
# -----------------------------------------------------
st.header("📊 Full Weekly Oasis Overview")

oasis_overview_monday = datetime.now(OFFICE_TIMEZONE).date() - timedelta(days=datetime.now(OFFICE_TIMEZONE).weekday())
oasis_overview_days_dates = [oasis_overview_monday + timedelta(days=i) for i in range(5)]
oasis_overview_day_names = [d.strftime("%A") for d in oasis_overview_days_dates]
oasis_capacity = oasis.get("capacity", 15)

conn_matrix = get_connection(pool)
if not conn_matrix:
    st.error("No DB connection for Oasis Overview")
else:
    try:
        with conn_matrix.cursor() as cur:
            cur.execute(
                """
                SELECT team_name, date 
                FROM weekly_allocations 
                WHERE room_name = 'Oasis' 
                  AND date >= %s 
                  AND date <= %s
                """,
                (oasis_overview_monday, oasis_overview_days_dates[-1])
            )
            rows = cur.fetchall()

        df_matrix = pd.DataFrame(rows, columns=["Name", "Date"]) if rows else pd.DataFrame(columns=["Name", "Date"])
        if not df_matrix.empty:
            df_matrix["Date"] = pd.to_datetime(df_matrix["Date"]).dt.date

        # Gather unique allocated names + any from preferences + "Niek"
        unique_names_allocated = set(df_matrix["Name"]) if not df_matrix.empty else set()
        names_from_prefs = set()
        try:
            with conn_matrix.cursor() as cur:
                cur.execute("SELECT DISTINCT person_name FROM oasis_preferences")
                pref_rows = cur.fetchall()
                names_from_prefs = {row[0] for row in pref_rows}
        except psycopg2.Error:
            st.warning("Could not fetch names from Oasis preferences for matrix display.")

        all_relevant_names = sorted(list(unique_names_allocated.union(names_from_prefs).union({"Niek"})))
        if not all_relevant_names:
            all_relevant_names = ["Niek"]

        matrix_df = pd.DataFrame(False, index=all_relevant_names, columns=oasis_overview_day_names)

        # Mark allocations in the matrix
        if not df_matrix.empty:
            for _, row_data in df_matrix.iterrows():
                person_name = row_data["Name"]
                alloc_date = row_data["Date"]
                if alloc_date in oasis_overview_days_dates:
                    day_label = alloc_date.strftime("%A")
                    if person_name in matrix_df.index:
                        matrix_df.at[person_name, day_label] = True

        # Ensure Niek is always "signed up"
        if "Niek" in matrix_df.index:
            for day_n in oasis_overview_day_names:
                matrix_df.at["Niek", day_n] = True

        # Oasis Availability Summary
        st.subheader("🪑 Oasis Availability Summary")
        for day_dt, day_str_label in zip(oasis_overview_days_dates, oasis_overview_day_names):
            current_day_allocations = df_matrix[df_matrix["Date"] == day_dt]["Name"].tolist() if not df_matrix.empty else []
            used_spots = len(set(current_day_allocations))
            spots_left = max(0, oasis_capacity - used_spots)
            st.markdown(f"**{day_str_label} ({day_dt.strftime('%b %d')})**: {spots_left} spot(s) left")

        # Data Editor for manual matrix changes
        edited_matrix = st.data_editor(
            matrix_df,
            use_container_width=True,
            disabled=["Niek"] if "Niek" in matrix_df.index else [],
            key="oasis_matrix_editor"
        )

        if st.button("💾 Save Oasis Matrix Changes"):
            try:
                with conn_matrix.cursor() as cur:
                    # Clear existing Oasis allocations for the current week (except Niek)
                    cur.execute(
                        """
                        DELETE FROM weekly_allocations 
                        WHERE room_name = 'Oasis' 
                          AND team_name != 'Niek'
                          AND date >= %s 
                          AND date <= %s
                        """,
                        (oasis_overview_monday, oasis_overview_days_dates[-1])
                    )

                    # Re-insert Niek if indicated
                    if "Niek" in edited_matrix.index:
                        cur.execute(
                            """
                            DELETE FROM weekly_allocations 
                            WHERE room_name = 'Oasis' 
                              AND team_name = 'Niek'
                              AND date >= %s 
                              AND date <= %s
                            """,
                            (oasis_overview_monday, oasis_overview_days_dates[-1])
                        )
                        for day_idx, day_str_col in enumerate(oasis_overview_day_names):
                            if edited_matrix.at["Niek", day_str_col]:
                                date_obj_niek = oasis_overview_monday + timedelta(days=day_idx)
                                cur.execute(
                                    """
                                    INSERT INTO weekly_allocations (team_name, room_name, date)
                                    VALUES (%s, %s, %s)
                                    """,
                                    ("Niek", "Oasis", date_obj_niek)
                                )

                    # Re-insert others
                    occupied_counts_per_day = {
                        day_col: (1 if ("Niek" in edited_matrix.index and edited_matrix.at["Niek", day_col] == True) else 0)
                        for day_col in oasis_overview_day_names
                    }

                    for person_name_matrix in edited_matrix.index:
                        if person_name_matrix == "Niek":
                            continue
                        for day_idx, day_str_col in enumerate(oasis_overview_day_names):
                            if edited_matrix.at[person_name_matrix, day_str_col]:
                                if occupied_counts_per_day[day_str_col] < oasis_capacity:
                                    date_obj_alloc = oasis_overview_monday + timedelta(days=day_idx)
                                    cur.execute(
                                        """
                                        INSERT INTO weekly_allocations (team_name, room_name, date)
                                        VALUES (%s, %s, %s)
                                        """,
                                        (person_name_matrix, "Oasis", date_obj_alloc)
                                    )
                                    occupied_counts_per_day[day_str_col] += 1
                                else:
                                    st.warning(f"⚠️ {person_name_matrix} could not be added to Oasis on {day_str_col}: capacity reached.")

                    conn_matrix.commit()
                    st.success("✅ Oasis Matrix saved successfully!")
                    st.rerun()
            except Exception as e_matrix_save:
                st.error(f"❌ Failed to save Oasis Matrix: {e_matrix_save}")
                if conn_matrix:
                    conn_matrix.rollback()
    except Exception as e_matrix_load:
        st.error(f"❌ Error loading Oasis Matrix data: {e_matrix_load}")
    finally:
        return_connection(pool, conn_matrix)

# -----------------------------------------------------
# Final Note: DB connectivity check
# -----------------------------------------------------
if not pool:
    st.error("🚨 Cannot connect to the database. Please check configurations or contact an admin.")