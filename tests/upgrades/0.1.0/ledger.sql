BEGIN TRANSACTION;
CREATE TABLE audit_marks (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        sequence     INTEGER NOT NULL,
        head         TEXT NOT NULL,
        state_digest TEXT NOT NULL,
        status       TEXT NOT NULL CHECK (status IN ('verified', 'failed')),
        detail       TEXT NOT NULL,
        auditor      TEXT NOT NULL,
        recorded_at  TEXT NOT NULL,
        signature    TEXT
    );
CREATE TABLE control_events (
                    id                INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id          TEXT NOT NULL UNIQUE,
                    timestamp         TEXT NOT NULL,
                    event_type        TEXT NOT NULL,
                    scope_id          TEXT NOT NULL,
                    reason            TEXT NOT NULL,
                    ledger_head_hash  TEXT NOT NULL,
                    entry_id          TEXT
                );
CREATE TABLE entries (
                    sequence        INTEGER PRIMARY KEY,
                    entry_id        TEXT NOT NULL UNIQUE,
                    transaction_id  TEXT NOT NULL,
                    timestamp       TEXT NOT NULL,
                    entry_type      TEXT NOT NULL,
                    direction       TEXT NOT NULL,
                    scope_id        TEXT NOT NULL,
                    counterparty_id TEXT,
                    amount          TEXT NOT NULL,
                    balance_after   TEXT NOT NULL,
                    prev_hash       TEXT NOT NULL,
                    entry_hash      TEXT NOT NULL,
                    memo            TEXT NOT NULL DEFAULT '',
                    version         TEXT NOT NULL DEFAULT 'AGOV1',
                    ref             TEXT
                );
INSERT INTO "entries" VALUES(1,'26fd1cf9-3109-401b-8272-eddd8a28d716','327a594f-5b65-4d04-895a-f9bf15eed7b5','2026-10-23T04:12:33.692052Z','funding','CR','fiduciary',NULL,'0.00000001','0.00000001','0000000000000000000000000000000000000000000000000000000000000000','d8683b86e6d46995f0a4471badcf34aa5a027eb8878714982c5fc6da591b866e','FIDU1 anchors: this scope spends nothing','AGOV3',NULL);
INSERT INTO "entries" VALUES(2,'7b997d3a-08a5-45a8-816d-71526b49695f','b920370d-611d-4514-9d6b-0434d3507982','2026-10-23T04:12:33.692405Z','funding','CR','support',NULL,'1000.00000000','1000.00000000','d8683b86e6d46995f0a4471badcf34aa5a027eb8878714982c5fc6da591b866e','e2b53dee3139d88aaf11755df268904b5a8fd8661d448dc1e5b7980afd18d2d4','root envelope issued','AGOV3',NULL);
INSERT INTO "entries" VALUES(3,'cd297823-fe5d-4616-99e9-349460bed520','7c2a9a77-d90e-4e7d-a8c3-4f1c4c265a9e','2026-10-23T04:12:33.697273Z','anchor','--','fiduciary',NULL,'0.00000000','0.00000001','e2b53dee3139d88aaf11755df268904b5a8fd8661d448dc1e5b7980afd18d2d4','f5a8a34ae96a3960b93b2584ebb80baf7e0181b10c0c57755a25c93984861ad4','FIDU1 fiduciary 1 67649ba7c3e6db738812ed07a48fd183c8684e266f528dec42cca8cfa387f9cf','AGOV3',NULL);
INSERT INTO "entries" VALUES(4,'cd2a14a0-bd23-4324-b97c-b73a867fda7d','e07f3d67-43f2-4b3f-93e6-80231477d3be','2026-10-23T04:12:33.698618Z','anchor','--','fiduciary',NULL,'0.00000000','0.00000001','f5a8a34ae96a3960b93b2584ebb80baf7e0181b10c0c57755a25c93984861ad4','02958393ee938648cb72958ca504a9ca0acbc3ea979c2a695634d2eabd345e7d','FIDU1 fiduciary 2 7a682c37758f4694ecfaa24c2df43d7b97b57683d61a566c1969136afa4c086c','AGOV3',NULL);
INSERT INTO "entries" VALUES(5,'70422209-1300-4919-871d-2d19c2709b9f','44d76d6c-14f3-4a6d-a0d9-3ed217758efe','2026-10-23T04:12:33.699971Z','anchor','--','fiduciary',NULL,'0.00000000','0.00000001','02958393ee938648cb72958ca504a9ca0acbc3ea979c2a695634d2eabd345e7d','352bc9512f421296b912deb51679242e9fa76225533076aaaabb2b8cd667e406','FIDU1 fiduciary 3 952233d9506c4f7f25c8cc8e7cf9a67baf36c34ad9ad5722e0f3c0adf123f861','AGOV3',NULL);
INSERT INTO "entries" VALUES(6,'dddd700a-288e-4a4a-b76a-f4da5cbc431e','500c703e-1046-4431-9281-6f3ae3f74424','2026-10-23T04:12:33.701616Z','anchor','--','fiduciary',NULL,'0.00000000','0.00000001','352bc9512f421296b912deb51679242e9fa76225533076aaaabb2b8cd667e406','62f81896b8fec1130ac606892809e4c70f81212a5a9404ca5e7245f15a75f766','FIDU1 fiduciary 4 500253bc15601c74e4d67877f4184a2b54485e8b932bcb47324a188e8fdc5681','AGOV3',NULL);
INSERT INTO "entries" VALUES(7,'00ab4249-9c5f-40ee-8ba7-46cccc3b1052','9e7556fb-825f-4e71-91e5-a0a8fd8ac941','2026-10-23T04:12:33.707808Z','anchor','--','fiduciary',NULL,'0.00000000','0.00000001','62f81896b8fec1130ac606892809e4c70f81212a5a9404ca5e7245f15a75f766','6fc278dc5597f0bbd083a5fd96a8a824efed776c603c8aa9c60046fbef572bdb','FIDU1 fiduciary 5 9babb9ab9de3d286da29e30a6a753c4603c2923a6fc7b4970b8b9a0c5a94c7b5','AGOV3',NULL);
INSERT INTO "entries" VALUES(8,'69eb37e3-f085-49fe-830c-b027b116354a','6b53e74b-9d22-4bf4-be55-d9c2d1924649','2026-10-23T04:12:33.709297Z','anchor','--','fiduciary',NULL,'0.00000000','0.00000001','6fc278dc5597f0bbd083a5fd96a8a824efed776c603c8aa9c60046fbef572bdb','256e23cd764261bb5e7afc4b944637715e01253cc932711510fb63821f584027','FIDU1 fiduciary 6 034e0868118885d15be516771cd6c79ba123b69a3092dcf5f7eaf8932a0f64f1','AGOV3',NULL);
INSERT INTO "entries" VALUES(9,'513d7357-9ee9-4774-b9fe-33e6b344c8d0','fbe2218e-e882-4686-9bad-7a06526092d3','2026-10-23T04:12:33.711762Z','anchor','--','fiduciary',NULL,'0.00000000','0.00000001','256e23cd764261bb5e7afc4b944637715e01253cc932711510fb63821f584027','57a79f92b190a3b85104a8adc1107ebc30674d2416140e31806deede1ae4894f','FIDU1 fiduciary 7 28eb51b09a9d31042a194a2a7483d4164b6c835059f7fda6b46d7e780932d049','AGOV3',NULL);
CREATE TABLE idempotency (
        key        TEXT PRIMARY KEY,
        request    TEXT NOT NULL,
        outcome    TEXT NOT NULL,
        sequence   INTEGER NOT NULL,
        created_at TEXT NOT NULL
    );
CREATE TABLE nodes (
                    scope_id   TEXT PRIMARY KEY,
                    parent_id  TEXT,
                    depth      INTEGER NOT NULL,
                    allocated  TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
INSERT INTO "nodes" VALUES('fiduciary',NULL,0,'1E-8','2026-10-23T04:12:33.692052Z');
INSERT INTO "nodes" VALUES('support',NULL,0,'1000.00000000','2026-10-23T04:12:33.692405Z');
CREATE TABLE open_authorizations (
                    authorization_id TEXT PRIMARY KEY,
                    scope_id         TEXT NOT NULL,
                    amount           TEXT NOT NULL,
                    opened_at        TEXT NOT NULL,
                    entry_id         TEXT NOT NULL
                );
CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
INSERT INTO "schema_meta" VALUES('schema_version','3');
CREATE TABLE snapshots (
        sequence   INTEGER PRIMARY KEY CHECK (sequence > 0),
        digest     TEXT NOT NULL,
        body       BLOB NOT NULL,
        signature  TEXT,
        created_at TEXT NOT NULL
    );
CREATE INDEX entries_by_scope ON entries (scope_id, sequence);
CREATE INDEX entries_by_type_memo ON entries (entry_type, memo);
CREATE INDEX entries_reversals ON entries (ref) WHERE entry_type = 'reversal';
CREATE INDEX control_events_by_entry ON control_events (entry_id);
DELETE FROM "sqlite_sequence";
COMMIT;
