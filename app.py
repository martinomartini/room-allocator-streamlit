import streamlit as st
from datetime import datetime
import pytz
from allocate_rooms import get_db_connection_pool, insert_preference, insert_oasis

# --- Page Setup ---
st.set_page_config(page_title="Weekly Room Allocator", layout="wide")

# --- Timezone Setup ---
OFFICE_TIMEZONE_STR = st.secrets.get("OFFICE_TIMEZONE", "Europe/Amsterdam")
OFFICE_TIMEZONE = pytz.timezone(OFFICE_TIMEZONE_STR)
now_local = datetime.now(OFFICE_TIMEZONE)

# --- DB Pool ---
pool = get_db_connection_pool()

# --- Header ---
st.title("📅 Weekly Room Allocator for TS")
st.info(f"Current Office Time: **{now_local.strftime('%Y-%m-%d %H:%M:%S')}** ({OFFICE_TIMEZONE_STR})")

# --- Instructions ---
st.markdown("""
### 💡 How This Works:

- 🧑‍🤝‍🧑 Project teams can select **Monday & Wednesday** or **Tuesday & Thursday**.
- 🌿 Oasis users can choose **up to 5 preferred weekdays**, and are fairly assigned.
- ❗ You may only submit **once**. To change input, contact an admin.
- 🗓️ **Project Room Preference**: Wed 09:00 – Thu 16:00
- 🌿 **Oasis Preference**: Wed 09:00 – Fri 16:00

---
""")

# --- Project Room Form ---
st.header("🧑‍🤝‍🧑 Project Room Reservation")
with st.form("team_form"):
    name = st.text_input("Team Name")
    contact = st.text_input("Contact Person")
    size = st.number_input("Team Size", min_value=3, max_value=6)
    choice = st.selectbox("Preferred Days", ["Monday and Wednesday", "Tuesday and Thursday"])
    submit = st.form_submit_button("Submit")

    if submit:
        day_map = {
            "Monday and Wednesday": "Monday,Wednesday",
            "Tuesday and Thursday": "Tuesday,Thursday"
        }
        if insert_preference(pool, name, contact, size, day_map[choice]):
            st.success("✅ Submitted!")

# --- Oasis Form ---
st.header("🌿 Reserve Oasis Seat")
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
            padded_days = selected_days + [None] * (5 - len(selected_days))
            if insert_oasis(pool, person.strip(), *padded_days[:2]):
                st.success("✅ Oasis preference submitted!")