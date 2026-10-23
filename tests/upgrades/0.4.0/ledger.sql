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
INSERT INTO "control_events" VALUES(1,'506c210f-b3e9-4770-a697-80149386aef7','2026-10-23T04:12:35.610664Z','circuit_tripped','support-agent/child','FIDU1 revoked 7b4873fb81298ab416f68cfcf96e64bcf624b7a28e5113176028ecb6329ddf51','db6998227e6e5cd7ee7873791d6e44ee6acbeb2da25ad95c6aee772ea5e6e16b','506c210f-b3e9-4770-a697-80149386aef7');
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
INSERT INTO "entries" VALUES(1,'63da1520-6220-4ec5-acb4-f8a8f958caf7','dc164461-dbca-48e8-8b20-2872c67f18f5','2026-10-23T04:12:35.420735Z','funding','CR','fiduciary',NULL,'0.00000001','0.00000001','0000000000000000000000000000000000000000000000000000000000000000','2a3caff68b27f53e6e39ed1feab50e5120bc48a31f174dc380781720114dbc05','FIDU1 anchors: this scope spends nothing','AGOV3',NULL);
INSERT INTO "entries" VALUES(2,'d271117e-e4b6-45c2-bf58-77adec3ea0a8','68bcdeda-3263-40bb-ae65-4835151eac8e','2026-10-23T04:12:35.421185Z','funding','CR','support',NULL,'1000.00000000','1000.00000000','2a3caff68b27f53e6e39ed1feab50e5120bc48a31f174dc380781720114dbc05','cd9c6659cc5e14e5537f79586aa5d415d733d11058700649fa930b3fdbc7ab22','root envelope issued','AGOV3',NULL);
INSERT INTO "entries" VALUES(3,'bb5b43fa-993e-4987-9c5e-e9228023ee8f','623a64d1-5056-4e97-a7a0-8fc8b08cae65','2026-10-23T04:12:35.432598Z','anchor','--','fiduciary',NULL,'0.00000000','0.00000001','cd9c6659cc5e14e5537f79586aa5d415d733d11058700649fa930b3fdbc7ab22','a2d535339268906ee84fa9a99744ff1a68bb34f6781e1fce27af52a8780a9740','FIDU1 fiduciary 1 8c4d656a31a54b2cc78bc9f47299ade7dfa126b01f0d8709560d5b9de2939d77','AGOV3',NULL);
INSERT INTO "entries" VALUES(4,'a4110bf4-7b43-4cff-8b19-cf51bfe0a8a7','1ba3f8d9-c8d7-45ba-ab10-f51922af4578','2026-10-23T04:12:35.434354Z','anchor','--','fiduciary',NULL,'0.00000000','0.00000001','a2d535339268906ee84fa9a99744ff1a68bb34f6781e1fce27af52a8780a9740','fb56a52fbb754999e2bdc2fdcfbb8e31b1914f31f2793f7c67a937aaebc40639','FIDU1 fiduciary 2 067d424059c903b2e2f50a5799d6aac10e10d6c709554ec0a0882547031d8a81','AGOV3',NULL);
INSERT INTO "entries" VALUES(5,'b5baa7f3-4a11-42f7-8939-8cab151c21e2','9291cc49-7227-4486-8d36-2a966d752af3','2026-10-23T04:12:35.435875Z','anchor','--','fiduciary',NULL,'0.00000000','0.00000001','fb56a52fbb754999e2bdc2fdcfbb8e31b1914f31f2793f7c67a937aaebc40639','59345dd358749268666c8df9035e5e26cd554afc3f521b0f8c49005f5fda54bb','FIDU1 fiduciary 3 8ff82e636b1794a21a4fec86d9ceaaf03d9435f2462cac419438ba9e7c10848b','AGOV3',NULL);
INSERT INTO "entries" VALUES(6,'e67c84ed-325d-44be-9533-2d03174db46f','b091b4dc-ff35-4fb9-9508-f46f8709f23b','2026-10-23T04:12:35.437717Z','anchor','--','fiduciary',NULL,'0.00000000','0.00000001','59345dd358749268666c8df9035e5e26cd554afc3f521b0f8c49005f5fda54bb','cfa4dd605a29fc961142f2ece78226aba1927ab31dc9e6be3e40d26d0de4af89','FIDU1 fiduciary 4 136ff60d7b3693b2ac6ef987959f41c8ece51e4f6c528e00e7437078306a0887','AGOV3',NULL);
INSERT INTO "entries" VALUES(7,'e9f8d8c7-4aae-43f6-b093-64041e1b5d1f','72dfde86-b607-4cec-b915-284c2f648f5b','2026-10-23T04:12:35.451714Z','anchor','--','fiduciary',NULL,'0.00000000','0.00000001','cfa4dd605a29fc961142f2ece78226aba1927ab31dc9e6be3e40d26d0de4af89','442975de625c3287ea62d6d1c2d38c9399a270c8842450d7c2875e351001742c','FIDU1 fiduciary 5 06080289b8ce96643b4e21c4cd97bc9a6b3a081055f5539e48a66be17ab0925c','AGOV3',NULL);
INSERT INTO "entries" VALUES(8,'9c3e01e1-31bc-43c7-b851-61b57e1e9445','03e24768-5386-460a-8556-0a9330db4445','2026-10-23T04:12:35.453604Z','anchor','--','fiduciary',NULL,'0.00000000','0.00000001','442975de625c3287ea62d6d1c2d38c9399a270c8842450d7c2875e351001742c','dc295c50044767169b32e541035afac708c2f97f951f0def7582af0fa1012eba','FIDU1 fiduciary 6 e4541ea0e13dd174a370850c761cf7e73bd83cb3b7cf6e8db96683223199d068','AGOV3',NULL);
INSERT INTO "entries" VALUES(9,'5c54e002-ba2f-4fd1-96dc-571011bfa9eb','2bb48165-9b2d-4a49-b695-dc7f1d00cd62','2026-10-23T04:12:35.456806Z','anchor','--','fiduciary',NULL,'0.00000000','0.00000001','dc295c50044767169b32e541035afac708c2f97f951f0def7582af0fa1012eba','4b93cef78ab591a61779403b3209b487f1630169cf6445ae53b9683bd6a20c5a','FIDU1 fiduciary 7 4a954df1907a1c11c2557210b3f8373800bca4577ec64700f395628a8626abbe','AGOV3',NULL);
INSERT INTO "entries" VALUES(10,'f4608e96-39de-412f-a519-960a025128d9','b68ac2b2-0df2-4ea9-afdc-8db7b11c3d56','2026-10-23T04:12:35.460975Z','allocation','DR','support','support-agent','250.00000000','750.00000000','4b93cef78ab591a61779403b3209b487f1630169cf6445ae53b9683bd6a20c5a','8698154b5bc7750aaa36c3c5a7a3086815084534581a35d43309ff90a6a7a542','FIDU1 bind 8ab55252d174c6afc50440ea6913403f80e5282db09b93c53fcf3c250f371621','AGOV3',NULL);
INSERT INTO "entries" VALUES(11,'3db96962-e868-4682-aff0-ee8ee299e306','b68ac2b2-0df2-4ea9-afdc-8db7b11c3d56','2026-10-23T04:12:35.460975Z','allocation','CR','support-agent','support','250.00000000','250.00000000','8698154b5bc7750aaa36c3c5a7a3086815084534581a35d43309ff90a6a7a542','2ecb60997fde241c0d35a57da829e7367417c94889d7ceb12286cae6f18386f7','FIDU1 bind 8ab55252d174c6afc50440ea6913403f80e5282db09b93c53fcf3c250f371621','AGOV3',NULL);
INSERT INTO "entries" VALUES(12,'e9101904-c17e-47c7-8424-b11349236a4a','9bb3deed-ca3a-41f2-ad8f-1cd8964545db','2026-10-23T04:12:35.462242Z','anchor','--','fiduciary',NULL,'0.00000000','0.00000001','2ecb60997fde241c0d35a57da829e7367417c94889d7ceb12286cae6f18386f7','aa4dcedca6e769e883accd751ed52a72f76d58c7aae02258ff8d2ba5cac34df0','FIDU1 fiduciary 8 2cb7e107f877e69cd4b6ab932fb606884d8acf920fb3216142b0f17fa4139c92','AGOV3',NULL);
INSERT INTO "entries" VALUES(13,'dfb8b64a-ff5b-4409-aded-a6305ea184e6','27672579-844f-4f49-9a4f-810e92fcdc8c','2026-10-23T04:12:35.472265Z','anchor','--','fiduciary',NULL,'0.00000000','0.00000001','aa4dcedca6e769e883accd751ed52a72f76d58c7aae02258ff8d2ba5cac34df0','77d15bd728d0dbc35a7f58bc9d9182887983328db2323e93a42e6600dd78dd69','FIDU1 fiduciary 9 17afbbde45bc2faaa4f106caa741cc3238abe78ae90fdb2b82ddc585f6fe0d2a','AGOV3',NULL);
INSERT INTO "entries" VALUES(14,'baac601f-72c9-40f7-ae3e-a173d636ba48','3223dfa7-0d5b-4323-bd3c-257a7dacbe48','2026-10-23T04:12:35.478809Z','allocation','DR','support-agent','support-agent/child','50.00000000','200.00000000','77d15bd728d0dbc35a7f58bc9d9182887983328db2323e93a42e6600dd78dd69','bf9b5e4d24b1e9b79a46442994b4385bda8d4caf24cba9de01243df8745b2cbf','FIDU1 bind 7b4873fb81298ab416f68cfcf96e64bcf624b7a28e5113176028ecb6329ddf51','AGOV3',NULL);
INSERT INTO "entries" VALUES(15,'bdc511d2-5e6e-40b3-86b9-3af48f100488','3223dfa7-0d5b-4323-bd3c-257a7dacbe48','2026-10-23T04:12:35.478809Z','allocation','CR','support-agent/child','support-agent','50.00000000','50.00000000','bf9b5e4d24b1e9b79a46442994b4385bda8d4caf24cba9de01243df8745b2cbf','61083466b256c1487598d9ad175dd2b9a201ea6c734aad444582281d1ddf75d7','FIDU1 bind 7b4873fb81298ab416f68cfcf96e64bcf624b7a28e5113176028ecb6329ddf51','AGOV3',NULL);
INSERT INTO "entries" VALUES(16,'5cf4b79c-7955-4405-9494-3e2ce4c4124a','0f90a7ec-b883-4ee8-bdc3-a08943440b5d','2026-10-23T04:12:35.480028Z','anchor','--','fiduciary',NULL,'0.00000000','0.00000001','61083466b256c1487598d9ad175dd2b9a201ea6c734aad444582281d1ddf75d7','a9a882e71e9d791b9b6a979be724f234b4a0e6dccd49d9501f872cefdeaad367','FIDU1 fiduciary 10 42adbd24ca2b57bc7e4980cac8f4f67f6632fcf746598b7ff8f590f626f61dd1','AGOV3',NULL);
INSERT INTO "entries" VALUES(17,'a19b181a-747e-411c-a79a-d4ddf4b2671d','4d3a1038-0222-468c-a11c-ce8c77eb488c','2026-10-23T04:12:35.486393Z','anchor','--','fiduciary',NULL,'0.00000000','0.00000001','a9a882e71e9d791b9b6a979be724f234b4a0e6dccd49d9501f872cefdeaad367','5e5c6fb963d9692537e90c5a2f35911d02eadd4c5c0180fae6bbffdefc41a756','FIDU1 fiduciary 11 95f87bea07b68b7c59239de6c709b2431bf0b06bd97b6b9e529522ce68ef5a89','AGOV3',NULL);
INSERT INTO "entries" VALUES(18,'ca8a517e-b858-49e9-8fc1-3d0316a3f700','c5b8d978-7fa1-403c-b22a-97f19daa1348','2026-10-23T04:12:35.504708Z','hold','DR','support-agent',NULL,'0.01000000','199.99000000','5e5c6fb963d9692537e90c5a2f35911d02eadd4c5c0180fae6bbffdefc41a756','f0ac9f7dcfec46ac30e7f788ef01ce8a9967645544200aa26201da08fe6516ce','interlock:df9f698704bd0d98','AGOV3',NULL);
INSERT INTO "entries" VALUES(19,'0806882c-5873-435b-8b9b-5f3104cd2f26','d790193f-a03d-429e-a165-7f3586dc9cb0','2026-10-23T04:12:35.505069Z','hold_void','CR','support-agent',NULL,'0.01000000','200.00000000','f0ac9f7dcfec46ac30e7f788ef01ce8a9967645544200aa26201da08fe6516ce','77a9d4f6fd4eb46bb54df788aa4877ee517727215fbda6925e6af4930eee7c3f','authorization captured','AGOV3','ca8a517e-b858-49e9-8fc1-3d0316a3f700');
INSERT INTO "entries" VALUES(20,'e6a8b190-de09-4764-adff-41eea47c5fe5','d790193f-a03d-429e-a165-7f3586dc9cb0','2026-10-23T04:12:35.505069Z','spend','DR','support-agent',NULL,'0.01000000','199.99000000','77a9d4f6fd4eb46bb54df788aa4877ee517727215fbda6925e6af4930eee7c3f','e993e1159c5129b02db56b1715a8b3817dcbdd2510d5e086304fd0bb4a879706','interlock:df9f698704bd0d98','AGOV3','ca8a517e-b858-49e9-8fc1-3d0316a3f700');
INSERT INTO "entries" VALUES(21,'af836fbf-395d-4ebd-a415-28482a141afb','b78c5129-adfe-4f72-abdb-5e7a905c5bbb','2026-10-23T04:12:35.548105Z','anchor','--','fiduciary',NULL,'0.00000000','0.00000001','e993e1159c5129b02db56b1715a8b3817dcbdd2510d5e086304fd0bb4a879706','7f7379b0da10676dab888c62a1b4865f7ff51f549e2546ca805cdc85115a01dd','FIDU1 fiduciary 12 9f245c6909454673281a322f8cc55dbf94ec36c2822959a90c5670a4748adeb6','AGOV3',NULL);
INSERT INTO "entries" VALUES(22,'82beba24-6ea8-4f79-b918-6183e562ec30','fc358306-f916-49a9-9e38-82a5c0bb4255','2026-10-23T04:12:35.562577Z','anchor','--','fiduciary',NULL,'0.00000000','0.00000001','7f7379b0da10676dab888c62a1b4865f7ff51f549e2546ca805cdc85115a01dd','de626148671e9c6593b64d24436afaf1d76f4334a7aeba18939017f8d57f3ba9','FIDU1 fiduciary 13 243bdb760d3611a5aa45dcedac2de54cbc89414d416522c3c279dea3030b4eb1','AGOV3',NULL);
INSERT INTO "entries" VALUES(23,'00a1fc43-f0b9-4565-b869-7d6b5367dde7','ce0bd4c3-bb92-4d58-990f-815157ba3b23','2026-10-23T04:12:35.562926Z','anchor','--','fiduciary',NULL,'0.00000000','0.00000001','de626148671e9c6593b64d24436afaf1d76f4334a7aeba18939017f8d57f3ba9','714944cf645d9f3423c8a6bf03745a06777303f29888d6c54001b27fbc62eb00','FIDU1 fiduciary 14 61c414861490e81f446e10d2f8a9ebc77782f52212104dce59f71ca05460f376','AGOV3',NULL);
INSERT INTO "entries" VALUES(24,'0b77d9d1-827c-4454-81bf-c94002a82302','afd03d6d-6499-466d-932c-ab2aa707c327','2026-10-23T04:12:35.566058Z','anchor','--','fiduciary',NULL,'0.00000000','0.00000001','714944cf645d9f3423c8a6bf03745a06777303f29888d6c54001b27fbc62eb00','628e8b54c104e3c730fdd209d4cb04dd86ddf7d1d8307fefde7e0353f477a2ad','FIDU1 fiduciary 15 9bbf1e8e8ece2329a62ef1fd96b45b929f64cf4fe300a6eecb94d484be9d97e8','AGOV3',NULL);
INSERT INTO "entries" VALUES(25,'76de99cc-4fe3-4e06-9a56-2e0a7f72467f','7b3368da-bcc5-4dfe-ae21-a2873688c265','2026-10-23T04:12:35.572211Z','anchor','--','fiduciary',NULL,'0.00000000','0.00000001','628e8b54c104e3c730fdd209d4cb04dd86ddf7d1d8307fefde7e0353f477a2ad','551b514db8d72d97f9287bd1ae6a778334c4df6c203aaf17e56362da1aca02ee','FIDU1 fiduciary 16 fdb090014c3a391c30fb726a18ad7f85a53044fb15410c6bb684013d78cd51e4','AGOV3',NULL);
INSERT INTO "entries" VALUES(26,'da4ff826-3c10-42ee-972e-238c3d764553','d8562286-a4e1-4cc9-baa6-e24a78ae9cf1','2026-10-23T04:12:35.576905Z','anchor','--','fiduciary',NULL,'0.00000000','0.00000001','551b514db8d72d97f9287bd1ae6a778334c4df6c203aaf17e56362da1aca02ee','921a9223a7e81439020059aedcccb036cfccea92405b93ebe9c5b6a6bf009a9d','FIDU1 fiduciary 17 62958bb48edd38ac1e0b3d553570abb1b324cd39ca6ca571a90ead3c95865e01','AGOV3',NULL);
INSERT INTO "entries" VALUES(27,'2a06b496-b8ff-4c6b-ba53-d59dd95cf0ec','48431b14-4976-4081-84aa-99f75eac2d73','2026-10-23T04:12:35.583843Z','hold','DR','support-agent',NULL,'0.01000000','199.98000000','921a9223a7e81439020059aedcccb036cfccea92405b93ebe9c5b6a6bf009a9d','53b21003ea1e1e6ab06d80a99fe2dc8c14bf651232070928b3fbc280892fc120','interlock:21cdc6990bd6a856','AGOV3',NULL);
INSERT INTO "entries" VALUES(28,'cabca6a3-1a2b-4b0e-bb1f-c946c275a3bf','76660017-6c48-43d1-870b-6d7db5c287b6','2026-10-23T04:12:35.584185Z','hold_void','CR','support-agent',NULL,'0.01000000','199.99000000','53b21003ea1e1e6ab06d80a99fe2dc8c14bf651232070928b3fbc280892fc120','55a6759656d542c7fe05dcf02352e856afa8f1764dc7f084045648a18f1ad946','authorization captured','AGOV3','2a06b496-b8ff-4c6b-ba53-d59dd95cf0ec');
INSERT INTO "entries" VALUES(29,'8114525d-5475-4663-b593-21d0ad1e3cba','76660017-6c48-43d1-870b-6d7db5c287b6','2026-10-23T04:12:35.584185Z','spend','DR','support-agent',NULL,'0.01000000','199.98000000','55a6759656d542c7fe05dcf02352e856afa8f1764dc7f084045648a18f1ad946','2cf2b3c359ecd919114756280c3cf0368f2c1f447c3c27fb60243c47b32b7edf','interlock:21cdc6990bd6a856','AGOV3','2a06b496-b8ff-4c6b-ba53-d59dd95cf0ec');
INSERT INTO "entries" VALUES(30,'d7939aae-8194-4282-b1de-4f3b024eb9c0','380db35c-ad43-4eab-a679-6a1acd6a41e5','2026-10-23T04:12:35.588547Z','hold','DR','support-agent',NULL,'0.00100000','199.97900000','2cf2b3c359ecd919114756280c3cf0368f2c1f447c3c27fb60243c47b32b7edf','c105bf4930c7a79f9ae99f3d58fb3dbdc495de9522b6bf9957c5e98a2dc44f4d','FIDU1 tool lookup_order 675784cb90ac4a1e','AGOV3',NULL);
INSERT INTO "entries" VALUES(31,'d9be8375-33c6-417a-afc4-dbc9bc415360','60f35f05-aab4-4379-9114-590ee5911040','2026-10-23T04:12:35.588859Z','hold_void','CR','support-agent',NULL,'0.00100000','199.98000000','c105bf4930c7a79f9ae99f3d58fb3dbdc495de9522b6bf9957c5e98a2dc44f4d','d52b80acb09d68f00d9ea13ce7b41a1c963f476b3de156e5f06a7b0bdc4ae331','authorization captured','AGOV3','d7939aae-8194-4282-b1de-4f3b024eb9c0');
INSERT INTO "entries" VALUES(32,'ddac3287-77ab-4ca8-8038-e359447e3d54','60f35f05-aab4-4379-9114-590ee5911040','2026-10-23T04:12:35.588859Z','spend','DR','support-agent',NULL,'0.00100000','199.97900000','d52b80acb09d68f00d9ea13ce7b41a1c963f476b3de156e5f06a7b0bdc4ae331','69a9e7e5bcfd6c0f62aed0b350e5c428e675348675d2c4b7a85809000aab5f17','FIDU1 tool lookup_order','AGOV3','d7939aae-8194-4282-b1de-4f3b024eb9c0');
INSERT INTO "entries" VALUES(33,'1cfb016f-3a5a-48e0-a624-6007b918e29f','fd3da172-66e6-4404-ab92-d7f1ef72950b','2026-10-23T04:12:35.591187Z','anchor','--','fiduciary',NULL,'0.00000000','0.00000001','69a9e7e5bcfd6c0f62aed0b350e5c428e675348675d2c4b7a85809000aab5f17','14933dd6e041be3430b43a181ab959e06ca9b336a68cc2fee5075d7cb92fe3e0','FIDU1 fiduciary 18 c45956ff6da0f888f848bed8695210be87cc27a71f7b2ea1c6411c1acce1e946','AGOV3',NULL);
INSERT INTO "entries" VALUES(34,'63f1c9d3-c3a5-4488-96ce-7182806a13da','fd1e639f-6fa7-4e40-83d3-5dc7c26950b7','2026-10-23T04:12:35.603697Z','anchor','--','fiduciary',NULL,'0.00000000','0.00000001','14933dd6e041be3430b43a181ab959e06ca9b336a68cc2fee5075d7cb92fe3e0','55c38c388ff804c786db20acc779822bbdc9a61fcf8c52032e2f8c679dbce55b','FIDU1 fiduciary 19 e2be09029e7ee8ee404f7365855ba0614bd9c37ea40e362a090e0b29a6391c29','AGOV3',NULL);
INSERT INTO "entries" VALUES(35,'ab961c16-fb1b-4663-a5d3-35d4debb7944','d1019ffa-99b2-406b-8a19-6d430d602048','2026-10-23T04:12:35.606503Z','anchor','--','fiduciary',NULL,'0.00000000','0.00000001','55c38c388ff804c786db20acc779822bbdc9a61fcf8c52032e2f8c679dbce55b','db6998227e6e5cd7ee7873791d6e44ee6acbeb2da25ad95c6aee772ea5e6e16b','FIDU1 fiduciary 20 9b67dc277e15f399638fe44ae8191eb010f0ee54cd5e8d10a60a7e3a237ae703','AGOV3',NULL);
INSERT INTO "entries" VALUES(36,'506c210f-b3e9-4770-a697-80149386aef7','798c7596-4480-4b2d-8eeb-a02f4030c57d','2026-10-23T04:12:35.610664Z','circuit_tripped','--','support-agent/child',NULL,'0.00000000','50.00000000','db6998227e6e5cd7ee7873791d6e44ee6acbeb2da25ad95c6aee772ea5e6e16b','ba7acc55357e0e5bb4c3b88aacb9d091ced1bf95bfba76c0e9b823ca7bc24389','FIDU1 revoked 7b4873fb81298ab416f68cfcf96e64bcf624b7a28e5113176028ecb6329ddf51','AGOV3',NULL);
INSERT INTO "entries" VALUES(37,'3ecb9b30-6042-43ef-8b38-117b4d6ad4bf','a3a94f15-a53f-4322-a670-db14f1099aba','2026-10-23T04:12:35.611565Z','anchor','--','fiduciary',NULL,'0.00000000','0.00000001','ba7acc55357e0e5bb4c3b88aacb9d091ced1bf95bfba76c0e9b823ca7bc24389','d05b37768ab8530653bf73dcade15782b00254101a1ec13ad2fa610dd4fa6c25','FIDU1 fiduciary 21 37799f22c54e8ab572fe069448e7b12c0eb6c28e36d40040b02b9f96a644ad12','AGOV3',NULL);
CREATE TABLE idempotency (
        key        TEXT PRIMARY KEY,
        request    TEXT NOT NULL,
        outcome    TEXT NOT NULL,
        sequence   INTEGER NOT NULL,
        created_at TEXT NOT NULL
    );
INSERT INTO "idempotency" VALUES('fidu1-bind-8ab55252d174c6afc50440ea6913403f80e5282db09b93c53fcf3c250f371621','{"amount":"250.00000000","child_id":"support-agent","memo":"FIDU1 bind 8ab55252d174c6afc50440ea6913403f80e5282db09b93c53fcf3c250f371621","op":"delegate","parent_id":"support"}','node',11,'2026-10-23T04:12:35.460975Z');
INSERT INTO "idempotency" VALUES('fidu1-bind-7b4873fb81298ab416f68cfcf96e64bcf624b7a28e5113176028ecb6329ddf51','{"amount":"50.00000000","child_id":"support-agent/child","memo":"FIDU1 bind 7b4873fb81298ab416f68cfcf96e64bcf624b7a28e5113176028ecb6329ddf51","op":"delegate","parent_id":"support-agent"}','node',15,'2026-10-23T04:12:35.478809Z');
CREATE TABLE nodes (
                    scope_id   TEXT PRIMARY KEY,
                    parent_id  TEXT,
                    depth      INTEGER NOT NULL,
                    allocated  TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
INSERT INTO "nodes" VALUES('fiduciary',NULL,0,'1E-8','2026-10-23T04:12:35.420735Z');
INSERT INTO "nodes" VALUES('support',NULL,0,'1000.00000000','2026-10-23T04:12:35.421185Z');
INSERT INTO "nodes" VALUES('support-agent','support',1,'250.00000000','2026-10-23T04:12:35.460975Z');
INSERT INTO "nodes" VALUES('support-agent/child','support-agent',2,'50.00000000','2026-10-23T04:12:35.478809Z');
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
