import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime, timedelta, date
import pytz
import sys
import os

# Import from parent directory
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

st.set_page_config(page_title="Current & Historical Analytics", layout="wide")

# --- Configuration ---
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
    # Calculate room counts from rooms.json
    PROJECT_ROOMS = [r for r in AVAILABLE_ROOMS if r["name"] != "Oasis"]
    OASIS_ROOM = next((r for r in AVAILABLE_ROOMS if r["name"] == "Oasis"), {"capacity": 16})
    
    TOTAL_PROJECT_ROOMS = len(PROJECT_ROOMS)
    OASIS_CAPACITY = OASIS_ROOM["capacity"]
    
except ImportError:
    st.error("❌ Could not import from main app. Please check file structure.")
    st.stop()

# --- Data Functions ---
def get_current_allocations(pool):
    """Get current week allocation data from active table"""
    if not pool: return pd.DataFrame()
    conn = get_connection(pool)
    if not conn: return pd.DataFrame()
    
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT team_name, room_name, date, 
                       EXTRACT(DOW FROM date) as day_of_week,
                       EXTRACT(WEEK FROM date) as week_number,
                       EXTRACT(YEAR FROM date) as year
                FROM weekly_allocations 
                ORDER BY date DESC
            """)
            
            rows = cur.fetchall()
            if not rows:
                return pd.DataFrame()
                
            df = pd.DataFrame(rows, columns=["Team", "Room", "Date", "DayOfWeek", "WeekNumber", "Year"])
            df['Date'] = pd.to_datetime(df['Date'])
            df['WeekDay'] = df['Date'].dt.day_name()
            df['WeekStart'] = df['Date'] - pd.to_timedelta(df['Date'].dt.dayofweek, unit='d')
            
            # Add room type classification
            df['Room_Type'] = df['Room'].apply(lambda x: 'Oasis' if x == 'Oasis' else 'Project Room')
            df['Data_Source'] = 'Current'
            
            return df
            
    except Exception as e:
        st.error(f"Failed to fetch current allocation data: {e}")
        return pd.DataFrame()
    finally:
        return_connection(pool, conn)

def get_historical_allocations(pool, start_date=None, end_date=None):
    """Get historical allocation data from archive table"""
    if not pool: return pd.DataFrame()
    conn = get_connection(pool)
    if not conn: return pd.DataFrame()
    
    try:
        with conn.cursor() as cur:
            if start_date and end_date:
                cur.execute("""
                    SELECT team_name, room_name, date, 
                           EXTRACT(DOW FROM date) as day_of_week,
                           EXTRACT(WEEK FROM date) as week_number,
                           EXTRACT(YEAR FROM date) as year
                    FROM weekly_allocations_archive 
                    WHERE date >= %s AND date <= %s
                    ORDER BY date DESC
                """, (start_date, end_date))
            else:
                cur.execute("""
                    SELECT team_name, room_name, date,
                           EXTRACT(DOW FROM date) as day_of_week,
                           EXTRACT(WEEK FROM date) as week_number,
                           EXTRACT(YEAR FROM date) as year
                    FROM weekly_allocations_archive 
                    ORDER BY date DESC
                    LIMIT 1000
                """)
            
            rows = cur.fetchall()
            if not rows:
                return pd.DataFrame()
                
            df = pd.DataFrame(rows, columns=["Team", "Room", "Date", "DayOfWeek", "WeekNumber", "Year"])
            df['Date'] = pd.to_datetime(df['Date'])
            df['WeekDay'] = df['Date'].dt.day_name()
            df['WeekStart'] = df['Date'] - pd.to_timedelta(df['Date'].dt.dayofweek, unit='d')
            
            # Add room type classification
            df['Room_Type'] = df['Room'].apply(lambda x: 'Oasis' if x == 'Oasis' else 'Project Room')
            df['Data_Source'] = 'Historical'
            
            return df
            
    except Exception as e:
        st.error(f"Failed to fetch historical data: {e}")
        return pd.DataFrame()
    finally:
        return_connection(pool, conn)

def get_current_preferences(pool):
    """Get current week preferences"""
    if not pool: return {'project': 0, 'oasis': 0}
    conn = get_connection(pool)
    if not conn: return {'project': 0, 'oasis': 0}
    
    try:
        with conn.cursor() as cur:
            # Get current preferences count
            cur.execute("SELECT COUNT(*) FROM weekly_preferences")
            project_prefs = cur.fetchone()[0] or 0
            
            cur.execute("SELECT COUNT(*) FROM oasis_preferences")
            oasis_prefs = cur.fetchone()[0] or 0
            
            return {'project': project_prefs, 'oasis': oasis_prefs}
            
    except Exception as e:
        st.error(f"Failed to fetch current preferences: {e}")
        return {'project': 0, 'oasis': 0}
    finally:
        return_connection(pool, conn)

def get_combined_data(pool, weeks_back=12, include_current=True):
    """Get combined current and historical data"""
    end_date = date.today()
    start_date = end_date - timedelta(weeks=weeks_back)
    
    historical_df = get_historical_allocations(pool, start_date, end_date)
    current_df = pd.DataFrame()
    
    if include_current:
        current_df = get_current_allocations(pool)
    
    # Combine datasets
    if not historical_df.empty and not current_df.empty:
        combined_df = pd.concat([current_df, historical_df], ignore_index=True)
    elif not historical_df.empty:
        combined_df = historical_df
    elif not current_df.empty:
        combined_df = current_df
    else:
        combined_df = pd.DataFrame()
    
    return combined_df, current_df, historical_df

def calculate_utilization_stats(df, time_period="daily"):
    """Calculate utilization statistics"""
    if df.empty:
        return pd.DataFrame()
    
    stats = []
    
    if time_period == "daily":
        for date_val in df['Date'].dt.date.unique():
            day_data = df[df['Date'].dt.date == date_val]
            
            # Project rooms utilization
            project_rooms_used = len(day_data[day_data['Room_Type'] == 'Project Room']['Room'].unique())
            project_utilization = (project_rooms_used / TOTAL_PROJECT_ROOMS * 100) if TOTAL_PROJECT_ROOMS > 0 else 0
            
            # Oasis utilization (count people, not rooms)
            oasis_people = len(day_data[day_data['Room_Type'] == 'Oasis'])
            oasis_utilization = (oasis_people / OASIS_CAPACITY * 100) if OASIS_CAPACITY > 0 else 0
            
            stats.append({
                'Date': date_val,
                'WeekDay': day_data.iloc[0]['WeekDay'],
                'Project_Rooms_Used': project_rooms_used,
                'Project_Utilization': round(project_utilization, 1),
                'Oasis_People': oasis_people,
                'Oasis_Utilization': round(oasis_utilization, 1),
                'Data_Source': day_data.iloc[0]['Data_Source'] if 'Data_Source' in day_data.columns else 'Unknown'
            })
    
    return pd.DataFrame(stats).sort_values('Date', ascending=False)

# --- Streamlit App ---
st.title("📊 Current & Historical Analytics")
st.caption(f"📋 **Room Configuration**: {TOTAL_PROJECT_ROOMS} Project Rooms + Oasis ({OASIS_CAPACITY} people)")

pool = get_db_connection_pool()

# Admin authentication
with st.expander("🔐 Admin Access Required"):
    pwd = st.text_input("Enter admin password:", type="password", key="admin_pwd_analytics")
    
    if pwd != RESET_PASSWORD and pwd:
        st.error("❌ Incorrect password.")
        st.stop()
    elif pwd == RESET_PASSWORD:
        st.success("✅ Access granted.")
    else:
        st.info("Please enter admin password to view analytics.")
        st.stop()

# Main analytics interface
st.header("📈 Analytics Dashboard")

# Data source and view selector
st.info("📊 **Data Sources**: **Current Week** (active allocations) + **Historical Data** (archived from previous weeks)")

col1, col2, col3 = st.columns(3)
with col1:
    weeks_back = st.selectbox("Historical Period", [4, 8, 12, 24, 52], index=2, key="weeks_selector")
with col2:
    include_current = st.toggle("Include Current Week", value=True, key="include_current")
with col3:
    analysis_mode = st.selectbox("Analysis Mode", ["Combined", "Current Only", "Historical Only"], key="analysis_mode")

# Get data
with st.spinner("Loading data..."):
    combined_df, current_df, historical_df = get_combined_data(pool, weeks_back, include_current)
    current_prefs = get_current_preferences(pool)
    
    # Choose dataset based on analysis mode
    if analysis_mode == "Current Only":
        analysis_df = current_df
    elif analysis_mode == "Historical Only":
        analysis_df = historical_df
    else:  # Combined
        analysis_df = combined_df

# === CURRENT WEEK OVERVIEW ===
if not current_df.empty:
    st.subheader("📅 Current Week Status")
    
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        current_total = len(current_df)
        st.metric("Current Allocations", current_total)
    
    with col2:
        current_project = len(current_df[current_df['Room_Type'] == 'Project Room'])
        st.metric("Project Room Allocations", current_project)
    
    with col3:
        current_oasis = len(current_df[current_df['Room_Type'] == 'Oasis'])
        st.metric("Oasis Allocations", current_oasis)
    
    with col4:
        current_teams = current_df['Team'].nunique()
        st.metric("Active Teams", current_teams)
    
    # Current week utilization
    col1, col2, col3 = st.columns(3)
    with col1:
        current_project_rooms = len(current_df[current_df['Room_Type'] == 'Project Room']['Room'].unique())
        current_project_util = (current_project_rooms / TOTAL_PROJECT_ROOMS * 100) if TOTAL_PROJECT_ROOMS > 0 else 0
        st.metric("Project Room Utilization", f"{current_project_util:.1f}%", 
                 f"{current_project_rooms}/{TOTAL_PROJECT_ROOMS} rooms")
    
    with col2:
        current_oasis_people = len(current_df[current_df['Room_Type'] == 'Oasis'])
        current_oasis_util = (current_oasis_people / OASIS_CAPACITY * 100) if OASIS_CAPACITY > 0 else 0
        st.metric("Oasis Utilization", f"{current_oasis_util:.1f}%", 
                 f"{current_oasis_people}/{OASIS_CAPACITY} people")
    
    with col3:
        st.metric("Pending Preferences", f"Project: {current_prefs['project']}, Oasis: {current_prefs['oasis']}")
    
    # Current week schedule
    with st.expander("📋 View Current Week Schedule"):
        current_project = current_df[current_df['Room_Type'] == 'Project Room']
        current_oasis = current_df[current_df['Room_Type'] == 'Oasis']
        
        col1, col2 = st.columns(2)
        
        with col1:
            st.write("**Project Rooms**")
            if not current_project.empty:
                pivot_current = current_project.pivot_table(
                    index='Room', 
                    columns='WeekDay', 
                    values='Team', 
                    aggfunc='first',
                    fill_value='Available'
                )
                day_order = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday']
                pivot_current = pivot_current.reindex(columns=[d for d in day_order if d in pivot_current.columns])
                st.dataframe(pivot_current, use_container_width=True)
            else:
                st.info("No current project room allocations.")
        
        with col2:
            st.write("**Oasis**")
            if not current_oasis.empty:
                oasis_current = current_oasis.groupby('WeekDay')['Team'].count().reset_index()
                oasis_current.columns = ['Day', 'People']
                oasis_current['Utilization'] = (oasis_current['People'] / OASIS_CAPACITY * 100).round(1)
                st.dataframe(oasis_current, use_container_width=True)
            else:
                st.info("No current Oasis allocations.")

else:
    st.info("ℹ️ No current week allocations found.")

# === ANALYTICS SECTION ===
if not analysis_df.empty:
    st.subheader(f"📈 {analysis_mode} Analytics")
    
    # Calculate utilization stats
    util_stats = calculate_utilization_stats(analysis_df)
    
    if not util_stats.empty:
        # Overall metrics
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            total_allocations = len(analysis_df)
            st.metric("Total Allocations", total_allocations)
        
        with col2:
            avg_project_util = util_stats['Project_Utilization'].mean()
            st.metric("Avg Project Utilization", f"{avg_project_util:.1f}%")
        
        with col3:
            avg_oasis_util = util_stats['Oasis_Utilization'].mean()
            st.metric("Avg Oasis Utilization", f"{avg_oasis_util:.1f}%")
        
        with col4:
            unique_teams = analysis_df['Team'].nunique()
            st.metric("Unique Teams", unique_teams)
        
        # Daily utilization by day of week
        st.write("**Average Utilization by Day of Week**")
        
        project_daily = util_stats.groupby('WeekDay')['Project_Utilization'].mean().reset_index()
        oasis_daily = util_stats.groupby('WeekDay')['Oasis_Utilization'].mean().reset_index()
        
        day_order = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday']
        project_daily['WeekDay'] = pd.Categorical(project_daily['WeekDay'], categories=day_order, ordered=True)
        project_daily = project_daily.sort_values('WeekDay')
        oasis_daily['WeekDay'] = pd.Categorical(oasis_daily['WeekDay'], categories=day_order, ordered=True)
        oasis_daily = oasis_daily.sort_values('WeekDay')
        
        col1, col2 = st.columns(2)
        
        with col1:
            fig_project = px.bar(project_daily, x='WeekDay', y='Project_Utilization',
                                title="Project Rooms - Daily Average Utilization",
                                labels={'Project_Utilization': 'Utilization (%)', 'WeekDay': 'Day'},
                                color='Project_Utilization',
                                color_continuous_scale='RdYlGn')
            fig_project.update_layout(height=400)
            st.plotly_chart(fig_project, use_container_width=True)
        
        with col2:
            fig_oasis = px.bar(oasis_daily, x='WeekDay', y='Oasis_Utilization',
                              title="Oasis - Daily Average Utilization",
                              labels={'Oasis_Utilization': 'Utilization (%)', 'WeekDay': 'Day'},
                              color='Oasis_Utilization',
                              color_continuous_scale='Blues')
            fig_oasis.update_layout(height=400)
            st.plotly_chart(fig_oasis, use_container_width=True)
        
        # Team activity
        st.write("**Most Active Teams**")
        team_activity = analysis_df['Team'].value_counts().head(10).reset_index()
        team_activity.columns = ['Team', 'Allocations']
        
        fig_teams = px.bar(team_activity, x='Allocations', y='Team', orientation='h',
                           title="Top 10 Most Active Teams",
                           labels={'Allocations': 'Number of Allocations', 'Team': 'Team Name'})
        fig_teams.update_layout(height=400)
        st.plotly_chart(fig_teams, use_container_width=True)
        
        # Detailed utilization table
        with st.expander("📋 View Detailed Daily Utilization Data"):
            display_df = util_stats[['Date', 'WeekDay', 'Project_Rooms_Used', 'Project_Utilization', 
                                    'Oasis_People', 'Oasis_Utilization', 'Data_Source']].copy()
            display_df.columns = ['Date', 'Day', 'Rooms Used', 'Project %', 'Oasis People', 'Oasis %', 'Source']
            st.dataframe(display_df, use_container_width=True)

else:
    st.info("No data available for the selected analysis mode.")

# === HISTORICAL DATA BROWSER ===
if not historical_df.empty:
    st.subheader("🗓️ Historical Data Browser")
    
    # Week selector for historical view
    available_weeks = sorted(historical_df['WeekStart'].dt.date.unique(), reverse=True)
    
    if available_weeks:
        selected_week = st.selectbox("Select Week to View", 
                                    options=available_weeks,
                                    format_func=lambda x: f"Week of {x}",
                                    key="week_browser")
        
        if selected_week:
            week_data = historical_df[historical_df['WeekStart'].dt.date == selected_week]
            
            if not week_data.empty:
                st.write(f"**Showing allocations for week of {selected_week}**")
                
                project_data = week_data[week_data['Room_Type'] == 'Project Room']
                oasis_data = week_data[week_data['Room_Type'] == 'Oasis']
                
                col1, col2 = st.columns(2)
                
                with col1:
                    st.write("**Project Rooms**")
                    if not project_data.empty:
                        pivot_data = project_data.pivot_table(
                            index='Room', 
                            columns='WeekDay', 
                            values='Team', 
                            aggfunc='first',
                            fill_value='Vacant'
                        )
                        day_order = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday']
                        pivot_data = pivot_data.reindex(columns=[d for d in day_order if d in pivot_data.columns])
                        st.dataframe(pivot_data, use_container_width=True)
                    else:
                        st.info("No project room allocations for this week.")
                
                with col2:
                    st.write("**Oasis**")
                    if not oasis_data.empty:
                        oasis_summary = oasis_data.groupby('WeekDay')['Team'].count().reset_index()
                        oasis_summary.columns = ['Day', 'People']
                        oasis_summary['Utilization'] = (oasis_summary['People'] / OASIS_CAPACITY * 100).round(1)
                        st.dataframe(oasis_summary, use_container_width=True)
                    else:
                        st.info("No Oasis allocations for this week.")

# === EXPORT FUNCTIONALITY ===
if not analysis_df.empty:
    st.subheader("📤 Export Data")
    
    col1, col2 = st.columns(2)
    
    with col1:
        if st.button("📊 Export Analytics Summary"):
            project_allocations = len(analysis_df[analysis_df['Room_Type'] == 'Project Room'])
            oasis_allocations = len(analysis_df[analysis_df['Room_Type'] == 'Oasis'])
            
            summary_data = {
                'Metric': ['Total Allocations', 'Project Allocations', 'Oasis Allocations', 'Unique Teams', 'Analysis Mode'],
                'Value': [len(analysis_df), project_allocations, oasis_allocations, 
                         analysis_df['Team'].nunique(), analysis_mode]
            }
            summary_df = pd.DataFrame(summary_data)
            csv = summary_df.to_csv(index=False)
            st.download_button(
                label="Download Summary",
                data=csv,
                file_name=f"room_analytics_summary_{date.today()}.csv",
                mime="text/csv"
            )
    
    with col2:
        if st.button("📋 Export Raw Data"):
            export_df = analysis_df[['Date', 'Team', 'Room', 'WeekDay', 'Room_Type', 'Data_Source']].copy()
            export_df['Date'] = export_df['Date'].dt.strftime('%Y-%m-%d')
            csv = export_df.to_csv(index=False)
            st.download_button(
                label="Download Raw Data",
                data=csv,
                file_name=f"room_allocations_data_{date.today()}.csv",
                mime="text/csv"
            )

# === INSIGHTS ===
if not analysis_df.empty:
    st.subheader("💡 Key Insights")
    
    insights = []
    
    # Current vs Historical comparison
    if not current_df.empty and not historical_df.empty:
        current_util = calculate_utilization_stats(current_df)
        historical_util = calculate_utilization_stats(historical_df)
        
        if not current_util.empty and not historical_util.empty:
            current_avg_project = current_util['Project_Utilization'].mean()
            historical_avg_project = historical_util['Project_Utilization'].mean()
            
            current_avg_oasis = current_util['Oasis_Utilization'].mean()
            historical_avg_oasis = historical_util['Oasis_Utilization'].mean()
            
            if current_avg_project > historical_avg_project:
                insights.append(f"📈 **Project Rooms**: Current utilization ({current_avg_project:.1f}%) is higher than historical average ({historical_avg_project:.1f}%)")
            else:
                insights.append(f"📉 **Project Rooms**: Current utilization ({current_avg_project:.1f}%) is lower than historical average ({historical_avg_project:.1f}%)")
            
            if current_avg_oasis > historical_avg_oasis:
                insights.append(f"📈 **Oasis**: Current utilization ({current_avg_oasis:.1f}%) is higher than historical average ({historical_avg_oasis:.1f}%)")
            else:
                insights.append(f"📉 **Oasis**: Current utilization ({current_avg_oasis:.1f}%) is lower than historical average ({historical_avg_oasis:.1f}%)")
    
    # Preference insights
    if current_prefs['project'] > 0 or current_prefs['oasis'] > 0:
        insights.append(f"⏳ **Pending Preferences**: {current_prefs['project']} project teams and {current_prefs['oasis']} individuals have submitted preferences for upcoming allocation")
    else:
        insights.append("✅ **No Pending Preferences**: All preferences have been processed into allocations")
    
    # Usage patterns
    if not analysis_df.empty:
        project_df = analysis_df[analysis_df['Room_Type'] == 'Project Room']
        oasis_df = analysis_df[analysis_df['Room_Type'] == 'Oasis']
        
        if not project_df.empty:
            most_popular_project_day = project_df['WeekDay'].mode().iloc[0] if len(project_df['WeekDay'].mode()) > 0 else "N/A"
            insights.append(f"📅 **Peak Project Day**: {most_popular_project_day} is the most requested day for project rooms")
        
        if not oasis_df.empty:
            most_popular_oasis_day = oasis_df['WeekDay'].mode().iloc[0] if len(oasis_df['WeekDay'].mode()) > 0 else "N/A"
            insights.append(f"🌿 **Peak Oasis Day**: {most_popular_oasis_day} is the most popular day for Oasis")
    
    # Data archiving info
    insights.append("🔄 **Data Retention**: Current allocations are archived when weekly resets occur, preserving long-term usage trends")
    
    for insight in insights:
        st.info(insight)

else:
    st.info("📊 No allocation data available. Data will appear here once the system is in use.")
