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
INSERT INTO "control_events" VALUES(1,'19669569-badd-4f81-a9fe-be7fd531eb4d','2026-10-23T04:12:36.334464Z','circuit_tripped','support-agent/child','FIDU1 revoked 366e78f73df01478814a8f3533baa332502e78724bbd5aa6f6905573c262835e','ce3c73dae92d2ddc3edc7df3badab289be2df8c94748113cddb3e4fc21e2d8ae','19669569-badd-4f81-a9fe-be7fd531eb4d');
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
INSERT INTO "entries" VALUES(1,'9b721d9c-3d82-44ea-9703-09be281ec924','72944686-a4c0-482e-a618-277b597a0703','2026-10-23T04:12:36.143706Z','funding','CR','fiduciary',NULL,'0.00000001','0.00000001','0000000000000000000000000000000000000000000000000000000000000000','6fcd5ff2839f60a2b718b9e50b60d3de5a3c838c17b8da7dd34658a596ab4e10','FIDU1 anchors: this scope spends nothing','AGOV3',NULL);
INSERT INTO "entries" VALUES(2,'b6b77e00-d19c-4929-b71a-4eb6c1341eff','f4f51d52-ab38-4b5a-b813-f24188b748c4','2026-10-23T04:12:36.144073Z','funding','CR','support',NULL,'1000.00000000','1000.00000000','6fcd5ff2839f60a2b718b9e50b60d3de5a3c838c17b8da7dd34658a596ab4e10','0a340a064081da967111e086db18b4bdfc30a069772856666b97ca515d0e9568','root envelope issued','AGOV3',NULL);
INSERT INTO "entries" VALUES(3,'56df7b6b-db45-46fd-ba72-17c87f87b0c4','a25e7162-f2e3-4781-aec2-ff7ed4b0efc2','2026-10-23T04:12:36.155024Z','anchor','--','fiduciary',NULL,'0.00000000','0.00000001','0a340a064081da967111e086db18b4bdfc30a069772856666b97ca515d0e9568','3e05b0d5c7cabefac7f50a9f10f6cd2370590a451a559afeb27deadc46eca42f','FIDU1 fiduciary 1 3b613e940dd934b7daded33bb7a553f66a33ffee03ee9748f5efc58afe993d5f','AGOV3',NULL);
INSERT INTO "entries" VALUES(4,'36fd2e12-9fe7-49d6-8d8f-d81c1fd31690','3534fdb4-b802-46b1-be60-a0109c170b79','2026-10-23T04:12:36.156853Z','anchor','--','fiduciary',NULL,'0.00000000','0.00000001','3e05b0d5c7cabefac7f50a9f10f6cd2370590a451a559afeb27deadc46eca42f','fee21d82d54b17bb5e921f06adf2a0fe9486613df0569c86b1afb43d5ecf80e2','FIDU1 fiduciary 2 102264e51e29a99a299c0253e740aa7019848e8ff7c514ed07275b6aac4bf045','AGOV3',NULL);
INSERT INTO "entries" VALUES(5,'a4c78aaa-8b59-44f8-adce-716d25cbfdff','76d8d715-3ac6-410e-9b15-26ac78191094','2026-10-23T04:12:36.158364Z','anchor','--','fiduciary',NULL,'0.00000000','0.00000001','fee21d82d54b17bb5e921f06adf2a0fe9486613df0569c86b1afb43d5ecf80e2','a24c184c53e374e7de5d1fe7eff40c5307bdd15e42aff7059bc270b453e42faa','FIDU1 fiduciary 3 94421e20afe10f9e2109a55ceedf77a8eca619e3f079c5fd0d165dbc2473f19a','AGOV3',NULL);
INSERT INTO "entries" VALUES(6,'0e38cf4f-587e-4a10-bbb0-a80ccf55de70','1f3e114d-a149-4f9c-8fa6-e5431715e2be','2026-10-23T04:12:36.160354Z','anchor','--','fiduciary',NULL,'0.00000000','0.00000001','a24c184c53e374e7de5d1fe7eff40c5307bdd15e42aff7059bc270b453e42faa','26b04c7f7dad6c4ea6c527c32c7c8684d801ae86f1d9fabf2c91c955cca3c977','FIDU1 fiduciary 4 01525bc09f27177ab6433c81a4ccadae7dc13421e7c8480c9144f2469e2ec40b','AGOV3',NULL);
INSERT INTO "entries" VALUES(7,'f7a79763-7f89-4ad0-8a32-5cd0a184500d','0acf20bb-5995-4808-a0a0-602ba6a451cd','2026-10-23T04:12:36.172721Z','anchor','--','fiduciary',NULL,'0.00000000','0.00000001','26b04c7f7dad6c4ea6c527c32c7c8684d801ae86f1d9fabf2c91c955cca3c977','0051ebe78623a19d98875650e16865c0d96c130147b0c631f4e45687b691016e','FIDU1 fiduciary 5 3a4e6b145322f7b97d52b67cdc319806a57d154bb8bdb962c001739c0aeec0a4','AGOV3',NULL);
INSERT INTO "entries" VALUES(8,'db79dc8a-8549-4687-b63a-256bf98f5de8','cd9dabed-9264-4501-93ad-45cf8ccc3a06','2026-10-23T04:12:36.174581Z','anchor','--','fiduciary',NULL,'0.00000000','0.00000001','0051ebe78623a19d98875650e16865c0d96c130147b0c631f4e45687b691016e','3b6343eb8c206de3cdb9f37bb3a21cd6144382d4644e57db9da1e2def79cb6ae','FIDU1 fiduciary 6 637d632090aa34fa932ea4bc0a2eb3d745087ec830f7578c659dbb7add8a8695','AGOV3',NULL);
INSERT INTO "entries" VALUES(9,'9288c3aa-03cc-4bc4-b887-c10745c832a7','5f5b34e1-dad8-400a-87ee-6867e83a5cec','2026-10-23T04:12:36.178155Z','anchor','--','fiduciary',NULL,'0.00000000','0.00000001','3b6343eb8c206de3cdb9f37bb3a21cd6144382d4644e57db9da1e2def79cb6ae','f213b817bacb9927c52cc3a1f63e9fcb96782ff3071cab949e782f973a93229c','FIDU1 fiduciary 7 d654c898669fef55f939794e3248aed1bd13c794929033073de9526804e59b2a','AGOV3',NULL);
INSERT INTO "entries" VALUES(10,'fd3d950c-bde5-4651-82f4-b412f3ae2756','097a7994-50c4-4501-b27c-1fb5d67829dd','2026-10-23T04:12:36.182832Z','allocation','DR','support','support-agent','250.00000000','750.00000000','f213b817bacb9927c52cc3a1f63e9fcb96782ff3071cab949e782f973a93229c','c8927f52018ee311eebba0fda1beecc6f4c29183d7e21c11d9cd1e424e3f6ca8','FIDU1 bind 37a73c553f5e73e4400a8429d489f2a71d7492545afb5ace89db87a5ebc73fcd','AGOV3',NULL);
INSERT INTO "entries" VALUES(11,'ca3553c7-e795-453a-9286-41626b963036','097a7994-50c4-4501-b27c-1fb5d67829dd','2026-10-23T04:12:36.182832Z','allocation','CR','support-agent','support','250.00000000','250.00000000','c8927f52018ee311eebba0fda1beecc6f4c29183d7e21c11d9cd1e424e3f6ca8','4850214b2a62f5914e8a5898dc7808efd8162aac12b75fb52d4d324b38bd0819','FIDU1 bind 37a73c553f5e73e4400a8429d489f2a71d7492545afb5ace89db87a5ebc73fcd','AGOV3',NULL);
INSERT INTO "entries" VALUES(12,'39334ad7-796b-4bd3-a64b-34b33d5efe8d','50a576ad-95f4-4b12-a954-95fc16755d79','2026-10-23T04:12:36.184257Z','anchor','--','fiduciary',NULL,'0.00000000','0.00000001','4850214b2a62f5914e8a5898dc7808efd8162aac12b75fb52d4d324b38bd0819','f0b50e28ccec283e667d4caba4f2f8f125965557fd1c96523a646179835ffbe9','FIDU1 fiduciary 8 0cb6c40000a519c88656732690beec6da3dc669bde16aef90dfd15b9e217630e','AGOV3',NULL);
INSERT INTO "entries" VALUES(13,'b583f733-b7fe-400f-9f6e-4d1a7a9142a8','2bdff8ad-545e-43ee-8c7b-e2f3f1c19881','2026-10-23T04:12:36.194494Z','anchor','--','fiduciary',NULL,'0.00000000','0.00000001','f0b50e28ccec283e667d4caba4f2f8f125965557fd1c96523a646179835ffbe9','200727b6a2a22dc8adf53e10bd430f9602c735ddd12e2018fbaebc6d671813a5','FIDU1 fiduciary 9 cb0aae627b4743af1841f9a622176b91a325e3501aeb3417a647ea3a4b424988','AGOV3',NULL);
INSERT INTO "entries" VALUES(14,'b31c4ad6-3332-46b4-868c-20d308685957','3db56a95-f21a-4ea9-9bb0-95f761013cbf','2026-10-23T04:12:36.200506Z','allocation','DR','support-agent','support-agent/child','50.00000000','200.00000000','200727b6a2a22dc8adf53e10bd430f9602c735ddd12e2018fbaebc6d671813a5','331f81512ed56b4a95d2a586223ca4a9962da4aa79897c40fc50dbab65306d31','FIDU1 bind 366e78f73df01478814a8f3533baa332502e78724bbd5aa6f6905573c262835e','AGOV3',NULL);
INSERT INTO "entries" VALUES(15,'4fde5b8a-29fe-4bb4-b849-e328bc267b91','3db56a95-f21a-4ea9-9bb0-95f761013cbf','2026-10-23T04:12:36.200506Z','allocation','CR','support-agent/child','support-agent','50.00000000','50.00000000','331f81512ed56b4a95d2a586223ca4a9962da4aa79897c40fc50dbab65306d31','31f0b159664de5eca1b8ae598626b2a46cdc9911478e8e01db4e22a23025ea9a','FIDU1 bind 366e78f73df01478814a8f3533baa332502e78724bbd5aa6f6905573c262835e','AGOV3',NULL);
INSERT INTO "entries" VALUES(16,'0b786a78-fe8b-4242-be65-c67839240fa8','999b300d-3a02-4a69-9e8c-8d03f586353c','2026-10-23T04:12:36.201720Z','anchor','--','fiduciary',NULL,'0.00000000','0.00000001','31f0b159664de5eca1b8ae598626b2a46cdc9911478e8e01db4e22a23025ea9a','4b09f012b2f5b21c1b1eae69f115c9a0cf561a002d0ade5495928443a7b9fad0','FIDU1 fiduciary 10 ea0cbf5b4b147dfc19468ce82a49712c65767158ffbffff78e3a27c3147a0d48','AGOV3',NULL);
INSERT INTO "entries" VALUES(17,'eeef62a0-92e4-4678-8bff-6987f9a9b4c7','1371784b-f603-4a5a-a694-e230b47b6e44','2026-10-23T04:12:36.208147Z','anchor','--','fiduciary',NULL,'0.00000000','0.00000001','4b09f012b2f5b21c1b1eae69f115c9a0cf561a002d0ade5495928443a7b9fad0','7a1e7035adfe86dd39f7bb29cdf6759613144e0a1e27fafe3936d54de83287ce','FIDU1 fiduciary 11 8f4b30463bd34000b7491bfafeb81c1d264eeb07557e5c156745ddf3088cb53c','AGOV3',NULL);
INSERT INTO "entries" VALUES(18,'86f87840-35ec-4b1e-8e56-840389548297','b791bf9c-b6aa-48eb-92d2-2500a1b137eb','2026-10-23T04:12:36.215021Z','hold','DR','support-agent',NULL,'0.01000000','199.99000000','7a1e7035adfe86dd39f7bb29cdf6759613144e0a1e27fafe3936d54de83287ce','49dc73c88f60136c4eaf4a9fc8eee58fbe070afa935e8b350e5cf2ddcb9ac99a','interlock:87594d537c76910b','AGOV3',NULL);
INSERT INTO "entries" VALUES(19,'cd1a3e6c-1a77-4365-b412-b13c93cf14b5','973cf745-61ab-4e7a-8f01-1d110ebab29f','2026-10-23T04:12:36.215339Z','hold_void','CR','support-agent',NULL,'0.01000000','200.00000000','49dc73c88f60136c4eaf4a9fc8eee58fbe070afa935e8b350e5cf2ddcb9ac99a','a48a95a270b72774af2b6ac1239a8419ca5d5e3857de89772dd85172e4083896','authorization captured','AGOV3','86f87840-35ec-4b1e-8e56-840389548297');
INSERT INTO "entries" VALUES(20,'6e72377a-4616-458d-81f2-6d52ad89c96f','973cf745-61ab-4e7a-8f01-1d110ebab29f','2026-10-23T04:12:36.215339Z','spend','DR','support-agent',NULL,'0.01000000','199.99000000','a48a95a270b72774af2b6ac1239a8419ca5d5e3857de89772dd85172e4083896','b06d77f6d684e84c168088dbacd76e0424917145e52edc97dfd0d35915a4de1c','interlock:87594d537c76910b','AGOV3','86f87840-35ec-4b1e-8e56-840389548297');
INSERT INTO "entries" VALUES(21,'5bb67373-74fe-45a3-9037-ae9dea0c3bb4','7a12f357-36b4-46b8-a1bf-ad8285d254d9','2026-10-23T04:12:36.267793Z','anchor','--','fiduciary',NULL,'0.00000000','0.00000001','b06d77f6d684e84c168088dbacd76e0424917145e52edc97dfd0d35915a4de1c','e7f73cd79f58e8d0081cefeacc7c911610186281ff9723f84c7639a14752e409','FIDU1 fiduciary 12 df3cee27797e06e4d10f574ecd1a10bcc55abfdaeed74038f3d9572ffdf5a82b','AGOV3',NULL);
INSERT INTO "entries" VALUES(22,'46ae302b-e0f3-4f3c-990a-8638cf35f73c','f83fadd8-6594-4812-b937-d38b319c3434','2026-10-23T04:12:36.281778Z','anchor','--','fiduciary',NULL,'0.00000000','0.00000001','e7f73cd79f58e8d0081cefeacc7c911610186281ff9723f84c7639a14752e409','3ce3012c12dfc4aa073079db7ca8a7d1fae5505faf0bf9d4ccc13bab23622bda','FIDU1 fiduciary 13 aaf2ac7a4af6ab17293b230e890bdaec7bacc875344e006cf7b9b5dc4b8805cf','AGOV3',NULL);
INSERT INTO "entries" VALUES(23,'980bd0e1-f911-4436-b810-61db26c9b685','58fe5505-da92-4657-b9e0-ce9d4c731352','2026-10-23T04:12:36.282258Z','anchor','--','fiduciary',NULL,'0.00000000','0.00000001','3ce3012c12dfc4aa073079db7ca8a7d1fae5505faf0bf9d4ccc13bab23622bda','98f631aaa77b85b2c7cda9aeda81d9efd94d8e32b888dd33b160ea97f8f78ad5','FIDU1 fiduciary 14 a947918a4f862acd0b412533316b0dd68fe69785b47222b61aaa6ee25efbb965','AGOV3',NULL);
INSERT INTO "entries" VALUES(24,'fd06a920-cd74-4e9b-851c-d42ca657bff5','11965348-f4b1-419d-9775-b141cd14b52c','2026-10-23T04:12:36.285545Z','anchor','--','fiduciary',NULL,'0.00000000','0.00000001','98f631aaa77b85b2c7cda9aeda81d9efd94d8e32b888dd33b160ea97f8f78ad5','59b40264fe6683f2d64a0473831a80952358da18ed51931a58494f2a26c2cac8','FIDU1 fiduciary 15 a807c6578660b21d96b7f88e0f674877e17551f504cc99bdc02287613b3be0c6','AGOV3',NULL);
INSERT INTO "entries" VALUES(25,'6dca7fc1-8b44-4c0c-8b2e-42ed3e390146','d8c96add-400e-41b3-9dc6-aa9b306389a8','2026-10-23T04:12:36.293004Z','anchor','--','fiduciary',NULL,'0.00000000','0.00000001','59b40264fe6683f2d64a0473831a80952358da18ed51931a58494f2a26c2cac8','b791f7f21144861b754e2c3c8366b8ea28b017de778e7bfbc39f83efa50a5806','FIDU1 fiduciary 16 5cfbb38fa4dff0bb3460d7d6be59bca16970deb72a0836a406d6a98ae988f9d7','AGOV3',NULL);
INSERT INTO "entries" VALUES(26,'1d85ba6d-6019-4277-983d-2b6780709dd8','7954153e-99bf-4ad1-b084-20266ad5d645','2026-10-23T04:12:36.298416Z','anchor','--','fiduciary',NULL,'0.00000000','0.00000001','b791f7f21144861b754e2c3c8366b8ea28b017de778e7bfbc39f83efa50a5806','889aa9b2c4a0a0075f49b7c4ea9b4be99b0d1a2e573b33ba19355ef3e9aa53a7','FIDU1 fiduciary 17 076b7021471f4fd9826baa147de7542d32006a039279d8551c1d552f314a4604','AGOV3',NULL);
INSERT INTO "entries" VALUES(27,'7abbd1d1-5f49-4f17-b9de-5e5ce8194a8b','9bdd4e2c-a8db-4fa7-a75f-c9374ce7f017','2026-10-23T04:12:36.305057Z','hold','DR','support-agent',NULL,'0.01000000','199.98000000','889aa9b2c4a0a0075f49b7c4ea9b4be99b0d1a2e573b33ba19355ef3e9aa53a7','ad871725d97c8cbfe2c2ae6af9288b011eba3e53189e66a7ce1df5287d04e021','interlock:c51b12899fe2dae1','AGOV3',NULL);
INSERT INTO "entries" VALUES(28,'aac014ab-f063-4738-a2b8-6ba68a26aa6a','13a1b0cb-e669-4215-a719-d24efd02bc2f','2026-10-23T04:12:36.305384Z','hold_void','CR','support-agent',NULL,'0.01000000','199.99000000','ad871725d97c8cbfe2c2ae6af9288b011eba3e53189e66a7ce1df5287d04e021','14c4d7f3f64d255a7fb182b21e9e4c77ff2123d85e76a73902d2ce776c06f0c0','authorization captured','AGOV3','7abbd1d1-5f49-4f17-b9de-5e5ce8194a8b');
INSERT INTO "entries" VALUES(29,'544c213c-f991-4c82-8e2d-7969851d4416','13a1b0cb-e669-4215-a719-d24efd02bc2f','2026-10-23T04:12:36.305384Z','spend','DR','support-agent',NULL,'0.01000000','199.98000000','14c4d7f3f64d255a7fb182b21e9e4c77ff2123d85e76a73902d2ce776c06f0c0','03053153ac9bbe567466f450a390b6dceacda83e739f3f6f7d927c22ce790791','interlock:c51b12899fe2dae1','AGOV3','7abbd1d1-5f49-4f17-b9de-5e5ce8194a8b');
INSERT INTO "entries" VALUES(30,'0f2fb847-4113-4dc6-8dec-a77149e5fc30','feaefb3d-42ee-49f7-9079-b767e5c67b3b','2026-10-23T04:12:36.309820Z','hold','DR','support-agent',NULL,'0.00100000','199.97900000','03053153ac9bbe567466f450a390b6dceacda83e739f3f6f7d927c22ce790791','77ab41b658674658a7f4902022b095a5e001e5d917eefc739fb145bf82770526','FIDU1 session 004080db-1cb7-4024-91f6-ba283e6b6811 tool lookup_order','AGOV3',NULL);
INSERT INTO "entries" VALUES(31,'d302a296-fb71-46f9-974e-89a1b9365292','fefadae3-3d98-49df-a079-f3cef1d42a76','2026-10-23T04:12:36.310629Z','hold_void','CR','support-agent',NULL,'0.00100000','199.98000000','77ab41b658674658a7f4902022b095a5e001e5d917eefc739fb145bf82770526','30d09ebe0560f4e1cbec6c9d964dba38c7e64f7c3cbe9f7674c33a4db6e2267f','authorization captured','AGOV3','0f2fb847-4113-4dc6-8dec-a77149e5fc30');
INSERT INTO "entries" VALUES(32,'0b89a30d-aa31-4daf-982f-df3192ed8116','fefadae3-3d98-49df-a079-f3cef1d42a76','2026-10-23T04:12:36.310629Z','spend','DR','support-agent',NULL,'0.00100000','199.97900000','30d09ebe0560f4e1cbec6c9d964dba38c7e64f7c3cbe9f7674c33a4db6e2267f','c287ca62d96951bd57ff01f2587651328f77859684c70314e3c11814a6f12951','FIDU1 session 004080db-1cb7-4024-91f6-ba283e6b6811 tool lookup_order','AGOV3','0f2fb847-4113-4dc6-8dec-a77149e5fc30');
INSERT INTO "entries" VALUES(33,'1a58fccc-30e7-4e28-922b-98be6c4b0ebc','06da02a0-5783-4118-8ba0-9cb8c2b3bf84','2026-10-23T04:12:36.313410Z','anchor','--','fiduciary',NULL,'0.00000000','0.00000001','c287ca62d96951bd57ff01f2587651328f77859684c70314e3c11814a6f12951','8d15a08c27421eea902059a0f9f7473423354a21f752b8bc77dfe8ee636420a2','FIDU1 fiduciary 18 f654d4215bca69758281f41d447895c7fc1d6349a6599019645acfc68dbd55cd','AGOV3',NULL);
INSERT INTO "entries" VALUES(34,'e9a62848-77e9-418e-b015-a16771f16334','e7f4b2a6-3ee2-4654-9168-79fddc801cc5','2026-10-23T04:12:36.327170Z','anchor','--','fiduciary',NULL,'0.00000000','0.00000001','8d15a08c27421eea902059a0f9f7473423354a21f752b8bc77dfe8ee636420a2','fcedeba2e17a82c30bb9d21fe0c479b4e511c6353e56aa825e80d36a8e9cf94e','FIDU1 fiduciary 19 d763c3ef3619a0b59314455ba86f3521d3c0f285b2815f05634ffded318b6a4d','AGOV3',NULL);
INSERT INTO "entries" VALUES(35,'ee4294e7-a055-4afc-bae0-e35b25196bdb','428a7b68-9cec-41ff-a4e0-5ec2d6c1a7d3','2026-10-23T04:12:36.330068Z','anchor','--','fiduciary',NULL,'0.00000000','0.00000001','fcedeba2e17a82c30bb9d21fe0c479b4e511c6353e56aa825e80d36a8e9cf94e','ce3c73dae92d2ddc3edc7df3badab289be2df8c94748113cddb3e4fc21e2d8ae','FIDU1 fiduciary 20 7b244a0395072e0ca5fca967cae43ec84afbd3149840c20bff02aa3cb7fec6b1','AGOV3',NULL);
INSERT INTO "entries" VALUES(36,'19669569-badd-4f81-a9fe-be7fd531eb4d','5024ca8d-901d-4b2b-924c-fb0de803938b','2026-10-23T04:12:36.334464Z','circuit_tripped','--','support-agent/child',NULL,'0.00000000','50.00000000','ce3c73dae92d2ddc3edc7df3badab289be2df8c94748113cddb3e4fc21e2d8ae','dcf964e923eb7fa51d41452fa762aeecde71c7d3b8f5009434d7dc059377a345','FIDU1 revoked 366e78f73df01478814a8f3533baa332502e78724bbd5aa6f6905573c262835e','AGOV3',NULL);
INSERT INTO "entries" VALUES(37,'6b3804ea-3d04-4aba-b5a7-083e112cfc4d','844d1a89-a530-4e5a-89cb-45170d347821','2026-10-23T04:12:36.335418Z','anchor','--','fiduciary',NULL,'0.00000000','0.00000001','dcf964e923eb7fa51d41452fa762aeecde71c7d3b8f5009434d7dc059377a345','9b893be1a6298c35585d0cb80a01267914c29181d07a75fe238b93993bfe8bca','FIDU1 fiduciary 21 f1882b9f10cfaf59fb1aad33631c03fa8937f9303fcf2e4918defa3ddf31c717','AGOV3',NULL);
CREATE TABLE idempotency (
        key        TEXT PRIMARY KEY,
        request    TEXT NOT NULL,
        outcome    TEXT NOT NULL,
        sequence   INTEGER NOT NULL,
        created_at TEXT NOT NULL
    );
INSERT INTO "idempotency" VALUES('fidu1-bind-37a73c553f5e73e4400a8429d489f2a71d7492545afb5ace89db87a5ebc73fcd','{"amount":"250.00000000","child_id":"support-agent","memo":"FIDU1 bind 37a73c553f5e73e4400a8429d489f2a71d7492545afb5ace89db87a5ebc73fcd","op":"delegate","parent_id":"support"}','node',11,'2026-10-23T04:12:36.182832Z');
INSERT INTO "idempotency" VALUES('fidu1-bind-366e78f73df01478814a8f3533baa332502e78724bbd5aa6f6905573c262835e','{"amount":"50.00000000","child_id":"support-agent/child","memo":"FIDU1 bind 366e78f73df01478814a8f3533baa332502e78724bbd5aa6f6905573c262835e","op":"delegate","parent_id":"support-agent"}','node',15,'2026-10-23T04:12:36.200506Z');
CREATE TABLE nodes (
                    scope_id   TEXT PRIMARY KEY,
                    parent_id  TEXT,
                    depth      INTEGER NOT NULL,
                    allocated  TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
INSERT INTO "nodes" VALUES('fiduciary',NULL,0,'1E-8','2026-10-23T04:12:36.143706Z');
INSERT INTO "nodes" VALUES('support',NULL,0,'1000.00000000','2026-10-23T04:12:36.144073Z');
INSERT INTO "nodes" VALUES('support-agent','support',1,'250.00000000','2026-10-23T04:12:36.182832Z');
INSERT INTO "nodes" VALUES('support-agent/child','support-agent',2,'50.00000000','2026-10-23T04:12:36.200506Z');
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
