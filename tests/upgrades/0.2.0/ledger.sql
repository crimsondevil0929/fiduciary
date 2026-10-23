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
INSERT INTO "control_events" VALUES(1,'873fc243-6df4-4d1f-b8dc-e064a834250d','2026-10-23T04:12:34.211238Z','circuit_tripped','support-agent/child','FIDU1 revoked 3e10918eacd6afc1c6bd948c75bc685a59d99b1e47f4d5cc8da672bc15f9a888','c8e5d270e8ee287c07c9b88c77edfa42e2a7d1fd38e163d859e9edd58c3f67be','873fc243-6df4-4d1f-b8dc-e064a834250d');
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
INSERT INTO "entries" VALUES(1,'64834164-3d0f-4b3c-9439-446a6e1fc29f','ea9950bf-3990-4935-ae6f-f2d53038d397','2026-10-23T04:12:34.138572Z','funding','CR','fiduciary',NULL,'0.00000001','0.00000001','0000000000000000000000000000000000000000000000000000000000000000','53747f92da5d7cf2c58e8078e6bbe946f845cc3c2e20d6677ac30f9f5e5c8316','FIDU1 anchors: this scope spends nothing','AGOV3',NULL);
INSERT INTO "entries" VALUES(2,'42e9f677-a0d6-4c70-bd58-4435ab00ee2a','cd6c6db7-38c9-4cfd-8eb5-ad268502548c','2026-10-23T04:12:34.138964Z','funding','CR','support',NULL,'1000.00000000','1000.00000000','53747f92da5d7cf2c58e8078e6bbe946f845cc3c2e20d6677ac30f9f5e5c8316','68c4fc0c8f67ce65a48a174fa0cb5a44cfed52ca742f20a9a1990d0121107022','root envelope issued','AGOV3',NULL);
INSERT INTO "entries" VALUES(3,'635bb151-c5b7-41ad-9c77-6ead548d5b37','cdc70485-9f40-4041-9a39-b802e1612e09','2026-10-23T04:12:34.151090Z','anchor','--','fiduciary',NULL,'0.00000000','0.00000001','68c4fc0c8f67ce65a48a174fa0cb5a44cfed52ca742f20a9a1990d0121107022','e51e52d1fd22aaa017d67967bd62e4873a99340b08d7dbe2dc931c37a590537b','FIDU1 fiduciary 1 c4237557791b1828549fd4793cf688a0eac154ce7aea35770c6586b7b9070b84','AGOV3',NULL);
INSERT INTO "entries" VALUES(4,'40bd8198-cf9e-4fd1-86b0-5accca1c5872','4d3f2854-de04-4cfd-a356-e3068161092a','2026-10-23T04:12:34.152480Z','anchor','--','fiduciary',NULL,'0.00000000','0.00000001','e51e52d1fd22aaa017d67967bd62e4873a99340b08d7dbe2dc931c37a590537b','d197b91fd4d704723d90792e195b839f0871731ea3a097c57021288d086ba729','FIDU1 fiduciary 2 cba3829e3a450f190660107493221708ff3c6a6c0aa71fdc6e1c9bd2f4ba66e5','AGOV3',NULL);
INSERT INTO "entries" VALUES(5,'f3eb2987-fcf6-4189-bca8-673b369b42ce','907583fb-1a51-441c-ad09-066b3d0d7ffb','2026-10-23T04:12:34.153840Z','anchor','--','fiduciary',NULL,'0.00000000','0.00000001','d197b91fd4d704723d90792e195b839f0871731ea3a097c57021288d086ba729','904ccf6d3cc003594629e734c87ca2f941e6382e8dcf9cdf27168d426219027b','FIDU1 fiduciary 3 ceec3d4bd5c7df25d5efad37441d5f45cb963a29e5d4ab6eece0218fa91ce01b','AGOV3',NULL);
INSERT INTO "entries" VALUES(6,'c1826c4e-4875-4cda-96e0-7c5242e20d30','e7d52fc6-af9b-4940-a6c2-c5c1f13def75','2026-10-23T04:12:34.155621Z','anchor','--','fiduciary',NULL,'0.00000000','0.00000001','904ccf6d3cc003594629e734c87ca2f941e6382e8dcf9cdf27168d426219027b','0bb80f80a9a6277ee728826f3d999dff23c866888d4958e5618698ca2523f653','FIDU1 fiduciary 4 3886e11040844aa0247439c0c7a33c15bc207f614a1e86631a0de806033f3d82','AGOV3',NULL);
INSERT INTO "entries" VALUES(7,'4f5f2337-7691-4ff1-92a2-29c7acb5b1c1','2c1b2f66-6dad-484e-9221-98d9eaa80ac2','2026-10-23T04:12:34.168369Z','anchor','--','fiduciary',NULL,'0.00000000','0.00000001','0bb80f80a9a6277ee728826f3d999dff23c866888d4958e5618698ca2523f653','7f3d2a3b50ac7420ae95171e12d96b29accf9f882a5141905e74b70293c8a989','FIDU1 fiduciary 5 90a283286a75a6bace058f3495a40ac06cb977d299974cabb56d16faa2435a4a','AGOV3',NULL);
INSERT INTO "entries" VALUES(8,'cc5d064a-b944-4758-bf86-2cb68b1368f8','dce4f65c-95ca-4995-9020-c3ce77e0fd82','2026-10-23T04:12:34.170197Z','anchor','--','fiduciary',NULL,'0.00000000','0.00000001','7f3d2a3b50ac7420ae95171e12d96b29accf9f882a5141905e74b70293c8a989','d4f5d3486c37ae48f6e54bee2e09f54f7210fa1c4017685e80edfe7e13cf985a','FIDU1 fiduciary 6 5feab22f03457d40b0591532fae701607675425632a7c3fb8f901c4d83175c9d','AGOV3',NULL);
INSERT INTO "entries" VALUES(9,'7cc6fd4f-cac7-4061-8bc8-07c8d21a8628','3c6fbe1d-4deb-4909-bd7e-1e5a57afe553','2026-10-23T04:12:34.173060Z','anchor','--','fiduciary',NULL,'0.00000000','0.00000001','d4f5d3486c37ae48f6e54bee2e09f54f7210fa1c4017685e80edfe7e13cf985a','7852f4b8dae98a228258672397b387a62e199c5239d609ffbe19f5db19e2d75b','FIDU1 fiduciary 7 151fefa63a2458fe1686eb0da32ad56fdf381f43b06075baa00fcb265b385850','AGOV3',NULL);
INSERT INTO "entries" VALUES(10,'f5be87e4-e74f-4093-94c0-49046c39a426','06d3feef-5d6c-49dd-a310-e7e793d7e60a','2026-10-23T04:12:34.176593Z','allocation','DR','support','support-agent','250.00000000','750.00000000','7852f4b8dae98a228258672397b387a62e199c5239d609ffbe19f5db19e2d75b','f037f9bb8630de68873882f6473d13905dadcc6252290db9a4ecaba2220ca1ee','FIDU1 bind 2aac514688ef5253fbc371bbd56c4e564f4826df2236af5a898e77b7aa038461','AGOV3',NULL);
INSERT INTO "entries" VALUES(11,'811ed3d7-0f4c-4022-aebc-bf6659b448f0','06d3feef-5d6c-49dd-a310-e7e793d7e60a','2026-10-23T04:12:34.176593Z','allocation','CR','support-agent','support','250.00000000','250.00000000','f037f9bb8630de68873882f6473d13905dadcc6252290db9a4ecaba2220ca1ee','d82916640b562add3aaab1821556089c6f3810b837624c4ceca1a5af6e15e088','FIDU1 bind 2aac514688ef5253fbc371bbd56c4e564f4826df2236af5a898e77b7aa038461','AGOV3',NULL);
INSERT INTO "entries" VALUES(12,'d260eedd-2d3a-4041-aad7-f2d81fb54ccb','fd8f448e-f839-4659-a90c-fdc0b8a1702d','2026-10-23T04:12:34.177784Z','anchor','--','fiduciary',NULL,'0.00000000','0.00000001','d82916640b562add3aaab1821556089c6f3810b837624c4ceca1a5af6e15e088','810e2a777c2ee4a114e2f1fd3d84239de91d77a1b0021fcedf8ff296c3557f49','FIDU1 fiduciary 8 5ec4c62c1dc84f462ad4835be1b59b0c9f84a084da5f5ad59de57613ecedc90e','AGOV3',NULL);
INSERT INTO "entries" VALUES(13,'e1606b29-e81d-49a3-a496-ef3a4661b284','bcd11817-6627-48d1-b4f1-6e1576395139','2026-10-23T04:12:34.186171Z','anchor','--','fiduciary',NULL,'0.00000000','0.00000001','810e2a777c2ee4a114e2f1fd3d84239de91d77a1b0021fcedf8ff296c3557f49','0a7d9b15bf734d4cc18b5b7af0ec83a46d9ebf2e27d4e42aae2318790109e244','FIDU1 fiduciary 9 24a2f78ef9c9d0fee76a07a52fdea32140ae4ab2c3f0332154670db9f51515c7','AGOV3',NULL);
INSERT INTO "entries" VALUES(14,'026bcece-e8cb-42c4-b89c-b4559d7a8c54','98048f4b-abf3-4061-8ecc-a67a98089a63','2026-10-23T04:12:34.191632Z','allocation','DR','support-agent','support-agent/child','50.00000000','200.00000000','0a7d9b15bf734d4cc18b5b7af0ec83a46d9ebf2e27d4e42aae2318790109e244','45c599d734ab6987ee2214d5571890aee58de778cdfc568df6a8ec5f6bff045d','FIDU1 bind 3e10918eacd6afc1c6bd948c75bc685a59d99b1e47f4d5cc8da672bc15f9a888','AGOV3',NULL);
INSERT INTO "entries" VALUES(15,'cb86130d-2618-4246-a007-c6f4abc27046','98048f4b-abf3-4061-8ecc-a67a98089a63','2026-10-23T04:12:34.191632Z','allocation','CR','support-agent/child','support-agent','50.00000000','50.00000000','45c599d734ab6987ee2214d5571890aee58de778cdfc568df6a8ec5f6bff045d','af5d129bd58e11fbce12412c45a48a93aa1ba867bd7fec602194dff84b5555ce','FIDU1 bind 3e10918eacd6afc1c6bd948c75bc685a59d99b1e47f4d5cc8da672bc15f9a888','AGOV3',NULL);
INSERT INTO "entries" VALUES(16,'946bcf06-0319-4d22-a4f5-a984b2947902','f711aada-a627-42df-80f1-f5d21e8357af','2026-10-23T04:12:34.192773Z','anchor','--','fiduciary',NULL,'0.00000000','0.00000001','af5d129bd58e11fbce12412c45a48a93aa1ba867bd7fec602194dff84b5555ce','786372990bea1c6a20a04e1666987ad515fa2ac25bc7872d12f1471d34ef5b15','FIDU1 fiduciary 10 5651a282ad33263e4243619bbbf52727b50aa04f5be3dc5527b90fc69488181c','AGOV3',NULL);
INSERT INTO "entries" VALUES(17,'d6a84f68-cbaa-427f-841d-dd08fbba3ac2','4f98c260-243b-460c-a073-82461d882ad5','2026-10-23T04:12:34.198861Z','hold','DR','support-agent',NULL,'0.01000000','199.99000000','786372990bea1c6a20a04e1666987ad515fa2ac25bc7872d12f1471d34ef5b15','0cd3dea1bb961112a0a707b51cb0e189afaddf98ab09351b9680920ace3a4491','interlock:232833d24899db4f','AGOV3',NULL);
INSERT INTO "entries" VALUES(18,'7a79a082-2634-4593-a280-a00d8b737b41','9bf91e62-9c2c-4644-95c9-b1d04f12b3ed','2026-10-23T04:12:34.199248Z','hold_void','CR','support-agent',NULL,'0.01000000','200.00000000','0cd3dea1bb961112a0a707b51cb0e189afaddf98ab09351b9680920ace3a4491','8ebab540229900e1abe979ebad06ff9a95eb275992a2f68fd79b5fbe0b3e6ffb','authorization captured','AGOV3','d6a84f68-cbaa-427f-841d-dd08fbba3ac2');
INSERT INTO "entries" VALUES(19,'8454acb0-d175-40bb-a7cc-60067cb893b5','9bf91e62-9c2c-4644-95c9-b1d04f12b3ed','2026-10-23T04:12:34.199248Z','spend','DR','support-agent',NULL,'0.01000000','199.99000000','8ebab540229900e1abe979ebad06ff9a95eb275992a2f68fd79b5fbe0b3e6ffb','61130586b116ecc1d172102d9cc9908b27dfb11badd64af4bf6e420de3a5e52b','interlock:232833d24899db4f','AGOV3','d6a84f68-cbaa-427f-841d-dd08fbba3ac2');
INSERT INTO "entries" VALUES(20,'c79100a5-5cb6-48fe-ba98-7a22b465e14b','738fddfc-14e6-453e-b85d-93761adce445','2026-10-23T04:12:34.206127Z','anchor','--','fiduciary',NULL,'0.00000000','0.00000001','61130586b116ecc1d172102d9cc9908b27dfb11badd64af4bf6e420de3a5e52b','b66cabb2b37de1083709765bb8ac6f58f0c7245d95a5f8f357f588686b223571','FIDU1 fiduciary 11 a1d336d72d6f77dbd9b67023b873acade60205ad759bda44b4e3046ca9006d4c','AGOV3',NULL);
INSERT INTO "entries" VALUES(21,'781c3263-dbf6-4a5d-a884-792e2f6a88fd','e3087d40-326a-4710-bd20-a5cdd66b0247','2026-10-23T04:12:34.208079Z','anchor','--','fiduciary',NULL,'0.00000000','0.00000001','b66cabb2b37de1083709765bb8ac6f58f0c7245d95a5f8f357f588686b223571','c8e5d270e8ee287c07c9b88c77edfa42e2a7d1fd38e163d859e9edd58c3f67be','FIDU1 fiduciary 12 84b8702974b7ad1735e473a4a6652836d129f33fad33c0e54e8d4a3b2e91dfd1','AGOV3',NULL);
INSERT INTO "entries" VALUES(22,'873fc243-6df4-4d1f-b8dc-e064a834250d','dff1c04c-9f57-4e47-9cf0-db9b27c25f27','2026-10-23T04:12:34.211238Z','circuit_tripped','--','support-agent/child',NULL,'0.00000000','50.00000000','c8e5d270e8ee287c07c9b88c77edfa42e2a7d1fd38e163d859e9edd58c3f67be','4cf5d3ce7f6fc882aa4b213b780b8602176118feeb5c837d8aedef9a759e1c73','FIDU1 revoked 3e10918eacd6afc1c6bd948c75bc685a59d99b1e47f4d5cc8da672bc15f9a888','AGOV3',NULL);
INSERT INTO "entries" VALUES(23,'3a27f166-a62c-49b3-b160-feba8603f742','07a7c575-5810-413d-bbaf-f77d189e93c3','2026-10-23T04:12:34.212243Z','anchor','--','fiduciary',NULL,'0.00000000','0.00000001','4cf5d3ce7f6fc882aa4b213b780b8602176118feeb5c837d8aedef9a759e1c73','535cd5b86fc7b249febd4dd7c0844b83da17672433b1a699e87657cf28713f3b','FIDU1 fiduciary 13 1246e9602133a21b524d8c1d3248493f7ecc62ac48e31ee0e5cab8a9a85c3d83','AGOV3',NULL);
CREATE TABLE idempotency (
        key        TEXT PRIMARY KEY,
        request    TEXT NOT NULL,
        outcome    TEXT NOT NULL,
        sequence   INTEGER NOT NULL,
        created_at TEXT NOT NULL
    );
INSERT INTO "idempotency" VALUES('fidu1-bind-2aac514688ef5253fbc371bbd56c4e564f4826df2236af5a898e77b7aa038461','{"amount":"250.00000000","child_id":"support-agent","memo":"FIDU1 bind 2aac514688ef5253fbc371bbd56c4e564f4826df2236af5a898e77b7aa038461","op":"delegate","parent_id":"support"}','node',11,'2026-10-23T04:12:34.176593Z');
INSERT INTO "idempotency" VALUES('fidu1-bind-3e10918eacd6afc1c6bd948c75bc685a59d99b1e47f4d5cc8da672bc15f9a888','{"amount":"50.00000000","child_id":"support-agent/child","memo":"FIDU1 bind 3e10918eacd6afc1c6bd948c75bc685a59d99b1e47f4d5cc8da672bc15f9a888","op":"delegate","parent_id":"support-agent"}','node',15,'2026-10-23T04:12:34.191632Z');
CREATE TABLE nodes (
                    scope_id   TEXT PRIMARY KEY,
                    parent_id  TEXT,
                    depth      INTEGER NOT NULL,
                    allocated  TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
INSERT INTO "nodes" VALUES('fiduciary',NULL,0,'1E-8','2026-10-23T04:12:34.138572Z');
INSERT INTO "nodes" VALUES('support',NULL,0,'1000.00000000','2026-10-23T04:12:34.138964Z');
INSERT INTO "nodes" VALUES('support-agent','support',1,'250.00000000','2026-10-23T04:12:34.176593Z');
INSERT INTO "nodes" VALUES('support-agent/child','support-agent',2,'50.00000000','2026-10-23T04:12:34.191632Z');
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
