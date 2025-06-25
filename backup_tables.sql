-- Archive tables for data backup and audit trail
-- Add these to your Supabase database

-- Archive for deleted weekly preferences
CREATE TABLE weekly_preferences_archive (
    archive_id SERIAL PRIMARY KEY,
    original_id INTEGER,
    team_name VARCHAR(255),
    contact_person VARCHAR(255),
    team_size INTEGER,
    preferred_days VARCHAR(100),
    submission_time TIMESTAMP,
    deleted_at TIMESTAMP DEFAULT NOW(),
    deleted_by VARCHAR(255),
    deletion_reason TEXT
);

-- Archive for deleted oasis preferences  
CREATE TABLE oasis_preferences_archive (
    archive_id SERIAL PRIMARY KEY,
    original_id INTEGER,
    person_name VARCHAR(255),
    preferred_day_1 VARCHAR(20),
    preferred_day_2 VARCHAR(20),
    preferred_day_3 VARCHAR(20),
    preferred_day_4 VARCHAR(20),
    preferred_day_5 VARCHAR(20),
    submission_time TIMESTAMP,
    deleted_at TIMESTAMP DEFAULT NOW(),
    deleted_by VARCHAR(255),
    deletion_reason TEXT
);

-- Archive for deleted allocations
CREATE TABLE weekly_allocations_archive (
    archive_id SERIAL PRIMARY KEY,
    original_id INTEGER,
    team_name VARCHAR(255),
    room_name VARCHAR(255),
    date DATE,
    allocated_at TIMESTAMP,
    deleted_at TIMESTAMP DEFAULT NOW(),
    deleted_by VARCHAR(255),
    deletion_reason TEXT
);

-- Create indexes for better performance
CREATE INDEX idx_weekly_prefs_arch_team ON weekly_preferences_archive(team_name);
CREATE INDEX idx_oasis_prefs_arch_person ON oasis_preferences_archive(person_name);
CREATE INDEX idx_weekly_alloc_arch_date ON weekly_allocations_archive(date);
