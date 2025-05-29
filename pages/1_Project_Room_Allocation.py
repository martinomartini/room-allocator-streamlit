import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
import pytz
from psycopg2.extras import RealDictCursor
import sys
import os
import json

# Import from parent directory
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

st.set_page_config(page_title="Project Room Allocation", layout="wide")

# --- Configuration (copy from main app) ---
DATABASE_URL = st.secrets.get("SUPABASE_DB_URI", os.environ.get("SUPABASE_DB_URI"))
OFFICE_TIMEZONE_STR = st.secrets.get("OFFICE_TIMEZONE", os.environ.get("OFFICE_TIMEZONE", "UTC"))
RESET_PASSWORD = "trainee"

try:
    OFFICE_TIMEZONE = pytz.timezone(OFFICE_TIMEZONE_STR)
except pytz.UnknownTimeZoneError:
    OFFICE_TIMEZONE = pytz.utc

# --- Import from main app ---
try:
    from app import get_db_connection_pool, get_connection, return_connection, AVAILABLE_ROOMS
except ImportError:
    st.error("❌ Could not import from main app. Please check file structure.")
    st.stop()

from allocate_rooms import run_allocation

# --- Functions ---
def get_room_grid(pool):
    # Set up current week's Monday and day mapping
    today = datetime.now(OFFICE_TIMEZONE).date()
    this_monday = today - timedelta(days=today.weekday())
    day_mapping = {
        this_monday + timedelta(days=0): "Monday",
        this_monday + timedelta(days=1): "Tuesday",
        this_monday + timedelta(days=2): "Wednesday",
        this_monday + timedelta(days=3): "Thursday"
    }

    day_labels = list(day_mapping.values())
    all_rooms = [r["name"] for r in AVAILABLE_ROOMS if r["name"] != "Oasis"]

    grid = {
        room: {**{"Room": room}, **{day: "Vacant" for day in day_labels}}
        for room in all_rooms
    }

    conn = pool.getconn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT team_name, room_name, date
                FROM weekly_allocations
                WHERE room_name != 'Oasis'
            """)
            allocations = cur.fetchall()

            cur.execute("""
                SELECT team_name, contact_person
                FROM weekly_preferences
            """)
            contacts = {row["team_name"]: row["contact_person"] for row in cur.fetchall()}

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

    finally:
        pool.putconn(conn)

def get_unallocated_teams(pool):
    conn = get_connection(pool)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT team_name, contact_person, team_size, preferred_days FROM weekly_preferences")
            all_teams = cur.fetchall()
            
            cur.execute("SELECT DISTINCT team_name FROM weekly_allocations WHERE room_name != 'Oasis'")
            allocated_teams = {row[0] for row in cur.fetchall()}
            
            unallocated = [team for team in all_teams if team[0] not in allocated_teams]
            
            return pd.DataFrame(unallocated, columns=["Team", "Contact", "Size", "Preferred Days"])
    except Exception as e:
        st.warning(f"Failed to fetch unallocated teams: {e}")
        return pd.DataFrame()
    finally:
        return_connection(pool, conn)

def get_preferences(pool):
    conn = get_connection(pool)
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

# --- Main Content ---
pool = get_db_connection_pool()

st.title("📊 Project Room Allocation")

# Display current time
now_local = datetime.now(OFFICE_TIMEZONE)
st.info(f"**Current Office Time:** {now_local.strftime('%Y-%m-%d %H:%M:%S')} ({OFFICE_TIMEZONE_STR})")

# Current allocations
st.header("📌 Current Project Room Allocations")
alloc_df = get_room_grid(pool)
if alloc_df.empty:
    st.info("No project room allocations generated yet.")
    st.info("💡 Use the admin controls below to run the allocation algorithm.")
else:
    st.dataframe(alloc_df, use_container_width=True)

# Teams without rooms
st.header("⚠️ Teams That Submitted Preferences But Weren't Allocated")
unallocated_df = get_unallocated_teams(pool)
if unallocated_df.empty:
    if not alloc_df.empty:
        st.success("✅ All teams that submitted preferences have been allocated!")
    else:
        st.info("ℹ️ No preferences submitted yet.")
else:
    st.warning(f"⚠️ {len(unallocated_df)} teams submitted preferences but weren't allocated:")
    st.dataframe(unallocated_df, use_container_width=True)

    with st.expander("💡 Why might teams not be allocated?"):
        st.markdown("""
        **Possible reasons for non-allocation:**
        - Team size exceeds available room capacity (max 6 people)
        - No rooms available for their preferred days (Monday/Wednesday or Tuesday/Thursday)
        - Submitted after the deadline
        - All appropriate rooms already allocated to other teams
        - Technical issues with the submission
        
        **What to do:**
        - Contact admin for manual allocation if space becomes available
        - Consider splitting large teams
        - Check if submission was successful
        """)

# All preferences
st.header("📝 All Submitted Project Team Preferences")
prefs_df = get_preferences(pool)
if not prefs_df.empty:
    st.dataframe(prefs_df, use_container_width=True)
    st.info(f"📊 **Summary:** {len(prefs_df)} teams submitted preferences")
else:
    st.info("No team preferences submitted yet.")

# Admin controls
with st.expander("🔐 Admin Controls"):
    pwd = st.text_input("Enter admin password:", type="password")
    if pwd == RESET_PASSWORD:
        st.success("✅ Access granted.")

        col1, col2 = st.columns(2)
        
        with col1:
            if st.button("🚀 Run Project Room Allocation"):
                with st.spinner("Running allocation algorithm..."):
                    success, _ = run_allocation(DATABASE_URL, only="project")
                    if success:
                        st.success("✅ Project room allocation completed.")
                        st.rerun()
                    else:
                        st.error("❌ Project room allocation failed.")

        with col2:
            if st.button("🧹 Clear Project Allocations"):
                conn = get_connection(pool)
                try:
                    with conn.cursor() as cur:
                        cur.execute("DELETE FROM weekly_allocations WHERE room_name != 'Oasis'")
                        conn.commit()
                        st.success("✅ Project room allocations cleared.")
                        st.rerun()
                except Exception as e:
                    st.error(f"❌ Failed to clear allocations: {e}")
                finally:
                    return_connection(pool, conn)

        st.subheader("🗑️ Reset Data")
        if st.button("🧽 Clear All Project Preferences", type="secondary"):
            conn = get_connection(pool)
            try:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM weekly_preferences")
                    conn.commit()
                    st.success("✅ All project preferences cleared.")
                    st.rerun()
            except Exception as e:
                st.error(f"❌ Failed to clear preferences: {e}")
            finally:
                return_connection(pool, conn)
    elif pwd:
        st.error("❌ Incorrect password.")