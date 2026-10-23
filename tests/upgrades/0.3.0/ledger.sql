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
INSERT INTO "control_events" VALUES(1,'72f5622c-96e9-4871-b907-efc04c0b9f68','2026-10-23T04:12:34.876888Z','circuit_tripped','support-agent/child','FIDU1 revoked fd4d4036672c043c1d2e962a07e984d4f223fbdd43d0780a8817d488a38f4dbb','f3852f8426fb3408a2e24eea5e7dba9da1dfbe4871a68cfb0120c5c04b815ba0','72f5622c-96e9-4871-b907-efc04c0b9f68');
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
INSERT INTO "entries" VALUES(1,'b69ac624-a8f8-4909-9f5f-fd9a190e44bc','c741bf08-1802-44ae-b7de-72472e93e1cf','2026-10-23T04:12:34.721343Z','funding','CR','fiduciary',NULL,'0.00000001','0.00000001','0000000000000000000000000000000000000000000000000000000000000000','bee57e5d200d1ddf61711fe7e93910888580913de20d29668912ae1101d9406a','FIDU1 anchors: this scope spends nothing','AGOV3',NULL);
INSERT INTO "entries" VALUES(2,'e0e4a7d6-7e92-4e4d-94d0-923b5647add5','5758d295-a37c-4948-b2a9-b2e8a6e6ddb3','2026-10-23T04:12:34.721719Z','funding','CR','support',NULL,'1000.00000000','1000.00000000','bee57e5d200d1ddf61711fe7e93910888580913de20d29668912ae1101d9406a','ec9751e15be362dca09f484b142c7b16212f0c0f6d77a809f7bdfb2dd34a2978','root envelope issued','AGOV3',NULL);
INSERT INTO "entries" VALUES(3,'d6b8811b-5156-4a4b-b8b0-970634e7c9ab','c879ea31-cd4a-476f-847e-7b227245b8d4','2026-10-23T04:12:34.733372Z','anchor','--','fiduciary',NULL,'0.00000000','0.00000001','ec9751e15be362dca09f484b142c7b16212f0c0f6d77a809f7bdfb2dd34a2978','af2e07e2ac6e873e27910ef74ea35649f59618f2b67f2371c452a27f0514499a','FIDU1 fiduciary 1 44c27c958d98aa279315d54ef78cc6770582cf81268f8d77a9b49810bc8ebdbc','AGOV3',NULL);
INSERT INTO "entries" VALUES(4,'00d78f20-eea9-4898-8b1f-d669c7d1e96e','a5636dfd-73f1-46ab-96c3-42b068f685b6','2026-10-23T04:12:34.734883Z','anchor','--','fiduciary',NULL,'0.00000000','0.00000001','af2e07e2ac6e873e27910ef74ea35649f59618f2b67f2371c452a27f0514499a','e7be5a4963f72c56198785ef527ed678fc18b82813089a7b1730359420166c3c','FIDU1 fiduciary 2 16382587d2d9666a1a0fb000638d7b64dc1e48805737ba7a8baee1127d5ef838','AGOV3',NULL);
INSERT INTO "entries" VALUES(5,'e2dd4f6f-dba1-4c01-acee-4d6849069c42','7e19f43f-e533-4fe2-aa02-5c9da0a23e9b','2026-10-23T04:12:34.736313Z','anchor','--','fiduciary',NULL,'0.00000000','0.00000001','e7be5a4963f72c56198785ef527ed678fc18b82813089a7b1730359420166c3c','e55e29ff4d3fddb2e41338149ac4c67195a43f0b9e380351c45a4a437869dd09','FIDU1 fiduciary 3 e1ec78c34c05010366844d4ca8025d1f7a8beb1229aa440788d3a7e989eef96e','AGOV3',NULL);
INSERT INTO "entries" VALUES(6,'616d6aa0-93a0-4060-90ad-50e8959b8f9f','4937a082-dc7f-4676-a4f4-d6e5c803676d','2026-10-23T04:12:34.738149Z','anchor','--','fiduciary',NULL,'0.00000000','0.00000001','e55e29ff4d3fddb2e41338149ac4c67195a43f0b9e380351c45a4a437869dd09','22311b3ec54972e918f5f867c52ffef287a9ff7dc72a985f85b7927c8317a823','FIDU1 fiduciary 4 c8007848ea7da9b8df6c676b1f71c5031675d4691a82d436e6c97f241f7a85c4','AGOV3',NULL);
INSERT INTO "entries" VALUES(7,'2ab65e89-32a8-4bc9-b1cd-d0766e127c26','24569b0f-fd94-4819-ae9b-fe595eec07dd','2026-10-23T04:12:34.748850Z','anchor','--','fiduciary',NULL,'0.00000000','0.00000001','22311b3ec54972e918f5f867c52ffef287a9ff7dc72a985f85b7927c8317a823','3a8e5ecec9c51a788c26eaef98a6741579b8fdc9affb5d5383fb58205251c2fe','FIDU1 fiduciary 5 2d74e515e7303402e89ae30a5193c195f72ec3c8c2ed85b260916aead2eb2fd0','AGOV3',NULL);
INSERT INTO "entries" VALUES(8,'fb7298b7-a543-4b9f-9f8a-f3d58667f30d','7f7f3664-772c-4a02-92fe-2523e647eb5a','2026-10-23T04:12:34.750432Z','anchor','--','fiduciary',NULL,'0.00000000','0.00000001','3a8e5ecec9c51a788c26eaef98a6741579b8fdc9affb5d5383fb58205251c2fe','1954695df0733e6a86cad019d0f18ea47cc1766bd48fb415ce8bf9131719f8eb','FIDU1 fiduciary 6 2ae5e4f96538d34d03aca8448af82cf3ad66422b4d345ad379bdfd400934bbc5','AGOV3',NULL);
INSERT INTO "entries" VALUES(9,'5def7649-7b12-49c6-96af-5f780315df1f','6902bd9b-18e3-4655-ab4d-6a5fb526371c','2026-10-23T04:12:34.753054Z','anchor','--','fiduciary',NULL,'0.00000000','0.00000001','1954695df0733e6a86cad019d0f18ea47cc1766bd48fb415ce8bf9131719f8eb','d75917c0db8d0efa27f4bb8767b93055669bce7be0d445dc9ea6f64d353db26e','FIDU1 fiduciary 7 2052a02217e11dcad7b5209196635c287f3e0fa1b62a837160c3321dca6c0c9a','AGOV3',NULL);
INSERT INTO "entries" VALUES(10,'af29b836-8674-4cfe-9f57-5ccae7a2039f','fef86d7a-9d71-49c9-9f6a-f1acceec7e9e','2026-10-23T04:12:34.756405Z','allocation','DR','support','support-agent','250.00000000','750.00000000','d75917c0db8d0efa27f4bb8767b93055669bce7be0d445dc9ea6f64d353db26e','7f9bc9fa3423796818ed16a7580d6d69c372298d0b24b74d1f72ec654cd7c1d2','FIDU1 bind 9f565943f8b48ea67c32c968e3b1f65be3583ac2ddaffc6e25a1d0da6e58ce42','AGOV3',NULL);
INSERT INTO "entries" VALUES(11,'a12a95e1-dd69-49c1-8e0e-b64215909403','fef86d7a-9d71-49c9-9f6a-f1acceec7e9e','2026-10-23T04:12:34.756405Z','allocation','CR','support-agent','support','250.00000000','250.00000000','7f9bc9fa3423796818ed16a7580d6d69c372298d0b24b74d1f72ec654cd7c1d2','0bc98e6cb91235d93722f85240da7364a0f8a8f9f1624d5f154cf72fcb601257','FIDU1 bind 9f565943f8b48ea67c32c968e3b1f65be3583ac2ddaffc6e25a1d0da6e58ce42','AGOV3',NULL);
INSERT INTO "entries" VALUES(12,'42d4e24d-7436-45f4-a1ae-3892584f11e4','5a31c359-688d-4b63-8698-c2d30ccd0d9a','2026-10-23T04:12:34.757491Z','anchor','--','fiduciary',NULL,'0.00000000','0.00000001','0bc98e6cb91235d93722f85240da7364a0f8a8f9f1624d5f154cf72fcb601257','fb5ce8b2c00cdc19488759c96285fbc407bb069ec0fc076e61dfa531594afff3','FIDU1 fiduciary 8 96b6f81dcc7c39e7903dc19c5ab4ad983dfce08bbe4b51cacbf70417ae3d0e01','AGOV3',NULL);
INSERT INTO "entries" VALUES(13,'ac5a1126-a69f-4e9d-8e35-2ecdd660d651','a79680b8-7113-4640-8daa-103753156db2','2026-10-23T04:12:34.764967Z','anchor','--','fiduciary',NULL,'0.00000000','0.00000001','fb5ce8b2c00cdc19488759c96285fbc407bb069ec0fc076e61dfa531594afff3','c42d605626f61342084957f5bf73f4421a77916ba6ddff33c5eb4af46821737f','FIDU1 fiduciary 9 6b4ad88697818173d74fe56abc6ca43a81235325f229c0b7afa03f85a1c931b2','AGOV3',NULL);
INSERT INTO "entries" VALUES(14,'5c249ca6-ae0f-47df-b9f6-6c5d33805584','b7003d86-b9f3-45a3-a4a6-21c8bdf05e79','2026-10-23T04:12:34.769929Z','allocation','DR','support-agent','support-agent/child','50.00000000','200.00000000','c42d605626f61342084957f5bf73f4421a77916ba6ddff33c5eb4af46821737f','1de6dd8f6e08bcc4f4e0e01560ea4a93a8b132e309a6b11931a847acff0f42e2','FIDU1 bind fd4d4036672c043c1d2e962a07e984d4f223fbdd43d0780a8817d488a38f4dbb','AGOV3',NULL);
INSERT INTO "entries" VALUES(15,'96b40901-af23-41a7-b331-95b6f3567760','b7003d86-b9f3-45a3-a4a6-21c8bdf05e79','2026-10-23T04:12:34.769929Z','allocation','CR','support-agent/child','support-agent','50.00000000','50.00000000','1de6dd8f6e08bcc4f4e0e01560ea4a93a8b132e309a6b11931a847acff0f42e2','5f2414266f2ad72221ed31f1aabdec534eff4b1876ac9ad078076864aa1c2293','FIDU1 bind fd4d4036672c043c1d2e962a07e984d4f223fbdd43d0780a8817d488a38f4dbb','AGOV3',NULL);
INSERT INTO "entries" VALUES(16,'540f952b-e4b6-4ff8-b3f6-87cb19d38410','3d4dbfc9-67a3-4afa-8468-a790f5fe2e4a','2026-10-23T04:12:34.770962Z','anchor','--','fiduciary',NULL,'0.00000000','0.00000001','5f2414266f2ad72221ed31f1aabdec534eff4b1876ac9ad078076864aa1c2293','9dd2baca691049a02919b7e995c7134eeb23167e392f7aa96716a188b7c44de9','FIDU1 fiduciary 10 bdfb73ea4fdcd008cb9afaa7b6b27654913f361bd6cab3470e28a8b83c91d2d1','AGOV3',NULL);
INSERT INTO "entries" VALUES(17,'e782c18d-c323-45ed-8bc2-24be02b38968','9d595211-cce2-49ef-bf0d-8d35b117c429','2026-10-23T04:12:34.776529Z','hold','DR','support-agent',NULL,'0.01000000','199.99000000','9dd2baca691049a02919b7e995c7134eeb23167e392f7aa96716a188b7c44de9','5d635758ab4622444071ac330aa9f0afff824a75692f3756644d138c21b149fe','interlock:528efb062cbb5009','AGOV3',NULL);
INSERT INTO "entries" VALUES(18,'db056b06-8d62-41c9-9b8f-4ad983a7c805','7cf0802c-0e69-4a5b-b1d3-12a016a5409d','2026-10-23T04:12:34.776790Z','hold_void','CR','support-agent',NULL,'0.01000000','200.00000000','5d635758ab4622444071ac330aa9f0afff824a75692f3756644d138c21b149fe','5c09066aac8f1f50315292679901eb8f0ab1cc6842636d9e820078259cd4390b','authorization captured','AGOV3','e782c18d-c323-45ed-8bc2-24be02b38968');
INSERT INTO "entries" VALUES(19,'f54e8349-a9cc-4c14-a85a-17a4fed90c42','7cf0802c-0e69-4a5b-b1d3-12a016a5409d','2026-10-23T04:12:34.776790Z','spend','DR','support-agent',NULL,'0.01000000','199.99000000','5c09066aac8f1f50315292679901eb8f0ab1cc6842636d9e820078259cd4390b','c6b8b049dd10a73481a9f47825d2608189073b7f18cf2bed056980e0201c6f35','interlock:528efb062cbb5009','AGOV3','e782c18d-c323-45ed-8bc2-24be02b38968');
INSERT INTO "entries" VALUES(20,'405c1a85-8a83-4862-a8c8-c39631207236','3ee08a6b-9812-4196-8bb0-c5805ed52d86','2026-10-23T04:12:34.833760Z','anchor','--','fiduciary',NULL,'0.00000000','0.00000001','c6b8b049dd10a73481a9f47825d2608189073b7f18cf2bed056980e0201c6f35','b140932408fd0c180cc9c50c5b58088697b74beee239b329821e8458e8beddbb','FIDU1 fiduciary 11 2d1d41493a803dcdab411a2389fc53961c8ea7c3794a5765e23e9dca927c65ee','AGOV3',NULL);
INSERT INTO "entries" VALUES(21,'1c3de125-4d01-4132-81ae-982a4a0e71ac','46732fe6-94f4-4058-a87e-6c1220f61849','2026-10-23T04:12:34.845937Z','anchor','--','fiduciary',NULL,'0.00000000','0.00000001','b140932408fd0c180cc9c50c5b58088697b74beee239b329821e8458e8beddbb','021de62810a9243a4438a67be8d3ff74fea34e2ee09585166ff833da4642dcb6','FIDU1 fiduciary 12 5b4340bdde38d839e14ed91516d6e239b7ca2afe93f0be64d313e4e69bae2b9b','AGOV3',NULL);
INSERT INTO "entries" VALUES(22,'60fef468-2695-4698-b69d-1f52e609bdf3','51b03ffc-3d41-4258-be6d-0d3b1d20dd4e','2026-10-23T04:12:34.846509Z','anchor','--','fiduciary',NULL,'0.00000000','0.00000001','021de62810a9243a4438a67be8d3ff74fea34e2ee09585166ff833da4642dcb6','d9ff34270a5028248448f6f733ee7f1650f6c359d2916fd96b8ad2f084caf5dd','FIDU1 fiduciary 13 de799b95410537adb5fd6494d362e7f1aa1068fb8f2bc4f8e1585997d7d3f398','AGOV3',NULL);
INSERT INTO "entries" VALUES(23,'a95ddba6-d085-42a8-8ae1-64de945c8f5b','25ed17fa-06ad-418b-8364-e514339c3178','2026-10-23T04:12:34.849142Z','anchor','--','fiduciary',NULL,'0.00000000','0.00000001','d9ff34270a5028248448f6f733ee7f1650f6c359d2916fd96b8ad2f084caf5dd','0d7edc9ce181d4512044ee368cdc170ef1b104ebe7fe5e189a3823d12eb4a032','FIDU1 fiduciary 14 fcc18e56fde747176340360e9efde865e8d56f5b11fa4da204196ea499b4bff4','AGOV3',NULL);
INSERT INTO "entries" VALUES(24,'39aeeb03-f60e-4027-ac92-2d5dde580f53','7c880fb3-1c63-499c-bfd1-8788408b343c','2026-10-23T04:12:34.854922Z','anchor','--','fiduciary',NULL,'0.00000000','0.00000001','0d7edc9ce181d4512044ee368cdc170ef1b104ebe7fe5e189a3823d12eb4a032','4dc54ae29b716d79158076a29eceac0e3b54d530ff9984d9ed4c7ea8485fab62','FIDU1 fiduciary 15 0a95f3c3eb94adf70e0f2d151377cfd3b5569e902d710d7604a7a8f225202ec9','AGOV3',NULL);
INSERT INTO "entries" VALUES(25,'324d4746-e97f-458a-8881-a6ae7cba8ed4','5afe0c26-6c2d-4b33-9add-1a08e8ffcfe2','2026-10-23T04:12:34.859540Z','anchor','--','fiduciary',NULL,'0.00000000','0.00000001','4dc54ae29b716d79158076a29eceac0e3b54d530ff9984d9ed4c7ea8485fab62','482dd16b4803f4771e91a869a518bc8e9ed3180e67374bac05041c4472125dcd','FIDU1 fiduciary 16 fa8dfb143a3a3e25a0aab3fd722de46d10d918151759d9a14d63141cd6e172b5','AGOV3',NULL);
INSERT INTO "entries" VALUES(26,'b81ad067-371d-4f54-b501-6bff118bf2eb','bdcc9b27-4af8-41d8-bf3f-6bf476230548','2026-10-23T04:12:34.870610Z','anchor','--','fiduciary',NULL,'0.00000000','0.00000001','482dd16b4803f4771e91a869a518bc8e9ed3180e67374bac05041c4472125dcd','e92a26c45ffcdddea7e0027d68bf11f4e50c06edb8a5de9eb79b7b9d37b0aa9c','FIDU1 fiduciary 17 18429a99e1872fac7164865920c6d6316a72ceae1c59027ef46379b598d257c4','AGOV3',NULL);
INSERT INTO "entries" VALUES(27,'404bf127-010f-47bb-a269-7e8f621b4a95','b132d28b-6b6e-4c86-befb-90a099070921','2026-10-23T04:12:34.873143Z','anchor','--','fiduciary',NULL,'0.00000000','0.00000001','e92a26c45ffcdddea7e0027d68bf11f4e50c06edb8a5de9eb79b7b9d37b0aa9c','f3852f8426fb3408a2e24eea5e7dba9da1dfbe4871a68cfb0120c5c04b815ba0','FIDU1 fiduciary 18 a06abe20902c1531b59b2d028e60209797fa2b95d2ca0944ca199fac52054b61','AGOV3',NULL);
INSERT INTO "entries" VALUES(28,'72f5622c-96e9-4871-b907-efc04c0b9f68','b93333c0-0d33-48b4-83bb-5d7a63d632f8','2026-10-23T04:12:34.876888Z','circuit_tripped','--','support-agent/child',NULL,'0.00000000','50.00000000','f3852f8426fb3408a2e24eea5e7dba9da1dfbe4871a68cfb0120c5c04b815ba0','3b07067d74a89d72464a33be289ecd045e304b1971f30f735afa13329aa0e35f','FIDU1 revoked fd4d4036672c043c1d2e962a07e984d4f223fbdd43d0780a8817d488a38f4dbb','AGOV3',NULL);
INSERT INTO "entries" VALUES(29,'1bfdaf37-bbd2-47dc-95aa-15c1cddf636e','60090814-e040-440a-88be-b8af6b42afc5','2026-10-23T04:12:34.878181Z','anchor','--','fiduciary',NULL,'0.00000000','0.00000001','3b07067d74a89d72464a33be289ecd045e304b1971f30f735afa13329aa0e35f','5e138f5f604ad9f3baf79a9c3d97b591a061e3ad4bdbefe130e8f947aa76e593','FIDU1 fiduciary 19 aedf7bba9503ef371a40e1f578e6dbdfc8a117a3040a74f4c1c6cc1e19038f79','AGOV3',NULL);
CREATE TABLE idempotency (
        key        TEXT PRIMARY KEY,
        request    TEXT NOT NULL,
        outcome    TEXT NOT NULL,
        sequence   INTEGER NOT NULL,
        created_at TEXT NOT NULL
    );
INSERT INTO "idempotency" VALUES('fidu1-bind-9f565943f8b48ea67c32c968e3b1f65be3583ac2ddaffc6e25a1d0da6e58ce42','{"amount":"250.00000000","child_id":"support-agent","memo":"FIDU1 bind 9f565943f8b48ea67c32c968e3b1f65be3583ac2ddaffc6e25a1d0da6e58ce42","op":"delegate","parent_id":"support"}','node',11,'2026-10-23T04:12:34.756405Z');
INSERT INTO "idempotency" VALUES('fidu1-bind-fd4d4036672c043c1d2e962a07e984d4f223fbdd43d0780a8817d488a38f4dbb','{"amount":"50.00000000","child_id":"support-agent/child","memo":"FIDU1 bind fd4d4036672c043c1d2e962a07e984d4f223fbdd43d0780a8817d488a38f4dbb","op":"delegate","parent_id":"support-agent"}','node',15,'2026-10-23T04:12:34.769929Z');
CREATE TABLE nodes (
                    scope_id   TEXT PRIMARY KEY,
                    parent_id  TEXT,
                    depth      INTEGER NOT NULL,
                    allocated  TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
INSERT INTO "nodes" VALUES('fiduciary',NULL,0,'1E-8','2026-10-23T04:12:34.721343Z');
INSERT INTO "nodes" VALUES('support',NULL,0,'1000.00000000','2026-10-23T04:12:34.721719Z');
INSERT INTO "nodes" VALUES('support-agent','support',1,'250.00000000','2026-10-23T04:12:34.756405Z');
INSERT INTO "nodes" VALUES('support-agent/child','support-agent',2,'50.00000000','2026-10-23T04:12:34.769929Z');
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
INSERT INTO "sqlite_sequence" VALUES('control_events',1);
COMMIT;
