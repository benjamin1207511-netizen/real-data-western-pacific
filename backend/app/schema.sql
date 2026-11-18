PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS stations (
    id TEXT PRIMARY KEY,
    wmo_id TEXT,
    icao_id TEXT,
    name TEXT NOT NULL,
    name_zh TEXT,
    country TEXT,
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    elev_m REAL,
    type TEXT NOT NULL DEFAULT 'land',
    primary_source TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_stations_geo ON stations (lat, lon);
CREATE INDEX IF NOT EXISTS idx_stations_codes ON stations (wmo_id, icao_id);

CREATE TABLE IF NOT EXISTS station_aliases (
    source TEXT NOT NULL,
    source_id TEXT NOT NULL,
    station_id TEXT NOT NULL,
    PRIMARY KEY (source, source_id)
);

CREATE TABLE IF NOT EXISTS observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    station_id TEXT NOT NULL,
    obs_time TEXT NOT NULL,
    source TEXT NOT NULL,
    wind_dir_deg REAL,
    wind_ms REAL,
    gust_ms REAL,
    pressure_hpa REAL,
    pressure_basis TEXT,
    temp_c REAL,
    dewpoint_c REAL,
    rh_pct REAL,
    sea_temp_c REAL,
    precip_1h_mm REAL,
    precip_3h_mm REAL,
    precip_24h_mm REAL,
    visibility_m REAL,
    weather TEXT,
    clouds TEXT,
    qc_flags TEXT,
    raw_json TEXT,
    ingested_at TEXT NOT NULL,
    UNIQUE (station_id, obs_time, source)
);
CREATE INDEX IF NOT EXISTS idx_obs_station_time ON observations (station_id, obs_time DESC);
CREATE INDEX IF NOT EXISTS idx_obs_time ON observations (obs_time DESC);

CREATE TABLE IF NOT EXISTS station_current (
    station_id TEXT PRIMARY KEY,
    obs_time TEXT,
    pressure_hpa REAL,
    pressure_basis TEXT,
    pressure_source TEXT,
    dp_24h REAL,
    dp_24h_gap_min REAL,
    temp_c REAL,
    temp_source TEXT,
    dt_24h REAL,
    dt_24h_gap_min REAL,
    rh_pct REAL,
    rh_source TEXT,
    sea_temp_c REAL,
    sea_temp_source TEXT,
    wind_dir_deg REAL,
    wind_ms REAL,
    gust_ms REAL,
    wind_source TEXT,
    precip_1h_mm REAL,
    precip_3h_mm REAL,
    precip_24h_mm REAL,
    precip_source TEXT,
    quality TEXT,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_current_dp ON station_current (dp_24h);
CREATE INDEX IF NOT EXISTS idx_current_wind ON station_current (wind_ms DESC);

CREATE TABLE IF NOT EXISTS storms (
    id TEXT PRIMARY KEY,
    agency TEXT NOT NULL,
    season INTEGER,
    number TEXT,
    name_en TEXT,
    name_zh TEXT,
    status TEXT,
    category TEXT,
    lat REAL,
    lon REAL,
    pressure_hpa REAL,
    wind_ms REAL,
    motion_dir TEXT,
    motion_speed_kmh REAL,
    observed_at TEXT,
    updated_at TEXT NOT NULL,
    raw_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_storms_status ON storms (status, observed_at DESC);

CREATE TABLE IF NOT EXISTS track_points (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    storm_id TEXT NOT NULL,
    agency TEXT NOT NULL,
    valid_time TEXT NOT NULL,
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    pressure_hpa REAL,
    wind_ms REAL,
    category TEXT,
    wind_radii_json TEXT,
    forecast INTEGER NOT NULL DEFAULT 0,
    issued_at TEXT,
    UNIQUE (storm_id, agency, valid_time, forecast)
);
CREATE INDEX IF NOT EXISTS idx_track_storm_time ON track_points (storm_id, valid_time);

CREATE TABLE IF NOT EXISTS satellite_frames (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel TEXT NOT NULL,
    frame_time TEXT NOT NULL,
    path TEXT NOT NULL,
    west REAL NOT NULL,
    south REAL NOT NULL,
    east REAL NOT NULL,
    north REAL NOT NULL,
    width INTEGER,
    height INTEGER,
    bytes INTEGER,
    source TEXT NOT NULL DEFAULT 'NASA GIBS Himawari-9',
    created_at TEXT NOT NULL,
    UNIQUE (channel, frame_time)
);

CREATE TABLE IF NOT EXISTS imerg_meta (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    frame_time TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS source_status (
    source TEXT PRIMARY KEY,
    label TEXT,
    status TEXT NOT NULL DEFAULT 'idle',
    last_attempt TEXT,
    last_success TEXT,
    latency_ms INTEGER,
    item_count INTEGER,
    message TEXT
);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
