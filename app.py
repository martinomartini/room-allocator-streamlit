import streamlit as st
import psycopg2
import psycopg2.pool
import json
import os
from datetime import datetime
import pytz
import pandas as pd
from datetime import datetime, timedelta

# --- Configuration ---
st.set_page_config(page_title="Weekly Room Allocator", layout="wide")
DATABASE_URL = st.secrets.get("SUPABASE_DB_URI", os.environ.get("SUPABASE_DB_URI"))
OFFICE_TIMEZONE_STR = st.secrets.get("OFFICE_TIMEZONE", os.environ.get("OFFICE_TIMEZONE", "UTC"))
RESET_PASSWORD = "trainee"

try:
    OFFICE_TIMEZONE = pytz.timezone(OFFICE_TIMEZONE_STR)
except pytz.UnknownTimeZoneError:
    st.error(f"Invalid Timezone: '{OFFICE_TIMEZONE_STR}', defaulting to UTC.")
    OFFICE_TIMEZONE = pytz.utc

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOMS_FILE = os.path.join(BASE_DIR, 'rooms.json')
with open(ROOMS_FILE, 'r') as f:
    AVAILABLE_ROOMS = json.load(f)

oasis = next((r for r in AVAILABLE_ROOMS if r["name"] == "Oasis"), {"capacity": 15})

@st.cache_resource
def get_db_connection_pool():
    return psycopg2.pool.SimpleConnectionPool(1, 25, dsn=DATABASE_URL)

def get_connection(pool): return pool.getconn()
def return_connection(pool, conn): pool.putconn(conn)

def insert_preference(pool, team, contact, size, days):
    if size < 3:
        st.error("❌ Team size must be at least 3.")
        return False
    if size > 6:
        st.error("❌ Team size cannot exceed 6.")
        return False
    conn = get_connection(pool)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT preferred_days FROM weekly_preferences WHERE team_name = %s", (team,))
            existing = cur.fetchall()
            voted_days = set(d for row in existing for d in row[0].split(','))
            new_days = set(days.split(','))
            if len(voted_days) >= 2 or len(voted_days.union(new_days)) > 2:
                st.error("❌ Max 2 days allowed per team.")
                return False
            if not (new_days == {"Monday", "Wednesday"} or new_days == {"Tuesday", "Thursday"}):
                st.error("❌ Must select Monday & Wednesday or Tuesday & Thursday.")
                return False
            cur.execute("""
                INSERT INTO weekly_preferences (team_name, contact_person, team_size, preferred_days, submission_time)
                VALUES (%s, %s, %s, %s, NOW())
            """, (team, contact, size, days))
            conn.commit()
            return True
    except Exception as e:
        st.error(f"Insert failed: {e}")
        return False
    finally:
        return_connection(pool, conn)

# --- Main App ---
pool = get_db_connection_pool()

st.title("📅 Weekly Room Allocator for TS")

# --- Navigation Info ---
st.info("""
📑 **Navigate between pages using the sidebar:**
- **🏠 Home** - This page (General info & submit preferences)  
- **📊 Project Room Allocation** - View allocations & admin tools
- **🌿 Oasis Overview** - Full oasis management
""")

st.info("""
💡 **How This Works:**

- 🧑‍🤝‍🧑 Project teams can select **either Monday & Wednesday** or **Tuesday & Thursday**. **Friday** is (for now) flexible. There are 6 rooms for 4 persons and 1 room for 6 persons.
- 🌿 Oasis users can choose **up to 5 preferred weekdays**, and will be randomly assigned—fairness is guaranteed. There are 16 places in the Oasis.
- ❗ You may only submit **once**. If you need to change your input, contact an admin.
- 🗓️ **From Wednesday 09:00** you can submit your **project room preference** until **Thursday 16:00**. The allocations will be shared on **Thursday at 16:00**.
- 🌿 **Oasis preferences** can be submitted **from Wednesday 09:00 until Friday 16:00**, and allocation will be done at **Friday 16:00**.
- ✅ Allocations are refreshed **weekly** by an admin. 
        
---

### 🌿 Oasis: How to Join

1. **✅ Reserve Oasis Seat (recommended)**  
   ➤ Submit your **preferred days** (up to 5).  
   ➤ Allocation is done **automatically and fairly** at **Friday 16:00**.  
   ➤ Everyone gets **at least one** of their preferred days, depending on availability.

2. **⚠️ Add Yourself to Oasis Allocation (only if you forgot)**  
   ➤ Use this **only if you missed the deadline** or forgot to submit your preferences.  
   ➤ You will be added **immediately** to the selected days **if there's space left**.  
   ➤ This option does **not guarantee fairness** and bypasses the regular process.

ℹ️ Always use **"Reserve Oasis Seat"** before Friday 16:00 to ensure fair participation.  
Only use **"Add Yourself"** if you forgot to register.
""")

now_local = datetime.now(OFFICE_TIMEZONE)
st.info(f"Current Office Time: **{now_local.strftime('%Y-%m-%d %H:%M:%S')}** ({OFFICE_TIMEZONE_STR})")

# --- Team Form ---
st.header("Request project room for teams of 3 or more for the week of 2 June - to be filled in between Wednesday 28 May 09:00 until Thursday 29 May 16:00")
with st.form("team_form"):
    name = st.text_input("Team Name")
    contact = st.text_input("Contact Person")
    size = st.number_input("Team Size", min_value=1, max_value=6)
    choice = st.selectbox("Preferred Days", ["Monday and Wednesday", "Tuesday and Thursday"])
    submit = st.form_submit_button("Submit")
    if submit:
        day_map = {
            "Monday and Wednesday": "Monday,Wednesday",
            "Tuesday and Thursday": "Tuesday,Thursday"
        }
        if insert_preference(pool, name, contact, size, day_map[choice]):
            st.success("✅ Submitted!")
            st.info("➡️ Check the **Project Room Allocation** page for results after the deadline.")

# --- Oasis Form ---
st.header("Reserve Oasis Seat for the week of 2 June - Personally sumbit preference between Wednesday 28 May 09:00 until Friday 30 May 16:00")
with st.form("oasis_form"):
    person = st.text_input("Your Name")
    selected_days = st.multiselect(
        "Select Your Preferred Days for Oasis:",
        ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"],
        max_selections=5
    )
    submit_oasis = st.form_submit_button("Submit Oasis Preference")

    if submit_oasis:
        if not person:
            st.error("❌ Please enter your name.")
        elif len(selected_days) == 0:
            st.error("❌ Select at least 1 preferred day.")
        else:
            conn = get_connection(pool)
            try:
                with conn.cursor() as cur:
                    # Prevent duplicate entry
                    cur.execute("SELECT 1 FROM oasis_preferences WHERE person_name = %s", (person,))
                    if cur.fetchone():
                        st.error("❌ You've already submitted. Contact admin to change your selection.")
                    else:
                        # Pad to 5 days with NULLs if needed
                        padded_days = selected_days + [None] * (5 - len(selected_days))
                        cur.execute("""
                            INSERT INTO oasis_preferences (
                                person_name,
                                preferred_day_1,
                                preferred_day_2,
                                preferred_day_3,
                                preferred_day_4,
                                preferred_day_5,
                                submission_time
                            ) VALUES (%s, %s, %s, %s, %s, %s, NOW())
                        """, (person.strip(), *padded_days))
                        conn.commit()
                        st.success("✅ Oasis preference submitted!")
                        st.info("➡️ Visit the **Oasis Overview** page for detailed tracking.")
            except Exception as e:
                st.error(f"❌ Failed to save preference: {e}")
            finally:
                return_connection(pool, conn)

# --- Quick Info ---
st.header("📊 Quick Overview")
col1, col2 = st.columns(2)

with col1:
    st.info("🏢 **Project Rooms Available:**\n- 6 rooms for 4 people\n- 2 rooms for 6 people")
    
with col2:
    st.info(f"🌿 **Oasis Capacity:**\n- {oasis['capacity']} seats available\n- Fair allocation system")