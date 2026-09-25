-- SQLite schema (ARCHITECTURE §3.5). Owner: Jaiveer. Task J4.
-- Copied verbatim from §3.5; the schema is a contract (changes are gated, TEAM_PROTOCOL §4).
-- jaiveer_db.py applies it once (when the nodes table is missing) and sets the PRAGMAs on every connection.

PRAGMA journal_mode=WAL;
PRAGMA synchronous=FULL;          -- naive mode: OFF
PRAGMA foreign_keys=ON;

CREATE TABLE nodes (
  id TEXT PRIMARY KEY, addr TEXT NOT NULL, display_name TEXT NOT NULL,
  labels TEXT NOT NULL DEFAULT '{}',            -- JSON {"power":"A","switch":"S1","disk_batch":"D1","version":"1.0"}
  capacity_bytes INTEGER NOT NULL,
  persisted_state TEXT NOT NULL DEFAULT 'ALIVE',-- only DEAD / DRAINING / RETIRED / ALIVE are persisted
  epoch INTEGER NOT NULL DEFAULT 1,
  created_at REAL NOT NULL
);
CREATE TABLE buckets (name TEXT PRIMARY KEY, policy TEXT NOT NULL, created_at REAL NOT NULL);  -- policy JSON
CREATE TABLE objects (
  bucket TEXT NOT NULL, key TEXT NOT NULL,
  current_version_id TEXT,                       -- data or tombstone version; NULL never after first commit
  last_seq INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (bucket, key)
);
CREATE TABLE versions (
  version_id TEXT PRIMARY KEY, bucket TEXT NOT NULL, key TEXT NOT NULL,
  kind TEXT NOT NULL,                            -- data | tombstone
  state TEXT NOT NULL,                           -- VersionState
  seq INTEGER, commit_seq INTEGER,               -- set at commit
  size INTEGER NOT NULL DEFAULT 0, sha256 TEXT, policy TEXT NOT NULL, chunk_size INTEGER NOT NULL,
  created_at REAL NOT NULL, committed_at REAL, superseded_at REAL
);
CREATE INDEX versions_key   ON versions(bucket, key, commit_seq);
CREATE INDEX versions_state ON versions(state, created_at);
CREATE TABLE chunks (
  chunk_id TEXT PRIMARY KEY, version_id TEXT NOT NULL REFERENCES versions(version_id),
  idx INTEGER NOT NULL, size INTEGER NOT NULL, sha256 TEXT, frag_size INTEGER NOT NULL
);
CREATE INDEX chunks_version ON chunks(version_id, idx);
CREATE TABLE fragments (
  chunk_id TEXT NOT NULL REFERENCES chunks(chunk_id), frag_idx INTEGER NOT NULL, node_id TEXT NOT NULL,
  state TEXT NOT NULL, sha256 TEXT, updated_at REAL NOT NULL,
  PRIMARY KEY (chunk_id, frag_idx, node_id)
);
CREATE INDEX fragments_node  ON fragments(node_id, state);
CREATE INDEX fragments_state ON fragments(state);
CREATE TABLE jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, reason TEXT NOT NULL,
  chunk_id TEXT, frag_idx INTEGER, source_node TEXT, target_node TEXT,
  priority INTEGER NOT NULL, state TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
  bytes INTEGER NOT NULL DEFAULT 0, error TEXT, incident_id INTEGER,
  created_at REAL NOT NULL, started_at REAL, finished_at REAL
);
CREATE INDEX jobs_queue ON jobs(state, priority, created_at);
CREATE TABLE incidents (
  id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL,   -- node_dead | corruption | power_cut | fate | partition
  subject TEXT, fault_at REAL, detected_at REAL, repair_started_at REAL, recovered_at REAL,
  affected_chunks INTEGER NOT NULL DEFAULT 0, remaining_chunks INTEGER NOT NULL DEFAULT 0,
  bytes_repaired INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, type TEXT NOT NULL, severity TEXT NOT NULL,
  subject TEXT NOT NULL DEFAULT '{}', human TEXT NOT NULL, technical TEXT NOT NULL, data TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE kv (k TEXT PRIMARY KEY, v TEXT NOT NULL);   -- commit_seq, config overrides, cluster_id
