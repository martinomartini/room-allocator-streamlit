# Button State Management Improvements for app.py
# Add these functions and modify button handlers to prevent auto-execution

import streamlit as st

# Add this at the top after imports
def init_session_state():
    """Initialize session state variables for button management"""
    if "button_clicked" not in st.session_state:
        st.session_state.button_clicked = False
    if "pending_action" not in st.session_state:
        st.session_state.pending_action = None
    if "confirm_deletion" not in st.session_state:
        st.session_state.confirm_deletion = False

# Call this early in your app
init_session_state()

# Replace your current button handlers with these improved versions:

def safe_button_handler(button_key, action_name, dangerous=False):
    """
    Safe button handler that requires explicit confirmation for dangerous actions
    """
    if dangerous:
        # Two-step process for dangerous actions
        if not st.session_state.get(f"confirm_{button_key}", False):
            if st.button(f"🔴 {action_name}", key=button_key):
                st.session_state[f"confirm_{button_key}"] = True
                st.warning(f"⚠️ Click the confirmation button below to proceed with: {action_name}")
                st.rerun()
            return False
        else:
            col1, col2 = st.columns(2)
            with col1:
                if st.button(f"✅ Confirm: {action_name}", key=f"{button_key}_confirm"):
                    st.session_state[f"confirm_{button_key}"] = False
                    return True
            with col2:
                if st.button("❌ Cancel", key=f"{button_key}_cancel"):
                    st.session_state[f"confirm_{button_key}"] = False
                    st.info("Action cancelled.")
                    st.rerun()
            return False
    else:
        # Simple button for non-dangerous actions
        return st.button(action_name, key=button_key)

# Example usage in your admin section:
def improved_admin_section():
    """
    Improved admin section with better button handling
    """
    with st.expander("🔐 Admin Controls"):
        pwd = st.text_input("Enter admin password:", type="password", key="admin_pwd_main")

        if pwd == RESET_PASSWORD:
            st.success("✅ Access granted.")

            # Non-dangerous actions (simple buttons)
            if safe_button_handler("btn_run_proj_alloc", "🚀 Run Project Room Allocation"):
                if run_allocation:
                    success, _ = run_allocation(DATABASE_URL, only="project", base_monday_date=st.session_state.project_rooms_display_monday)
                    if success:
                        st.success("✅ Project room allocation completed.")
                        st.rerun()
                    else:
                        st.error("❌ Project room allocation failed.")

            # Dangerous actions (require confirmation)
            if safe_button_handler("btn_reset_all_proj_prefs", "Remove All Project Room Preferences", dangerous=True):
                conn_reset_prp = get_connection(pool)
                if conn_reset_prp:
                    try:
                        with conn_reset_prp.cursor() as cur:
                            # First backup to archive
                            cur.execute("""
                                INSERT INTO weekly_preferences_archive 
                                (team_name, contact_person, team_size, preferred_days, submission_time, deleted_by, deletion_reason)
                                SELECT team_name, contact_person, team_size, preferred_days, submission_time, 'admin', 'Manual deletion via admin panel'
                                FROM weekly_preferences
                            """)
                            # Then delete
                            cur.execute("DELETE FROM weekly_preferences")
                            conn_reset_prp.commit()
                            st.success("✅ All project room preferences removed and archived.")
                            st.rerun()
                    except Exception as e:
                        st.error(f"❌ Failed: {e}")
                        conn_reset_prp.rollback()
                    finally:
                        return_connection(pool, conn_reset_prp)

        elif pwd:
            st.error("❌ Incorrect password.")
