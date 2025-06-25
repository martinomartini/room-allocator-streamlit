# Oasis Confirmation Tracking Setup

## Overview
This update adds confirmation tracking for Oasis attendance. Now the analytics will only count people who have actually confirmed their attendance via the matrix interface, not just those who were automatically allocated.

## Database Setup Required

To enable this feature, you need to run the SQL commands in `backup_tables.sql` on your Supabase database.

### Steps:

1. **Open your Supabase Dashboard**
   - Go to your project dashboard
   - Navigate to "SQL Editor"

2. **Run the SQL Commands**
   - Copy the contents of `backup_tables.sql`
   - Paste and execute in the SQL Editor
   - This will:
     - Add `confirmed` and `confirmed_at` columns to `weekly_allocations` table
     - Update archive tables to include confirmation status
     - Create necessary indexes

3. **Verify Setup**
   - After running the SQL, the analytics page will no longer show the warning message
   - Oasis utilization will now only count confirmed attendees

## How It Works

### Before Setup (Current Behavior)
- All Oasis allocations are counted in analytics
- No distinction between allocated and confirmed attendees

### After Setup (New Behavior)
- **Ad-hoc additions**: Marked as `confirmed = FALSE` (need matrix confirmation)
- **Matrix selections**: Marked as `confirmed = TRUE` when saved
- **Analytics**: Only counts confirmed attendees (`confirmed = TRUE`)

## User Experience

1. **Ad-hoc Addition**: User adds themselves to Oasis → Shows as unconfirmed
2. **Matrix Confirmation**: User checks boxes in matrix and saves → Marked as confirmed
3. **Analytics**: Only confirmed attendees appear in utilization calculations

This ensures analytics reflect actual attendance, not just allocations.
