-- Portfolio OS phase 1: private, single-owner schema. Initially paused.
-- No project data, login passwords, or API keys belong in this migration.
CREATE SCHEMA portfolio_os;
REVOKE ALL ON SCHEMA portfolio_os FROM PUBLIC, anon, authenticated;
CREATE ROLE portfolio_os_runtime NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
GRANT USAGE ON SCHEMA portfolio_os TO portfolio_os_runtime;

CREATE TABLE portfolio_os.control (
 id integer PRIMARY KEY, paused boolean NOT NULL, lease_token varchar(36),
 lease_until integer NOT NULL, CONSTRAINT singleton_control CHECK (id=1)
);
CREATE TABLE portfolio_os.projects (
 id varchar(36) PRIMARY KEY, slug varchar(64) NOT NULL UNIQUE,
 name varchar(160) NOT NULL, summary text NOT NULL, domain varchar(20) NOT NULL,
 source_note text NOT NULL, stage varchar(20) NOT NULL, mode varchar(20) NOT NULL,
 human_slot integer UNIQUE, automation_enabled boolean NOT NULL, revision integer NOT NULL,
 next_review_at integer NOT NULL, last_review_at integer NOT NULL, idle_reviews integer NOT NULL,
 created_at integer NOT NULL, updated_at integer NOT NULL,
 CONSTRAINT stage_allowed CHECK (stage IN ('INBOX','IDEA','EXPLORE','VALIDATE','PILOT','EXECUTE')),
 CONSTRAINT mode_allowed CHECK (mode IN ('AI','HUMAN','PARKED','ARCHIVED')),
 CONSTRAINT human_capacity CHECK (human_slot IS NULL OR human_slot BETWEEN 1 AND 3),
 CONSTRAINT human_slot_consistency CHECK ((mode='HUMAN' AND human_slot IS NOT NULL) OR (mode<>'HUMAN' AND human_slot IS NULL)),
 CONSTRAINT revision_positive CHECK (revision>=1)
);
CREATE INDEX project_due_idx ON portfolio_os.projects (automation_enabled,next_review_at);
CREATE TABLE portfolio_os.agent_runs (
 id varchar(36) PRIMARY KEY, project_id varchar(36) NOT NULL REFERENCES portfolio_os.projects(id),
 kind varchar(20) NOT NULL, provider varchar(20) NOT NULL, model varchar(120) NOT NULL,
 status varchar(24) NOT NULL, day varchar(10) NOT NULL, started_at integer NOT NULL,
 finished_at integer, input_hash varchar(64) NOT NULL, policy_hash varchar(64) NOT NULL,
 output json NOT NULL, input_tokens integer NOT NULL, output_tokens integer NOT NULL,
 request_id varchar(200), error_code varchar(200)
);
CREATE INDEX run_day_idx ON portfolio_os.agent_runs(day,provider);
CREATE INDEX run_project_idx ON portfolio_os.agent_runs(project_id,status);
CREATE TABLE portfolio_os.approvals (
 id varchar(36) PRIMARY KEY, project_id varchar(36) NOT NULL REFERENCES portfolio_os.projects(id),
 project_revision integer NOT NULL, action varchar(24) NOT NULL, target_stage varchar(20),
 reason text NOT NULL, objection text NOT NULL, status varchar(24) NOT NULL,
 digest varchar(64) NOT NULL, created_at integer NOT NULL, expires_at integer NOT NULL,
 decided_at integer, decision_note text,
 CONSTRAINT approval_allowlist CHECK (action IN ('HUMAN_ACTIVE','ADVANCE','PARK','ARCHIVE'))
);
CREATE INDEX approval_project_idx ON portfolio_os.approvals(project_id,status);
CREATE TABLE portfolio_os.decisions (
 id varchar(36) PRIMARY KEY, project_id varchar(36) REFERENCES portfolio_os.projects(id),
 actor varchar(30) NOT NULL, event varchar(50) NOT NULL, data json NOT NULL, created_at integer NOT NULL
);
CREATE INDEX decision_project_idx ON portfolio_os.decisions(project_id,created_at);
CREATE TABLE portfolio_os.evidence (
 id varchar(36) PRIMARY KEY, project_id varchar(36) NOT NULL REFERENCES portfolio_os.projects(id),
 statement text NOT NULL, url text, title text NOT NULL, limitation text NOT NULL,
 kind varchar(30) NOT NULL, customer_signal boolean NOT NULL,
 run_id varchar(36) REFERENCES portfolio_os.agent_runs(id), created_at integer NOT NULL,
 fingerprint varchar(64) NOT NULL,
 CONSTRAINT evidence_dedupe UNIQUE(project_id,fingerprint),
 CONSTRAINT evidence_kind_allowed CHECK (kind IN ('HUMAN_OBSERVATION','WEB_RETRIEVED','DEMO')),
 CONSTRAINT customer_evidence_human_only CHECK (NOT customer_signal OR kind='HUMAN_OBSERVATION')
);
CREATE INDEX evidence_run_idx ON portfolio_os.evidence(run_id);
CREATE TABLE portfolio_os.tasks (
 id varchar(36) PRIMARY KEY, project_id varchar(36) NOT NULL REFERENCES portfolio_os.projects(id),
 project_revision integer NOT NULL, kind varchar(30) NOT NULL, status varchar(24) NOT NULL,
 objective text NOT NULL, success_condition text NOT NULL, dedupe_key varchar(64) NOT NULL UNIQUE,
 created_at integer NOT NULL, started_at integer, finished_at integer,
 run_id varchar(36) REFERENCES portfolio_os.agent_runs(id), artifact text, error_code varchar(200),
 CONSTRAINT task_allowlist CHECK (kind IN ('RESEARCH','CODEX_BRIEF'))
);
CREATE INDEX task_project_idx ON portfolio_os.tasks(project_id,status);
CREATE INDEX task_run_idx ON portfolio_os.tasks(run_id);
INSERT INTO portfolio_os.control(id,paused,lease_token,lease_until) VALUES(1,true,NULL,0);

-- Runtime has no schema mutation / DELETE privileges. Decisions are append-only.
DO $security$
DECLARE t text;
BEGIN
 FOREACH t IN ARRAY ARRAY['control','projects','agent_runs','approvals','decisions','evidence','tasks'] LOOP
  EXECUTE format('ALTER TABLE portfolio_os.%I ENABLE ROW LEVEL SECURITY',t);
  EXECUTE format('REVOKE ALL ON TABLE portfolio_os.%I FROM PUBLIC, anon, authenticated',t);
  EXECUTE format('GRANT SELECT, INSERT ON TABLE portfolio_os.%I TO portfolio_os_runtime',t);
  IF t<>'decisions' THEN
   EXECUTE format('GRANT UPDATE ON TABLE portfolio_os.%I TO portfolio_os_runtime',t);
  END IF;
  EXECUTE format('CREATE POLICY runtime_access ON portfolio_os.%I FOR ALL TO portfolio_os_runtime USING (true) WITH CHECK (true)',t);
 END LOOP;
END
$security$;
ALTER DEFAULT PRIVILEGES IN SCHEMA portfolio_os REVOKE ALL ON TABLES FROM PUBLIC,anon,authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA portfolio_os REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC,anon,authenticated;
-- A dedicated LOGIN and its password must be provisioned securely, not in Git/chat.
