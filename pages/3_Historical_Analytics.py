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

st.set_page_config(page_title="Historical Data & Analytics", layout="wide")

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

# --- Historical Data Functions ---
def get_historical_allocations(pool, start_date=None, end_date=None):
    """Get historical allocation data with room type classification"""
    if not pool: return pd.DataFrame()
    conn = get_connection(pool)
    if not conn: return pd.DataFrame()
    
    try:
        with conn.cursor() as cur:
            if start_date and end_date:                cur.execute("""
                    SELECT team_name, room_name, date, 
                           EXTRACT(DOW FROM date) as day_of_week,
                           EXTRACT(WEEK FROM date) as week_number,
                           EXTRACT(YEAR FROM date) as year
                    FROM weekly_allocations_archive 
                    WHERE date >= %s AND date <= %s
                    ORDER BY date DESC
                """, (start_date, end_date))
            else:                cur.execute("""
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
            
            return df
            
    except Exception as e:
        st.error(f"Failed to fetch historical data: {e}")
        return pd.DataFrame()
    finally:
        return_connection(pool, conn)

def get_preferences_data(pool, weeks_back=12):
    """Get preferences data for both project teams and Oasis users"""
    if not pool: return {'project': pd.DataFrame(), 'oasis': pd.DataFrame()}
    conn = get_connection(pool)
    if not conn: return {'project': pd.DataFrame(), 'oasis': pd.DataFrame()}
    
    try:
        with conn.cursor() as cur:
            # Get project preferences
            cur.execute("""
                SELECT team_name, contact_person, team_size, preferred_days, submission_time
                FROM weekly_preferences
                ORDER BY submission_time DESC
            """)
            project_rows = cur.fetchall()
            
            # Get Oasis preferences  
            cur.execute("""
                SELECT person_name, preferred_day_1, preferred_day_2, preferred_day_3, 
                       preferred_day_4, preferred_day_5, submission_time
                FROM oasis_preferences
                ORDER BY submission_time DESC
            """)
            oasis_rows = cur.fetchall()
            
            project_df = pd.DataFrame(project_rows, columns=[
                "Team", "Contact", "Size", "Preferred_Days", "Submission_Time"
            ]) if project_rows else pd.DataFrame()
            
            oasis_df = pd.DataFrame(oasis_rows, columns=[
                "Person", "Day1", "Day2", "Day3", "Day4", "Day5", "Submission_Time"
            ]) if oasis_rows else pd.DataFrame()
            
            return {'project': project_df, 'oasis': oasis_df}
            
    except Exception as e:
        st.error(f"Failed to fetch preferences data: {e}")
        return {'project': pd.DataFrame(), 'oasis': pd.DataFrame()}
    finally:
        return_connection(pool, conn)

def get_usage_statistics(pool, weeks_back=12):
    """Get usage statistics for analysis with room type separation"""
    if not pool: return {}
    
    end_date = date.today()
    start_date = end_date - timedelta(weeks=weeks_back)
    df = get_historical_allocations(pool, start_date, end_date)
    preferences = get_preferences_data(pool, weeks_back)
    
    if df.empty:
        return {}
    
    # Separate project rooms and Oasis
    project_df = df[df['Room_Type'] == 'Project Room']
    oasis_df = df[df['Room_Type'] == 'Oasis']
    
    # Calculate statistics
    stats = {
        'total_allocations': len(df),
        'project_allocations': len(project_df),
        'oasis_allocations': len(oasis_df),
        'unique_teams': project_df['Team'].nunique() if not project_df.empty else 0,
        'unique_oasis_users': oasis_df['Team'].nunique() if not oasis_df.empty else 0,
        'unique_project_rooms': project_df['Room'].nunique() if not project_df.empty else 0,
        'date_range': f"{df['Date'].min().strftime('%Y-%m-%d')} to {df['Date'].max().strftime('%Y-%m-%d')}",
        'most_popular_project_room': project_df['Room'].mode().iloc[0] if not project_df.empty and len(project_df['Room'].mode()) > 0 else "N/A",
        'most_popular_day_projects': project_df['WeekDay'].mode().iloc[0] if not project_df.empty and len(project_df['WeekDay'].mode()) > 0 else "N/A",
        'most_popular_day_oasis': oasis_df['WeekDay'].mode().iloc[0] if not oasis_df.empty and len(oasis_df['WeekDay'].mode()) > 0 else "N/A",
        'most_active_team': project_df['Team'].mode().iloc[0] if not project_df.empty and len(project_df['Team'].mode()) > 0 else "N/A",
        'current_project_preferences': len(preferences['project']),
        'current_oasis_preferences': len(preferences['oasis'])
    }
    
    return stats

def get_room_utilization(pool, weeks_back=8):
    """Calculate room utilization rates separated by type"""
    if not pool: return {'project': pd.DataFrame(), 'oasis': pd.DataFrame()}
    
    end_date = date.today()
    start_date = end_date - timedelta(weeks=weeks_back)
    df = get_historical_allocations(pool, start_date, end_date)
    
    if df.empty:
        return {'project': pd.DataFrame(), 'oasis': pd.DataFrame()}
    
    # Separate project rooms and Oasis
    project_df = df[df['Room_Type'] == 'Project Room']
    oasis_df = df[df['Room_Type'] == 'Oasis']
    
    # Calculate project room utilization
    project_usage = pd.DataFrame()
    if not project_df.empty:
        project_usage = project_df.groupby('Room').size().reset_index(name='Usage_Count')
        # Assuming 4 days per week for project rooms
        total_possible_slots = 4 * weeks_back
        project_usage['Utilization_Rate'] = (project_usage['Usage_Count'] / total_possible_slots * 100).round(1)
        project_usage = project_usage.sort_values('Utilization_Rate', ascending=False)
    
    # Calculate Oasis utilization (daily basis)
    oasis_usage = pd.DataFrame()
    if not oasis_df.empty:
        # Count unique users per day for Oasis
        oasis_daily = oasis_df.groupby(['Date'])['Team'].nunique().reset_index()
        oasis_daily.columns = ['Date', 'Users_Count']
        
        # Calculate average utilization
        oasis_capacity = 15  # Based on the capacity from rooms.json
        avg_users = oasis_daily['Users_Count'].mean()
        utilization_rate = (avg_users / oasis_capacity * 100).round(1)
        
        oasis_usage = pd.DataFrame({
            'Room': ['Oasis'],
            'Usage_Count': [len(oasis_df)],
            'Avg_Daily_Users': [avg_users],
            'Utilization_Rate': [utilization_rate]
        })
    
    return {'project': project_usage, 'oasis': oasis_usage}

def get_weekly_trends(pool, weeks_back=12):
    """Get weekly allocation trends"""
    if not pool: return pd.DataFrame()
    
    end_date = date.today()
    start_date = end_date - timedelta(weeks=weeks_back)
    df = get_historical_allocations(pool, start_date, end_date)
    
    if df.empty:
        return pd.DataFrame()
    
    # Group by week
    weekly_trends = df.groupby('WeekStart').agg({
        'Team': 'count',
        'Room': 'nunique'
    }).reset_index()
    
    weekly_trends.columns = ['Week', 'Total_Allocations', 'Rooms_Used']
    weekly_trends['Week'] = weekly_trends['Week'].dt.strftime('%Y-%m-%d')
    
    return weekly_trends

def get_daily_utilization(pool, weeks_back=8):
    """Calculate daily utilization rates for project rooms and Oasis separately"""
    if not pool: return pd.DataFrame()
    
    end_date = date.today()
    start_date = end_date - timedelta(weeks=weeks_back)
    df = get_historical_allocations(pool, start_date, end_date)
    
    if df.empty:
        return pd.DataFrame()
    
    # Calculate daily utilization
    daily_stats = []
    
    for date_val in df['Date'].dt.date.unique():
        day_data = df[df['Date'].dt.date == date_val]
        
        # Project rooms utilization
        project_rooms_used = len(day_data[day_data['Room_Type'] == 'Project Room']['Room'].unique())
        project_utilization = (project_rooms_used / TOTAL_PROJECT_ROOMS * 100) if TOTAL_PROJECT_ROOMS > 0 else 0
        
        # Oasis utilization (count people, not rooms)
        oasis_people = len(day_data[day_data['Room_Type'] == 'Oasis'])
        oasis_utilization = (oasis_people / OASIS_CAPACITY * 100) if OASIS_CAPACITY > 0 else 0
        
        daily_stats.append({
            'Date': date_val,
            'WeekDay': day_data.iloc[0]['WeekDay'],
            'WeekStart': day_data.iloc[0]['WeekStart'].date(),
            'Project_Rooms_Used': project_rooms_used,
            'Project_Rooms_Total': TOTAL_PROJECT_ROOMS,
            'Project_Utilization': round(project_utilization, 1),
            'Oasis_People': oasis_people,
            'Oasis_Capacity': OASIS_CAPACITY,
            'Oasis_Utilization': round(oasis_utilization, 1)
        })
    
    return pd.DataFrame(daily_stats).sort_values('Date', ascending=False)

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

def get_combined_allocations(pool, start_date=None, end_date=None, include_current=True):
    """Get combined current and historical allocation data"""
    historical_df = get_historical_allocations(pool, start_date, end_date)
    current_df = pd.DataFrame()
    
    if include_current:
        current_df = get_current_allocations(pool)
        if not current_df.empty:
            current_df['Data_Source'] = 'Current'
    
    if not historical_df.empty:
        historical_df['Data_Source'] = 'Historical'
    
    # Combine datasets
    if not historical_df.empty and not current_df.empty:
        combined_df = pd.concat([current_df, historical_df], ignore_index=True)
    elif not historical_df.empty:
        combined_df = historical_df
    elif not current_df.empty:
        combined_df = current_df
    else:
        combined_df = pd.DataFrame()
    
    return combined_df

# --- Streamlit App ---
st.title("📊 Historical Data & Analytics")

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
st.header("📈 Usage Analytics Dashboard")

# Data source and view selector
st.info("📊 **Data Sources**: This dashboard shows both **current week allocations** (active) and **archived historical data** (from previous weeks).")

col1, col2, col3 = st.columns(3)
with col1:
    weeks_back = st.selectbox("Historical Analysis Period", [4, 8, 12, 24, 52], index=2, key="weeks_selector")
with col2:
    include_current = st.toggle("Include Current Week", value=True, key="include_current")
with col3:
    st.metric("Analyzing Last", f"{weeks_back} weeks", "Historical data" + (" + Current" if include_current else ""))

# Get data
with st.spinner("Loading analytics data..."):
    # Get current week data
    current_df = get_current_allocations(pool)
    
    # Get combined data for analysis
    combined_df = get_combined_allocations(pool, 
                                         date.today() - timedelta(weeks=weeks_back), 
                                         date.today(), 
                                         include_current)
      # Get statistics
    stats = get_usage_statistics(pool, weeks_back)
    daily_util = get_daily_utilization(pool, weeks_back)
    
    # Get additional data for analysis
    historical_df = get_historical_allocations(pool, 
                                              date.today() - timedelta(weeks=weeks_back), 
                                              date.today())
    room_util = get_room_utilization(pool, weeks_back)
    weekly_trends = get_weekly_trends(pool, weeks_back)
    preferences = get_preferences_data(pool, weeks_back)

# Display key metrics
if stats:
    st.subheader("📊 Key Metrics Overview")
    
    # Overall metrics
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Total Allocations", stats['total_allocations'])
    with col2:
        st.metric("Current Project Preferences", stats['current_project_preferences'])
    with col3:
        st.metric("Current Oasis Preferences", stats['current_oasis_preferences'])
    with col4:
        st.metric("Analysis Period", f"{weeks_back} weeks")
    
    # Separated metrics
    st.subheader("🏢 Project Rooms vs 🌿 Oasis Breakdown")
    
    col1, col2 = st.columns(2)
    with col1:
        st.info("**Project Rooms**")
        st.metric("Project Allocations", stats['project_allocations'])
        st.metric("Unique Teams", stats['unique_teams'])
        st.metric("Project Rooms Used", stats['unique_project_rooms'])
        st.metric("Most Popular Room", stats['most_popular_project_room'])
        st.metric("Most Popular Day", stats['most_popular_day_projects'])
        
    with col2:
        st.success("**Oasis**")
        st.metric("Oasis Allocations", stats['oasis_allocations'])
        st.metric("Unique Oasis Users", stats['unique_oasis_users'])
        st.metric("Most Popular Day", stats['most_popular_day_oasis'])
        if stats['oasis_allocations'] > 0:
            oasis_percentage = (stats['oasis_allocations'] / stats['total_allocations'] * 100)
            st.metric("% of Total Usage", f"{oasis_percentage:.1f}%")

# Current Week Overview
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
    if not current_df.empty:
        st.write("**Current Week Utilization**")
        
        # Calculate current utilization
        current_project_rooms = len(current_df[current_df['Room_Type'] == 'Project Room']['Room'].unique())
        current_project_util = (current_project_rooms / TOTAL_PROJECT_ROOMS * 100) if TOTAL_PROJECT_ROOMS > 0 else 0
        
        current_oasis_people = len(current_df[current_df['Room_Type'] == 'Oasis'])
        current_oasis_util = (current_oasis_people / OASIS_CAPACITY * 100) if OASIS_CAPACITY > 0 else 0
        
        col1, col2 = st.columns(2)
        with col1:
            st.metric("Project Rooms Utilization", f"{current_project_util:.1f}%", 
                     f"{current_project_rooms}/{TOTAL_PROJECT_ROOMS} rooms")
        with col2:
            st.metric("Oasis Utilization", f"{current_oasis_util:.1f}%", 
                     f"{current_oasis_people}/{OASIS_CAPACITY} people")
    
    # Current week schedule
    with st.expander("📋 View Current Week Schedule"):
        # Separate project and oasis data
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
    st.info("No current week allocations found.")

# Room Utilization Charts
st.subheader("🏢 Room Utilization Analysis")

col1, col2 = st.columns(2)

with col1:
    st.write("**Project Rooms Utilization**")
    if not room_util['project'].empty:
        fig_project = px.bar(room_util['project'], x='Room', y='Utilization_Rate',
                            title=f"Project Room Utilization Over Last {weeks_back} Weeks",
                            labels={'Utilization_Rate': 'Utilization (%)', 'Room': 'Room Name'},
                            color='Utilization_Rate',
                            color_continuous_scale='RdYlGn')
        fig_project.update_layout(height=400)
        st.plotly_chart(fig_project, use_container_width=True)
        
        st.dataframe(room_util['project'], use_container_width=True, hide_index=True)
    else:
        st.info("No project room data available for the selected period.")

with col2:
    st.write("**Oasis Utilization**")
    if not room_util['oasis'].empty:
        oasis_data = room_util['oasis'].iloc[0]
        
        # Create a gauge-like visualization for Oasis
        fig_oasis = go.Figure(go.Indicator(
            mode = "gauge+number+delta",
            value = oasis_data['Utilization_Rate'],
            domain = {'x': [0, 1], 'y': [0, 1]},
            title = {'text': "Oasis Utilization %"},
            delta = {'reference': 75},
            gauge = {'axis': {'range': [None, 100]},
                     'bar': {'color': "darkblue"},
                     'steps': [
                         {'range': [0, 50], 'color': "lightgray"},
                         {'range': [50, 80], 'color': "yellow"},
                         {'range': [80, 100], 'color': "red"}],
                     'threshold': {'line': {'color': "red", 'width': 4},
                                   'thickness': 0.75, 'value': 90}}))
        
        fig_oasis.update_layout(height=300)
        st.plotly_chart(fig_oasis, use_container_width=True)
        
        st.dataframe(room_util['oasis'][['Usage_Count', 'Avg_Daily_Users', 'Utilization_Rate']], 
                    use_container_width=True, hide_index=True)
    else:
        st.info("No Oasis data available for the selected period.")

# Weekly Trends
if not weekly_trends.empty:
    st.subheader("📈 Weekly Allocation Trends")
    
    fig_trends = go.Figure()
    fig_trends.add_trace(go.Scatter(x=weekly_trends['Week'], y=weekly_trends['Total_Allocations'],
                                    mode='lines+markers', name='Total Allocations'))
    fig_trends.add_trace(go.Scatter(x=weekly_trends['Week'], y=weekly_trends['Rooms_Used'],
                                    mode='lines+markers', name='Rooms Used', yaxis='y2'))
    
    fig_trends.update_layout(
        title=f"Allocation Trends Over Last {weeks_back} Weeks",
        xaxis_title="Week Starting",
        yaxis_title="Total Allocations",
        yaxis2=dict(title="Rooms Used", overlaying='y', side='right'),
        height=400
    )
    st.plotly_chart(fig_trends, use_container_width=True)

# Day of Week Analysis
if not historical_df.empty:
    st.subheader("📅 Day of Week Popularity")
    
    day_popularity = historical_df['WeekDay'].value_counts().reset_index()
    day_popularity.columns = ['Day', 'Count']
    
    # Reorder by weekday
    day_order = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday']
    day_popularity['Day'] = pd.Categorical(day_popularity['Day'], categories=day_order, ordered=True)
    day_popularity = day_popularity.sort_values('Day')
    
    fig_days = px.pie(day_popularity, values='Count', names='Day',
                      title="Allocation Distribution by Day of Week")
    st.plotly_chart(fig_days, use_container_width=True)

# Team Activity Analysis
if not historical_df.empty:
    st.subheader("👥 Most Active Teams")
    
    project_df = historical_df[historical_df['Room_Type'] == 'Project Room']
    oasis_df = historical_df[historical_df['Room_Type'] == 'Oasis']
    
    col1, col2 = st.columns(2)
    
    with col1:
        st.write("**Most Active Project Teams**")
        if not project_df.empty:
            team_activity = project_df['Team'].value_counts().head(10).reset_index()
            team_activity.columns = ['Team', 'Allocations']
            
            fig_teams = px.bar(team_activity, x='Allocations', y='Team', orientation='h',
                              title="Top 10 Most Active Project Teams",
                              labels={'Allocations': 'Number of Allocations', 'Team': 'Team Name'})
            fig_teams.update_layout(height=400)
            st.plotly_chart(fig_teams, use_container_width=True)
        else:
            st.info("No project team data available.")
            
    with col2:
        st.write("**Most Active Oasis Users**")
        if not oasis_df.empty:
            oasis_activity = oasis_df['Team'].value_counts().head(10).reset_index()
            oasis_activity.columns = ['User', 'Allocations']
            
            fig_oasis_users = px.bar(oasis_activity, x='Allocations', y='User', orientation='h',
                                    title="Top 10 Most Active Oasis Users",
                                    labels={'Allocations': 'Number of Allocations', 'User': 'User Name'})
            fig_oasis_users.update_layout(height=400)
            st.plotly_chart(fig_oasis_users, use_container_width=True)
        else:
            st.info("No Oasis user data available.")

# Current Status and Data Management
st.subheader("📋 Current Data Status & Management")

col1, col2 = st.columns(2)

with col1:
    st.write("**Current Preferences (Active)**")
    if not preferences['project'].empty:
        st.info(f"🏢 **Project Teams**: {len(preferences['project'])} active preferences")
        with st.expander("View Project Preferences"):
            st.dataframe(preferences['project'][['Team', 'Contact', 'Size', 'Preferred_Days']], 
                        use_container_width=True, hide_index=True)
    else:
        st.warning("No active project preferences")
        
    if not preferences['oasis'].empty:
        st.info(f"🌿 **Oasis Users**: {len(preferences['oasis'])} active preferences")
        with st.expander("View Oasis Preferences"):
            st.dataframe(preferences['oasis'][['Person', 'Day1', 'Day2', 'Day3', 'Day4', 'Day5']], 
                        use_container_width=True, hide_index=True)
    else:
        st.warning("No active Oasis preferences")

with col2:
    st.write("**Data Management Info**")
    st.info("""
    **How Data Transitions Work:**
    
    🔄 **Weekly Cycle:**
    - Preferences collected during submission period
    - Allocations generated from preferences
    - Data archived before deletion (backup system)
    
    🗃️ **Backup System:**
    - All deletions are backed up to archive tables
    - Historical data preserved for analysis
    - Admin can restore if needed
    
    📊 **Analytics:**
    - Project rooms and Oasis analyzed separately
    - Utilization calculated differently for each type
    - Current preferences vs historical allocations
    """)

    if st.button("🔍 Check Archive Tables"):
        # Check if archive tables have data
        conn = get_connection(pool)
        if conn:
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT COUNT(*) FROM weekly_preferences_archive")
                    archived_prefs = cur.fetchone()[0]
                    cur.execute("SELECT COUNT(*) FROM oasis_preferences_archive")
                    archived_oasis = cur.fetchone()[0]
                    cur.execute("SELECT COUNT(*) FROM weekly_allocations_archive")
                    archived_allocs = cur.fetchone()[0]
                    
                    st.success(f"""
                    **Archive Status:**
                    - Project Preferences: {archived_prefs} archived
                    - Oasis Preferences: {archived_oasis} archived
                    - Allocations: {archived_allocs} archived
                    """)
            except Exception as e:
                st.error(f"Error checking archives: {e}")
            finally:
                return_connection(pool, conn)

# Historical Data Browser
st.subheader("🗓️ Historical Data Browser")

if not historical_df.empty:
    # Week selector for historical view
    available_weeks = sorted(historical_df['WeekStart'].dt.date.unique(), reverse=True)
    
    selected_week = st.selectbox("Select Week to View", 
                                options=available_weeks,
                                format_func=lambda x: f"Week of {x}",
                                key="week_browser")
    
    if selected_week:
        week_data = historical_df[historical_df['WeekStart'].dt.date == selected_week]
        
        if not week_data.empty:
            st.write(f"**Showing allocations for week of {selected_week}**")
            
            # Create a pivot table for better visualization
            pivot_data = week_data.pivot_table(
                index='Room', 
                columns='WeekDay', 
                values='Team', 
                aggfunc='first',
                fill_value='Vacant'
            )
            
            # Reorder columns by weekday
            day_order = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday']
            pivot_data = pivot_data.reindex(columns=[d for d in day_order if d in pivot_data.columns])
            
            st.dataframe(pivot_data, use_container_width=True)
        else:
            st.info("No allocations found for the selected week.")

# Export functionality
st.subheader("📤 Export Historical Data")

if not historical_df.empty:
    col1, col2 = st.columns(2)
    
    with col1:
        if st.button("📊 Export Analytics Summary"):
            summary_data = {
                'Metric': ['Total Allocations', 'Unique Teams', 'Unique Rooms', 'Most Popular Room', 'Most Popular Day'],
                'Value': [stats['total_allocations'], stats['unique_teams'], stats['unique_rooms'], 
                         stats['most_popular_room'], stats['most_popular_day']]
            }
            summary_df = pd.DataFrame(summary_data)
            csv = summary_df.to_csv(index=False)
            st.download_button(
                label="Download Analytics Summary",
                data=csv,
                file_name=f"room_analytics_summary_{date.today()}.csv",
                mime="text/csv"
            )
    
    with col2:
        if st.button("📋 Export Raw Historical Data"):
            export_df = historical_df[['Date', 'Team', 'Room', 'WeekDay']].copy()
            export_df['Date'] = export_df['Date'].dt.strftime('%Y-%m-%d')
            csv = export_df.to_csv(index=False)
            st.download_button(
                label="Download Historical Data",
                data=csv,
                file_name=f"room_allocations_history_{date.today()}.csv",
                mime="text/csv"
            )

# Data insights
if stats:
    st.subheader("💡 Insights & Recommendations")
    
    insights = []
    
    # Utilization insights
    if not daily_util.empty:
        avg_project_util = daily_util['Project_Utilization'].mean()
        avg_oasis_util = daily_util['Oasis_Utilization'].mean()
        
        if avg_project_util > 80:
            insights.append(f"🔥 **High Project Room Demand**: {avg_project_util:.1f}% average utilization - rooms are in high demand")
        elif avg_project_util < 30:
            insights.append(f"📉 **Low Project Room Usage**: {avg_project_util:.1f}% average utilization - rooms are underutilized")
        else:
            insights.append(f"✅ **Balanced Project Room Usage**: {avg_project_util:.1f}% average utilization")
            
        if avg_oasis_util > 80:
            insights.append(f"🌿 **Oasis High Demand**: {avg_oasis_util:.1f}% average utilization - consider expanding capacity")
        elif avg_oasis_util < 30:
            insights.append(f"🌿 **Oasis Low Usage**: {avg_oasis_util:.1f}% average utilization - promote Oasis benefits")
        else:
            insights.append(f"🌿 **Oasis Balanced Usage**: {avg_oasis_util:.1f}% average utilization")
    
    # Day preferences insights
    if not historical_df.empty:
        project_df = historical_df[historical_df['Room_Type'] == 'Project Room']
        oasis_df = historical_df[historical_df['Room_Type'] == 'Oasis']
        
        if not project_df.empty:
            most_popular_project_day = project_df['WeekDay'].mode().iloc[0] if len(project_df['WeekDay'].mode()) > 0 else "N/A"
            insights.append(f"📅 **Project Peak Day**: {most_popular_project_day} is most requested for project rooms")
            
        if not oasis_df.empty:
            most_popular_oasis_day = oasis_df['WeekDay'].mode().iloc[0] if len(oasis_df['WeekDay'].mode()) > 0 else "N/A"
            insights.append(f"📅 **Oasis Peak Day**: {most_popular_oasis_day} is most popular for Oasis")
    
    # Preference vs allocation insights
    if stats['current_project_preferences'] == 0:
        insights.append("⚠️ **No Active Project Preferences**: Teams may need to submit preferences for upcoming week")
    
    if stats['current_oasis_preferences'] == 0:
        insights.append("⚠️ **No Active Oasis Preferences**: Individuals may need to submit Oasis preferences")
    
    # Usage balance
    if stats['total_allocations'] > 0:
        project_ratio = (stats['project_allocations'] / stats['total_allocations']) * 100
        oasis_ratio = (stats['oasis_allocations'] / stats['total_allocations']) * 100
        insights.append(f"⚖️ **Usage Split**: {project_ratio:.1f}% Project Rooms, {oasis_ratio:.1f}% Oasis")
    
    for insight in insights:
        st.info(insight)

else:
    st.info("No historical data available. Allocations will appear here once the system has been used.")
