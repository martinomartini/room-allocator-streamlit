import streamlit as st
import psycopg2
import psycopg2.pool
import json
import os
import pandas as pd
import pytz
from datetime import datetime, timedelta
from psycopg2.extras import RealDictCursor
from allocate_rooms import run_allocation  # Placeholder for your allocation logic

# -- Global Config / Constants --
st.set_page_config(page_title="Weekly Room Allocator", layout="wide")
DATABASE_URL = st.secrets.get("SUPABASE_DB_URI", os.environ.get("SUPABASE_DB_URI"))
OFFICE_TZ_STR = st.secrets.get("OFFICE_TIMEZONE", os.environ.get("OFFICE_TIMEZONE", "UTC"))
RESET_PASSWORD = "trainee"
ROOMS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'rooms.json')

# -- Timezone Handling --
try:
    OFFICE_TZ = pytz.timezone(OFFICE_TZ_STR)
except pytz.UnknownTimeZoneError:
    st.error(f"Invalid Timezone '{OFFICE_TZ_STR}', defaulting to UTC.")
    OFFICE_TZ = pytz.utc

def load_rooms(file_path):
    """Load room definitions from JSON."""
    try:
        with open(file_path, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        st.error(f"Cannot load rooms from {file_path}.")
    return []

AVAILABLE_ROOMS = load_rooms(ROOMS_FILE)
OASIS_INFO = next((r for r in AVAILABLE_ROOMS if r.get("name") == "Oasis"), {"capacity": 15})

# -- Database Pool --
@st.cache_resource
def create_db_pool():
    if not DATABASE_URL:
        st.error("No database URL configured.")
        return None
    return psycopg2.pool.SimpleConnectionPool(minconn=1, maxconn=25, dsn=DATABASE_URL)

pool = create_db_pool()

def db_connection():
    """Retrieve connection from pool."""
    return pool.getconn() if pool else None

def release_conn(conn):
    """Return connection to pool."""
    if pool and conn:
        pool.putconn(conn)

# -- Generic Helpers --
def fetch_records(query, params=()):
    """Fetch records from DB into a DataFrame."""
    conn = db_connection()
    if not conn:
        return pd.DataFrame()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, params)
            rows = cur.fetchall()
        return pd.DataFrame(rows)
    except Exception as e:
        st.warning(f"DB query failed: {e}")
        return pd.DataFrame()
    finally:
        release_conn(conn)

def execute_query(query, params=()):
    """Execute a statement (INSERT/UPDATE/DELETE)."""
    conn = db_connection()
    if not conn:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute(query, params)
        conn.commit()
        return True
    except Exception as e:
        st.error(f"DB execute failed: {e}")
        conn.rollback()
        return False
    finally:
        release_conn(conn)

# -- Queries: Project Rooms + Oasis --
def get_current_week_days():
    today = datetime.now(OFFICE_TZ).date()
    monday = today - timedelta(days=today.weekday())
    return monday, [monday + timedelta(days=i) for i in range(5)]

def get_project_allocation():
    monday, _ = get_current_week_days()
    day_map = {
        monday + timedelta(days=i): ["Monday", "Tuesday", "Wednesday", "Thursday"][i]
        for i in range(4)
    }
    rooms = [r["name"] for r in AVAILABLE_ROOMS if r["name"] != "Oasis"]
    # Create a "Vacant" grid
    result = {
        room: {"Room": room, "Monday": "Vacant", "Tuesday": "Vacant",
               "Wednesday": "Vacant", "Thursday": "Vacant"} for room in rooms
    }
    df = fetch_records(
        """SELECT team_name, room_name, date 
           FROM weekly_allocations 
           WHERE room_name != 'Oasis'"""
    )
    prefs_df = fetch_records(
        """SELECT team_name, contact_person 
           FROM weekly_preferences"""
    )
    contacts = dict(zip(prefs_df["team_name"], prefs_df["contact_person"])) if not prefs_df.empty else {}
    for _, row in df.iterrows():
        team, room, dt = row["team_name"], row["room_name"], row["date"]
        day = day_map.get(dt)
        if day and room in result:
            label = f"{team} ({contacts.get(team, '')})".strip()
            result[room][day] = label
    return pd.DataFrame(result.values())

def get_oasis_allocations():
    df = fetch_records(
        """SELECT team_name AS Person, date AS Date 
           FROM weekly_allocations 
           WHERE room_name = 'Oasis'"""
    )
    if df.empty:
        return pd.DataFrame()
    df["Date"] = pd.to_datetime(df["Date"])
    df["Day"] = df["Date"].dt.strftime('%A')
    all_days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
    grouped = df.groupby("Day")["Person"].apply(lambda x: ", ".join(sorted(set(x))))
    grouped = grouped.reindex(all_days, fill_value="Vacant").reset_index()
    grouped.rename(columns={"Day": "Weekday", "Person": "People"}, inplace=True)
    return grouped

# -- Submissions --
def insert_preference(team, contact, size, days):
    if not team or not contact:
        st.error("Team Name and Contact Person required.")
        return False
    if size < 3 or size > 6:
        st.error("Team size must be between 3 and 6.")
        return False
    # Validate day choice
    valid_pairs = [set(["Monday", "Wednesday"]), set(["Tuesday", "Thursday"])]
    day_set = set(days.split(','))
    if day_set not in valid_pairs:
        st.error("Must be Monday & Wednesday or Tuesday & Thursday.")
        return False
    # Check if team already exists
    existing = fetch_records("SELECT 1 FROM weekly_preferences WHERE team_name = %s", (team,))
    if not existing.empty:
        st.error(f"Team '{team}' has already submitted.")
        return False
    return execute_query(
        """INSERT INTO weekly_preferences (team_name, contact_person, team_size, preferred_days, submission_time)
           VALUES (%s, %s, %s, %s, NOW() AT TIME ZONE 'UTC')""",
        (team, contact, size, days)
    )

def insert_oasis(person, selected_days):
    if not person:
        st.error("Name required.")
        return False
    if not selected_days:
        st.error("Select at least one day.")
        return False
    if len(selected_days) > 5:
        st.error("Max 5 possible days.")
        return False
    # Check if user already submitted
    existing = fetch_records("SELECT 1 FROM oasis_preferences WHERE person_name = %s", (person,))
    if not existing.empty:
        st.error("You've already submitted. Contact admin to change.")
        return False
    # Insert preference
    padded_days = selected_days + [None]*(5 - len(selected_days))
    return execute_query(
        """INSERT INTO oasis_preferences (
             person_name, preferred_day_1, preferred_day_2, 
             preferred_day_3, preferred_day_4, preferred_day_5, submission_time
           ) VALUES (%s, %s, %s, %s, %s, %s, NOW() AT TIME ZONE 'UTC')""",
        (person.strip(), *padded_days)
    )

# -- App UI --
st.title("📅 Weekly Room Allocator")
now = datetime.now(OFFICE_TZ).strftime('%Y-%m-%d %H:%M:%S')
st.info(f"Office Time: **{now}** ({OFFICE_TZ_STR})")

# -- Admin Controls --
with st.expander("🔐 Admin Controls"):
    pwd = st.text_input("Password:", type="password")
    if pwd == RESET_PASSWORD:
        st.success("✅ Admin Access Granted")

        # Date references
        st.subheader("Update References")
        week_of = st.text_input("Week of:", st.session_state.get("week_of_text", "9 June"))
        sub_start = st.text_input(
            "Submission Start:", 
            st.session_state.get("submission_start_text", "Wednesday 4 June 09:00")
        )
        sub_end = st.text_input(
            "Submission End:", 
            st.session_state.get("submission_end_text", "Thursday 5 June 16:00")
        )
        oasis_end = st.text_input(
            "Oasis Submission End:", 
            st.session_state.get("oasis_end_text", "Friday 6 June 16:00")
        )
        if st.button("Update"):
            st.session_state["week_of_text"] = week_of
            st.session_state["submission_start_text"] = sub_start
            st.session_state["submission_end_text"] = sub_end
            st.session_state["oasis_end_text"] = oasis_end
            st.success("Updated references.")

        # Run allocations
        c1, c2 = st.columns(2)
        if c1.button("🚀 Run Project Room Allocation"):
            if run_allocation:
                success, _ = run_allocation(DATABASE_URL, only="project")
                st.success("Done!") if success else st.error("Failed")
        if c2.button("🌿 Run Oasis Allocation"):
            if run_allocation:
                success, _ = run_allocation(DATABASE_URL, only="oasis")
                st.success("Done!") if success else st.error("Failed")

        # Remove data
        st.subheader("Reset Data")
        col1, col2, col3 = st.columns(3)
        if col1.button("🗑 Project Allocations"):
            if execute_query("DELETE FROM weekly_allocations WHERE room_name != 'Oasis'"):
                st.success("Non-Oasis rooms removed.")
        if col1.button("🗑 Project Preferences"):
            if execute_query("DELETE FROM weekly_preferences"):
                st.success("Project preferences removed.")
        if col2.button("🗑 Oasis Allocations"):
            if execute_query("DELETE FROM weekly_allocations WHERE room_name = 'Oasis'"):
                st.success("Oasis allocations removed.")
        if col2.button("🗑 Oasis Preferences"):
            if execute_query("DELETE FROM oasis_preferences"):
                st.success("Oasis preferences removed.")

        # Manually edit allocations
        st.subheader("Edit Project Allocations")
        df_alloc_admin = get_project_allocation()
        if not df_alloc_admin.empty:
            editable = st.data_editor(df_alloc_admin, num_rows="dynamic", use_container_width=True)
            if st.button("Save Project Alloc"):
                try:
                    # Clear
                    execute_query("DELETE FROM weekly_allocations WHERE room_name != 'Oasis'")
                    # Re-insert
                    today = datetime.now(OFFICE_TZ).date()
                    monday = today - timedelta(days=today.weekday())
                    day_map = {"Monday": 0, "Tuesday": 1, "Wednesday": 2, "Thursday": 3}
                    for _, row in editable.iterrows():
                        room = row["Room"]
                        for day_name, offset in day_map.items():
                            if row[day_name] != "Vacant":
                                team_person = row[day_name].split("(")[0].strip()
                                dt = monday + timedelta(days=offset)
                                execute_query(
                                    """INSERT INTO weekly_allocations (team_name, room_name, date)
                                       VALUES (%s, %s, %s)""",
                                    (team_person, room, dt)
                                )
                    st.success("Saved project room allocations.")
                except Exception as e:
                    st.error(f"Error: {e}")
        else:
            st.info("No data to edit.")

    elif pwd:
        st.error("Wrong password.")

# -- Request Project Room --
st.header("📝 Request Project Room")
week_of_text = st.session_state.get("week_of_text", "9 June")
st.markdown(f"Submissions for week of {week_of_text} open from {st.session_state.get('submission_start_text','')} to {st.session_state.get('submission_end_text','')}.")

with st.form("team_form"):
    team_name = st.text_input("Team Name")
    contact_person = st.text_input("Contact Person")
    size = st.number_input("Team Size (3-6)", min_value=3, max_value=6, value=3)
    day_map_choice = {"Monday and Wednesday": "Monday,Wednesday", "Tuesday and Thursday": "Tuesday,Thursday"}
    day_choice = st.selectbox("Preferred Days", list(day_map_choice.keys()))
    if st.form_submit_button("Submit"):
        if insert_preference(team_name, contact_person, size, day_map_choice[day_choice]):
            st.success(f"Saved preference for {team_name}!")

# -- Oasis Form --
st.header("🌿 Reserve Oasis Seat")
st.markdown(f"Open until {st.session_state.get('oasis_end_text','')}.")

with st.form("oasis_form"):
    person = st.text_input("Name")
    days = st.multiselect("Preferred Days", ["Monday","Tuesday","Wednesday","Thursday","Friday"], max_selections=5)
    if st.form_submit_button("Submit"):
        if insert_oasis(person, days):
            st.success(f"Saved Oasis preference for {person}!")

# -- Display Project Rooms --
st.header("📌 Project Room Allocations")
proj_df = get_project_allocation()
if proj_df.empty:
    st.info("No project room allocations for the current week.")
else:
    st.dataframe(proj_df, use_container_width=True)

# -- Ad-hoc Oasis --
st.header("🚶 Ad-hoc Oasis Registration")
st.caption("If you missed previous submissions, add yourself directly if space is available.")
monday, days_list = get_current_week_days()

with st.form("oasis_adhoc_form"):
    adhoc_name = st.text_input("Your Name")
    adhoc_days = st.multiselect("Days to join Oasis", ["Monday","Tuesday","Wednesday","Thursday","Friday"])
    if st.form_submit_button("Add Me"):
        if not adhoc_name:
            st.error("Name required.")
        elif not adhoc_days:
            st.error("Select at least one day.")
        else:
            day_idx = {"Monday":0,"Tuesday":1,"Wednesday":2,"Thursday":3,"Friday":4}
            for d in adhoc_days:
                date_obj = monday + timedelta(days=day_idx[d])
                # Check capacity
                check_df = fetch_records(
                    """SELECT COUNT(*) AS c 
                       FROM weekly_allocations 
                       WHERE room_name='Oasis' AND date=%s""",
                    (date_obj,)
                )
                used = check_df["c"].iloc[0] if not check_df.empty else 0
                if used < OASIS_INFO.get("capacity",15):
                    execute_query(
                        """INSERT INTO weekly_allocations (team_name, room_name, date)
                           VALUES (%s, 'Oasis', %s)""",
                        (adhoc_name.strip(), date_obj)
                    )
                else:
                    st.warning(f"Oasis full on {d}. {adhoc_name} not added.")
            st.success("Ad-hoc Oasis update done.")
            st.rerun()

# -- Oasis Overview --
st.header("📊 Oasis Weekly Overview")
_, days_list = get_current_week_days()
oasis_df = fetch_records(
    """SELECT team_name AS Name, date 
       FROM weekly_allocations 
       WHERE room_name='Oasis' AND date BETWEEN %s AND %s""",
    (days_list[0], days_list[-1])
)

oasis_cap = OASIS_INFO.get("capacity", 15)
day_names = [d.strftime("%A") for d in days_list]
if oasis_df.empty:
    st.write("No Oasis data for this week.")
else:
    oasis_df["date"] = pd.to_datetime(oasis_df["date"]).dt.date
    st.subheader("🪑 Oasis Availability Summary")
    for dt_obj, d_str in zip(days_list, day_names):
        used = oasis_df[oasis_df["date"] == dt_obj]["Name"].nunique()
        st.markdown(f"**{d_str}**: {oasis_cap - used} spot(s) left")

    # Build matrix
    all_people = sorted(set(oasis_df["Name"]))
    matrix = pd.DataFrame(False, index=all_people, columns=day_names)
    for _, row in oasis_df.iterrows():
        person, d = row["Name"], row["date"]
        dlabel = d.strftime("%A")
        matrix.at[person, dlabel] = True

    edited = st.data_editor(matrix, use_container_width=True, key="oasis_matrix")
    if st.button("Save Oasis Matrix"):
        try:
            # Clear existing
            execute_query(
                """DELETE FROM weekly_allocations 
                   WHERE room_name='Oasis' 
                     AND date BETWEEN %s AND %s 
                     AND team_name != 'Niek'""",
                (days_list[0], days_list[-1])
            )
            # Re-insert
            occupancy = {d: 0 for d in day_names}
            for person in edited.index:
                for col_idx, col in enumerate(day_names):
                    if edited.at[person, col]:
                        if occupancy[col] < oasis_cap:
                            dt_val = days_list[col_idx]
                            execute_query(
                                "INSERT INTO weekly_allocations (team_name, room_name, date) VALUES (%s,'Oasis',%s)",
                                (person, dt_val)
                            )
                            occupancy[col] += 1
                        else:
                            st.warning(f"Capacity reached on {col}. {person} not added.")
            st.success("Oasis matrix updated.")
            st.rerun()
        except Exception as e:
            st.error(f"Failed to save matrix: {e}")